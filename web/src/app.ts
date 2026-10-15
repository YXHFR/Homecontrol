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
  let shownFloor: Floor | null = null;
  let lastState: StateMessage | null = null;
  const view = deps.createView(stageContainer, {
    onMovePerson: (position) => {
      if (shownFloor !== null) {
        connection.send({ type: "move_person", floor_id: shownFloor.id, ...position });
      }
    },
  });

  /** Rum som redan har fått reglagets värde för att kärnan inte visste något. */
  const roomsGivenSliderLux = new Set<string>();

  const sendLightLevel = (roomIds: string[]) => {
    for (const roomId of roomIds) {
      connection.send({ type: "set_light_level", room_id: roomId, lux: Number(lux.value) });
    }
  };

  /** Samma ljusnivå i alla rum, som dagsljuset ute. */
  const sendLightLevelEverywhere = () => {
    sendLightLevel(home?.floors.flatMap((floor) => floor.rooms.map((room) => room.id)) ?? []);
  };

  const showExplanations = () => {
    const lamps = shownFloor?.lamps ?? [];
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

  /** Reglaget visar ljusnivån i första rummet på våningen som visas. */
  const showLux = () => {
    if (document.activeElement === lux) {
      return; // användaren drar i reglaget just nu
    }
    const shownRoom = shownFloor?.rooms[0]?.id;
    const roomLux = lastState?.rooms.find((room) => room.id === shownRoom)?.lux;
    if (roomLux !== undefined && roomLux !== null) {
      lux.value = String(roomLux);
      luxValue.textContent = `${roomLux} lux`;
    }
  };

  const showFloor = (next: Floor) => {
    shownFloor = next;
    for (const button of floorPicker.querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(button.dataset.floor === next.id));
    }
    view.setFloor(next);
    showLux();
    showExplanations();
  };

  const showHome = (next: HomeMessage) => {
    home = next;
    // Ny anslutning: kärnan kan ha startat om och glömt ljusnivåerna.
    roomsGivenSliderLux.clear();
    floorPicker.replaceChildren(
      ...next.floors.map((floor) => {
        const button = element("button", { type: "button" }, floor.name);
        button.dataset.floor = floor.id;
        button.addEventListener("click", () => showFloor(floor));
        return button;
      }),
    );
    // Gränssnittet visar en våning åt gången; väljaren behövs bara om det finns flera.
    floorPicker.hidden = next.floors.length < 2;
    const same = next.floors.find((floor) => floor.id === shownFloor?.id);
    showFloor(same ?? startFloor(next));
  };

  const showState = (state: StateMessage) => {
    lastState = state;
    view.setState(state);
    // Kärnan vet inte hur ljust det är i de här rummen; ge dem reglagets värde.
    const unknown = state.rooms
      .filter((room) => room.lux === null && !roomsGivenSliderLux.has(room.id))
      .map((room) => room.id);
    unknown.forEach((roomId) => roomsGivenSliderLux.add(roomId));
    sendLightLevel(unknown);
    showLux();
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
