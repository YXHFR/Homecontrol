import { describe, expect, it } from "vitest";
import { mountApp, startFloor, type AppDependencies, type Connection } from "./app";
import type { ConnectionHandlers } from "./connection";
import { CoreError } from "./homeApi";
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
  lamps: [{ id: "taklampa", name: "Taklampa", room_id: "vardagsrum", x: 2.5, y: 2, virtual: true, lux_contribution: 0 }],
};

const upstairs: Floor = {
  id: "uppe",
  name: "Övervåning",
  level: 1,
  rooms: [rectangle("sovrum", "Sovrum")],
  nodes: [],
  lamps: [
    { id: "sovrumslampa", name: "Sovrumslampa", room_id: "sovrum", x: 2.5, y: 2, virtual: true, lux_contribution: 0 },
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
    rooms: [
      {
        id: "vardagsrum",
        status: on ? "upptaget" : "tomt",
        lux,
        adjusted_lux: lux,
        manual: false,
        explanation: on ? "Någon är i Vardagsrum." : "Vardagsrum är tomt.",
      },
    ],
    zones: [],
    lamps: [{ id: "taklampa", on, brightness: 100, explanation: "", responding: true }],
    events: [],
    simulation,
    cards: null,
    calibration: null,
    database: null,
    zigbee: null,
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
  const calibrations: string[] = [];
  let refusal: string | null = null;
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
    calibration: {
      start: (roomId) => {
        calibrations.push(`start ${roomId}`);
        return refusal === null ? Promise.resolve() : Promise.reject(new CoreError(refusal));
      },
      cancel: () => {
        calibrations.push("avbryt");
        return Promise.resolve();
      },
    },
  };
  const root = document.createElement("main");
  document.body.replaceChildren(root);
  mountApp(root, deps);
  if (handlers === undefined || callbacks === undefined) {
    throw new Error("mountApp kopplade inte upp sig");
  }
  const refuse = (text: string) => {
    refusal = text;
  };
  return { root, sent, shown, handlers, callbacks, calibrations, refuse };
}

describe("mountApp", () => {
  it("ansluter och visar att den är ansluten", () => {
    const { root } = setup();

    expect(root.dataset.ready).toBe("true");
    expect(root.dataset.status).toBe("open");
    expect(root.querySelector(".status")?.textContent).toBe("Ansluten");
  });

  it("skickar våningen och läget vidare till ritytan och visar förklaringen", () => {
    const { root, shown, handlers } = setup();

    handlers.onMessage(home);
    handlers.onMessage(state(10, true));

    expect(shown).toEqual([groundFloor, state(10, true)]);
    expect(root.querySelector(".rooms-now .explanation")?.textContent).toBe("Någon är i Vardagsrum.");
  });

  it("visar läget och ljusnivån i varje rum, även när läget är okänt", () => {
    const { root, handlers } = setup();
    handlers.onMessage(home);

    handlers.onMessage(state(10, true));
    expect(root.querySelector(".rooms-now .room-summary")?.textContent).toBe("Vardagsrum: upptaget, 10 lux");

    const unknown = state(null, false);
    handlers.onMessage({ ...unknown, rooms: [{ ...unknown.rooms[0], status: "okänt" }] });
    const item = find<HTMLLIElement>(root, ".rooms-now li");
    expect(find(item, ".room-summary").textContent).toBe(
      "Vardagsrum: okänt – en sensor svarar inte, ljusnivån okänd",
    );
    expect(item.dataset.status).toBe("okänt");
  });

  it("visar ljusnivån utan lampornas eget ljus när de lyser upp sensorn", () => {
    const { root, handlers } = setup();
    handlers.onMessage(home);
    const lit = state(160, true);

    handlers.onMessage({ ...lit, rooms: [{ ...lit.rooms[0], adjusted_lux: 10 }] });

    expect(root.querySelector(".rooms-now .room-summary")?.textContent).toBe(
      "Vardagsrum: upptaget, 160 lux (10 utan lampornas eget ljus)",
    );
  });

  it("tänder och släcker en lampa för hand med ett klick på kartan eller i panelen", () => {
    const { root, sent, handlers, callbacks } = setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, false));

    callbacks.onToggleLamp("taklampa");
    const toggle = find<HTMLButtonElement>(root, ".lamp-toggle");
    expect(toggle.textContent).toBe("Taklampa: släckt");
    expect(toggle.getAttribute("aria-pressed")).toBe("false");

    handlers.onMessage(state(10, true));
    find<HTMLButtonElement>(root, ".lamp-toggle").click();

    expect(sent).toEqual([
      { type: "set_lamp", lamp_id: "taklampa", on: true },
      { type: "set_lamp", lamp_id: "taklampa", on: false },
    ]);
  });

  it("bygger inte om panelen när inget den visar har ändrats, så att ett klick inte tappas", () => {
    const { root, handlers } = setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, true));
    const toggle = find<HTMLButtonElement>(root, ".lamp-toggle");

    // Ett nytt läge med ett flyttat spår men samma rum och lampor.
    handlers.onMessage({ ...state(10, true), tracks: [{ floor_id: "plan", x: 3, y: 2, held: false }] });

    expect(find<HTMLButtonElement>(root, ".lamp-toggle")).toBe(toggle);
  });

  it("tänder en lampa vars läge är okänt", () => {
    const { root, sent, handlers } = setup();
    handlers.onMessage(home);
    const unknown = state(10, false);
    handlers.onMessage({ ...unknown, lamps: [{ ...unknown.lamps[0], on: null }] });

    const toggle = find<HTMLButtonElement>(root, ".lamp-toggle");
    expect(toggle.textContent).toBe("Taklampa: okänt läge");
    toggle.click();

    expect(sent).toEqual([{ type: "set_lamp", lamp_id: "taklampa", on: true }]);
  });

  it("visar manuellt läge per rum och kan lämna tillbaka rummet till automatiken", () => {
    const { root, sent, handlers } = setup();
    handlers.onMessage(home);
    const manual = state(10, true);

    handlers.onMessage({ ...manual, rooms: [{ ...manual.rooms[0], manual: true }] });
    const item = find<HTMLLIElement>(root, ".rooms-now li");
    expect(item.dataset.manual).toBe("true");
    expect(find(item, ".badge").textContent).toBe("Manuellt läge");
    find<HTMLButtonElement>(item, "button.resume").click();

    expect(sent).toEqual([{ type: "resume_automation", room_id: "vardagsrum" }]);
  });

  it("listar vad som hänt på våningen, senaste först och med klockslag", () => {
    const { root, handlers } = setup();
    handlers.onMessage(twoFloors);
    const at = new Date(2026, 9, 16, 19, 2).getTime() / 1000;

    handlers.onMessage({
      ...state(10, true),
      events: [
        { number: 1, at, room_id: "vardagsrum", text: "Tändes: någon är i Vardagsrum och det var mörkt (9 lux, under 30)." },
        { number: 2, at: at + 60, room_id: "sovrum", text: "Sovrumslampa tändes för hand." },
        { number: 3, at: at + 120, room_id: "vardagsrum", text: "Dimrades till 50 %: Vardagsrum är tomt." },
      ],
    });

    expect([...root.querySelectorAll(".events li")].map((li) => li.textContent)).toEqual([
      "19:04 Dimrades till 50 %: Vardagsrum är tomt.",
      "19:02 Tändes: någon är i Vardagsrum och det var mörkt (9 lux, under 30).",
    ]);
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

  it("varnar när kärnan inte har kontakt med databasen, och slutar när den har det igen", () => {
    const { root, handlers } = setup();
    handlers.onMessage(home);
    handlers.onMessage({ ...state(null, false), database: { connected: true } });
    expect(find<HTMLElement>(root, ".database-warning").hidden).toBe(true);

    handlers.onMessage({ ...state(null, false), database: { connected: false } });

    const warning = find<HTMLElement>(root, ".database-warning");
    expect(warning.hidden).toBe(false);
    expect(warning.getAttribute("role")).toBe("alert");
    expect(warning.textContent).toContain("Databasen svarar inte");

    handlers.onMessage({ ...state(null, false), database: { connected: true } });

    expect(warning.hidden).toBe(true);
  });

  it("visar ingen databasvarning när kärnan inte har någon databas", () => {
    const { root, handlers } = setup();
    handlers.onMessage(home);
    handlers.onMessage(state(null, false));

    expect(find<HTMLElement>(root, ".database-warning").hidden).toBe(true);
  });

  it("visar att en lampa inte svarar", () => {
    const { root, handlers } = setup();
    handlers.onMessage(home);
    const silent = state(10, false);
    silent.lamps = [{ id: "taklampa", on: null, brightness: 100, explanation: "", responding: false }];
    handlers.onMessage(silent);

    expect(root.textContent).toContain("Taklampa: svarar inte");
  });
});

describe("lampkalibreringen", () => {
  const running = (step: number) => ({
    room_id: "vardagsrum",
    phase: "pågår" as const,
    text: "Mäter ljuset från Taklampa …",
    step,
    steps: 3,
    results: {},
  });

  it("öppnas från rummet, förklarar och startar mätningen i kärnan", () => {
    const { root, handlers, calibrations } = setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, false));
    const panel = find<HTMLElement>(root, "section.calibration");
    expect(panel.hidden).toBe(true);

    find<HTMLButtonElement>(root, ".rooms-now button.calibrate").click();
    expect(panel.hidden).toBe(false);
    expect(text(root, "section.calibration h2")).toBe("Lampkalibrering: Vardagsrum");
    expect(panel.textContent).toContain("helst när det är mörkt ute");
    expect(panel.textContent).toContain("Taklampa: inte mätt än");

    find<HTMLButtonElement>(root, "section.calibration button.start").click();
    expect(calibrations).toEqual(["start vardagsrum"]);
  });

  it("visar hur mätningen går och kan avbryta den", () => {
    const { root, handlers, calibrations } = setup();
    handlers.onMessage(home);
    handlers.onMessage({ ...state(10, false), calibration: running(2) });

    const panel = find<HTMLElement>(root, "section.calibration");
    expect(panel.hidden).toBe(false);
    expect(panel.textContent).toContain("Mätning 2 av 3: Mäter ljuset från Taklampa …");
    expect(find<HTMLProgressElement>(root, "section.calibration progress").value).toBe(1);

    find<HTMLButtonElement>(root, "section.calibration button.cancel").click();
    expect(calibrations).toEqual(["avbryt"]);
  });

  it("visar lampornas uppmätta ljus och går att köra igen", () => {
    const { root, handlers, calibrations } = setup();
    handlers.onMessage(home);
    handlers.onMessage({ ...state(10, false), calibration: running(3) });
    handlers.onMessage({
      ...state(10, false),
      calibration: {
        ...running(3),
        phase: "klar",
        text: "Lampkalibreringen i Vardagsrum är klar.",
        results: { taklampa: 20.5 },
      },
    });

    const panel = find<HTMLElement>(root, "section.calibration");
    expect(panel.textContent).toContain("Taklampa: 20,5 lux");
    find<HTMLButtonElement>(root, "section.calibration button.start").click();
    expect(calibrations).toEqual(["start vardagsrum"]);

    find<HTMLButtonElement>(root, "section.calibration button.close").click();
    expect(panel.hidden).toBe(true);
  });

  it("säger varför mätningen inte gick att starta", async () => {
    const { root, handlers, refuse } = setup();
    handlers.onMessage(home);
    handlers.onMessage(state(null, false));
    refuse("Ingen ljussensor har mätt ljuset i Vardagsrum.");

    find<HTMLButtonElement>(root, ".rooms-now button.calibrate").click();
    find<HTMLButtonElement>(root, "section.calibration button.start").click();
    await Promise.resolve();
    await Promise.resolve();

    expect(text(root, "section.calibration .problem")).toBe(
      "Ingen ljussensor har mätt ljuset i Vardagsrum.",
    );
  });
});

describe("startFloor", () => {
  it("väljer planet med nivå 0 och annars den första våningen", () => {
    expect(startFloor(twoFloors)).toBe(groundFloor);
    expect(startFloor({ ...twoFloors, floors: [upstairs] })).toBe(upstairs);
  });
});
