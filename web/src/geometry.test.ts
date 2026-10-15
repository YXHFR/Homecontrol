import { describe, expect, it } from "vitest";
import { fitTransform, MARGIN_METERS, parkingSpot } from "./geometry";
import { parseServerMessage } from "./protocol";

const room = { id: "r", name: "R", x: 0, y: 0, width: 5, height: 4 };

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

    expect(spot.x).toBeGreaterThan(room.width);
    expect(spot.x).toBeLessThan(room.width + MARGIN_METERS);
  });
});

describe("parseServerMessage", () => {
  it("känner igen läget och avvisar annat", () => {
    expect(parseServerMessage('{"type":"state","targets":[],"rooms":[],"lamps":[]}')).toEqual({
      type: "state",
      targets: [],
      rooms: [],
      lamps: [],
    });
    expect(parseServerMessage('{"type":"state"}')).toBeNull();
    expect(parseServerMessage("null")).toBeNull();
    expect(parseServerMessage("{")).toBeNull();
  });
});
