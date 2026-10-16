import { describe, expect, it } from "vitest";
import type { StateMessage } from "./protocol";
import { trackStyle, tracksOnFloor } from "./trackStyles";

describe("spåren i livevyn", () => {
  it("ett sett spår är en fylld prick och ett kvarhållet en streckad ring", () => {
    const seen = trackStyle({ floor_id: "plan", x: 1, y: 1, held: false });
    const held = trackStyle({ floor_id: "plan", x: 1, y: 1, held: true });

    expect(seen.fill).toBeDefined();
    expect(seen.dash).toBeUndefined();
    expect(held.fill).toBeUndefined();
    expect(held.dash).toEqual([3, 3]);
    expect(held.stroke).toBe(seen.fill);
  });

  it("bara spåren på våningen som visas ritas", () => {
    const state: StateMessage = {
      type: "state",
      tracks: [
        { floor_id: "nere", x: 1, y: 1, held: false },
        { floor_id: "uppe", x: 2, y: 2, held: true },
      ],
      rooms: [],
      zones: [],
      lamps: [],
      simulation: null,
    };

    expect(tracksOnFloor(state, "uppe")).toEqual([{ floor_id: "uppe", x: 2, y: 2, held: true }]);
    expect(tracksOnFloor(null, "uppe")).toEqual([]);
  });
});
