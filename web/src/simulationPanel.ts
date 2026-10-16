import { element } from "./dom";
import { MAX_PERSONS, SPEEDS, type ClientMessage, type Simulation } from "./protocol";

export interface SimulationPanel {
  /** Panelen som läggs in på sidan. Den är dold tills kärnan har sagt att det finns en simulering. */
  element: HTMLElement;
  /** Visar läget från kärnan. Utan simulering döljs panelen. */
  show(simulation: Simulation | null): void;
}

/**
 * Panelen för simuleringsläget: på/av, låtsaspersoner, dagsljus och tidsfart.
 * `addPerson` lägger till en person på våningen som visas.
 */
export function createSimulationPanel(
  send: (message: ClientMessage) => void,
  addPerson: () => void,
): SimulationPanel {
  const toggle = element("input", { type: "checkbox" });
  const addButton = element("button", { type: "button" }, "Lägg till person");
  const fullNotice = element("span", { className: "status", hidden: true }, ` Högst ${MAX_PERSONS} personer.`);
  const persons = element("ul", { className: "persons" });
  const daylight = element("input", { type: "range", min: "0", max: "300", step: "5" });
  const daylightValue = element("output", {}, "– lux");
  const speedButtons = SPEEDS.map((speed) => {
    const button = element("button", { type: "button" }, `${speed}×`);
    button.dataset.speed = String(speed);
    button.addEventListener("click", () => send({ type: "set_speed", speed }));
    return button;
  });
  const speedPicker = element("div", { className: "speeds" }, ...speedButtons);
  speedPicker.setAttribute("role", "group");
  speedPicker.setAttribute("aria-label", "Tidsfart");

  const controls = element(
    "div",
    { className: "simulation-controls", hidden: true },
    element(
      "p",
      { className: "help" },
      "Lägg till en låtsasperson och dra in den i ett rum med musen eller fingret. " +
        "När det är mörkt tänds lampan, och när rummet har varit tomt en stund släcks den.",
    ),
    element("div", { className: "controls" }, addButton, fullNotice),
    persons,
    element("div", { className: "controls" }, element("label", {}, "Dagsljus ", daylight), " ", daylightValue),
    element("div", { className: "controls" }, element("span", {}, "Tidsfart "), speedPicker),
  );
  const panel = element(
    "section",
    { className: "simulation", hidden: true },
    element("h2", {}, "Simulering"),
    element("label", {}, toggle, " Simuleringsläge"),
    controls,
  );
  panel.setAttribute("aria-label", "Simulering");

  toggle.addEventListener("change", () => send({ type: "set_simulation", enabled: toggle.checked }));
  addButton.addEventListener("click", addPerson);
  daylight.addEventListener("input", () => {
    daylightValue.textContent = `${daylight.value} lux`;
    send({ type: "set_daylight", lux: Number(daylight.value) });
  });

  const showPersons = (simulation: Simulation) => {
    persons.replaceChildren(
      ...simulation.persons.map((person) => {
        const remove = element("button", { type: "button" }, "Ta bort");
        remove.setAttribute("aria-label", `Ta bort person ${person.id}`);
        remove.addEventListener("click", () => send({ type: "remove_person", person_id: person.id }));
        return element("li", {}, `Person ${person.id} `, remove);
      }),
    );
    const isFull = simulation.persons.length >= MAX_PERSONS;
    addButton.disabled = isFull;
    fullNotice.hidden = !isFull;
  };

  return {
    element: panel,
    show(simulation) {
      panel.hidden = simulation === null;
      if (simulation === null) {
        return;
      }
      toggle.checked = simulation.enabled;
      controls.hidden = !simulation.enabled;
      showPersons(simulation);
      for (const button of speedButtons) {
        button.setAttribute("aria-pressed", String(button.dataset.speed === String(simulation.speed)));
      }
      // Reglaget flyttas inte medan användaren drar i det.
      if (simulation.daylight !== null && document.activeElement !== daylight) {
        daylight.value = String(simulation.daylight);
        daylightValue.textContent = `${simulation.daylight} lux`;
      }
    },
  };
}
