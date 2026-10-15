import { describe, expect, it } from "vitest";
import { DEFAULT_LUX, mountApp, startFloor, type AppDependencies, type Connection } from "./app";
import type { ConnectionHandlers } from "./connection";
import type { ClientMessage, Floor, HomeMessage, Room, StateMessage } from "./protocol";
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
  };
}

const groundFloor: Floor = {
  id: "plan",
  name: "Entréplan",
  level: 0,
  rooms: [rectangle("vardagsrum", "Vardagsrum")],
  lamps: [{ id: "taklampa", name: "Taklampa", room_id: "vardagsrum", x: 2.5, y: 2 }],
};

const upstairs: Floor = {
  id: "uppe",
  name: "Övervåning",
  level: 1,
  rooms: [rectangle("sovrum", "Sovrum")],
  lamps: [{ id: "sovrumslampa", name: "Sovrumslampa", room_id: "sovrum", x: 2.5, y: 2 }],
};

const home: HomeMessage = { type: "home", name: "Hemmet", floors: [groundFloor] };
const twoFloors: HomeMessage = { type: "home", name: "Hemmet", floors: [upstairs, groundFloor] };

function state(lux: number | null, on: boolean): StateMessage {
  return {
    type: "state",
    targets: [],
    rooms: [{ id: "vardagsrum", occupied: on, lux }],
    lamps: [{ id: "taklampa", on, explanation: on ? "Tänd: någon är här." : "Släckt: tomt." }],
  };
}

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

  it("flyttar personen till våningen som visas", () => {
    const { root, sent, handlers, callbacks } = setup();
    handlers.onMessage(twoFloors);
    find<HTMLButtonElement>(root, ".floors button").click();

    callbacks.onMovePerson({ x: 1, y: 2 });

    expect(sent).toContainEqual({ type: "move_person", floor_id: "uppe", x: 1, y: 2 });
  });

  it("frågar bara en gång per rum när ljusnivån är okänd", () => {
    const { sent, handlers } = setup();
    handlers.onMessage(home);

    handlers.onMessage(state(null, false));
    handlers.onMessage(state(null, false));

    expect(sent).toHaveLength(1);
  });

  it("ger kärnan en mörk ljusnivå om den inte vet någon", () => {
    const { sent, handlers } = setup();

    handlers.onMessage(home);
    handlers.onMessage(state(null, false));

    expect(sent).toEqual([{ type: "set_light_level", room_id: "vardagsrum", lux: DEFAULT_LUX }]);
  });

  it("skickar ny ljusnivå när reglaget dras", () => {
    const { root, sent, handlers } = setup();
    handlers.onMessage(home);
    const slider = find<HTMLInputElement>(root, "input[type=range]");

    slider.value = "150";
    slider.dispatchEvent(new Event("input"));

    expect(sent).toEqual([{ type: "set_light_level", room_id: "vardagsrum", lux: 150 }]);
    expect(root.querySelector("output")?.textContent).toBe("150 lux");
  });

  it("skickar personens nya plats och kan låta den gå", () => {
    const { root, sent, handlers, callbacks } = setup();
    handlers.onMessage(home);

    callbacks.onMovePerson({ x: 1, y: 2 });
    find<HTMLButtonElement>(root, ".controls button").click();

    expect(sent).toEqual([
      { type: "move_person", floor_id: "plan", x: 1, y: 2 },
      { type: "remove_person" },
    ]);
  });

  it("flyttar ingen person innan hemmet har kommit", () => {
    const { sent, callbacks } = setup();

    callbacks.onMovePerson({ x: 1, y: 2 });

    expect(sent).toEqual([]);
  });
});

describe("startFloor", () => {
  it("väljer planet med nivå 0 och annars den första våningen", () => {
    expect(startFloor(twoFloors)).toBe(groundFloor);
    expect(startFloor({ ...twoFloors, floors: [upstairs] })).toBe(upstairs);
  });
});
