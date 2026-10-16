import { describe, expect, it } from "vitest";
import type { HomeDocument, RoomSettings } from "./homefile";
import {
  DEFAULT_SETTINGS,
  roomSettings,
  bearing,
  cycleWall,
  deleteItem,
  findItem,
  moveItem,
  moveItemToRoom,
  normalizeRotation,
  placeItem,
  roomAt,
  setRoomSettings,
  settingsProblem,
  updateItem,
} from "./placing";

/** Två rum som delar väggen x = 3, och en lampa i en zon i köket. */
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
              id: "kok",
              name: "Kök",
              polygon: [
                [0, 0],
                [3, 0],
                [3, 2],
                [0, 2],
              ],
              walls: ["gips", "gips", "gips", "gips"],
              zones: [
                {
                  id: "bank",
                  name: "Bänk",
                  kind: "vanlig",
                  polygon: [
                    [0, 0],
                    [1, 0],
                    [1, 1],
                  ],
                },
              ],
              lamps: [{ id: "taklampa", name: "Taklampa", position: [1, 1], zone_id: "bank" }],
            },
            {
              id: "hall",
              name: "Hall",
              polygon: [
                [3, 0],
                [5, 0],
                [5, 4],
                [3, 4],
              ],
              walls: ["gips", "gips", "gips", "gips"],
            },
          ],
        },
      ],
    },
  };
}

const rooms = (document: HomeDocument) => document.home.floors[0].rooms;

/** Som placeItem, men testet avbryts om inget placerades. */
function place(...args: Parameters<typeof placeItem>) {
  const placed = placeItem(...args);
  if (placed === null) {
    throw new Error("Inget placerades");
  }
  return placed;
}

describe("väggarna", () => {
  it("växlar gips → betong → trä → glas → dörröppning → gips", () => {
    let document = home();
    const seen: string[] = [];
    for (let i = 0; i < 5; i++) {
      document = cycleWall(document, "kok", 0);
      seen.push(rooms(document)[0].walls[0]);
    }
    expect(seen).toEqual(["betong", "trä", "glas", "dörröppning", "gips"]);
  });

  it("byter en delad vägg i båda rummen, även när den bara delas en bit", () => {
    // Kökets vägg (3,0)–(3,2) är en del av hallens vägg (3,4)–(3,0).
    const document = cycleWall(home(), "kok", 1);

    expect(rooms(document)[0].walls).toEqual(["gips", "betong", "gips", "gips"]);
    expect(rooms(document)[1].walls).toEqual(["gips", "gips", "gips", "betong"]);
  });

  it("lämnar hemmet orört om rummet inte finns", () => {
    const document = home();
    expect(cycleWall(document, "saknas", 0)).toBe(document);
  });
});

describe("att placera", () => {
  it("ett rumskort hamnar i rummet där man klickar, med radar och vridning 0", () => {
    const placed = place(home(), "node", "plan", { x: 4.123, y: 1 });

    expect(placed.name).toBe("Rumskort 1");
    expect(rooms(placed.document)[1].nodes).toEqual([
      {
        id: "rumskort-1",
        name: "Rumskort 1",
        position: [4.12, 1],
        height: 2,
        rotation: 0,
        mirrored: false,
        capabilities: ["radar", "csi", "lux"],
      },
    ]);
  });

  it("rumskort och lampor kan inte placeras utanför rummen, men routrar kan", () => {
    expect(placeItem(home(), "lamp", "plan", { x: 8, y: 8 })).toBeNull();
    const placed = placeItem(home(), "router", "plan", { x: 8, y: 8 });
    expect(placed?.document.home.floors[0].signal_sources).toEqual([
      { id: "router-1", name: "Router 1", position: [8, 8], height: 1 },
    ]);
  });

  it("får ett nytt namn och id om namnet redan finns, men behåller ett givet id", () => {
    const first = place(home(), "lamp", "plan", { x: 4, y: 1 });
    const second = place(first.document, "lamp", "plan", { x: 4, y: 2 });
    expect(second.name).toBe("Lampa 2");

    const again = placeItem(home(), "lamp", "plan", { x: 4, y: 2 }, { id: "x", name: "X" });
    expect(again?.id).toBe("x");
    // Ett givet id som redan används byts ut.
    const taken = placeItem(home(), "lamp", "plan", { x: 4, y: 2 }, { id: "taklampa", name: "T" });
    expect(taken?.id).not.toBe("taklampa");
  });

  it("hittar rummet för en punkt, även på väggen", () => {
    const floor = home().home.floors[0];
    expect(roomAt(floor, { x: 1, y: 1 })?.id).toBe("kok");
    expect(roomAt(floor, { x: 4, y: 3 })?.id).toBe("hall");
    expect(roomAt(floor, { x: 0, y: 1 })?.id).toBe("kok");
    expect(roomAt(floor, { x: 9, y: 9 })).toBeNull();
  });
});

describe("att ändra det som är placerat", () => {
  it("en lampa som flyttas till ett annat rum styrs av det och släpper zonen", () => {
    const document = moveItem(home(), "taklampa", { x: 4, y: 3 });

    expect(rooms(document)[0].lamps).toEqual([]);
    expect(rooms(document)[1].lamps).toEqual([
      { id: "taklampa", name: "Taklampa", position: [4, 3], zone_id: null },
    ]);
  });

  it("en lampa som flyttas inom rummet, eller utanför alla rum, behåller rum och zon", () => {
    const inside = moveItem(home(), "taklampa", { x: 2, y: 1.5 });
    expect(findItem(inside, "taklampa")?.room?.id).toBe("kok");
    expect(rooms(inside)[0].lamps?.[0].zone_id).toBe("bank");

    const outside = moveItem(home(), "taklampa", { x: 9, y: 9 });
    expect(findItem(outside, "taklampa")?.room?.id).toBe("kok");
    expect(rooms(outside)[0].lamps?.[0].position).toEqual([9, 9]);
  });

  it("en lampa som släpps på en delad vägg, eller kopplats om i listan, behåller sitt rum", () => {
    // Väggen x = 3 delas av köket och hallen.
    expect(findItem(moveItem(home(), "taklampa", { x: 3, y: 1 }), "taklampa")?.room?.id).toBe(
      "kok",
    );
    // Kopplad till hallen men står kvar i köket: en liten dragning byter inte tillbaka.
    const relinked = moveItemToRoom(home(), "taklampa", "hall");
    const nudged = moveItem(relinked, "taklampa", { x: 1.05, y: 1 });
    expect(findItem(nudged, "taklampa")?.room?.id).toBe("hall");
  });

  it("en lampa kopplas till ett annat rum utan att flyttas", () => {
    const document = moveItemToRoom(home(), "taklampa", "hall");
    const found = findItem(document, "taklampa");
    expect(found?.room?.id).toBe("hall");
    expect(found?.item.position).toEqual([1, 1]);
  });

  it("ändrar fält och tar bort, men aldrig id:t", () => {
    const placed = place(home(), "node", "plan", { x: 4, y: 1 });
    const changed = updateItem(placed.document, placed.id, {
      rotation: 90,
      height: 2.4,
      mirrored: true,
      id: "annat",
    });
    expect(findItem(changed, placed.id)?.item).toMatchObject({
      rotation: 90,
      height: 2.4,
      mirrored: true,
    });

    const removed = deleteItem(changed, placed.id);
    expect(findItem(removed, placed.id)).toBeNull();
    expect(findItem(removed, "taklampa")).not.toBeNull();
  });

  it("vridningen blir hela grader mellan 0 och 359", () => {
    expect(normalizeRotation(-15)).toBe(345);
    expect(normalizeRotation(360)).toBe(0);
    expect(normalizeRotation(374.6)).toBe(15);
    expect(bearing({ x: 0, y: 0 }, { x: 1, y: 0 })).toBe(0);
    expect(bearing({ x: 0, y: 0 }, { x: 0, y: 1 })).toBe(90); // y växer nedåt: medurs
    expect(bearing({ x: 0, y: 0 }, { x: 0, y: -1 })).toBe(270);
  });
});

describe("rummens inställningar", () => {
  it("sparas i rummet", () => {
    const settings = {
      ...DEFAULT_SETTINGS,
      dark_below_lux: 20,
      bright_above_lux: 150,
      off_after_empty_seconds: 300,
      windowless: true,
    };
    expect(rooms(setRoomSettings(home(), "hall", settings))[1].settings).toEqual(settings);
  });

  it("mörkergränsen måste vara lägre än ljusgränsen, fördröjningen mer än noll och Wi-Fi-vikten 0–1", () => {
    const ok = DEFAULT_SETTINGS;
    expect(settingsProblem(ok)).toBeNull();
    expect(settingsProblem({ ...ok, dark_below_lux: 100 })).toMatch(/lägre än ljusgränsen/);
    expect(settingsProblem({ ...ok, off_after_empty_seconds: 0 })).toMatch(/Fördröjningen/);
    expect(settingsProblem({ ...ok, bright_above_lux: NaN })).toMatch(/alla värdena/);
    expect(settingsProblem({ ...ok, wifi_weight: 1.5 })).toMatch(/Wi-Fi-vikten/);
    expect(settingsProblem({ ...ok, wifi_weight: 1 })).toBeNull();
  });

  it("dimringen är 0 sekunder till ett dygn med 1–100 %, och dagsljustiden mer än noll", () => {
    const ok = DEFAULT_SETTINGS;
    expect(settingsProblem({ ...ok, dim_before_off_seconds: 0 })).toBeNull();
    expect(settingsProblem({ ...ok, dim_before_off_seconds: -1 })).toMatch(/Varningsdimringen/);
    expect(settingsProblem({ ...ok, dim_percent: 0 })).toMatch(/1 till 100/);
    expect(settingsProblem({ ...ok, dim_percent: 12.5 })).toMatch(/heltal/);
    expect(settingsProblem({ ...ok, daylight_off_after_seconds: 0 })).toMatch(/dagsljuset/);
  });

  it("ett rum från en äldre kärna får standardvärdena för det som saknas", () => {
    const old = { dark_below_lux: 20, bright_above_lux: 150, off_after_empty_seconds: 300, wifi_weight: 1 };
    const room = { ...rooms(home())[0], settings: old as RoomSettings };
    expect(roomSettings(room)).toEqual({ ...DEFAULT_SETTINGS, ...old });
  });
});
