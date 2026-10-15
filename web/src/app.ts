import { LiveConnection, liveUrl, type ConnectionHandlers, type ConnectionStatus } from "./connection";
import type { ClientMessage, HomeMessage, StateMessage } from "./protocol";
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

/** Ritar upp livevyn: ett rum, en virtuell lampa och en låtsasperson att dra omkring. */
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
  const luxLabel = element("label", {}, "Ljusnivå i rummet ", lux, " ", luxValue);
  const removePerson = element("button", { type: "button" }, "Låt personen gå hemifrån");
  const explanations = element("ul", { className: "explanations" });

  root.replaceChildren(
    element("h1", {}, "Homecontrol – livevy"),
    status,
    element(
      "p",
      { className: "help" },
      "Dra den röda pricken in i rummet. När det är mörkt tänds lampan. " +
        "När rummet har varit tomt i 3 minuter släcks den igen.",
    ),
    stageContainer,
    element("div", { className: "controls" }, luxLabel, removePerson),
    element("h2", {}, "Varför lamporna är som de är"),
    explanations,
  );

  let home: HomeMessage | null = null;
  const view = deps.createView(stageContainer, {
    onMovePerson: (position) => connection.send({ type: "move_person", ...position }),
  });

  const sendLightLevel = () => {
    const room = home?.rooms[0];
    if (room !== undefined) {
      connection.send({ type: "set_light_level", room_id: room.id, lux: Number(lux.value) });
    }
  };

  const showState = (state: StateMessage) => {
    view.setState(state);
    const roomLux = state.rooms[0]?.lux;
    if (roomLux === null) {
      // Kärnan vet inte hur ljust det är; ge den reglagets värde.
      sendLightLevel();
    } else if (roomLux !== undefined && document.activeElement !== lux) {
      lux.value = String(roomLux);
      luxValue.textContent = `${roomLux} lux`;
    }
    const names = new Map(home?.lamps.map((lamp) => [lamp.id, lamp.name]));
    explanations.replaceChildren(
      ...state.lamps.map((lamp) =>
        element("li", {}, `${names.get(lamp.id) ?? lamp.id}: ${lamp.explanation}`),
      ),
    );
  };

  const connection = deps.connect({
    onStatus: (next) => {
      status.textContent = STATUS_TEXT[next];
      root.dataset.status = next;
    },
    onMessage: (message) => {
      if (message.type === "home") {
        home = message;
        view.setHome(message);
      } else {
        showState(message);
      }
    },
  });

  lux.addEventListener("input", () => {
    luxValue.textContent = `${lux.value} lux`;
    sendLightLevel();
  });
  removePerson.addEventListener("click", () => connection.send({ type: "remove_person" }));

  connection.start();
  root.dataset.ready = "true";
}
