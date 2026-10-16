/**
 * Kartredigerarens delar för zoner: en knapp per sorts zon i verktygslådan,
 * instruktionen och egenskaperna för en vald zon. Delarna sparar inget själva
 * utan lämnar ändringarna till kartredigeraren, som sparar dem.
 */
import { button, element } from "./dom";
import type { HomeDocument } from "./homefile";
import type { ZoneKind } from "./protocol";
import { INSTRUCTIONS } from "./tools";
import { deleteZone, findZone, renameZone, setZoneKind } from "./zones";
import { ZONE_KINDS, ZONE_STYLES } from "./zoneStyles";

export interface ZoneTools {
  /** Rubriken och knapparna, i den ordning de ska stå i verktygslådan. */
  nodes: HTMLElement[];
  /** Markerar knappen för sorten som ritas, eller ingen. */
  render(active: ZoneKind | null): void;
}

export function zoneTools(onChoose: (kind: ZoneKind) => void): ZoneTools {
  const buttons = ZONE_KINDS.map((kind) => {
    const node = button(ZONE_STYLES[kind].label, () => onChoose(kind), "tool zone-tool");
    node.dataset.zoneKind = kind;
    node.style.setProperty("--zone-color", ZONE_STYLES[kind].color);
    return node;
  });
  return {
    nodes: [element("h2", {}, "Zoner"), ...buttons],
    render(active) {
      buttons.forEach((node, i) =>
        node.setAttribute("aria-pressed", String(ZONE_KINDS[i] === active)),
      );
    },
  };
}

/** Instruktionen för verktyget Zon: vad sorten betyder och hur zonen ritas. */
export function zoneInstruction(kind: ZoneKind): string {
  const style = ZONE_STYLES[kind];
  return `${style.label}: ${style.description} ${INSTRUCTIONS.zone}`;
}

export interface ZoneActions {
  /** Sparar en ändring av hemmet. */
  commit(edit: (document: HomeDocument) => HomeDocument): void;
  /** Zonen har tagits bort och ska inte längre vara vald. */
  deselect(): void;
  /** Visar ett meddelande, till exempel när namnet lämnas tomt. */
  showMessage(text: string): void;
}

/**
 * Egenskaperna för en vald zon: namn, sort och knappen för att ta bort den.
 * Null om zonen inte finns i hemmet (längre).
 */
export function zoneProperties(
  document: HomeDocument,
  zoneId: string,
  actions: ZoneActions,
): Node[] | null {
  const found = findZone(document, zoneId);
  if (found === null) {
    return null;
  }
  const { zone, room } = found;
  const name = element("input", { type: "text", value: zone.name, maxLength: 100, name: "name" });
  name.addEventListener("change", () => {
    const next = name.value.trim();
    if (next === "") {
      name.value = zone.name;
      actions.showMessage("Zonen måste ha ett namn.");
    } else if (next !== zone.name) {
      actions.commit((saved) => renameZone(saved, zoneId, next));
    }
  });
  name.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      name.blur();
    }
  });

  const kind = element(
    "select",
    { name: "kind" },
    ...ZONE_KINDS.map((option) =>
      element(
        "option",
        { value: option, selected: option === zone.kind },
        ZONE_STYLES[option].label,
      ),
    ),
  );
  kind.addEventListener("change", () => {
    const next = kind.value as ZoneKind;
    actions.commit((saved) => setZoneKind(saved, zoneId, next));
  });

  return [
    element("label", {}, "Namn ", name),
    element("label", {}, "Sort ", kind),
    element(
      "p",
      { className: "muted" },
      `Ligger i ${room.name}. ${ZONE_STYLES[zone.kind].description}`,
    ),
    button(
      "Ta bort zonen",
      () => {
        actions.deselect();
        actions.commit((saved) => deleteZone(saved, zoneId));
      },
      "danger",
    ),
  ];
}
