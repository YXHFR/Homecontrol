/**
 * Egenskapspanelens delar för det som placeras i hemmet: ett valt
 * rumskort, en router eller en lampa, och ett valt rums inställningar
 * (ljusgränser och fördröjning). Panelen ändrar hemmet genom `commit`,
 * precis som resten av kartredigeraren.
 */
import { button, element } from "./dom";
import type {
  HomeDocument,
  LampModel,
  NodeModel,
  RoomModel,
  RoomSettings,
  SignalSourceModel,
} from "./homefile";
import {
  deleteItem,
  type FoundItem,
  ITEM_LABELS,
  MAX_HEIGHT,
  moveItemToRoom,
  normalizeRotation,
  roomSettings,
  setRoomSettings,
  settingsProblem,
  updateItem,
} from "./placing";

/** Så många grader vrider knapparna rumskortet åt gången. */
export const ROTATION_STEP = 15;

export interface PanelActions {
  /** Ändrar hemmet; `edit` görs på det sparade hemmet när ändringen står på tur. */
  commit(edit: (document: HomeDocument) => HomeDocument): void;
  showMessage(text: string): void;
  /** Glömmer det valda, till exempel när det har tagits bort. */
  deselect(): void;
}

const NAME_MISSING = {
  node: "Rumskortet måste ha ett namn.",
  router: "Routern måste ha ett namn.",
  lamp: "Lampan måste ha ett namn.",
} as const;
const DELETE_LABELS = {
  node: "Ta bort rumskortet",
  router: "Ta bort routern",
  lamp: "Ta bort lampan",
} as const;

/** Ett textfält eller sifferfält som sparar när det ändras (eller Enter trycks). */
function field(
  label: string,
  props: Partial<HTMLInputElement>,
  onChange: (input: HTMLInputElement) => void,
): HTMLLabelElement {
  const input = element("input", props);
  input.addEventListener("change", () => onChange(input));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      input.blur();
    }
  });
  return element("label", {}, `${label} `, input);
}

function format(value: number, decimals = 2): string {
  return String(Math.round(value * 10 ** decimals) / 10 ** decimals);
}

/** Egenskaperna för ett valt rumskort, en vald router eller lampa. */
export function itemProperties(found: FoundItem, actions: PanelActions): HTMLElement[] {
  const { kind, item, floor, room } = found;
  const id = item.id;
  const update = (changes: Partial<NodeModel & LampModel>) =>
    actions.commit((document) => updateItem(document, id, changes));

  const parts: HTMLElement[] = [
    element("p", { className: "muted" }, ITEM_LABELS[kind]),
    field("Namn", { type: "text", value: item.name, maxLength: 100, name: "name" }, (input) => {
      const next = input.value.trim();
      if (next === "") {
        input.value = item.name;
        actions.showMessage(NAME_MISSING[kind]);
      } else if (next !== item.name) {
        update({ name: next });
      }
    }),
  ];

  if (kind === "node" || kind === "router") {
    const height = (item as NodeModel | SignalSourceModel).height;
    parts.push(
      field(
        "Höjd över golvet (m)",
        {
          type: "number",
          min: "0",
          max: String(MAX_HEIGHT),
          step: "0.1",
          value: format(height),
          name: "height",
        },
        (input) => {
          const next = Number(input.value);
          if (input.value === "" || !(next >= 0 && next <= MAX_HEIGHT)) {
            input.value = format(height);
            actions.showMessage(`Höjden ska vara mellan 0 och ${MAX_HEIGHT} meter.`);
          } else if (next !== height) {
            update({ height: next });
          }
        },
      ),
    );
  }

  if (kind === "node") {
    const node = item as NodeModel;
    const rotation = node.rotation ?? 0;
    const rotate = (degrees: number) => update({ rotation: normalizeRotation(degrees) });
    const mirrored = element("input", {
      type: "checkbox",
      checked: node.mirrored ?? false,
      name: "mirrored",
    });
    mirrored.addEventListener("change", () => update({ mirrored: mirrored.checked }));
    const turnLeft = button(`↺ ${ROTATION_STEP}°`, () => rotate(rotation - ROTATION_STEP));
    turnLeft.setAttribute("aria-label", `Vrid ${ROTATION_STEP}° moturs`);
    const turnRight = button(`↻ ${ROTATION_STEP}°`, () => rotate(rotation + ROTATION_STEP));
    turnRight.setAttribute("aria-label", `Vrid ${ROTATION_STEP}° medurs`);
    parts.push(
      field(
        "Vridning (grader medurs från höger)",
        {
          type: "number",
          min: "0",
          max: "359",
          step: "1",
          value: String(rotation),
          name: "rotation",
        },
        (input) => {
          const next = Number(input.value);
          if (input.value === "" || !Number.isFinite(next)) {
            input.value = String(rotation);
          } else if (normalizeRotation(next) !== rotation) {
            rotate(next);
          }
        },
      ),
      element("div", { className: "actions" }, turnLeft, turnRight),
      element("label", { className: "checkbox" }, mirrored, " Spegelvänd sidled"),
      element(
        "p",
        { className: "muted" },
        "Kryssa i om personerna i livevyn hamnar på fel sida om rumskortet, till exempel om kortet sitter upp och ned.",
      ),
    );
  }

  if (room !== null) {
    const choice = element("select", { name: "room" });
    choice.append(
      ...floor.rooms.map((r) =>
        element("option", { value: r.id, selected: r.id === room.id }, r.name),
      ),
    );
    choice.addEventListener("change", () =>
      actions.commit((document) => moveItemToRoom(document, id, choice.value)),
    );
    parts.push(
      element("label", {}, kind === "lamp" ? "Styrs av rummet " : "Sitter i rummet ", choice),
    );
  }

  parts.push(
    button(
      DELETE_LABELS[kind],
      () => {
        actions.deselect();
        actions.commit((document) => deleteItem(document, id));
      },
      "danger",
    ),
  );
  return parts;
}

/** Ljusgränserna, fördröjningen och Wi-Fi-vikten för ett valt rum. */
export function roomSettingsForm(room: RoomModel, actions: PanelActions): HTMLElement {
  const current = roomSettings(room);
  const inputs = {
    dark: element("input", {
      type: "number",
      min: "0",
      step: "1",
      value: format(current.dark_below_lux),
      name: "dark_below_lux",
    }),
    bright: element("input", {
      type: "number",
      min: "0",
      step: "1",
      value: format(current.bright_above_lux),
      name: "bright_above_lux",
    }),
    minutes: element("input", {
      type: "number",
      min: "0.5",
      step: "0.5",
      value: format(current.off_after_empty_seconds / 60),
      name: "off_after_empty_minutes",
    }),
    wifi: element("input", {
      type: "number",
      min: "0",
      max: "1",
      step: "0.1",
      value: format(current.wifi_weight),
      name: "wifi_weight",
    }),
  };
  const save = () => {
    const next: RoomSettings = {
      dark_below_lux: inputs.dark.valueAsNumber,
      bright_above_lux: inputs.bright.valueAsNumber,
      off_after_empty_seconds: Math.round(inputs.minutes.valueAsNumber * 60),
      wifi_weight: inputs.wifi.valueAsNumber,
    };
    const problem = settingsProblem(next);
    if (problem !== null) {
      actions.showMessage(problem);
      inputs.dark.value = format(current.dark_below_lux);
      inputs.bright.value = format(current.bright_above_lux);
      inputs.minutes.value = format(current.off_after_empty_seconds / 60);
      inputs.wifi.value = format(current.wifi_weight);
      return;
    }
    if (JSON.stringify(next) !== JSON.stringify(current)) {
      actions.commit((document) => setRoomSettings(document, room.id, next));
    }
  };
  for (const input of Object.values(inputs)) {
    input.addEventListener("change", save);
  }
  return element(
    "fieldset",
    { className: "room-settings" },
    element("legend", {}, "Ljus och fördröjning"),
    element("label", {}, "Tänd när det är mörkare än (lux) ", inputs.dark),
    element("label", {}, "Släck när dagsljuset är över (lux) ", inputs.bright),
    element("label", {}, "Släck när rummet varit tomt i (minuter) ", inputs.minutes),
    element("label", {}, "Wi-Fi-rörelsens vikt (0–1) ", inputs.wifi),
    element(
      "p",
      { className: "muted" },
      "Wi-Fi-rörelse håller kvar den som sitter still i en stillhetszon. " +
        "Först vid vikten 1 räcker rörelsen ensam för att rummet ska räknas som upptaget.",
    ),
    element("p", { className: "muted" }, "Dagsljusgränsen används när dagsljusregeln kommer."),
  );
}
