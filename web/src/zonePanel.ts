/**
 * Kartredigerarens delar för zoner: en knapp per sorts zon i verktygslådan och
 * egenskaperna för en vald zon. Delarna ändrar inget själva utan säger till
 * kartredigeraren, som sparar.
 */
import { button, element } from "./dom";
import type { ZoneModel } from "./homefile";
import type { ZoneKind } from "./protocol";
import { ZONE_KINDS, ZONE_STYLES } from "./zones";

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

export interface ZoneActions {
  rename(name: string): void;
  setKind(kind: ZoneKind): void;
  remove(): void;
  /** Visar ett meddelande, till exempel när namnet lämnas tomt. */
  complain(text: string): void;
}

/** Egenskaperna för en vald zon: namn, sort och knappen för att ta bort den. */
export function zoneProperties(zone: ZoneModel, roomName: string, actions: ZoneActions): Node[] {
  const name = element("input", { type: "text", value: zone.name, maxLength: 100, name: "name" });
  name.addEventListener("change", () => {
    const next = name.value.trim();
    if (next === "") {
      name.value = zone.name;
      actions.complain("Zonen måste ha ett namn.");
    } else if (next !== zone.name) {
      actions.rename(next);
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
  kind.addEventListener("change", () => actions.setKind(kind.value as ZoneKind));

  return [
    element("label", {}, "Namn ", name),
    element("label", {}, "Sort ", kind),
    element(
      "p",
      { className: "muted" },
      `Ligger i ${roomName}. ${ZONE_STYLES[zone.kind].description}`,
    ),
    button("Ta bort zonen", () => actions.remove(), "danger"),
  ];
}
