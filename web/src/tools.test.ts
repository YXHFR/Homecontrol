import { describe, expect, it } from "vitest";
import { Drawing } from "./tools";

const CLOSE = 0.2;

describe("fri form", () => {
  it("stängs när man klickar på första hörnet", () => {
    const drawing = new Drawing();
    drawing.setTool("polygon");
    expect(drawing.pointerDown({ x: 0, y: 0 }, CLOSE)).toBeNull();
    expect(drawing.pointerDown({ x: 4, y: 0 }, CLOSE)).toBeNull();
    expect(drawing.pointerDown({ x: 4, y: 3 }, CLOSE)).toBeNull();

    const outcome = drawing.pointerDown({ x: 0.1, y: 0.1 }, CLOSE);

    expect(outcome).toEqual({
      kind: "room",
      corners: [
        { x: 0, y: 0 },
        { x: 4, y: 0 },
        { x: 4, y: 3 },
      ],
    });
    expect(drawing.draft).toEqual({ kind: "none" });
  });

  it("med verktyget Zon blir det en zon i stället för ett rum", () => {
    const drawing = new Drawing();
    drawing.setTool("zone");
    drawing.pointerDown({ x: 0, y: 0 }, CLOSE);
    drawing.pointerDown({ x: 1, y: 0 }, CLOSE);
    drawing.pointerDown({ x: 1, y: 1 }, CLOSE);

    expect(drawing.finish()).toEqual({
      kind: "zone",
      corners: [
        { x: 0, y: 0 },
        { x: 1, y: 0 },
        { x: 1, y: 1 },
      ],
    });
  });

  it("ett klick på första hörnet stänger inte förrän det finns tre hörn", () => {
    const drawing = new Drawing();
    drawing.setTool("polygon");
    drawing.pointerDown({ x: 0, y: 0 }, CLOSE);
    drawing.pointerDown({ x: 4, y: 0 }, CLOSE);
    expect(drawing.pointerDown({ x: 0, y: 0 }, CLOSE)).toBeNull();
    expect(drawing.finish()).toBeNull();
  });

  it("ett dubbelklick på samma hörn blir inte två hörn", () => {
    const drawing = new Drawing();
    drawing.setTool("polygon");
    drawing.pointerDown({ x: 0, y: 0 }, CLOSE);
    drawing.pointerDown({ x: 4, y: 0 }, CLOSE);
    drawing.pointerDown({ x: 4.05, y: 0 }, CLOSE);
    expect(drawing.draft.kind === "polygon" && drawing.draft.points).toHaveLength(2);
  });

  it("hörn kan ångras, och det som ritas försvinner vid byte av verktyg", () => {
    const drawing = new Drawing();
    drawing.setTool("polygon");
    drawing.pointerDown({ x: 0, y: 0 }, CLOSE);
    drawing.pointerDown({ x: 4, y: 0 }, CLOSE);
    drawing.undo();
    expect(drawing.draft.kind === "polygon" && drawing.draft.points).toEqual([{ x: 0, y: 0 }]);
    drawing.setTool("rectangle");
    expect(drawing.draft).toEqual({ kind: "none" });
  });
});

it("en rektangel dras ut från ett hörn till hörnet snett emot", () => {
  const drawing = new Drawing();
  drawing.setTool("rectangle");
  drawing.pointerDown({ x: 1, y: 1 }, CLOSE);
  drawing.pointerMove({ x: 3, y: 2 });
  expect(drawing.draft.kind).toBe("rectangle");

  const outcome = drawing.pointerUp({ x: 4, y: 3 });

  expect(outcome).toEqual({
    kind: "room",
    corners: [
      { x: 1, y: 1 },
      { x: 4, y: 1 },
      { x: 4, y: 3 },
      { x: 1, y: 3 },
    ],
  });
});

it("skalningen ger två punkter, och ett tredje klick börjar om", () => {
  const drawing = new Drawing();
  drawing.setTool("scale");
  expect(drawing.pointerDown({ x: 1, y: 1 }, CLOSE)).toBeNull();
  expect(drawing.pointerDown({ x: 5, y: 1 }, CLOSE)).toEqual({
    kind: "scale",
    a: { x: 1, y: 1 },
    b: { x: 5, y: 1 },
  });
  drawing.pointerDown({ x: 2, y: 2 }, CLOSE);
  expect(drawing.draft.kind === "scale" && drawing.draft.points).toEqual([{ x: 2, y: 2 }]);
});

it("välj-verktyget ritar inget", () => {
  const drawing = new Drawing();
  expect(drawing.pointerDown({ x: 1, y: 1 }, CLOSE)).toBeNull();
  expect(drawing.pointerUp({ x: 1, y: 1 })).toBeNull();
  expect(drawing.draft).toEqual({ kind: "none" });
});
