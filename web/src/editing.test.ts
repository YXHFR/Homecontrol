import { describe, expect, it } from "vitest";
import {
  addRoom,
  deleteRoom,
  initialPlanImage,
  moveRoom,
  polygonArea,
  rectangleCorners,
  renameRoom,
  roomProblem,
  scalePlanImage,
  summarize,
  uniqueId,
} from "./editing";
import type { HomeDocument } from "./homefile";

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
          signal_sources: [{ id: "router", position: [1, 1] }],
          rooms: [
            {
              id: "kok",
              name: "Kök",
              polygon: [
                [1, 1],
                [4, 1],
                [4, 3],
                [1, 3],
              ],
              walls: ["gips", "betong", "gips", "dörröppning"],
              zones: [
                {
                  polygon: [
                    [2, 2],
                    [3, 2],
                    [3, 3],
                  ],
                },
              ],
              nodes: [{ position: [1, 1] }],
              lamps: [{ position: [2.5, 2] }],
            },
          ],
        },
      ],
    },
  };
}

const square = [
  { x: 0, y: 0 },
  { x: 2, y: 0 },
  { x: 2, y: 2 },
  { x: 0, y: 2 },
];

describe("rum", () => {
  it("läggs till med gipsväggar, ett nytt namn och ett id som kärnan godtar", () => {
    const { document, roomId } = addRoom(home(), "plan", [
      { x: 5.004, y: 1 },
      { x: 7, y: 1 },
      { x: 7, y: 3.996 },
    ]);
    const room = document.home.floors[0].rooms[1];

    expect(roomId).toBe("rum-2");
    expect(room).toEqual({
      id: "rum-2",
      name: "Rum 2",
      polygon: [
        [5, 1],
        [7, 1],
        [7, 4],
      ],
      walls: ["gips", "gips", "gips"],
    });
  });

  it("ändrar inte det gamla dokumentet", () => {
    const before = home();
    addRoom(before, "plan", square);
    expect(before.home.floors[0].rooms).toHaveLength(1);
  });

  it("byter namn men behåller id och allt annat", () => {
    const room = renameRoom(home(), "kok", "Köket").home.floors[0].rooms[0];
    expect(room).toEqual({ ...home().home.floors[0].rooms[0], name: "Köket" });
  });

  it("tas bort", () => {
    expect(deleteRoom(home(), "kok").home.floors[0].rooms).toEqual([]);
  });

  it("flyttas tillsammans med zoner, noder och lampor", () => {
    const room = moveRoom(home(), "kok", 2, 0.5).home.floors[0].rooms[0];
    expect(room.polygon[0]).toEqual([3, 1.5]);
    expect(room.zones?.[0].polygon[0]).toEqual([4, 2.5]);
    expect(room.nodes?.[0].position).toEqual([3, 1.5]);
    expect(room.lamps?.[0].position).toEqual([4.5, 2.5]);
  });

  it("flyttas inte utanför planet", () => {
    const room = moveRoom(home(), "kok", -5, -5).home.floors[0].rooms[0];
    expect(room.polygon[0]).toEqual([0, 0]);
    expect(room.lamps?.[0].position).toEqual([1.5, 1]);
  });
});

describe("id", () => {
  it("görs av namnet utan å, ä, ö och mellanslag", () => {
    expect(uniqueId(home(), "Stora sovrummet på övervåningen")).toBe(
      "stora-sovrummet-pa-overvaningen",
    );
  });

  it("krockar inte med något som redan finns", () => {
    expect(uniqueId(home(), "Kök")).toBe("kok-2");
    expect(uniqueId(home(), "Router")).toBe("router-2");
    expect(uniqueId(home(), "!!!")).toBe("rum");
  });
});

describe("ytor", () => {
  it("har en area", () => {
    expect(polygonArea(square)).toBe(4);
  });

  it("som är för små, har för få hörn eller korsar sig själva blir inga rum", () => {
    expect(roomProblem(square)).toBeNull();
    expect(roomProblem(square.slice(0, 2))).toMatch(/tre hörn/);
    expect(roomProblem(rectangleCorners({ x: 0, y: 0 }, { x: 0.1, y: 0.1 }))).toMatch(/litet/);
    const bowtie = [square[0], square[2], square[1], square[3]];
    expect(roomProblem(bowtie)).toMatch(/korsar/);
  });

  it("en rektangel går att dra åt vilket håll som helst", () => {
    expect(rectangleCorners({ x: 3, y: 4 }, { x: 1, y: 2 })).toEqual([
      { x: 1, y: 2 },
      { x: 3, y: 2 },
      { x: 3, y: 4 },
      { x: 1, y: 4 },
    ]);
  });
});

describe("planritningen", () => {
  it("blir 20 meter bred innan den skalas", () => {
    expect(initialPlanImage(1000)).toEqual({ position: [0, 0], meters_per_pixel: 0.02 });
  });

  it("skalas så att avståndet mellan två punkter blir det angivna", () => {
    const plan = { position: [1, 1] as [number, number], meters_per_pixel: 0.02 };
    // 2 meter med nuvarande skala är 100 bildpunkter; de ska vara 5 meter.
    const scaled = scalePlanImage(plan, { x: 1, y: 1 }, { x: 3, y: 1 }, 5);
    expect(scaled).toEqual({ position: [1, 1], meters_per_pixel: 0.05 });
  });
});

it("sammanfattningen räknar allt i hemmet", () => {
  expect(summarize(home())).toEqual({
    rooms: 1,
    zones: 1,
    nodes: 1,
    signalSources: 1,
    lamps: 1,
    area: 6,
  });
});
