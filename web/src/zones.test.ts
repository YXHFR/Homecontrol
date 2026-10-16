import { describe, expect, it } from "vitest";
import { inPolygon } from "./editing";
import type { FloorModel, HomeDocument } from "./homefile";
import {
  addZone,
  deleteZone,
  findZone,
  nearestOnWall,
  planZone,
  renameZone,
  roomAround,
  setZoneKind,
  zoneProblem,
} from "./zones";
import { ZONE_KINDS, ZONE_STYLES } from "./zoneStyles";

/** Ett L-format rum: 4 × 4 meter där den nedre högra kvadranten saknas. */
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
          rooms: [
            {
              id: "vardagsrum",
              name: "Vardagsrum",
              polygon: [
                [0, 0],
                [4, 0],
                [4, 2],
                [2, 2],
                [2, 4],
                [0, 4],
              ],
              walls: ["gips", "gips", "gips", "gips", "gips", "gips"],
              lamps: [{ id: "taklampa", name: "Taklampa", position: [1, 1], zone_id: null }],
            },
          ],
        },
      ],
    },
  };
}

const floor = (document: HomeDocument): FloorModel => document.home.floors[0];

const square = (x: number, y: number, side = 1) => [
  { x, y },
  { x: x + side, y },
  { x: x + side, y: y + side },
  { x, y: y + side },
];

describe("zonernas sorter", () => {
  it("har var sin färg", () => {
    const colors = ZONE_KINDS.map((kind) => ZONE_STYLES[kind].color);
    expect(new Set(colors).size).toBe(4);
  });
});

describe("inPolygon", () => {
  const room = floor(home()).rooms[0].polygon.map(([x, y]) => ({ x, y }));

  it("räknar kanten som inne men inte hacket i ett L", () => {
    expect(inPolygon({ x: 1, y: 1 }, room)).toBe(true);
    expect(inPolygon({ x: 0, y: 3 }, room)).toBe(true); // på väggen
    expect(inPolygon({ x: 3, y: 2 }, room)).toBe(true); // på väggen in mot hacket
    expect(inPolygon({ x: 3, y: 3 }, room)).toBe(false);
  });
});

describe("var en zon kan ritas", () => {
  it("i ett rum, även längs väggen", () => {
    expect(roomAround(floor(home()), square(0, 3))?.id).toBe("vardagsrum");
    expect(zoneProblem(floor(home()), square(0, 3))).toBeNull();
  });

  it("inte utanför ett rum eller genom en vägg", () => {
    expect(zoneProblem(floor(home()), square(3, 3, 0.5))).toMatch(/inne i ett rum/);
    expect(zoneProblem(floor(home()), square(3.5, 0.5))).toMatch(/inne i ett rum/);
    // Hörnen ligger i båda benen av L:et, men kanten mellan dem går genom hacket.
    const across = [
      { x: 1, y: 1 },
      { x: 3.5, y: 1 },
      { x: 1, y: 3.5 },
    ];
    expect(zoneProblem(floor(home()), across)).toMatch(/inne i ett rum/);
  });

  it("inte som en åtta, för liten eller med för få hörn", () => {
    const eight = [
      { x: 0, y: 0 },
      { x: 1, y: 1 },
      { x: 1, y: 0 },
      { x: 0, y: 1 },
    ];
    expect(zoneProblem(floor(home()), eight)).toMatch(/korsar/);
    expect(zoneProblem(floor(home()), square(1, 1, 0.1))).toMatch(/för liten/);
    expect(zoneProblem(floor(home()), square(1, 1).slice(0, 2))).toMatch(/tre hörn/);
  });
});

describe("nearestOnWall", () => {
  it("drar en punkt nära väggen till väggen, men inte en punkt längre bort", () => {
    expect(nearestOnWall(floor(home()), { x: 0.02, y: 3 }, 0.05)).toEqual({ x: 0, y: 3 });
    expect(nearestOnWall(floor(home()), { x: 3, y: 2.03 }, 0.05)).toEqual({ x: 3, y: 2 });
    expect(nearestOnWall(floor(home()), { x: 1, y: 1 }, 0.05)).toBeNull();
  });
});

describe("planZone", () => {
  it("ger ett problem för en zon utanför rummen", () => {
    expect(planZone(home(), floor(home()), "vanlig", square(3, 3, 0.5))).toEqual({
      problem: "Rita zonen inne i ett rum, utan att den går ut genom någon vägg.",
    });
  });

  it("ger samma id när ändringen görs på ett nyare hem", () => {
    const plan = planZone(home(), floor(home()), "entré", square(0, 3));
    if ("problem" in plan) {
      throw new Error(plan.problem);
    }
    // Någon annan har hunnit byta namn på rummet; zonen läggs ändå till.
    const newer = structuredClone(home());
    newer.home.floors[0].rooms[0].name = "Stora rummet";

    const saved = plan.edit(newer);

    expect(findZone(saved, plan.zoneId)?.zone).toMatchObject({ kind: "entré", name: "Entré 1" });
    expect(findZone(saved, plan.zoneId)?.room.name).toBe("Stora rummet");
  });
});

describe("ändringar av zoner", () => {
  it("en ny zon får ett namn efter sin sort och ett unikt id", () => {
    const first = addZone(home(), "vardagsrum", "stillhet", square(0, 0));
    const second = addZone(first.document, "vardagsrum", "stillhet", square(0, 2));

    expect(first.name).toBe("Stillhet 1");
    expect(second.name).toBe("Stillhet 2");
    expect(second.zoneId).not.toBe(first.zoneId);
    expect(findZone(second.document, second.zoneId)?.zone).toEqual({
      id: second.zoneId,
      name: "Stillhet 2",
      kind: "stillhet",
      polygon: [
        [0, 2],
        [1, 2],
        [1, 3],
        [0, 3],
      ],
    });
  });

  it("ett givet id som redan används byts ut", () => {
    const { document, zoneId } = addZone(home(), "vardagsrum", "vanlig", square(0, 0), {
      zoneId: "vardagsrum",
      name: "Skrivbord",
    });
    expect(zoneId).not.toBe("vardagsrum");
    expect(findZone(document, zoneId)?.zone.name).not.toBe("Skrivbord");
  });

  it("byter namn och sort och lämnar det gamla dokumentet orört", () => {
    const original = addZone(home(), "vardagsrum", "vanlig", square(0, 0));
    const renamed = renameZone(original.document, original.zoneId, "Skrivbord");
    const changed = setZoneKind(renamed, original.zoneId, "ignorera");

    expect(findZone(changed, original.zoneId)?.zone).toMatchObject({
      id: original.zoneId,
      name: "Skrivbord",
      kind: "ignorera",
    });
    expect(findZone(original.document, original.zoneId)?.zone).toMatchObject({
      name: "Zon 1",
      kind: "vanlig",
    });
  });

  it("när en zon tas bort hör lamporna i den till hela rummet", () => {
    const { document, zoneId } = addZone(home(), "vardagsrum", "stillhet", square(0, 0));
    const withLamp = structuredClone(document);
    const lamps = withLamp.home.floors[0].rooms[0].lamps ?? [];
    lamps[0].zone_id = zoneId;

    const removed = deleteZone(withLamp, zoneId);

    expect(findZone(removed, zoneId)).toBeNull();
    expect(removed.home.floors[0].rooms[0].zones).toEqual([]);
    expect(removed.home.floors[0].rooms[0].lamps?.[0].zone_id).toBeNull();
  });
});
