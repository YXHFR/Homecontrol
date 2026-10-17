/**
 * Panelen där användaren lägger in en iPhone-skanning (se roomplan.ts):
 * vad som lästes, vad som inte gick att läsa, var skanningen ska in och hur
 * den ska vridas och flyttas. Kartan visar under tiden hur det blir.
 */
import { button, element } from "./dom";
import type { FloorModel } from "./homefile";
import type { Placement, Scan, ScanTarget } from "./roomplan";

/** En skanning som har lästs men inte lagts in än. */
export interface ScanImport {
  scan: Scan;
  placement: Placement;
  target: ScanTarget;
}

export interface ScanActions {
  change(next: ScanImport): void;
  apply(): void;
  cancel(): void;
}

export const SCAN_INSTRUCTION =
  "Så här blir skanningen. Vrid och flytta den om det behövs, välj var den ska in och " +
  "tryck på Läs in. Väggarna blir gips; byt material med verktyget Väggar och dörrar efteråt.";

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** Vad skanningen innehåller, i en mening. */
export function scanSummary(scan: Scan): string {
  return (
    `Skanningen har ${plural(scan.rooms.length, "rum", "rum")}, ` +
    `${plural(scan.doorways, "dörröppning", "dörröppningar")}, ` +
    `${plural(scan.windows, "fönster", "fönster")} och ` +
    `${plural(scan.furniture.length, "möbel", "möbler")}.`
  );
}

export function scanForm(current: ScanImport, floors: FloorModel[], actions: ScanActions): Node[] {
  const { scan, placement, target } = current;
  const nodes: Node[] = [element("p", {}, scanSummary(scan))];
  if (scan.problems.length > 0) {
    const problems = element(
      "ul",
      { className: "scan-problems" },
      ...scan.problems.map((problem) => element("li", {}, problem)),
    );
    problems.setAttribute("aria-label", "Det som inte gick att läsa");
    nodes.push(element("p", { className: "muted" }, "Det här gick inte att läsa:"), problems);
  }

  const where = element("select", { name: "target" });
  where.append(element("option", { value: "" }, "Som en ny våning"));
  for (const floor of floors) {
    where.append(element("option", { value: floor.id }, `I stället för ${floor.name}`));
  }
  where.value = target.kind === "replace" ? target.floorId : "";
  where.addEventListener("change", () => {
    const next: ScanTarget =
      where.value === "" ? { kind: "new" } : { kind: "replace", floorId: where.value };
    actions.change({ ...current, target: next });
  });
  nodes.push(element("label", {}, "Var ", where));
  const replaced = target.kind === "replace" ? floors.find((f) => f.id === target.floorId) : null;
  if (replaced != null && replaced.rooms.length > 0) {
    nodes.push(
      element(
        "p",
        { className: "muted" },
        `Rummen på ${replaced.name} tas bort, med sina zoner, rumskort och lampor.`,
      ),
    );
  }

  const coordinate = (axis: "x" | "y", label: string) => {
    const input = element("input", {
      type: "number",
      min: "0",
      max: "900",
      step: "0.1",
      name: axis,
      value: String(placement.position[axis]),
    });
    input.addEventListener("change", () => {
      const value = Number(input.value);
      if (Number.isFinite(value) && value >= 0) {
        const position = { ...placement.position, [axis]: value };
        actions.change({ ...current, placement: { ...placement, position } });
      }
    });
    return element("label", {}, label, input);
  };
  nodes.push(
    element(
      "div",
      { className: "actions" },
      button("Vrid 90°", () =>
        actions.change({
          ...current,
          placement: { ...placement, quarterTurns: (placement.quarterTurns + 1) % 4 },
        }),
      ),
      coordinate("x", "Från vänster (m) "),
      coordinate("y", "Från toppen (m) "),
    ),
    element(
      "div",
      { className: "actions" },
      button("Läs in", actions.apply),
      button("Avbryt", actions.cancel),
    ),
  );
  return nodes;
}
