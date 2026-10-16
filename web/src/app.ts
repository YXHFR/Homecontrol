import { LiveConnection, liveUrl, type ConnectionHandlers, type ConnectionStatus } from "./connection";
import { parkingSpot } from "./geometry";
import {
  SPEEDS,
  type ClientMessage,
  type Floor,
  type HomeMessage,
  type Simulation,
  type StateMessage,
} from "./protocol";
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
 * Ritar upp livevyn: hemmet en våning åt gången, lamporna, och panelen för
 * simuleringsläget med låtsaspersoner, dagsljus och tidsfart.
 */
export function mountApp(root: HTMLElement, deps: AppDependencies = defaultDependencies): void {
  const status = element("p", { className: "status" }, STATUS_TEXT.connecting);
  const stageContainer = element("div", { className: "stage" });
  const explanations = element("ul", { className: "explanations" });
  const floorPicker = element("nav", { className: "floors", hidden: true });
  floorPicker.setAttribute("aria-label", "Våningar");

  // Simuleringspanelen. Den visas bara om kärnan har en simulering.
  const simulationToggle = element("input", { type: "checkbox" });
  const addPerson = element("button", { type: "button" }, "Lägg till person");
  const persons = element("ul", { className: "persons" });
  const daylight = element("input", { type: "range", min: "0", max: "300", step: "5" });
  const daylightValue = element("output", {}, "– lux");
  const speedButtons = SPEEDS.map((speed) => {
    const button = element("button", { type: "button" }, `${speed}×`);
    button.dataset.speed = String(speed);
    button.addEventListener("click", () => connection.send({ type: "set_speed", speed }));
    return button;
  });
  const speedPicker = element("div", { className: "speeds" }, ...speedButtons);
  speedPicker.setAttribute("role", "group");
  speedPicker.setAttribute("aria-label", "Tidsfart");
  const simulationControls = element(
    "div",
    { className: "simulation-controls", hidden: true },
    element(
      "p",
      { className: "help" },
      "Lägg till en låtsasperson och dra in den i ett rum med musen eller fingret. " +
        "När det är mörkt tänds lampan, och när rummet har varit tomt en stund släcks den.",
    ),
    element("div", { className: "controls" }, addPerson),
    persons,
    element("div", { className: "controls" }, element("label", {}, "Dagsljus ", daylight), " ", daylightValue),
    element("div", { className: "controls" }, element("span", {}, "Tidsfart "), speedPicker),
  );
  const simulationPanel = element(
    "section",
    { className: "simulation", hidden: true },
    element("h2", {}, "Simulering"),
    element("label", {}, simulationToggle, " Simuleringsläge"),
    simulationControls,
  );
  simulationPanel.setAttribute("aria-label", "Simulering");

  root.replaceChildren(
    element("h1", {}, "Homecontrol – livevy"),
    status,
    floorPicker,
    stageContainer,
    simulationPanel,
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

  const showPersons = (simulation: Simulation) => {
    persons.replaceChildren(
      ...simulation.persons.map((person) => {
        const remove = element("button", { type: "button" }, "Ta bort");
        remove.setAttribute("aria-label", `Ta bort person ${person.id}`);
        remove.addEventListener("click", () =>
          connection.send({ type: "remove_person", person_id: person.id }),
        );
        return element("li", {}, `Person ${person.id} `, remove);
      }),
    );
  };

  const showSimulation = (simulation: Simulation | null) => {
    simulationPanel.hidden = simulation === null;
    if (simulation === null) {
      return;
    }
    simulationToggle.checked = simulation.enabled;
    simulationControls.hidden = !simulation.enabled;
    showPersons(simulation);
    for (const button of speedButtons) {
      button.setAttribute("aria-pressed", String(button.dataset.speed === String(simulation.speed)));
    }
    // Reglaget flyttas inte medan användaren drar i det.
    if (simulation.daylight !== null && document.activeElement !== daylight) {
      daylight.value = String(simulation.daylight);
      daylightValue.textContent = `${simulation.daylight} lux`;
    }
  };

  const showFloor = (next: Floor) => {
    shownFloor = next;
    for (const button of floorPicker.querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(button.dataset.floor === next.id));
    }
    view.setFloor(next);
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
    showSimulation(state.simulation);
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

  simulationToggle.addEventListener("change", () =>
    connection.send({ type: "set_simulation", enabled: simulationToggle.checked }),
  );
  addPerson.addEventListener("click", () => {
    if (shownFloor === null) {
      return;
    }
    // Nya personer väntar i kö bredvid rummen tills någon drar in dem.
    const waiting = lastState?.simulation?.persons.length ?? 0;
    const spot = parkingSpot(shownFloor.rooms, waiting);
    connection.send({ type: "add_person", floor_id: shownFloor.id, ...spot });
  });
  daylight.addEventListener("input", () => {
    daylightValue.textContent = `${daylight.value} lux`;
    connection.send({ type: "set_daylight", lux: Number(daylight.value) });
  });

  connection.start();
  root.dataset.ready = "true";
}
