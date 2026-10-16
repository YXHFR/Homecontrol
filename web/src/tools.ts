/**
 * Ritverktygen: vad ett klick eller en dragning på kartan betyder för
 * verktyget som är valt. Allt är i hemmets meter. Koden ritar inget själv,
 * så den går att testa utan webbläsare.
 */
import { distance, rectangleCorners } from "./editing";
import type { Point } from "./protocol";

/** Med "zone" ritas en zon med fri form, av den sort som kartredigeraren har valt. */
export type ToolName = "select" | "polygon" | "rectangle" | "scale" | "zone";

/** Det som håller på att ritas och ska visas på kartan. Zoner ritas som en polygon. */
export type Draft =
  | { kind: "none" }
  | { kind: "polygon"; points: Point[]; pointer: Point | null }
  | { kind: "rectangle"; corners: Point[] }
  | { kind: "scale"; points: Point[]; pointer: Point | null };

/** Vad ett avslutat ritande blev. */
export type Outcome =
  | { kind: "room"; corners: Point[] }
  | { kind: "zone"; corners: Point[] }
  | { kind: "scale"; a: Point; b: Point }
  | null;

export const INSTRUCTIONS: Record<ToolName, string> = {
  select:
    "Klicka på ett rum eller en zon för att välja det. Dra ett valt rum för att flytta det. " +
    "Dra i en tom del av kartan för att se en annan del.",
  polygon:
    "Klicka där rummets hörn är, ett i taget runt rummet. " +
    "Stäng rummet genom att klicka på det första hörnet igen.",
  rectangle: "Tryck där ett hörn av rummet är och dra till hörnet snett emot. Släpp för att spara.",
  zone:
    "Klicka där zonens hörn är, ett i taget, inne i ett rum. " +
    "Stäng zonen genom att klicka på det första hörnet igen.",
  scale:
    "Klicka på två punkter på bilden som du vet avståndet mellan, till exempel båda ändarna " +
    "av en vägg. Skriv sedan in avståndet.",
};

export class Drawing {
  private current: Draft = { kind: "none" };
  private toolName: ToolName = "select";
  /** Där dragningen av en rektangel började. */
  private rectangleStart: Point | null = null;

  get tool(): ToolName {
    return this.toolName;
  }

  get draft(): Draft {
    return this.current;
  }

  /** Byter verktyg. Det som höll på att ritas försvinner. */
  setTool(tool: ToolName): void {
    this.toolName = tool;
    this.cancel();
  }

  cancel(): void {
    this.current = { kind: "none" };
    this.rectangleStart = null;
  }

  /** Tar bort det senast utklickade hörnet eller punkten. */
  undo(): void {
    const draft = this.current;
    if (draft.kind === "polygon" || draft.kind === "scale") {
      const points = draft.points.slice(0, -1);
      this.current = points.length === 0 ? { kind: "none" } : { ...draft, points };
    }
  }

  /**
   * Ett tryck på kartan. `closeRadius` är hur nära det första hörnet (i meter)
   * ett klick måste vara för att stänga rummet.
   */
  pointerDown(point: Point, closeRadius: number): Outcome {
    const draft = this.current;
    switch (this.toolName) {
      case "polygon":
      case "zone": {
        const points = draft.kind === "polygon" ? draft.points : [];
        if (points.length >= 3 && distance(point, points[0]) <= closeRadius) {
          return this.finish();
        }
        if (points.some((corner) => distance(point, corner) <= closeRadius)) {
          return null; // ett hörn som redan finns, till exempel vid dubbelklick
        }
        this.current = { kind: "polygon", points: [...points, point], pointer: point };
        return null;
      }
      case "rectangle":
        this.current = { kind: "rectangle", corners: rectangleCorners(point, point) };
        this.rectangleStart = point;
        return null;
      case "scale": {
        const points = draft.kind === "scale" && draft.points.length < 2 ? draft.points : [];
        const next = [...points, point];
        this.current = { kind: "scale", points: next, pointer: null };
        return next.length === 2 ? { kind: "scale", a: next[0], b: next[1] } : null;
      }
      case "select":
        return null;
    }
  }

  pointerMove(point: Point): void {
    const draft = this.current;
    if (draft.kind === "polygon") {
      this.current = { ...draft, pointer: point };
    } else if (draft.kind === "scale" && draft.points.length === 1) {
      this.current = { ...draft, pointer: point };
    } else if (draft.kind === "rectangle" && this.rectangleStart !== null) {
      this.current = { kind: "rectangle", corners: rectangleCorners(this.rectangleStart, point) };
    }
  }

  pointerUp(point: Point): Outcome {
    if (this.current.kind !== "rectangle" || this.rectangleStart === null) {
      return null;
    }
    const corners = rectangleCorners(this.rectangleStart, point);
    this.cancel();
    return { kind: "room", corners };
  }

  /** Stänger rummet eller zonen som håller på att ritas med fri form, om den har minst tre hörn. */
  finish(): Outcome {
    const draft = this.current;
    if (draft.kind !== "polygon" || draft.points.length < 3) {
      return null;
    }
    this.cancel();
    return { kind: this.toolName === "zone" ? "zone" : "room", corners: draft.points };
  }
}
