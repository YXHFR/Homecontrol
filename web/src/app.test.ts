import { describe, expect, it } from "vitest";
import { mountApp, startFloor, type AppDependencies, type Connection } from "./app";
import type { ConnectionHandlers } from "./connection";
import {
  MAX_PERSONS,
  type Card,
  type ClientMessage,
  type Floor,
  type HomeMessage,
  type Room,
  type Simulation,
  type StateMessage,
} from "./protocol";
import type { LiveViewCallbacks } from "./view";

function rectangle(id: string, name: string): Room {
  return {
    id,
    name,
    polygon: [
      [0, 0],
      [5, 0],
      [5, 4],
      [0, 4],
    ],
    walls: ["gips", "gips", "gips", "dörröppning"],
    zones: [],
  };
}

const groundFloor: Floor = {
  id: "plan",
  name: "Entréplan",
  level: 0,
  rooms: [rectangle("vardagsrum", "Vardagsrum")],
  nodes: [],
  lamps: [{ id: "taklampa", name: "Taklampa", room_id: "vardagsrum", x: 2.5, y: 2, virtual: true }],
};

const upstairs: Floor = {
  id: "uppe",
  name: "Övervåning",
  level: 1,
  rooms: [rectangle("sovrum", "Sovrum")],
  nodes: [],
  lamps: [
    { id: "sovrumslampa", name: "Sovrumslampa", room_id: "sovrum", x: 2.5, y: 2, virtual: true },
  ],
};

const home: HomeMessage = { type: "home", name: "Hemmet", floors: [groundFloor] };
const twoFloors: HomeMessage = { type: "home", name: "Hemmet", floors: [upstairs, groundFloor] };

const simulationOn: Simulation = {
  enabled: true,
  speed: 1,
  speed_locked: false,
  daylight: 10,
  persons: [],
};

function state(
  lux: number | null,
  on: boolean,
  simulation: Simulation | null = simulationOn,
): StateMessage {
  return {
    type: "state",
    tracks: on ? [{ floor_id: "plan", x: 2.5, y: 2, held: false }] : [],
    rooms: [{ id: "vardagsrum", status: on ? "upptaget" : "tomt", lux }],
    zones: [],
    lamps: [{ id: "taklampa", on, explanation: on ? "Tänd: någon är här." : "Släckt: tomt." }],
    simulation,
    cards: null,
  };
}

function card(changes: Partial<Card> = {}): Card {
  return {
    id: "homecontrol-vardagsrum",
    name: "Homecontrol vardagsrum",
    placed: true,
    online: true,
    problems: [],
    calibrating: false,
    wifi_signal: null,
    firmware_version: "0.4.0",
    update: null,
    ...changes,
  };
}

const text = (root: HTMLElement, selector: string) =>
  root.querySelector(selector)?.textContent ?? "";

function find<T extends Element>(root: Element, selector: string): T {
  const found = root.querySelector<T>(selector);
  if (found === null) {
    throw new Error(`Hittade inte ${selector}`);
  }
  return found;
}

function setup() {
  const sent: ClientMessage[] = [];
  const shown: (Floor | StateMessage)[] = [];
  let handlers: ConnectionHandlers | undefined;
  let callbacks: LiveViewCallbacks | undefined;
  const deps: AppDependencies = {
    createView: (_container, cb) => {
      callbacks = cb;
      return {
        setFloor: (f) => shown.push(f),
        setState: (m) => shown.push(m),
        destroy: () => undefined,
      };
    },
    connect: (h): Connection => {
      handlers = h;
      return { start: () => h.onStatus("open"), send: (m) => sent.push(m) };
    },
  };
  const root = document.createElement("main");
  document.body.replaceChildren(root);
  mountApp(root, deps);
  if (handlers === undefined || callbacks === undefined) {
    throw new Error("mountApp kopplade inte upp sig");
  }
  return { root, sent, shown, handlers, callbacks };
}

describe("mountApp", () => {
  it("ansluter och visar att den är ansluten", () => {
    const { root } = setup();

    expect(root.dataset.ready).toBe("true");
    expect(root.dataset.status).toBe("open");
    expect(root.querySelector(".status")?.textContent).toBe("Ansluten");
  });

  it("skickar våningen och läget vidare till ritytan och visar förklaringarna", () => {
    const { root, shown, handlers } = setup();

    handlers.onMessage(home);
    handlers.onMessage(state(10, true));

    expect(shown).toEqual([groundFloor, state(10, true)]);
    expect(root.querySelector(".explanations")?.textContent).toBe("Taklampa: Tänd: någon är här.");
  });

  it("visar läget och ljusnivån i varje rum, även när läget är okänt", () => {
    const { root, handlers } = setup();
    handlers.onMessage(home);

    handlers.onMessage(state(10, true));
    expect(root.querySelector(".rooms-now")?.textContent).toBe("Vardagsrum: upptaget, 10 lux");

    handlers.onMessage({ ...state(null, false), rooms: [{ id: "vardagsrum", status: "okänt", lux: null }] });
    const item = find<HTMLLIElement>(root, ".rooms-now li");
    expect(item.textContent).toBe("Vardagsrum: okänt – en sensor svarar inte, ljusnivån okänd");
    expect(item.dataset.status).toBe("okänt");
  });

  it("visar inte våningsväljaren när hemmet bara har en våning", () => {
    const { root, handlers } = setup();

    handlers.onMessage(home);

    expect(find<HTMLElement>(root, ".floors").hidden).toBe(true);
  });

  it("börjar på entréplanet och byter våning med väljaren", () => {
    const { root, shown, handlers } = setup();

    handlers.onMessage(twoFloors);
    const picker = find<HTMLElement>(root, ".floors");
    const buttons = [...picker.querySelectorAll("button")];

    expect(picker.hidden).toBe(false);
    expect(buttons.map((b) => b.textContent)).toEqual(["Övervåning", "Entréplan"]);
    expect(shown.at(-1)).toBe(groundFloor);
    expect(buttons[1].getAttribute("aria-pressed")).toBe("true");

    buttons[0].click();

    expect(shown.at(-1)).toBe(upstairs);
    expect(buttons[0].getAttribute("aria-pressed")).toBe("true");
    expect(buttons[1].getAttribute("aria-pressed")).toBe("false");
  });

  it("flyttar personen på våningen som visas", () => {
    const { root, sent, handlers, callbacks } = setup();
    handlers.onMessage(twoFloors);
    find<HTMLButtonElement>(root, ".floors button").click();

    callbacks.onMovePerson("1", { x: 1, y: 2 });

    expect(sent).toContainEqual({ type: "move_person", person_id: "1", floor_id: "uppe", x: 1, y: 2 });
  });

  it("flyttar ingen person innan hemmet har kommit", () => {
    const { sent, callbacks } = setup();

    callbacks.onMovePerson("1", { x: 1, y: 2 });

    expect(sent).toEqual([]);
  });

  it("döljer simuleringspanelen när kärnan inte har någon simulering", () => {
    const { root, handlers } = setup();
    handlers.onMessage(home);

    handlers.onMessage(state(10, false, null));

    expect(find<HTMLElement>(root, ".simulation").hidden).toBe(true);
  });

  it("slår på och av simuleringsläget", () => {
    const { root, sent, handlers } = setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, false, { ...simulationOn, enabled: false }));
    const toggle = find<HTMLInputElement>(root, ".simulation input[type=checkbox]");

    expect(find<HTMLElement>(root, ".simulation").hidden).toBe(false);
    expect(toggle.checked).toBe(false);
    expect(find<HTMLElement>(root, ".simulation-controls").hidden).toBe(true);

    toggle.checked = true;
    toggle.dispatchEvent(new Event("change"));
    handlers.onMessage(state(10, false));

    expect(sent).toEqual([{ type: "set_simulation", enabled: true }]);
    expect(find<HTMLElement>(root, ".simulation-controls").hidden).toBe(false);
  });

  it("lägger till personer bredvid rummen och tar bort dem", () => {
    const { root, sent, handlers } = setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, false));

    find<HTMLButtonElement>(root, ".simulation .controls button").click();
    handlers.onMessage(
      state(10, false, { ...simulationOn, persons: [{ id: "1", floor_id: "plan", x: 6, y: 0.5 }] }),
    );
    find<HTMLButtonElement>(root, ".simulation .controls button").click();
    const persons = find<HTMLElement>(root, ".persons");
    find<HTMLButtonElement>(persons, "button").click();

    expect(persons.textContent).toBe("Person 1 Ta bort");
    expect(sent).toEqual([
      { type: "add_person", floor_id: "plan", x: 6, y: 0.5 },
      { type: "add_person", floor_id: "plan", x: 6, y: 1.5 },
      { type: "remove_person", person_id: "1" },
    ]);
  });

  it("ger varje ny person en egen plats även när klicken kommer tätt", () => {
    const { root, sent, handlers } = setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, false));
    const add = find<HTMLButtonElement>(root, ".simulation .controls button");

    add.click();
    add.click();

    expect(sent.map((m) => (m.type === "add_person" ? m.y : null))).toEqual([0.5, 1.5]);
  });

  it("tar inte emot fler personer än kärnan tillåter", () => {
    const { root, handlers } = setup();
    handlers.onMessage(home);
    const persons = Array.from({ length: MAX_PERSONS }, (_, i) => ({
      id: String(i + 1),
      floor_id: "plan",
      x: 6,
      y: 1,
    }));

    handlers.onMessage(state(10, false, { ...simulationOn, persons }));

    expect(find<HTMLButtonElement>(root, ".simulation .controls button").disabled).toBe(true);
    expect(root.querySelector(".simulation")?.textContent).toContain("Högst 10 personer");
  });

  it("visar och skickar dagsljuset", () => {
    const { root, sent, handlers } = setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, false, { ...simulationOn, daylight: 40 }));
    const slider = find<HTMLInputElement>(root, "input[type=range]");
    const output = find<HTMLOutputElement>(root, "output");

    expect(slider.value).toBe("40");
    expect(output.textContent).toBe("40 lux");

    slider.value = "150";
    slider.dispatchEvent(new Event("input"));

    expect(sent).toEqual([{ type: "set_daylight", lux: 150 }]);
    expect(output.textContent).toBe("150 lux");
  });

  it("visar och byter tidsfart", () => {
    const { root, sent, handlers } = setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, false, { ...simulationOn, speed: 10 }));
    const buttons = [...root.querySelectorAll<HTMLButtonElement>(".speeds button")];

    expect(buttons.map((b) => b.textContent)).toEqual(["1×", "10×", "60×"]);
    expect(buttons.map((b) => b.getAttribute("aria-pressed"))).toEqual(["false", "true", "false"]);

    buttons[2].click();

    expect(sent).toEqual([{ type: "set_speed", speed: 60 }]);
  });

  it("låser tidsfarten när riktiga rumskort används", () => {
    const { root, handlers } = setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, false, { ...simulationOn, speed_locked: true }));
    const buttons = [...root.querySelectorAll<HTMLButtonElement>(".speeds button")];

    expect(buttons.map((b) => b.disabled)).toEqual([false, true, true]);
    expect(text(root, ".simulation")).toMatch(/vanlig takt så länge riktiga sensorer eller lampor används/);
  });

  it("varnar för kort som inte svarar och visar nya kort att placera", () => {
    const { root, handlers } = setup();
    handlers.onMessage(home);
    expect(find<HTMLElement>(root, ".cards-panel").hidden).toBe(true);

    handlers.onMessage({
      ...state(null, false),
      cards: [
        card({ problems: ["Radarn skickar inget."] }),
        card({ id: "homecontrol-kok", name: "Homecontrol kok", placed: false }),
      ],
    });

    expect(find<HTMLElement>(root, ".cards-panel").hidden).toBe(false);
    const warning = find<HTMLLIElement>(root, '.cards li[data-card="homecontrol-vardagsrum"]');
    expect(warning.dataset.warning).toBe("true");
    expect(warning.textContent).toBe("⚠ Homecontrol vardagsrum: Radarn skickar inget.");
    expect(text(root, ".new-cards")).toBe("Homecontrol kok (homecontrol-kok)");
  });

  it("uppdaterar ett kort först när någon har godkänt det", () => {
    const { root, sent, handlers } = setup();
    handlers.onMessage(home);
    const update = { version: "0.5.0", in_progress: false, progress: null };
    handlers.onMessage({ ...state(null, false), cards: [card({ update, wifi_signal: -58 })] });

    expect(text(root, ".cards li")).toMatch(/Fungerar\. Wi-Fi -58 dBm \(bra\)\. Version 0\.4\.0\./);
    expect(sent).toEqual([]);

    find<HTMLButtonElement>(root, ".cards button").click();
    expect(sent).toEqual([{ type: "install_update", card: "homecontrol-vardagsrum" }]);

    handlers.onMessage({
      ...state(null, false),
      cards: [card({ update: { ...update, in_progress: true, progress: 42 } })],
    });
    expect(root.querySelector(".cards button")).toBeNull();
    expect(text(root, ".cards li")).toMatch(/Uppdaterar till 0\.5\.0 … 42 %/);
  });
});

describe("startFloor", () => {
  it("väljer planet med nivå 0 och annars den första våningen", () => {
    expect(startFloor(twoFloors)).toBe(groundFloor);
    expect(startFloor({ ...twoFloors, floors: [upstairs] })).toBe(upstairs);
  });
});
