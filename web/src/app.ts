import { LiveConnection, liveUrl, type ConnectionHandlers, type ConnectionStatus } from "./connection";
import type { ClientMessage, Floor, HomeMessage, StateMessage } from "./protocol";
import { createKonvaView, type ViewFactory } from "./view";

export interface Connection {
  start(): void;
  send(message: ClientMessage): void;
}

export interface AppDependencies {
  createView: ViewFactory;
  connect(handlers: ConnectionHandlers): Connection;
}

const defaultDependencies: AppDependencies = {
  createView: createKonvaView,
  connect: (handlers) => new LiveConnection(liveUrl(window.location), handlers),
};

/** Ljusnivån som livevyn ställer in om kärnan inte vet någon än: mörkt. */
export const DEFAULT_LUX = 10;

const STATUS_TEXT: Record<ConnectionStatus, string> = {
  connecting: "Ansluter till kärnan …",
  open: "Ansluten",
  closed: "Tappade kontakten med kärnan, försöker igen …",
};

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Partial<HTMLElementTagNameMap[K]> = {},
  ...children: (Node | string)[]
): HTMLElementTagNameMap[K] {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

/** Våningen som visas först: entréplanet om det finns, annars den första. */
export function startFloor(home: HomeMessage): Floor {
  return home.floors.find((floor) => floor.level === 0) ?? home.floors[0];
}

/**
 * Ritar upp livevyn: hemmet en våning åt gången, de virtuella lamporna och
 * en låtsasperson att dra omkring.
 */
export function mountApp(root: HTMLElement, deps: AppDependencies = defaultDependencies): void {
  const status = element("p", { className: "status" }, STATUS_TEXT.connecting);
  const stageContainer = element("div", { className: "stage" });
  const lux = element("input", {
    type: "range",
    min: "0",
    max: "300",
    step: "5",
    value: String(DEFAULT_LUX),
  });
  const luxValue = element("output", {}, `${DEFAULT_LUX} lux`);
  const luxLabel = element("label", {}, "Ljusnivå i rummen ", lux, " ", luxValue);
  const removePerson = element("button", { type: "button" }, "Låt personen gå hemifrån");
  const explanations = element("ul", { className: "explanations" });
  const floorPicker = element("nav", { className: "floors", hidden: true });
  floorPicker.setAttribute("aria-label", "Våningar");

  root.replaceChildren(
    element("h1", {}, "Homecontrol – livevy"),
    status,
    element(
      "p",
      { className: "help" },
      "Dra den röda pricken in i ett rum. När det är mörkt tänds lampan. " +
        "När rummet har varit tomt en stund släcks den igen.",
    ),
    floorPicker,
    stageContainer,
    element("div", { className: "controls" }, luxLabel, removePerson),
    element("h2", {}, "Varför lamporna är som de är"),
    explanations,
  );

  let home: HomeMessage | null = null;
  let floor: Floor | null = null;
  let lastState: StateMessage | null = null;
  const view = deps.createView(stageContainer, {
    onMovePerson: (position) => {
      if (floor !== null) {
        connection.send({ type: "move_person", floor_id: floor.id, ...position });
      }
    },
  });

  /** Rum som redan har fått reglagets värde för att kärnan inte visste något. */
  const filledIn = new Set<string>();

  const sendLightLevel = (roomIds: string[]) => {
    for (const roomId of roomIds) {
      connection.send({ type: "set_light_level", room_id: roomId, lux: Number(lux.value) });
    }
  };

  /** Samma ljusnivå i alla rum, som dagsljuset ute. */
  const sendLightLevelEverywhere = () => {
    sendLightLevel(home?.floors.flatMap((f) => f.rooms.map((room) => room.id)) ?? []);
  };

  const showExplanations = () => {
    const lamps = floor?.lamps ?? [];
    const states = new Map(lastState?.lamps.map((lamp) => [lamp.id, lamp]));
    explanations.replaceChildren(
      ...lamps.flatMap((lamp) => {
        const lampState = states.get(lamp.id);
        return lampState === undefined
          ? []
          : [element("li", {}, `${lamp.name}: ${lampState.explanation}`)];
      }),
    );
  };

  const showFloor = (next: Floor) => {
    floor = next;
    for (const button of floorPicker.querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(button.dataset.floor === next.id));
    }
    view.setFloor(next);
    showExplanations();
  };

  const showHome = (next: HomeMessage) => {
    home = next;
    // Ny anslutning: kärnan kan ha startat om och glömt ljusnivåerna.
    filledIn.clear();
    floorPicker.replaceChildren(
      ...next.floors.map((f) => {
        const button = element("button", { type: "button" }, f.name);
        button.dataset.floor = f.id;
        button.addEventListener("click", () => showFloor(f));
        return button;
      }),
    );
    // Gränssnittet visar en våning åt gången; väljaren behövs bara om det finns flera.
    floorPicker.hidden = next.floors.length < 2;
    const same = next.floors.find((f) => f.id === floor?.id);
    showFloor(same ?? startFloor(next));
  };

  const showState = (state: StateMessage) => {
    lastState = state;
    view.setState(state);
    // Kärnan vet inte hur ljust det är i de här rummen; ge dem reglagets värde.
    const unknown = state.rooms
      .filter((room) => room.lux === null && !filledIn.has(room.id))
      .map((room) => room.id);
    unknown.forEach((roomId) => filledIn.add(roomId));
    sendLightLevel(unknown);
    if (document.activeElement !== lux) {
      const shownRoom = floor?.rooms[0]?.id;
      const roomLux = state.rooms.find((room) => room.id === shownRoom)?.lux;
      if (roomLux !== undefined && roomLux !== null) {
        lux.value = String(roomLux);
        luxValue.textContent = `${roomLux} lux`;
      }
    }
    showExplanations();
  };

  const connection = deps.connect({
    onStatus: (next) => {
      status.textContent = STATUS_TEXT[next];
      root.dataset.status = next;
    },
    onMessage: (message) => {
      if (message.type === "home") {
        showHome(message);
      } else {
        showState(message);
      }
    },
  });

  lux.addEventListener("input", () => {
    luxValue.textContent = `${lux.value} lux`;
    sendLightLevelEverywhere();
  });
  removePerson.addEventListener("click", () => connection.send({ type: "remove_person" }));

  connection.start();
  root.dataset.ready = "true";
}
