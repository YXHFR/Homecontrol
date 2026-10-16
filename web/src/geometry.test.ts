import { describe, expect, it } from "vitest";
import { corners, fitTransform, MARGIN_METERS, parkingSpot, wallSegments } from "./geometry";
import { parseServerMessage, type Room } from "./protocol";

const room: Room = {
  id: "r",
  name: "R",
  polygon: [
    [0, 0],
    [5, 0],
    [5, 4],
    [0, 4],
  ],
  walls: ["betong", "glas", "gips", "dörröppning"],
  zones: [],
};
const width = 5;

describe("fitTransform", () => {
  it("får plats med rummet och marginalen", () => {
    const t = fitTransform([room], 900, 900);

    expect(t.scale).toBe(900 / (5 + 2 * MARGIN_METERS));
    expect(t.toScreen({ x: -MARGIN_METERS, y: -MARGIN_METERS })).toEqual({ x: 0, y: 0 });
  });

  it("räknar fram och tillbaka till samma punkt", () => {
    const t = fitTransform([room], 800, 520);

    const back = t.toHome(t.toScreen({ x: 1.25, y: 3.5 }));

    expect(back.x).toBeCloseTo(1.25);
    expect(back.y).toBeCloseTo(3.5);
  });
});

describe("parkingSpot", () => {
  it("ligger utanför rummet men innanför marginalen", () => {
    const spot = parkingSpot([room]);

    expect(spot.x).toBeGreaterThan(width);
    expect(spot.x).toBeLessThan(width + MARGIN_METERS);
  });

  it("ställer nya personer under varandra och börjar om uppifrån", () => {
    const spots = [0, 1, 4].map((index) => parkingSpot([room], index));

    expect(spots.map((spot) => spot.y)).toEqual([0.5, 1.5, 0.5]);
  });
});

describe("wallSegments", () => {
  it("ger en vägg per sida, där sista sidan går tillbaka till första hörnet", () => {
    const walls = wallSegments(room);

    expect(walls).toHaveLength(4);
    expect(walls[0]).toEqual({ from: { x: 0, y: 0 }, to: { x: 5, y: 0 }, kind: "betong" });
    expect(walls[3]).toEqual({ from: { x: 0, y: 4 }, to: { x: 0, y: 0 }, kind: "dörröppning" });
  });
});

describe("fitTransform med ett L-format rum", () => {
  it("räknar gränserna från alla hörn", () => {
    const lShape: Room = {
      ...room,
      polygon: [
        [1, 1],
        [7, 1],
        [7, 3],
        [3, 3],
        [3, 6],
        [1, 6],
      ],
    };
    const t = fitTransform([lShape], 1000, 1000);

    expect(corners(lShape)).toHaveLength(6);
    expect(t.scale).toBe(1000 / (6 + 2 * MARGIN_METERS));
    expect(t.toScreen({ x: 1 - MARGIN_METERS, y: 1 - MARGIN_METERS })).toEqual({ x: 0, y: 0 });
  });
});

describe("parseServerMessage", () => {
  it("känner igen hemmet med minst en våning", () => {
    const text = JSON.stringify({ type: "home", name: "H", floors: [{ id: "f" }] });

    expect(parseServerMessage(text)?.type).toBe("home");
    expect(parseServerMessage('{"type":"home","name":"H","floors":[]}')).toBeNull();
    expect(parseServerMessage('{"type":"home","rooms":[],"lamps":[]}')).toBeNull();
  });

  it("känner igen läget och avvisar annat", () => {
    const state = {
      type: "state",
      tracks: [],
      rooms: [],
      zones: [],
      lamps: [],
      events: [],
      simulation: null,
      cards: null,
    };
    const simulation = { enabled: true, speed: 10, daylight: 5, persons: [] };
    expect(parseServerMessage(JSON.stringify(state))).toEqual(state);
    expect(parseServerMessage(JSON.stringify({ ...state, simulation }))).toEqual({
      ...state,
      simulation,
    });
    expect(parseServerMessage(JSON.stringify({ ...state, simulation: { ...simulation, speed: 2 } })))
      .toBeNull();
    expect(parseServerMessage('{"type":"state","tracks":[],"rooms":[],"zones":[],"lamps":[]}')).toBeNull();
    expect(parseServerMessage(JSON.stringify({ ...state, cards: "inga" }))).toBeNull();
    expect(parseServerMessage(JSON.stringify({ ...state, cards: [] }))).toEqual({ ...state, cards: [] });
    // Äldre kärnor skickade mål i stället för spår.
    expect(parseServerMessage('{"type":"state","targets":[],"rooms":[],"lamps":[],"simulation":null}')).toBeNull();
    expect(parseServerMessage('{"type":"state"}')).toBeNull();
    expect(parseServerMessage("null")).toBeNull();
    expect(parseServerMessage("{")).toBeNull();
  });
});
