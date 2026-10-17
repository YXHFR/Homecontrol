/**
 * Simuleringsläget: listen ovanpå kartan med knappen för en ny låtsasperson,
 * dagsljuset och tidsfarten, och listan över låtsaspersonerna i rummets kort.
 * Simuleringen slås på och av med knappen Sim i toppraden.
 */
import { button, element } from "./dom";
import { MAX_PERSONS, SPEEDS, type ClientMessage, type Simulation } from "./protocol";

export interface SimulationControls {
  /** Listen ovanpå kartan. Den syns bara när simuleringen är på. */
  strip: HTMLElement;
  /** Visar läget från kärnan. */
  show(simulation: Simulation | null): void;
}

/** Texten när tidsfarten är låst till 1×. */
export const SPEED_LOCKED_TEXT =
  "Tiden går i vanlig takt så länge riktiga sensorer eller lampor används, " +
  "så att riktiga lampor inte släcks för tidigt.";

/**
 * Listen ovanpå kartan. `addPerson` lägger till en låtsasperson bredvid
 * rummen, där den sedan dras in med musen eller fingret.
 */
export function createSimulationControls(
  send: (message: ClientMessage) => void,
  addPerson: () => void,
): SimulationControls {
  const addButton = button("+ Person", addPerson, "add-person");
  addButton.setAttribute("aria-label", "Lägg till person");
  const daylight = element("input", { type: "range", min: "0", max: "300", step: "5" });
  daylight.setAttribute("aria-label", "Dagsljus");
  const daylightValue = element("output", {}, "– lux");
  const speedButtons = SPEEDS.map((speed) => {
    const node = button(`${speed}×`, () => send({ type: "set_speed", speed }));
    node.dataset.speed = String(speed);
    return node;
  });
  const speedPicker = element("div", { className: "seg speeds" }, ...speedButtons);
  speedPicker.setAttribute("role", "group");
  speedPicker.setAttribute("aria-label", "Tidsfart");
  const strip = element(
    "div",
    { className: "sim-strip", hidden: true },
    addButton,
    element("label", { className: "daylight" }, "Dagsljus", daylight, daylightValue),
    speedPicker,
  );
  strip.setAttribute("aria-label", "Simulering");
  strip.setAttribute("role", "region");

  daylight.addEventListener("input", () => {
    daylightValue.textContent = `${daylight.value} lux`;
    send({ type: "set_daylight", lux: Number(daylight.value) });
  });

  return {
    strip,
    show(simulation) {
      strip.hidden = simulation === null || !simulation.enabled;
      if (simulation === null) {
        return;
      }
      const full = simulation.persons.length >= MAX_PERSONS;
      addButton.disabled = full;
      addButton.title = full ? `Högst ${MAX_PERSONS} personer.` : "";
      for (const node of speedButtons) {
        node.setAttribute("aria-pressed", String(node.dataset.speed === String(simulation.speed)));
        node.disabled = simulation.speed_locked && node.dataset.speed !== "1";
      }
      speedPicker.title = simulation.speed_locked ? SPEED_LOCKED_TEXT : "";
      // Reglaget flyttas inte medan användaren drar i det.
      if (simulation.daylight !== null && document.activeElement !== daylight) {
        daylight.value = String(simulation.daylight);
        daylightValue.textContent = `${simulation.daylight} lux`;
      }
    },
  };
}

/** Låtsaspersonerna, var och en med en knapp som tar bort den. */
export function personsList(
  simulation: Simulation,
  send: (message: ClientMessage) => void,
): HTMLElement[] {
  const parts: HTMLElement[] = [
    element(
      "p",
      { className: "help" },
      "Dra en låtsasperson in i rummet med musen eller fingret. När det är mörkt tänds lampan, " +
        "och när rummet har varit tomt en stund släcks den.",
    ),
  ];
  if (simulation.persons.length > 0) {
    parts.push(
      element(
        "ul",
        { className: "persons" },
        ...simulation.persons.map((person) => {
          const remove = button("Ta bort", () => send({ type: "remove_person", person_id: person.id }));
          remove.setAttribute("aria-label", `Ta bort person ${person.id}`);
          return element("li", {}, element("span", { className: "mono" }, `Person ${person.id}`), remove);
        }),
      ),
    );
  }
  if (simulation.persons.length >= MAX_PERSONS) {
    parts.push(element("p", { className: "help" }, `Högst ${MAX_PERSONS} personer.`));
  }
  if (simulation.speed_locked) {
    parts.push(element("p", { className: "help" }, SPEED_LOCKED_TEXT));
  }
  return parts;
}
