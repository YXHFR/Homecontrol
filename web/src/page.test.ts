import { describe, expect, it, vi } from "vitest";
import type { ConnectionHandlers } from "./connection";
import { CoreError, type HomeApi } from "./homeApi";
import type { HomeDocument } from "./homefile";
import type { MapCallbacks, MapScene } from "./mapView";
import { mountPage, startFloor, type Connection, type PageDependencies } from "./page";
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
  nodes: [{ id: "homecontrol-vardagsrum", name: "Homecontrol vardagsrum", room_id: "vardagsrum", x: 0.5, y: 0.5 }],
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

/** Samma hem som `home`, som kärnans REST-API ger det. */
function homeDocument(): HomeDocument {
  return {
    format: "homecontrol-hem",
    version: 1,
    home: {
      name: "Hemmet",
      floors: [
        {
          id: "plan",
          name: "Entréplan",
          level: 0,
          rooms: [
            {
              ...rectangle("vardagsrum", "Vardagsrum"),
              nodes: [
                {
                  id: "homecontrol-vardagsrum",
                  name: "Homecontrol vardagsrum",
                  position: [0.5, 0.5],
                  height: 2,
                  rotation: 45,
                },
              ],
              lamps: [{ id: "taklampa", name: "Taklampa", position: [2.5, 2] }],
            },
          ],
        },
      ],
    },
  };
}

/** Det testerna kan byta ut i kärnans REST-API. */
interface SetupOptions {
  save?: HomeApi["save"];
  /** Hemmet som kärnan har sparat från början, i stället för `homeDocument()`. */
  document?: HomeDocument;
}

async function setup(options: SetupOptions = {}) {
  const sent: ClientMessage[] = [];
  /** Hemmen som sidan har sparat i kärnan, ett per PUT. */
  const saves: HomeDocument[] = [];
  /** Hur många gånger sidan har läst hemmet. */
  const loads = { count: 0 };
  let latest = options.document ?? homeDocument();
  const calibrations: string[] = [];
  let refusal: string | null = null;
  const scenes: MapScene[] = [];
  let handlers: ConnectionHandlers | undefined;
  let callbacks: MapCallbacks | undefined;
  const api: HomeApi = {
    load: () => {
      loads.count++;
      return Promise.resolve({ document: structuredClone(latest), revision: String(saves.length + 1) });
    },
    save: (document, revision) => {
      if (options.save !== undefined) {
        return options.save(document, revision);
      }
      saves.push(structuredClone(document));
      latest = structuredClone(document);
      return Promise.resolve(String(saves.length + 1));
    },
    uploadPlanImage: () => Promise.resolve("2"),
    deletePlanImage: () => Promise.resolve("2"),
    planImageUrl: (floorId) => `/bild/${floorId}`,
    cards: () => Promise.resolve([]),
    lamps: () => Promise.resolve([]),
  };
  const deps: PageDependencies = {
    api,
    createMap: (_container, cb) => {
      callbacks = cb;
      return {
        render: (scene) => scenes.push(scene),
        metersPerPixel: () => 0.01,
        zoom: () => undefined,
        fit: () => undefined,
        centerOn: () => undefined,
        destroy: () => undefined,
      };
    },
    prepareImage: (file) => Promise.resolve({ blob: file, width: 500 }),
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
    hasDoorbell: () => Promise.resolve(false),
  };
  const root = document.createElement("main");
  document.body.replaceChildren(root);
  mountPage(root, deps);
  await vi.waitFor(() => expect(root.dataset.ready).toBe("true"));
  if (handlers === undefined || callbacks === undefined) {
    throw new Error("mountPage kopplade inte upp sig");
  }
  const refuse = (text: string) => {
    refusal = text;
  };
  const buttonNamed = (name: string): HTMLButtonElement => {
    const found = [...root.querySelectorAll("button")].find(
      (b) => b.textContent === name || b.getAttribute("aria-label") === name,
    );
    if (found === undefined) {
      throw new Error(`Hittade ingen knapp ${name}`);
    }
    return found;
  };
  return { root, sent, saves, loads, scenes, handlers, callbacks, calibrations, refuse, buttonNamed };
}

describe("sidan", () => {
  it("ansluter, läser hemmet och visar läget i hälsoraden", async () => {
    const { root, handlers } = await setup();

    expect(root.dataset.ready).toBe("true");
    expect(root.dataset.status).toBe("open");
    expect(text(root, ".health")).toBe("Ansluter …");

    handlers.onMessage(home);
    handlers.onMessage(state(10, true));
    expect(text(root, ".health")).toBe("Allt fungerar");
  });

  it("skickar läget vidare till kartan och visar förklaringen", async () => {
    const { root, scenes, handlers } = await setup();

    handlers.onMessage(home);
    handlers.onMessage(state(10, true));

    expect(scenes.at(-1)?.live.state).toEqual(state(10, true));
    expect(scenes.at(-1)?.floor.id).toBe("plan");
    expect(text(root, ".explanation")).toBe("Någon är i Vardagsrum.");
  });

  it("visar läget och ljusnivån i rummet, även när läget är okänt", async () => {
    const { root, handlers } = await setup();
    handlers.onMessage(home);

    handlers.onMessage(state(10, true));
    expect(text(root, ".card-head .status-line")).toBe("Upptaget · 1 person");
    expect(text(root, ".lux-value")).toBe("10 lux");

    const unknown = state(null, false);
    handlers.onMessage({ ...unknown, rooms: [{ ...unknown.rooms[0], status: "okänt" }] });
    expect(text(root, ".card-head .status-line")).toBe("Okänt läge – en sensor svarar inte");
    expect(text(root, ".lux-value")).toBe("ljusnivån okänd");
    expect(find<HTMLElement>(root, ".card-head").dataset.status).toBe("okänt");
  });

  it("visar ljusnivån utan lampornas eget ljus när de lyser upp sensorn", async () => {
    const { root, handlers } = await setup();
    handlers.onMessage(home);
    const lit = state(160, true);

    handlers.onMessage({ ...lit, rooms: [{ ...lit.rooms[0], adjusted_lux: 10 }] });

    expect(text(root, ".lux-value")).toBe("160 lux (10 utan lampornas eget ljus)");
  });

  it("ett tryck på lampan på kartan öppnar kortet; lampan tänds med knapparna i korten", async () => {
    const { root, sent, handlers, callbacks } = await setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, false));

    // Snabbknappen i rummets kort.
    const quick = find<HTMLButtonElement>(root, ".lamp-row .power");
    expect(quick.textContent).toBe("AV");
    expect(quick.getAttribute("aria-pressed")).toBe("false");
    quick.click();

    callbacks.onSelectItem("taklampa");
    expect(text(root, ".card-head h2")).toBe("Taklampa");
    expect(sent).toEqual([{ type: "set_lamp", lamp_id: "taklampa", on: true }]);

    handlers.onMessage(state(10, true));
    find<HTMLButtonElement>(root, ".power.big").click();

    expect(sent).toEqual([
      { type: "set_lamp", lamp_id: "taklampa", on: true },
      { type: "set_lamp", lamp_id: "taklampa", on: false },
    ]);
  });

  it("ett tryck på en sak i ett annat rum gör det rummet till det valda, så att ‹ Rummet leder dit", async () => {
    const document = homeDocument();
    const floor = document.home.floors[0];
    floor.rooms.push({
      ...rectangle("kok", "Kök"),
      lamps: [{ id: "koklampa", name: "Köklampa", position: [2.5, 2] }],
    });
    const { root, handlers, callbacks, buttonNamed } = await setup({ document });
    handlers.onMessage(home);
    expect(text(root, ".card-head h2")).toBe("Vardagsrum");

    callbacks.onSelectItem("koklampa");
    expect(text(root, ".card-head h2")).toBe("Köklampa");
    buttonNamed("Tillbaka till rummet").click();

    expect(text(root, ".card-head h2")).toBe("Kök");
  });

  it("bygger inte om kortet när inget det visar har ändrats, så att ett tryck inte tappas", async () => {
    const { root, handlers } = await setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, true));
    const toggle = find<HTMLButtonElement>(root, ".lamp-row .power");

    // Ett nytt läge med ett flyttat spår men samma rum och lampor.
    handlers.onMessage({ ...state(10, true), tracks: [{ floor_id: "plan", x: 3, y: 2, held: false }] });

    expect(find<HTMLButtonElement>(root, ".lamp-row .power")).toBe(toggle);
  });

  it("tänder en lampa vars läge är okänt", async () => {
    const { root, sent, handlers } = await setup();
    handlers.onMessage(home);
    const unknown = state(10, false);
    handlers.onMessage({ ...unknown, lamps: [{ ...unknown.lamps[0], on: null }] });

    expect(text(root, ".lamp-row")).toContain("okänt läge");
    find<HTMLButtonElement>(root, ".lamp-row .power").click();

    expect(sent).toEqual([{ type: "set_lamp", lamp_id: "taklampa", on: true }]);
  });

  it("visar manuellt läge och kan lämna tillbaka rummet till automatiken", async () => {
    const { root, sent, handlers } = await setup();
    handlers.onMessage(home);
    const manual = state(10, true);

    handlers.onMessage({ ...manual, rooms: [{ ...manual.rooms[0], manual: true }] });
    const row = find<HTMLElement>(root, ".automation");
    expect(row.dataset.manual).toBe("true");
    expect(row.textContent).toContain("Manuellt läge");
    find<HTMLButtonElement>(row, "button.resume").click();

    expect(sent).toEqual([{ type: "resume_automation", room_id: "vardagsrum" }]);
  });

  it("flyttar en låtsasperson på våningen som visas", async () => {
    const { sent, handlers, callbacks } = await setup();
    handlers.onMessage(twoFloors);
    handlers.onMessage(state(10, false));

    callbacks.onMovePerson("1", { x: 1, y: 2 }, true);

    expect(sent).toContainEqual({ type: "move_person", person_id: "1", floor_id: "plan", x: 1, y: 2 });
  });

  it("flyttar ingen person när simuleringen är av", async () => {
    const { sent, handlers, callbacks } = await setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, false, { ...simulationOn, enabled: false }));

    callbacks.onMovePerson("1", { x: 1, y: 2 }, true);

    expect(sent).toEqual([]);
  });

  it("döljer simuleringen när kärnan inte har någon", async () => {
    const { root, handlers, buttonNamed } = await setup();
    handlers.onMessage(home);

    handlers.onMessage(state(10, false, null));

    expect(buttonNamed("Sim").hidden).toBe(true);
    expect(find<HTMLElement>(root, ".sim-strip").hidden).toBe(true);
  });

  it("slår på och av simuleringsläget med knappen Sim", async () => {
    const { root, sent, handlers, buttonNamed } = await setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, false, { ...simulationOn, enabled: false }));
    const sim = buttonNamed("Sim");

    expect(sim.hidden).toBe(false);
    expect(sim.getAttribute("aria-pressed")).toBe("false");
    expect(find<HTMLElement>(root, ".sim-strip").hidden).toBe(true);

    sim.click();
    handlers.onMessage(state(10, false));

    expect(sent).toEqual([{ type: "set_simulation", enabled: true }]);
    expect(sim.getAttribute("aria-pressed")).toBe("true");
    expect(find<HTMLElement>(root, ".sim-strip").hidden).toBe(false);
  });

  it("lägger till personer bredvid rummen och tar bort dem", async () => {
    const { root, sent, handlers, buttonNamed } = await setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, false));

    buttonNamed("+ Person").click();
    handlers.onMessage(
      state(10, false, { ...simulationOn, persons: [{ id: "1", floor_id: "plan", x: 6, y: 0.5 }] }),
    );
    buttonNamed("+ Person").click();
    const persons = find<HTMLElement>(root, ".persons");
    find<HTMLButtonElement>(persons, "button").click();

    expect(persons.textContent).toBe("Person 1Ta bort");
    expect(sent).toEqual([
      { type: "add_person", floor_id: "plan", x: 6, y: 0.5 },
      { type: "add_person", floor_id: "plan", x: 6, y: 1.5 },
      { type: "remove_person", person_id: "1" },
    ]);
  });

  it("ger varje ny person en egen plats även när klicken kommer tätt", async () => {
    const { sent, handlers, buttonNamed } = await setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, false));
    const add = buttonNamed("+ Person");

    add.click();
    add.click();

    expect(sent.map((m) => (m.type === "add_person" ? m.y : null))).toEqual([0.5, 1.5]);
  });

  it("tar inte emot fler personer än kärnan tillåter", async () => {
    const { root, handlers, buttonNamed } = await setup();
    handlers.onMessage(home);
    const persons = Array.from({ length: MAX_PERSONS }, (_, i) => ({
      id: String(i + 1),
      floor_id: "plan",
      x: 6,
      y: 1,
    }));

    handlers.onMessage(state(10, false, { ...simulationOn, persons }));

    expect(buttonNamed("+ Person").disabled).toBe(true);
    expect(root.querySelector(".sheet")?.textContent).toContain("Högst 10 personer");
  });

  it("visar och skickar dagsljuset", async () => {
    const { root, sent, handlers } = await setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, false, { ...simulationOn, daylight: 40 }));
    const slider = find<HTMLInputElement>(root, ".sim-strip input[type=range]");
    const output = find<HTMLOutputElement>(root, ".sim-strip output");

    expect(slider.value).toBe("40");
    expect(output.textContent).toBe("40 lux");

    slider.value = "150";
    slider.dispatchEvent(new Event("input"));

    expect(sent).toEqual([{ type: "set_daylight", lux: 150 }]);
    expect(output.textContent).toBe("150 lux");
  });

  it("visar och byter tidsfart", async () => {
    const { root, sent, handlers } = await setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, false, { ...simulationOn, speed: 10 }));
    const buttons = [...root.querySelectorAll<HTMLButtonElement>(".speeds button")];

    expect(buttons.map((b) => b.textContent)).toEqual(["1×", "10×", "60×"]);
    expect(buttons.map((b) => b.getAttribute("aria-pressed"))).toEqual(["false", "true", "false"]);

    buttons[2].click();

    expect(sent).toEqual([{ type: "set_speed", speed: 60 }]);
  });

  it("låser tidsfarten när riktiga rumskort används", async () => {
    const { root, handlers } = await setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, false, { ...simulationOn, speed_locked: true }));
    const buttons = [...root.querySelectorAll<HTMLButtonElement>(".speeds button")];

    expect(buttons.map((b) => b.disabled)).toEqual([false, true, true]);
    expect(text(root, ".sheet")).toMatch(/vanlig takt så länge riktiga sensorer eller lampor används/);
  });

  it("varnar för kort som inte fungerar och visar nya kort att placera", async () => {
    const { root, handlers, callbacks } = await setup();
    handlers.onMessage(home);

    handlers.onMessage({
      ...state(null, false),
      cards: [
        card({ problems: ["Radarn skickar inget."] }),
        card({ id: "homecontrol-kok", name: "Homecontrol kok", placed: false }),
      ],
    });

    expect(text(root, ".health")).toBe("Rumskortet har en varning");
    callbacks.onSelectItem("homecontrol-vardagsrum");
    expect(text(root, ".problems")).toBe("Radarn skickar inget.");

    handlers.onMessage({
      ...state(null, false),
      cards: [card(), card({ id: "homecontrol-kok", name: "Homecontrol kok", placed: false })],
    });
    expect(text(root, ".health")).toBe("+1 ny enhet");
    find<HTMLButtonElement>(root, ".health").click();
    expect(text(root, ".card-head h2")).toBe("Homecontrol kok");
    expect(root.querySelector(".sheet")?.textContent).toContain("Placera på kartan");
  });

  it("uppdaterar ett kort först när någon har godkänt det", async () => {
    const { root, sent, handlers, callbacks } = await setup();
    handlers.onMessage(home);
    const update = { version: "0.5.0", in_progress: false, progress: null };
    handlers.onMessage({ ...state(null, false), cards: [card({ update, wifi_signal: -58 })] });
    callbacks.onSelectItem("homecontrol-vardagsrum");

    expect(text(root, ".kv")).toMatch(/Wi-Fi -58 dBm \(bra\)/);
    expect(text(root, ".kv")).toMatch(/0\.4\.0/);
    expect(sent).toEqual([]);

    find<HTMLButtonElement>(root, 'button[data-card="homecontrol-vardagsrum"]').click();
    expect(sent).toEqual([{ type: "install_update", card: "homecontrol-vardagsrum" }]);

    handlers.onMessage({
      ...state(null, false),
      cards: [card({ update: { ...update, in_progress: true, progress: 42 } })],
    });
    expect(root.querySelector('button[data-card="homecontrol-vardagsrum"]')).toBeNull();
    expect(text(root, ".sheet")).toMatch(/Uppdaterar till 0\.5\.0 … 42 %/);
  });

  it("varnar när kärnan inte har kontakt med databasen, och slutar när den har det igen", async () => {
    const { root, handlers } = await setup();
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

  it("visar ingen databasvarning när kärnan inte har någon databas", async () => {
    const { root, handlers } = await setup();
    handlers.onMessage(home);
    handlers.onMessage(state(null, false));

    expect(find<HTMLElement>(root, ".database-warning").hidden).toBe(true);
  });

  it("visar att en lampa inte svarar", async () => {
    const { root, handlers } = await setup();
    handlers.onMessage(home);
    const silent = state(10, false);
    silent.lamps = [{ id: "taklampa", on: null, brightness: 100, explanation: "", responding: false }];
    handlers.onMessage(silent);

    expect(text(root, ".health")).toBe("Lampan svarar inte");
    expect(text(root, ".lamp-row")).toContain("svarar inte");
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

  it("öppnas från rummet, förklarar och startar mätningen i kärnan", async () => {
    const { root, handlers, calibrations } = await setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, false));
    const panel = find<HTMLElement>(root, "section.calibration");
    expect(panel.hidden).toBe(true);

    find<HTMLButtonElement>(root, ".sheet button.calibrate").click();
    expect(panel.hidden).toBe(false);
    expect(text(root, "section.calibration h2")).toBe("Lampkalibrering: Vardagsrum");
    expect(panel.textContent).toContain("helst när det är mörkt ute");
    expect(panel.textContent).toContain("Taklampa: inte mätt än");

    find<HTMLButtonElement>(root, "section.calibration button.start").click();
    expect(calibrations).toEqual(["start vardagsrum"]);
  });

  it("visar hur mätningen går och kan avbryta den", async () => {
    const { root, handlers, calibrations } = await setup();
    handlers.onMessage(home);
    handlers.onMessage({ ...state(10, false), calibration: running(2) });

    const panel = find<HTMLElement>(root, "section.calibration");
    expect(panel.hidden).toBe(false);
    expect(panel.textContent).toContain("Mätning 2 av 3: Mäter ljuset från Taklampa …");
    expect(find<HTMLProgressElement>(root, "section.calibration progress").value).toBe(1);

    find<HTMLButtonElement>(root, "section.calibration button.cancel").click();
    expect(calibrations).toEqual(["avbryt"]);
  });

  it("visar lampornas uppmätta ljus och går att köra igen", async () => {
    const { root, handlers, calibrations } = await setup();
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
    const { root, handlers, refuse } = await setup();
    handlers.onMessage(home);
    handlers.onMessage(state(null, false));
    refuse("Ingen ljussensor har mätt ljuset i Vardagsrum.");

    find<HTMLButtonElement>(root, ".sheet button.calibrate").click();
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
    expect(startFloor(twoFloors.floors)).toBe(groundFloor);
    expect(startFloor([upstairs])).toBe(upstairs);
  });
});

/** Var rumskortet i vardagsrummet står och hur det är vridet i ett sparat hem. */
function nodeIn(document: HomeDocument | undefined) {
  const node = document?.home.floors[0].rooms[0].nodes?.[0];
  return { position: node?.position, rotation: node?.rotation };
}

const NODE = "homecontrol-vardagsrum";

const pause = (ms = 20) => new Promise((resolve) => setTimeout(resolve, ms));

describe("ett tryck på en lampa på kartan", () => {
  it("öppnar lampans kort utan att skicka något till kärnan, så att automatiken inte pausas", async () => {
    const { root, sent, scenes, handlers, callbacks } = await setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, false));

    callbacks.onSelectItem("taklampa");

    expect(text(root, ".card-head h2")).toBe("Taklampa");
    expect(find<HTMLButtonElement>(root, ".power.big").getAttribute("aria-pressed")).toBe("false");
    expect(scenes.at(-1)?.selection).toEqual({ kind: "item", id: "taklampa" });
    expect(sent).toEqual([]);
  });

  it("släcker inte heller en lampa som lyser när den trycks på flera gånger", async () => {
    const { sent, handlers, callbacks } = await setup();
    handlers.onMessage(home);
    handlers.onMessage(state(10, true));

    callbacks.onSelectItem("taklampa");
    callbacks.onSelectItem("taklampa");

    expect(sent).toEqual([]);
  });
});

describe("Flytta", () => {
  async function moving(options: SetupOptions = {}) {
    const page = await setup(options);
    page.handlers.onMessage(home);
    page.handlers.onMessage(state(10, false));
    page.callbacks.onSelectItem(NODE);
    find<HTMLButtonElement>(page.root, "button.move").click();
    return page;
  }

  it("låser upp just den saken och byter toppraden mot Flyttar … · Avbryt · Klar", async () => {
    const { root, scenes, buttonNamed } = await moving();

    expect(root.dataset.mode).toBe("move");
    expect(find<HTMLElement>(root, ".topbar").classList.contains("mode")).toBe(true);
    expect(text(root, ".topbar .title")).toBe("Flyttar Homecontrol vardagsrum");
    expect(buttonNamed("Avbryt").closest(".topbar")).not.toBeNull();
    expect(buttonNamed("Klar").closest(".topbar")).not.toBeNull();
    // Hälsoraden och Sim-knappen är borta medan något flyttas.
    expect(root.querySelector(".topbar .health")).toBeNull();
    expect(scenes.at(-1)?.movingId).toBe(NODE);
    expect(scenes.at(-1)?.mode).toBe("move");
  });

  it("sparar varje släpp direkt med en PUT och visar Sparat · Ångra, som ställer tillbaka", async () => {
    const { root, saves, callbacks } = await moving();

    callbacks.onDragStart(NODE);
    callbacks.onMoveItem(NODE, { x: 1, y: 1.5 });

    await vi.waitFor(() => expect(saves).toHaveLength(1));
    expect(nodeIn(saves[0])).toEqual({ position: [1, 1.5], rotation: 45 });
    await vi.waitFor(() => expect(find<HTMLElement>(root, ".toast").hidden).toBe(false));
    expect(text(root, ".toast .text")).toBe("Sparat");
    const undo = find<HTMLButtonElement>(root, ".toast button");
    expect(undo.textContent).toBe("Ångra");

    undo.click();

    await vi.waitFor(() => expect(saves).toHaveLength(2));
    expect(nodeIn(saves[1])).toEqual({ position: [0.5, 0.5], rotation: 45 });
    await vi.waitFor(() => expect(text(root, ".toast .text")).toBe("Ångrat"));
  });

  it("vrider med ringen och med knapparna −15° och +15°, en PUT per vridning", async () => {
    const { saves, callbacks, buttonNamed } = await moving();

    buttonNamed("Vrid 15° medurs").click();
    await vi.waitFor(() => expect(saves).toHaveLength(1));
    expect(nodeIn(saves[0]).rotation).toBe(60);

    buttonNamed("Vrid 15° moturs").click();
    buttonNamed("Vrid 15° moturs").click();
    await vi.waitFor(() => expect(saves).toHaveLength(3));
    expect(nodeIn(saves[2]).rotation).toBe(30);

    callbacks.onRotateNode(NODE, 370);
    await vi.waitFor(() => expect(saves).toHaveLength(4));
    expect(nodeIn(saves[3]).rotation).toBe(10);
  });

  it("Avbryt ställer tillbaka platsen och vridningen från innan Flytta trycktes", async () => {
    const { root, saves, callbacks, buttonNamed } = await moving();

    callbacks.onMoveItem(NODE, { x: 2, y: 2 });
    buttonNamed("Vrid 15° medurs").click();
    callbacks.onMoveItem(NODE, { x: 3, y: 1 });
    await vi.waitFor(() => expect(saves).toHaveLength(3));

    buttonNamed("Avbryt").click();

    await vi.waitFor(() => expect(saves).toHaveLength(4));
    expect(nodeIn(saves[3])).toEqual({ position: [0.5, 0.5], rotation: 45 });
    expect(root.dataset.mode).toBe("view");
    expect(root.querySelector(".topbar .health")).not.toBeNull();
  });

  it("Avbryt utan att något flyttats sparar inget", async () => {
    const { root, saves, buttonNamed } = await moving();

    buttonNamed("Avbryt").click();
    await pause();

    expect(saves).toEqual([]);
    expect(root.dataset.mode).toBe("view");
  });

  it("Escape avbryter också flytten", async () => {
    const { root, saves, callbacks } = await moving();
    callbacks.onMoveItem(NODE, { x: 2, y: 2 });
    await vi.waitFor(() => expect(saves).toHaveLength(1));

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));

    await vi.waitFor(() => expect(saves).toHaveLength(2));
    expect(nodeIn(saves[1]).position).toEqual([0.5, 0.5]);
    expect(root.dataset.mode).toBe("view");
  });

  it("Klar slutar flytta och behåller det som redan är sparat", async () => {
    const { root, saves, scenes, callbacks, buttonNamed } = await moving();
    callbacks.onMoveItem(NODE, { x: 2, y: 2 });
    await vi.waitFor(() => expect(saves).toHaveLength(1));

    buttonNamed("Klar").click();
    await pause();

    expect(saves).toHaveLength(1);
    expect(root.dataset.mode).toBe("view");
    expect(scenes.at(-1)?.movingId).toBeNull();
  });

  it("piltangenterna flyttar 5 cm och Skift+pil vrider 15°", async () => {
    const { saves } = await moving();
    const key = (k: string, shiftKey = false) =>
      window.dispatchEvent(new KeyboardEvent("keydown", { key: k, shiftKey }));

    key("ArrowRight");
    key("ArrowRight");
    key("ArrowDown");
    // Pilarna sparas tillsammans när tangenterna har släppts en stund.
    await vi.waitFor(() => expect(saves).toHaveLength(1), { timeout: 2000 });
    expect(nodeIn(saves[0]).position).toEqual([0.6, 0.55]);

    key("ArrowRight", true);
    await vi.waitFor(() => expect(saves).toHaveLength(2));
    expect(nodeIn(saves[1]).rotation).toBe(60);
    key("ArrowUp", true);
    await vi.waitFor(() => expect(saves).toHaveLength(3));
    expect(nodeIn(saves[2]).rotation).toBe(45);
  });

  it("går inte att dra något på kartan utan Flytta, eller något annat än det upplåsta", async () => {
    const page = await setup();
    page.handlers.onMessage(home);
    page.handlers.onMessage(state(10, false));

    page.callbacks.onMoveItem(NODE, { x: 2, y: 2 });
    page.callbacks.onRotateNode(NODE, 90);
    await pause();
    expect(page.saves).toEqual([]);

    page.callbacks.onSelectItem(NODE);
    find<HTMLButtonElement>(page.root, "button.move").click();
    page.callbacks.onMoveItem("taklampa", { x: 1, y: 1 });
    await pause();
    expect(page.saves).toEqual([]);
  });

  it("visar Någon annan ändrade – ladda om när kärnan säger att hemmet har ändrats (409)", async () => {
    const { root, callbacks } = await moving({
      save: () => Promise.reject(new CoreError("Hemmet har ändrats", true)),
    });

    callbacks.onMoveItem(NODE, { x: 2, y: 2 });

    await vi.waitFor(() => expect(text(root, ".toast .text")).toBe("Någon annan ändrade – ladda om"));
    expect(find<HTMLElement>(root, ".toast").dataset.level).toBe("bad");
    expect(text(root, ".toast button")).toBe("Ladda om");
  });

  it("tar inte emot nya hem från strömmen medan något dras eller sparas, men visar spåren", async () => {
    let finishSave: (revision: string) => void = () => undefined;
    const { loads, scenes, handlers, callbacks } = await moving({
      save: () =>
        new Promise<string>((resolve) => {
          finishSave = resolve;
        }),
    });
    const before = loads.count;

    callbacks.onDragStart(NODE);
    handlers.onMessage(home);
    handlers.onMessage(state(10, true));
    await pause();
    expect(loads.count).toBe(before);
    expect(scenes.at(-1)?.live.state?.tracks).toHaveLength(1);

    // Släppt men inte sparat än: fortfarande låst.
    callbacks.onMoveItem(NODE, { x: 2, y: 2 });
    handlers.onMessage(home);
    await pause();
    expect(loads.count).toBe(before);

    finishSave("2");
    await vi.waitFor(() => expect(loads.count).toBe(before + 1));
  });

  it("visar Räknar om … efter att ett rumskort flyttats, tills nya spår kommer", async () => {
    const { root, saves, handlers, callbacks } = await moving();
    const recalculating = find<HTMLElement>(root, ".recalculating");
    expect(recalculating.hidden).toBe(true);

    callbacks.onMoveItem(NODE, { x: 2, y: 2 });
    await vi.waitFor(() => expect(saves).toHaveLength(1));
    await vi.waitFor(() => expect(recalculating.hidden).toBe(false));
    expect(recalculating.textContent).toContain("Räknar om");

    // Spår som kommer direkt efteråt kan vara gamla; först efter en stund räknas de.
    await pause(600);
    handlers.onMessage(state(10, true));
    expect(recalculating.hidden).toBe(true);
  });

  it("är avstängt med en förklaring när databasen är nere", async () => {
    const { root, saves, handlers, callbacks } = await setup();
    handlers.onMessage(home);
    handlers.onMessage({ ...state(10, false), database: { connected: false } });
    callbacks.onSelectItem(NODE);

    const move = find<HTMLButtonElement>(root, "button.move");
    expect(move.disabled).toBe(true);
    expect(text(root, ".sheet")).toContain("Flytta går inte just nu: databasen svarar inte");

    move.click();
    callbacks.onMoveItem(NODE, { x: 2, y: 2 });
    await pause();
    expect(root.dataset.mode).toBe("view");
    expect(saves).toEqual([]);
  });
});

describe("ett rumskort som inte svarar", () => {
  it("syns i hälsoraden, och ett tryck på den öppnar kortet med vad man kan göra", async () => {
    const { root, scenes, handlers } = await setup();
    handlers.onMessage(home);
    handlers.onMessage({ ...state(null, false), cards: [card({ online: false })] });

    expect(text(root, ".health")).toBe("Rumskortet svarar inte");
    expect(find<HTMLElement>(root, ".health").dataset.level).toBe("bad");
    // Kartan får kortens läge och ritar kortet som tyst (se mapItems.test.ts).
    expect(scenes.at(-1)?.live.state?.cards?.[0].online).toBe(false);

    find<HTMLButtonElement>(root, ".health").click();

    expect(text(root, ".card-head h2")).toBe("Homecontrol vardagsrum");
    expect(text(root, ".notice")).toContain("Rumskortet svarar inte");
    expect(scenes.at(-1)?.selection).toEqual({ kind: "item", id: NODE });
  });

  it("hälsoraden säger Allt fungerar igen när kortet svarar", async () => {
    const { root, handlers } = await setup();
    handlers.onMessage(home);
    handlers.onMessage({ ...state(null, false), cards: [card({ online: false })] });
    handlers.onMessage({ ...state(null, false), cards: [card()] });

    expect(text(root, ".health")).toBe("Allt fungerar");
  });
});

describe("det som har tagits bort", () => {
  it("visar varken Varför hände det?, händelselistan, våningsväljaren eller Kartredigerare", async () => {
    const { root, handlers } = await setup();
    handlers.onMessage(twoFloors);
    handlers.onMessage({
      ...state(10, true),
      events: [{ number: 1, at: 1_700_000_000, room_id: "vardagsrum", text: "Taklampa tändes: någon kom in" }],
    });

    const all = root.textContent ?? "";
    expect(all).not.toContain("Varför hände det");
    expect(all).not.toContain("Taklampa tändes: någon kom in");
    expect(all).not.toContain("Kartredigerare");
    expect(root.querySelector("select")).toBeNull();
    expect(root.querySelector('a[href*="editor"]')).toBeNull();
  });
});
