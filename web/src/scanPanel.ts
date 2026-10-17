/**
 * Panelen där användaren lägger in en iPhone-skanning (se roomplan.ts):
 * vad som lästes, vad som inte gick att läsa, var skanningen ska in och hur
 * den ska vridas och flyttas. Kartan visar under tiden hur det blir.
 */
import { button, element } from "./dom";
import { MAX_COORDINATE } from "./editing";
import type { FloorModel } from "./homefile";
import { counted, type Placement, type Scan, type ScanTarget } from "./roomplan";

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

/** Hur långt skanningen kan flyttas, i meter. Resten av planet behövs för själva skanningen. */
const MAX_POSITION = MAX_COORDINATE / 2;

/** Vad skanningen innehåller, i en mening. */
export function scanSummary(scan: Scan): string {
  return (
    `Skanningen har ${counted(scan.rooms.length, "rum", "rum")}, ` +
    `${counted(scan.doorways, "dörröppning", "dörröppningar")}, ` +
    `${counted(scan.windows, "fönster", "fönster")} och ` +
    `${counted(scan.furniture.length, "möbel", "möbler")}.`
  );
}

export function scanForm(current: ScanImport, floors: FloorModel[], actions: ScanActions): Node[] {
  return [
    element("p", {}, scanSummary(current.scan)),
    ...problemList(current.scan.problems),
    ...targetChoice(current, floors, actions),
    placementControls(current, actions),
    element(
      "div",
      { className: "actions" },
      button("Läs in", actions.apply),
      button("Avbryt", actions.cancel),
    ),
  ];
}

function problemList(problems: string[]): Node[] {
  if (problems.length === 0) {
    return [];
  }
  const list = element(
    "ul",
    { className: "scan-problems" },
    ...problems.map((problem) => element("li", {}, problem)),
  );
  list.setAttribute("aria-label", "Det som inte gick att läsa");
  return [element("p", { className: "muted" }, "Det här gick inte att läsa:"), list];
}

/** Valet mellan en ny våning och att ersätta en av våningarna som finns. */
function targetChoice(current: ScanImport, floors: FloorModel[], actions: ScanActions): Node[] {
  const { target } = current;
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
  const nodes: Node[] = [element("label", {}, "Var ska skanningen in? ", where)];
  const replaced =
    target.kind === "replace" ? floors.find((floor) => floor.id === target.floorId) : undefined;
  if (replaced !== undefined && replaced.rooms.length > 0) {
    nodes.push(
      element(
        "p",
        { className: "muted" },
        `Rummen på ${replaced.name} tas bort, med sina zoner, rumskort och lampor.`,
      ),
    );
  }
  return nodes;
}

/** Vridningen och var skanningens övre vänstra hörn ska ligga. */
function placementControls(current: ScanImport, actions: ScanActions): Node {
  const { placement } = current;
  const coordinate = (axis: "x" | "y", label: string) => {
    const input = element("input", {
      type: "number",
      min: "0",
      max: String(MAX_POSITION),
      step: "0.1",
      name: axis,
      value: String(placement.position[axis]),
    });
    input.addEventListener("change", () => {
      const value = Number(input.value);
      if (Number.isFinite(value) && value >= 0 && value <= MAX_POSITION) {
        const position = { ...placement.position, [axis]: value };
        actions.change({ ...current, placement: { ...placement, position } });
      } else {
        input.value = String(placement.position[axis]);
      }
    });
    return element("label", {}, label, input);
  };
  const turn = () =>
    actions.change({
      ...current,
      placement: { ...placement, quarterTurns: (placement.quarterTurns + 1) % 4 },
    });
  return element(
    "div",
    { className: "actions" },
    button("Vrid 90°", turn),
    coordinate("x", "Från vänster (m) "),
    coordinate("y", "Från toppen (m) "),
  );
}
