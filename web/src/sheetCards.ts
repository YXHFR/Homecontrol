/**
 * Korten i arket (mobil) eller sidopanelen (dator): rummet när inget är
 * valt, och kortet för ett valt rumskort, en lampa, en router eller en ny
 * enhet. Korten ritar bara; allt de gör går genom `CardActions`.
 *
 * Ett tryck på en lampa på kartan öppnar lampans kort och tänder den inte,
 * så att en råkad beröring inte pausar automatiken. Lampan tänds med den
 * stora knappen i kortet eller med snabbknappen i rummets kort.
 */
import { cardStatus, clockTime, signalText } from "./cardsPanel";
import { button, element } from "./dom";
import { inPolygon, toPoint } from "./editing";
import type { NewDevice } from "./health";
import { zigbeeProblem } from "./health";
import type { Edit } from "./homeStore";
import type { FloorModel, LampModel, NodeModel, RoomModel, SignalSourceModel } from "./homefile";
import { roomSettingsForm, ROTATION_STEP } from "./itemPanel";
import { cardFor } from "./mapItems";
import { MAX_HEIGHT, RADAR_HALF_ANGLE, RADAR_RANGE, type FoundItem, updateItem } from "./placing";
import type { Card, ClientMessage, LampState, StateMessage } from "./protocol";
import { lampStateText, luxText, shouldTurnOn } from "./roomPanels";
import { personsList } from "./simulationPanel";
import { OCCUPANCY_TEXT, tracksOnFloor } from "./trackStyles";

/** Det korten visar. */
export interface CardData {
  floor: FloorModel;
  state: StateMessage | null;
  virtualLamps: ReadonlySet<string>;
  /** Kärnan har ingen kontakt med databasen, så inget kan sparas. */
  databaseDown: boolean;
  newDevices: NewDevice[];
  /** När varje riktigt kort senast svarade medan sidan var öppen, i millisekunder. */
  lastHeard: ReadonlyMap<string, number>;
}

/** Det korten kan göra. */
export interface CardActions {
  send(message: ClientMessage): void;
  /** Öppnar kortet för en sak på kartan. */
  select(itemId: string): void;
  /** Öppnar kortet för en ny enhet. */
  selectNew(deviceId: string): void;
  /** Tillbaka till rummets kort. */
  back(): void;
  /** Låser upp saken så att den går att flytta (Flytta). */
  move(itemId: string): void;
  /** Öppnar ritverktygen (Ändra rummet). */
  editRoom(): void;
  /** Öppnar lampkalibreringen för rummet. */
  calibrate(roomId: string): void;
  /** Placerar en ny enhet mitt i rummet och låser upp den för flytt. */
  place(device: NewDevice): void;
  /** Ändrar hemmet, till exempel ett rumskorts höjd. */
  commit(edit: Edit): void;
  showMessage(text: string): void;
}

/** Varför Flytta inte går när databasen är nere. */
export const DATABASE_DOWN_MOVE =
  "Flytta går inte just nu: databasen svarar inte, så ändringar kan inte sparas. " +
  "Lamporna styrs ändå.";

function head(title: string, status: HTMLElement | null, actions: CardActions | null): HTMLElement {
  const parts: HTMLElement[] = [];
  if (actions !== null) {
    const back = button("‹ Rummet", actions.back, "back");
    back.setAttribute("aria-label", "Tillbaka till rummet");
    parts.push(back);
  }
  parts.push(element("h2", {}, title));
  if (status !== null) {
    parts.push(status);
  }
  return element("div", { className: "card-head" }, ...parts);
}

function statusLine(text: string, level: string): HTMLElement {
  const dot = element("span", { className: "dot" });
  dot.dataset.level = level;
  const line = element("span", { className: "status-line" }, dot, text);
  line.dataset.level = level;
  return line;
}

function keyValues(rows: [string, string][]): HTMLElement {
  return element(
    "dl",
    { className: "kv" },
    ...rows.flatMap(([term, value]) => [element("dt", {}, term), element("dd", {}, value)]),
  );
}

function format(value: number, decimals = 2): string {
  return value.toLocaleString("sv-SE", { maximumFractionDigits: decimals });
}

/** Knappen Flytta, avstängd med en förklaring när databasen är nere. */
function moveButton(itemId: string, data: CardData, actions: CardActions): HTMLButtonElement {
  const node = button("Flytta", () => actions.move(itemId), "move");
  node.disabled = data.databaseDown;
  node.title = data.databaseDown ? DATABASE_DOWN_MOVE : "";
  return node;
}

/** Knapparna i en rad, med förklaringen under när Flytta är avstängt. */
function buttonRow(buttons: HTMLButtonElement[], data: CardData): HTMLElement[] {
  const row = element("div", { className: "button-row" }, ...buttons);
  return data.databaseDown ? [row, element("p", { className: "help" }, DATABASE_DOWN_MOVE)] : [row];
}

/** PÅ/AV för en lampa. `big` är den stora knappen i lampans kort. */
export function powerButton(
  lamp: LampModel,
  lampState: LampState | undefined,
  actions: CardActions,
  big = false,
): HTMLButtonElement {
  const on = lampState?.on === true && lampState.responding;
  const node = button(on ? "PÅ" : "AV", () =>
    actions.send({ type: "set_lamp", lamp_id: lamp.id, on: shouldTurnOn(lampState) }),
  );
  node.className = big ? "power big" : "power";
  node.setAttribute("aria-pressed", String(on));
  node.setAttribute("aria-label", lamp.name);
  node.title = on ? "Släck för hand" : "Tänd för hand";
  node.dataset.lamp = lamp.id;
  return node;
}

function deviceRow(name: string, state: string, level: string, onClick: () => void): HTMLLIElement {
  const dot = element("span", { className: "dot" });
  dot.dataset.level = level;
  const status = element("span", { className: "state" }, dot, state);
  status.dataset.level = level;
  const node = button("", onClick);
  node.replaceChildren(element("span", { className: "name" }, name), status);
  return element("li", {}, node);
}

function nodeState(card: Card | undefined): [string, string] {
  if (card === undefined) {
    return ["simulerat", "idle"];
  }
  const level = !card.online ? "bad" : card.problems.length > 0 || card.calibrating ? "warn" : "ok";
  return [cardStatus(card).toLowerCase(), level];
}

function lampLevel(lampState: LampState | undefined): string {
  if (lampState?.responding === false) {
    return "bad";
  }
  return lampState === undefined || lampState.on === null ? "unk" : "ok";
}

/** Hur många av kärnans spår som är i rummet just nu. */
function personsIn(room: RoomModel, data: CardData): number {
  const corners = room.polygon.map(toPoint);
  return tracksOnFloor(data.state, data.floor.id).filter((track) => inPolygon(track, corners))
    .length;
}

/** Rummets kort, när inget är valt: läget, lamporna, enheterna och vägen till ritverktygen. */
export function roomCard(room: RoomModel, data: CardData, actions: CardActions): HTMLElement[] {
  const state = data.state;
  const roomState = state?.rooms.find((r) => r.id === room.id);
  const lampStates = new Map(state?.lamps.map((lamp) => [lamp.id, lamp]));
  const status = roomState?.status ?? "okänt";
  const persons = personsIn(room, data);
  const statusText =
    status === "upptaget" && persons > 0
      ? `${OCCUPANCY_TEXT[status]} · ${persons} ${persons === 1 ? "person" : "personer"}`
      : OCCUPANCY_TEXT[status];
  const parts: HTMLElement[] = [
    head(room.name, statusLine(statusText, status === "okänt" ? "unk" : status === "upptaget" ? "ok" : "idle"), null),
  ];
  parts[0].dataset.status = status;

  const lamps = room.lamps ?? [];
  const lux = element(
    "span",
    { className: "mono lux-value" },
    luxText(roomState?.lux ?? null, roomState?.adjusted_lux ?? null),
  );
  lux.dataset.room = room.id;
  const rows: HTMLElement[] = [
    element(
      "div",
      { className: "row" },
      element("span", { className: "grow" }, "Ljus"),
      lux,
    ),
    ...lamps.map((lamp) => {
      const lampState = lampStates.get(lamp.id);
      return element(
        "div",
        { className: "row lamp-row" },
        element(
          "span",
          { className: "grow" },
          lamp.name,
          " ",
          element("span", { className: "muted mono" }, lampStateText(lampState)),
        ),
        powerButton(lamp, lampState, actions),
      );
    }),
  ];
  const automation = element("div", { className: "row automation" });
  if (roomState?.manual === true) {
    automation.append(
      element("span", { className: "grow" }, "Manuellt läge"),
      button("Automatik igen", () => actions.send({ type: "resume_automation", room_id: room.id }), "resume"),
    );
  } else {
    automation.append(element("span", { className: "grow" }, "Automatik på"));
  }
  automation.dataset.manual = String(roomState?.manual === true);
  rows.push(automation);
  parts.push(element("div", { className: "rows" }, ...rows));
  if (roomState !== undefined && roomState.explanation !== "") {
    parts.push(element("p", { className: "help explanation" }, roomState.explanation));
  }

  // Enheterna: rumskorten, lamporna och routrarna på kartan, Zigbee-stickan och nya enheter.
  const devices: HTMLLIElement[] = [];
  for (const node of room.nodes ?? []) {
    const [text, level] = nodeState(cardFor(state, node.id));
    devices.push(deviceRow(node.name, text, level, () => actions.select(node.id)));
  }
  for (const lamp of lamps) {
    const lampState = lampStates.get(lamp.id);
    devices.push(
      deviceRow(lamp.name, lampStateText(lampState), lampLevel(lampState), () => actions.select(lamp.id)),
    );
  }
  if (state?.zigbee != null) {
    const problem = zigbeeProblem(state);
    devices.push(
      deviceRow(
        "Zigbee-sticka",
        problem === null ? "ansluten" : "svarar inte",
        problem === null ? "ok" : "bad",
        () => actions.showMessage(problem ?? "Zigbee-stickan och MQTT-brokern svarar."),
      ),
    );
  }
  for (const router of data.floor.signal_sources ?? []) {
    devices.push(deviceRow(router.name, "vilande", "idle", () => actions.select(router.id)));
  }
  for (const device of data.newDevices) {
    devices.push(deviceRow(device.name, "ny · inte placerad", "new", () => actions.selectNew(device.id)));
  }
  if (devices.length > 0) {
    const list = element("ul", { className: "devices" }, ...devices);
    list.setAttribute("aria-label", "Enheter");
    parts.push(element("h3", { className: "section-title" }, "Enheter"), list);
  }

  const simulation = state?.simulation;
  if (simulation?.enabled === true) {
    parts.push(
      element("h3", { className: "section-title" }, "Simulering"),
      ...personsList(simulation, actions.send),
    );
  }

  const rules = element(
    "details",
    { className: "more light-rules" },
    element("summary", {}, "Ljusregler"),
    roomSettingsForm(room, {
      commit: actions.commit,
      showMessage: actions.showMessage,
      deselect: () => undefined,
    }),
  );
  const links: HTMLElement[] = [rules];
  if (lamps.length > 0) {
    const calibrate = button("Mät lampornas ljus", () => actions.calibrate(room.id), "link-row calibrate");
    calibrate.title = "Lampkalibrering: hur mycket lamporna själva lyser upp ljussensorn";
    links.push(calibrate);
  }
  links.push(button("Ändra rummet", actions.editRoom, "link-row edit-room"));
  parts.push(element("div", {}, ...links));
  parts.push(element("p", { className: "help" }, "Väggar, dörrar och zoner ändras lättast på en dator."));
  return parts;
}

/** Kortet för ett valt rumskort: hälsan, Flytta, kalibrering och fler inställningar. */
export function nodeCard(found: FoundItem, data: CardData, actions: CardActions): HTMLElement[] {
  const node = found.item as NodeModel;
  const card = cardFor(data.state, node.id);
  const [text, level] = nodeState(card);
  const parts: HTMLElement[] = [head(node.name, statusLine(text, level), actions)];
  const heard = data.lastHeard.get(node.id);
  const heardText = heard === undefined ? "okänt" : clockTime(heard);
  if (card !== undefined && !card.online) {
    parts.push(
      element(
        "div",
        { className: "notice", role: "alert" },
        element("p", {}, `Rumskortet svarar inte. Senast hört ${heardText}. Lampan lämnas som den är.`),
        element(
          "ol",
          { className: "steps" },
          element("li", {}, "Har kortet ström?"),
          element("li", {}, "Är Wi-Fi bra nära kortet?"),
          element("li", {}, "Dra ur och sätt i strömmen."),
        ),
      ),
    );
    parts[parts.length - 1].dataset.level = "bad";
  }
  if (card !== undefined && card.online && card.problems.length > 0) {
    parts.push(element("ul", { className: "notice problems" }, ...card.problems.map((p) => element("li", {}, p))));
  }
  const rows: [string, string][] = [];
  if (card === undefined) {
    rows.push(["Kort", "Simulerat, inget riktigt kort"]);
  } else {
    rows.push(
      ["Signal", card.wifi_signal === null ? "okänd" : signalText(card.wifi_signal)],
      ["Version", card.firmware_version ?? "okänd"],
      ["Senast hörd", card.online ? "nu" : heardText],
    );
  }
  rows.push(["Höjd", `${format(node.height)} m`], ["Vridning", `${node.rotation ?? 0}°`]);
  if (found.room !== null) {
    rows.push(["Rum", found.room.name]);
  }
  parts.push(keyValues(rows));

  const buttons = [moveButton(node.id, data, actions)];
  if (found.room !== null) {
    const roomId = found.room.id;
    const calibrate = button("Kalibrera tomt rum", () => actions.calibrate(roomId), "calibrate");
    calibrate.title = "Mät hur mycket rummets lampor lyser upp kortets ljussensor, med rummet tomt";
    buttons.push(calibrate);
  }
  parts.push(...buttonRow(buttons, data));

  parts.push(nodeSettings(node, actions));
  parts.push(
    element(
      "p",
      { className: "help" },
      "Installera eller uppdatera ett kort över USB: det kräver en dator med Chrome eller Edge och " +
        "webbinstalleraren (se firmware/README.md). Det går inte från mobilen.",
    ),
  );
  return parts;
}

/** Fler inställningar för ett rumskort: höjden och spegelvänt sidled. */
function nodeSettings(node: NodeModel, actions: CardActions): HTMLElement {
  const update = (changes: Partial<NodeModel>) =>
    actions.commit((document) => updateItem(document, node.id, changes));
  const height = element("input", {
    type: "number",
    min: "0",
    max: String(MAX_HEIGHT),
    step: "0.1",
    value: String(node.height),
    name: "height",
  });
  height.addEventListener("change", () => {
    const next = Number(height.value);
    if (height.value === "" || !(next >= 0 && next <= MAX_HEIGHT)) {
      height.value = String(node.height);
      actions.showMessage(`Höjden ska vara mellan 0 och ${MAX_HEIGHT} meter.`);
    } else if (next !== node.height) {
      update({ height: next });
    }
  });
  const mirrored = element("input", { type: "checkbox", checked: node.mirrored ?? false, name: "mirrored" });
  mirrored.addEventListener("change", () => update({ mirrored: mirrored.checked }));
  return element(
    "details",
    { className: "more" },
    element("summary", {}, "Fler inställningar"),
    element("label", {}, "Höjd över golvet (m) ", height),
    element("label", { className: "checkbox" }, mirrored, " Spegelvänd sidled"),
    element(
      "p",
      { className: "help" },
      "Kryssa i om prickarna hamnar på fel sida om rumskortet, till exempel om kortet sitter upp och ned.",
    ),
  );
}

/** Lampans kort: den stora PÅ/AV-knappen, ljusstyrkan och om lampan svarar. */
export function lampCard(found: FoundItem, data: CardData, actions: CardActions): HTMLElement[] {
  const lamp = found.item as LampModel;
  const lampState = data.state?.lamps.find((l) => l.id === lamp.id);
  const parts: HTMLElement[] = [
    head(lamp.name, statusLine(lampStateText(lampState), lampLevel(lampState)), actions),
  ];
  if (lampState?.responding === false) {
    const notice = element(
      "p",
      { className: "notice" },
      "Svarar inte. Lampan visas inte som tänd förrän den svarar igen. Är strömbrytaren på?",
    );
    notice.dataset.level = "bad";
    parts.push(notice);
  }
  parts.push(powerButton(lamp, lampState, actions, true));
  const rows: [string, string][] = [
    ["Ljusstyrka", lampState?.on === true ? `${lampState.brightness} %` : "–"],
  ];
  if (found.room !== null) {
    rows.push(["Rum", found.room.name]);
  }
  if (data.virtualLamps.has(lamp.id)) {
    rows.push(["Lampa", "Finns bara i simuleringen"]);
  }
  parts.push(keyValues(rows));
  const buttons = [moveButton(lamp.id, data, actions)];
  if (found.room !== null) {
    const roomId = found.room.id;
    buttons.push(button("Kalibrera mot ljussensorn", () => actions.calibrate(roomId), "calibrate"));
  }
  parts.push(...buttonRow(buttons, data));
  return parts;
}

/** Routerns kort. Wi-Fi-avkänningen är vilande, så routern gör inget just nu. */
export function routerCard(found: FoundItem, data: CardData, actions: CardActions): HTMLElement[] {
  const router = found.item as SignalSourceModel;
  return [
    head(router.name, statusLine("vilande", "idle"), actions),
    element("p", { className: "help" }, "Vilande – Wi-Fi-avkänning används inte just nu."),
    keyValues([["Höjd", `${format(router.height)} m`]]),
    ...buttonRow([moveButton(router.id, data, actions)], data),
  ];
}

/** Kortet för en ny enhet som kärnan har hittat. Den används först när den är placerad. */
export function newDeviceCard(device: NewDevice, data: CardData, actions: CardActions): HTMLElement[] {
  const what = device.kind === "node" ? "Nytt rumskort hittat · inte placerat" : "Ny lampa hittad · inte placerad";
  const place = button("Placera på kartan", () => actions.place(device), "primary");
  place.disabled = data.databaseDown;
  return [
    head(device.name, statusLine(what, "new"), actions),
    element(
      "p",
      { className: "help" },
      device.kind === "node"
        ? "Kärnan använder inte kortet förrän det är placerat. Det hamnar mitt i rummet; dra det dit det sitter och vrid det åt rätt håll."
        : "Kärnan styr inte lampan förrän den är placerad. Den hamnar mitt i rummet; dra den dit den sitter.",
    ),
    place,
    ...(data.databaseDown ? [element("p", { className: "help" }, DATABASE_DOWN_MOVE)] : []),
  ];
}

/** Kortet medan något flyttas: var det står, vridningen och hur man kontrollerar att det blev rätt. */
export function moveCard(
  found: FoundItem,
  rotate: (degrees: number) => void,
): HTMLElement[] {
  const item = found.item;
  const [x, y] = item.position;
  const rows: [string, string][] = [
    ["x", `${format(x)} m`],
    ["y", `${format(y)} m`],
  ];
  const parts: HTMLElement[] = [head(`Flyttar ${item.name}`, null, null)];
  if (found.kind === "node") {
    const rotation = (item as NodeModel).rotation ?? 0;
    rows.push(["Vridning", `${rotation}°`]);
    parts.push(keyValues(rows));
    const left = button(`−${ROTATION_STEP}°`, () => rotate(-ROTATION_STEP));
    left.setAttribute("aria-label", `Vrid ${ROTATION_STEP}° moturs`);
    const right = button(`+${ROTATION_STEP}°`, () => rotate(ROTATION_STEP));
    right.setAttribute("aria-label", `Vrid ${ROTATION_STEP}° medurs`);
    parts.push(
      element("div", { className: "button-row" }, left, right),
      element(
        "p",
        { className: "help" },
        "Dra kortet för att flytta det och dra i ringen för att vrida det. På datorn flyttar " +
          "piltangenterna 5 cm och Skift+pil vrider 15°.",
      ),
      element(
        "p",
        { className: "help" },
        `Konen visar radarns räckvidd i teorin (${RADAR_RANGE} m, ±${RADAR_HALF_ANGLE}°), inte vad den ser genom möbler.`,
      ),
      element("p", {}, "Gå runt i rummet. Hamnar din prick där du står sitter kortet rätt."),
    );
  } else {
    parts.push(
      keyValues(rows),
      element(
        "p",
        { className: "help" },
        "Dra för att flytta. På datorn flyttar piltangenterna 5 cm.",
      ),
    );
  }
  parts.push(element("p", { className: "help" }, "Varje släpp sparas direkt. Avbryt ställer tillbaka allt."));
  return parts;
}
