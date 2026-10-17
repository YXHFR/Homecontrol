import { describe, expect, it } from "vitest";
import type { HomeDocument } from "./homefile";
import type { Point } from "./protocol";
import {
  DEFAULT_PLACEMENT,
  importScan,
  MAX_ITEMS,
  placeScan,
  readMatrix,
  readScan,
  type Scan,
  ScanError,
} from "./roomplan";
/** Den påhittade tvåan i web/exempel: vardagsrum 5 × 4 m och sovrum 3 × 4 m, skannad snett. */
import EXAMPLE from "../exempel/roomplan-exempel.json?raw";

/** Matrisen för en yta som är vriden `degrees` runt y-axeln och står i (x, z), kolumnvis. */
function transform(x: number, z: number, degrees = 0): number[] {
  const a = (degrees * Math.PI) / 180;
  const [c, s] = [Math.cos(a), Math.sin(a)];
  return [c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, x, 0, z, 1];
}

/** En vägg (eller dörr, fönster) mellan två punkter (x, z) i skanningen. */
function surface(category: string, [x0, z0]: number[], [x1, z1]: number[], height = 2.4) {
  const degrees = (-Math.atan2(z1 - z0, x1 - x0) * 180) / Math.PI;
  return {
    category: { [category]: {} },
    dimensions: [Math.hypot(x1 - x0, z1 - z0), height, 0],
    transform: transform((x0 + x1) / 2, (z0 + z1) / 2, degrees),
  };
}

/** Ett rum 4 × 3 m i skanningen, med x åt höger och z nedåt på kartan, utan golv. */
function boxScan(extra: Record<string, unknown> = {}, x = 0): Record<string, unknown> {
  return {
    walls: [
      surface("wall", [x, 0], [x + 4, 0]),
      surface("wall", [x + 4, 0], [x + 4, 3]),
      surface("wall", [x + 4, 3], [x, 3]),
      surface("wall", [x, 3], [x, 0]),
    ],
    ...extra,
  };
}

function bounds(points: Point[]) {
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  return {
    minX: Math.min(...xs),
    minY: Math.min(...ys),
    width: Math.max(...xs) - Math.min(...xs),
    height: Math.max(...ys) - Math.min(...ys),
  };
}

function center(points: Point[]): Point {
  return {
    x: points.reduce((sum, p) => sum + p.x, 0) / points.length,
    y: points.reduce((sum, p) => sum + p.y, 0) / points.length,
  };
}

function home(): HomeDocument {
  return {
    format: "homecontrol-hem",
    version: 1,
    home: {
      name: "Hemmet",
      floors: [
        {
          id: "plan",
          name: "Plan 1",
          level: 0,
          signal_sources: [{ id: "router", name: "Router", position: [1, 1], height: 1 }],
          rooms: [
            {
              id: "vardagsrum",
              name: "Vardagsrum",
              polygon: [
                [1, 1],
                [4, 1],
                [4, 3],
                [1, 3],
              ],
              walls: ["gips", "gips", "gips", "gips"],
            },
          ],
        },
      ],
    },
  };
}

describe("exempelskanningen", () => {
  const scan = placeScan(readScan(EXAMPLE), DEFAULT_PLACEMENT);
  const [living, bedroom] = scan.rooms;

  it("blir två rum med namn efter skanningens avdelningar", () => {
    expect(scan.rooms.map((room) => room.name)).toEqual(["Vardagsrum", "Sovrum"]);
    expect(scan.problems).toEqual([]);
  });

  it("räta upp den snett skannade lägenheten, så att väggarna går rakt", () => {
    const b = bounds(living.polygon);
    expect(b.width).toBeCloseTo(5, 2);
    expect(b.height).toBeCloseTo(4, 2);
    expect(bounds(bedroom.polygon).width).toBeCloseTo(3, 2);
    // Sovrummet ligger till höger om vardagsrummet, som i skanningen.
    expect(bounds(bedroom.polygon).minX).toBeCloseTo(b.minX + 5.12, 2);
  });

  it("gör dörrarna till dörröppningar och fönstren till glas i rätt vägg", () => {
    expect(scan.doorways).toBe(2);
    expect(scan.windows).toBe(2);
    // Dörren mellan rummen finns i båda rummens vägg, på samma ställe.
    const doorway = (room: (typeof scan.rooms)[number]) =>
      room.polygon.filter((_, i) => room.walls[i] === "dörröppning");
    expect(doorway(living)).toHaveLength(2); // entrédörren och dörren till sovrummet
    expect(doorway(bedroom)).toHaveLength(1);
    expect(living.walls.filter((kind) => kind === "glas")).toHaveLength(1);
    expect(bedroom.walls.filter((kind) => kind === "glas")).toHaveLength(1);
    // Fönstret i vardagsrummet sitter i den övre väggen, 1,5–3,5 m från vänstra hörnet.
    const window = living.polygon[living.walls.indexOf("glas")];
    const b = bounds(living.polygon);
    expect(window.x - b.minX).toBeCloseTo(1.5, 1);
    expect(window.y).toBeCloseTo(b.minY, 2);
  });

  it("sparar takhöjden från väggarna", () => {
    expect(living.ceilingHeight).toBeCloseTo(2.5);
    expect(bedroom.ceilingHeight).toBeCloseTo(2.5);
  });

  it("ger möblerna på svenska, sängen i sovrummet och soffan i vardagsrummet", () => {
    expect(scan.furniture.map((item) => item.name)).toEqual([
      "Soffa",
      "Bord",
      "Tv",
      "Förvaring",
      "Säng",
      "Förvaring",
    ]);
    const bed = scan.furniture[4];
    const b = bounds(bedroom.polygon);
    const at = center(bed.polygon);
    expect(at.x).toBeGreaterThan(b.minX);
    expect(at.x).toBeLessThan(b.minX + b.width);
    expect(bounds(bed.polygon).width).toBeCloseTo(1.6, 2);
    expect(bounds(bed.polygon).height).toBeCloseTo(2.0, 2);
  });
});

describe("omräkningen av koordinater", () => {
  it("gör skanningens x till höger och z till nedåt på kartan, utan spegling", () => {
    const scan = readScan(
      JSON.stringify(
        boxScan({
          objects: [
            { category: { bed: {} }, dimensions: [1, 0.5, 1], transform: transform(3, 0.5) },
          ],
        }),
      ),
    );
    const placed = placeScan(scan, { quarterTurns: 0, position: { x: 0, y: 0 } });
    // Sängen står i rummets övre högra hörn: stor x och liten z i skanningen.
    const bed = center(placed.furniture[0].polygon);
    expect(bed.x).toBeCloseTo(3);
    expect(bed.y).toBeCloseTo(0.5);
  });

  it("läser matrisen kolumnvis, som fyra kolumner eller radvis på samma sätt", () => {
    const columns = transform(2, 3, 30);
    const nested = [0, 1, 2, 3].map((i) => columns.slice(4 * i, 4 * i + 4));
    const rows = [0, 1, 2, 3].flatMap((row) => [0, 1, 2, 3].map((col) => columns[4 * col + row]));
    expect(readMatrix(nested)).toEqual(columns);
    expect(readMatrix(rows)).toEqual(columns);
    expect(readMatrix([1, 2, 3])).toBeNull();
  });

  it("ritar rummet efter väggarna när skanningen saknar golv", () => {
    const scan = readScan(JSON.stringify(boxScan()));
    const b = bounds(scan.rooms[0].polygon);
    // Väggarnas mittlinjer möts i hörnen, så rummet blir 4 × 3 m.
    expect(b.width).toBeCloseTo(4);
    expect(b.height).toBeCloseTo(3);
    expect(scan.problems.join(" ")).toContain("inga golv");
  });

  it("läser golvets hörn med golvets matris", () => {
    // Golvets egna y pekar mot skanningens −z, som i RoomPlan.
    const floor = {
      category: { floor: {} },
      dimensions: [4, 3, 0],
      transform: [1, 0, 0, 0, 0, 0, -1, 0, 0, 1, 0, 0, 2, -1.4, 1.5, 1],
      polygonCorners: [
        [-2, 1.5, 0],
        [2, 1.5, 0],
        [2, -1.5, 0],
        [-2, -1.5, 0],
      ],
    };
    const scan = readScan(JSON.stringify(boxScan({ floors: [floor] })));
    expect(bounds(scan.rooms[0].polygon)).toEqual({
      minX: expect.closeTo(0) as number,
      minY: expect.closeTo(0) as number,
      width: expect.closeTo(4) as number,
      height: expect.closeTo(3) as number,
    });
    expect(scan.problems).toEqual([]);
  });
});

describe("vridning och flytt", () => {
  const scan: Scan = readScan(
    JSON.stringify(
      boxScan({
        objects: [
          { category: { sofa: {} }, dimensions: [1, 1, 1], transform: transform(3.5, 1.5) },
        ],
      }),
    ),
  );

  it("lägger skanningens övre vänstra hörn där användaren vill", () => {
    const placed = placeScan(scan, { quarterTurns: 0, position: { x: 2, y: 5 } });
    expect(bounds(placed.rooms[0].polygon)).toMatchObject({
      minX: expect.closeTo(2) as number,
      minY: expect.closeTo(5) as number,
    });
    expect(center(placed.furniture[0].polygon).x).toBeCloseTo(5.5);
  });

  it("vrider ett kvarts varv medurs i taget", () => {
    const at = (turns: number) => {
      const placed = placeScan(scan, { quarterTurns: turns, position: { x: 0, y: 0 } });
      return { room: bounds(placed.rooms[0].polygon), sofa: center(placed.furniture[0].polygon) };
    };
    // Soffan står vid rummets högra vägg. Ett kvarts varv medurs flyttar den till den nedre.
    expect(at(0).sofa).toEqual({ x: expect.closeTo(3.5), y: expect.closeTo(1.5) });
    const turned = at(1);
    expect(turned.room.width).toBeCloseTo(3);
    expect(turned.room.height).toBeCloseTo(4);
    expect(turned.sofa).toEqual({ x: expect.closeTo(1.5), y: expect.closeTo(3.5) });
    expect(at(2).sofa).toEqual({ x: expect.closeTo(0.5), y: expect.closeTo(1.5) });
    expect(at(4)).toEqual(at(0));
    expect(at(-1)).toEqual(at(3));
  });

  it("flyttar aldrig något utanför planet", () => {
    const placed = placeScan(scan, { quarterTurns: 0, position: { x: -3, y: -3 } });
    expect(bounds(placed.rooms[0].polygon).minX).toBeCloseTo(0);
  });
});

describe("en tolerant läsare", () => {
  it("säger på svenska när filen inte är en skanning", () => {
    expect(() => readScan("<usdz>")).toThrow(ScanError);
    expect(() => readScan("<usdz>")).toThrow("inte en JSON-fil");
    expect(() => readScan("[]")).toThrow("inte ut som en RoomPlan-skanning");
    expect(() => readScan('{"walls": []}')).toThrow("inga väggar eller golv");
  });

  it("hoppar över det som inte går att läsa och säger vad", () => {
    const scan = readScan(
      JSON.stringify(
        boxScan({
          doors: [{ category: "door", transform: transform(2, 0) }],
          objects: [
            { category: { piano: {} }, dimensions: [1, 1, 1], transform: transform(1, 1) },
            { category: { sofa: {} }, dimensions: [1, 1] },
          ],
        }),
      ),
    );
    expect(scan.rooms).toHaveLength(1);
    expect(scan.furniture.map((item) => item.name)).toEqual(["Möbel"]);
    expect(scan.problems).toEqual(
      expect.arrayContaining([
        "Dörr 1 saknar mått och hoppades över.",
        "Möbel 2 saknar mått eller läge och hoppades över.",
        'Möbler av okänd sort visas som "Möbel" (sorten i skanningen: piano).',
      ]),
    );
  });

  it("tar med en öppning som dörröppning och säger till om en dörr som inte sitter i någon vägg", () => {
    const scan = readScan(
      JSON.stringify(
        boxScan({
          openings: [surface("opening", [1, 3], [2.5, 3])],
          doors: [surface("door", [10, 10], [11, 10])],
        }),
      ),
    );
    const room = scan.rooms[0];
    expect(room.walls).toEqual(["gips", "gips", "gips", "dörröppning", "gips", "gips"]);
    expect(scan.problems).toContain(
      "1 dörr eller öppning låg inte vid någon vägg och kom inte med.",
    );
  });

  it("slår inte upp sorter i objektens prototyp", () => {
    const scan = readScan(
      JSON.stringify(
        boxScan({
          objects: ["constructor", "__proto__", "toString"].map((category) => ({
            category,
            dimensions: [1, 1, 1],
            transform: transform(1, 1),
          })),
          sections: [{ label: { toString: {} }, center: [2, 0, 1.5] }],
        }),
      ),
    );
    expect(scan.furniture.map((item) => item.name)).toEqual(["Möbel", "Möbel", "Möbel"]);
    expect(scan.rooms[0].name).toBe("Rum 1");
  });

  it("läser högst MAX_ITEMS av varje sort och säger till", () => {
    const sofa = { category: { sofa: {} }, dimensions: [1, 1, 1], transform: transform(1, 1) };
    const scan = readScan(
      JSON.stringify(boxScan({ objects: Array.from({ length: MAX_ITEMS + 5 }, () => sofa) })),
    );
    expect(scan.furniture).toHaveLength(MAX_ITEMS);
    expect(scan.problems.join(" ")).toContain(`Bara de första ${MAX_ITEMS} lästes.`);
  });

  it("läser ett rum i taget ur en skanning av flera rum", () => {
    const scan = readScan(JSON.stringify({ rooms: [boxScan(), boxScan({}, 6)] }));
    expect(scan.rooms).toHaveLength(2);
    expect(scan.rooms.map((room) => room.name)).toEqual(["Rum 1", "Rum 2"]);
  });
});

describe("att lägga in skanningen i hemmet", () => {
  const scan = placeScan(readScan(EXAMPLE), DEFAULT_PLACEMENT);

  it("som en ny våning ovanför den översta", () => {
    const { document, floorId } = importScan(home(), scan, { kind: "new" });
    const floor = document.home.floors.find((f) => f.id === floorId);
    expect(document.home.floors).toHaveLength(2);
    expect(floor).toMatchObject({ name: "Skannad våning", level: 1 });
    expect(floor?.rooms.map((room) => room.id)).toEqual(["vardagsrum-2", "sovrum"]);
    expect(floor?.rooms[0].ceiling_height).toBe(2.5);
    expect(floor?.furniture).toHaveLength(6);
    // Väggarna har lika många sorter som rummet har sidor, avrundat till centimeter.
    for (const room of floor?.rooms ?? []) {
      expect(room.walls).toHaveLength(room.polygon.length);
      expect(room.polygon.flat().every((v) => Math.round(v * 100) / 100 === v)).toBe(true);
    }
  });

  it("i stället för våningens rum, med våningens namn och routrar kvar", () => {
    const { document, floorId } = importScan(home(), scan, { kind: "replace", floorId: "plan" });
    const floor = document.home.floors[0];
    expect(floorId).toBe("plan");
    expect(document.home.floors).toHaveLength(1);
    expect(floor.name).toBe("Plan 1");
    expect(floor.signal_sources).toHaveLength(1);
    expect(floor.rooms.map((room) => room.id)).toEqual(["vardagsrum", "sovrum"]);
  });

  it("ändrar inte hemmet den fick", () => {
    const before = home();
    const copy = JSON.stringify(before);
    importScan(before, scan, { kind: "replace", floorId: "plan" });
    expect(JSON.stringify(before)).toBe(copy);
  });
});
