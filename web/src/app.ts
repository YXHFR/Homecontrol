import { LiveConnection, liveUrl, type ConnectionHandlers, type ConnectionStatus } from "./connection";
import { element } from "./dom";
import { parkingSpot } from "./geometry";
import type { ClientMessage, Floor, HomeMessage, StateMessage } from "./protocol";
import { createCardsPanel } from "./cardsPanel";
import { createSimulationPanel } from "./simulationPanel";
import { OCCUPANCY_TEXT } from "./trackStyles";
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

const STATUS_TEXT: Record<ConnectionStatus, string> = {
  connecting: "Ansluter till kärnan …",
  open: "Ansluten",
  closed: "Tappade kontakten med kärnan, försöker igen …",
};

/** Våningen som visas först: entréplanet om det finns, annars den första. */
export function startFloor(home: HomeMessage): Floor {
  return home.floors.find((floor) => floor.level === 0) ?? home.floors[0];
}

/**
 * Ritar upp livevyn: hemmet en våning åt gången, lamporna, och panelen för
 * simuleringsläget med låtsaspersoner, dagsljus och tidsfart.
 */
export function mountApp(root: HTMLElement, deps: AppDependencies = defaultDependencies): void {
  const status = element("p", { className: "status" }, STATUS_TEXT.connecting);
  const stageContainer = element("div", { className: "stage" });
  const explanations = element("ul", { className: "explanations" });
  const roomsNow = element("ul", { className: "rooms-now" });
  const floorPicker = element("nav", { className: "floors", hidden: true });
  floorPicker.setAttribute("aria-label", "Våningar");

  // Nya personer ställer sig i kö bredvid rummen, en ny plats för varje klick.
  let personsAdded = 0;
  const simulation = createSimulationPanel(
    (message) => connection.send(message),
    () => {
      if (shownFloor !== null) {
        const spot = parkingSpot(shownFloor.rooms, personsAdded++);
        connection.send({ type: "add_person", floor_id: shownFloor.id, ...spot });
      }
    },
  );

  const cards = createCardsPanel((message) => connection.send(message));

  root.replaceChildren(
    element("h1", {}, "Homecontrol – livevy"),
    status,
    floorPicker,
    stageContainer,
    element(
      "p",
      { className: "legend" },
      "Prick: en person som radarn ser. Streckad ring: radarn har tappat personen, " +
        "men den räknas som kvar en stund till. Fyrkant: ett rumskort, rött med ⚠ om " +
        "något på kortet inte svarar.",
    ),
    cards.element,
    simulation.element,
    element("h2", {}, "Just nu"),
    roomsNow,
    element("h2", {}, "Varför lamporna är som de är"),
    explanations,
  );

  let shownFloor: Floor | null = null;
  let lastState: StateMessage | null = null;
  const view = deps.createView(stageContainer, {
    onMovePerson: (personId, position) => {
      if (shownFloor !== null) {
        connection.send({ type: "move_person", person_id: personId, floor_id: shownFloor.id, ...position });
      }
    },
  });

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

  /** Läget och ljusnivån i varje rum på våningen som visas. */
  const showRooms = () => {
    const states = new Map(lastState?.rooms.map((room) => [room.id, room]));
    roomsNow.replaceChildren(
      ...(shownFloor?.rooms ?? []).flatMap((room) => {
        const roomState = states.get(room.id);
        if (roomState === undefined) {
          return [];
        }
        const lux =
          roomState.lux === null
            ? "ljusnivån okänd"
            : `${roomState.lux.toLocaleString("sv-SE", { maximumFractionDigits: 1 })} lux`;
        const item = element("li", {}, `${room.name}: ${OCCUPANCY_TEXT[roomState.status]}, ${lux}`);
        item.dataset.status = roomState.status;
        return [item];
      }),
    );
  };

  const showFloor = (next: Floor) => {
    shownFloor = next;
    for (const button of floorPicker.querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(button.dataset.floor === next.id));
    }
    view.setFloor(next);
    showRooms();
    showExplanations();
  };

  const showHome = (next: HomeMessage) => {
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
    cards.show(state.cards);
    simulation.show(state.simulation);
    showRooms();
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

  connection.start();
  root.dataset.ready = "true";
}
