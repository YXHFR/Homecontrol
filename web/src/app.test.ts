import { describe, expect, it } from "vitest";
import { DEFAULT_LUX, mountApp, type AppDependencies, type Connection } from "./app";
import type { ConnectionHandlers } from "./connection";
import type { ClientMessage, HomeMessage, StateMessage } from "./protocol";
import type { LiveViewCallbacks } from "./view";

const home: HomeMessage = {
  type: "home",
  rooms: [{ id: "vardagsrum", name: "Vardagsrum", x: 0, y: 0, width: 5, height: 4 }],
  lamps: [{ id: "taklampa", name: "Taklampa", room_id: "vardagsrum", x: 2.5, y: 2 }],
};

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
  const shown: (HomeMessage | StateMessage)[] = [];
  let handlers: ConnectionHandlers | undefined;
  let callbacks: LiveViewCallbacks | undefined;
  const deps: AppDependencies = {
    createView: (_container, cb) => {
      callbacks = cb;
      return {
        setHome: (m) => shown.push(m),
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

  it("skickar rummet och läget vidare till ritytan och visar förklaringarna", () => {
    const { root, shown, handlers } = setup();

    handlers.onMessage(home);
    handlers.onMessage(state(10, true));

    expect(shown.map((m) => m.type)).toEqual(["home", "state"]);
    expect(root.querySelector(".explanations")?.textContent).toBe("Taklampa: Tänd: någon är här.");
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
    const { root, sent, callbacks } = setup();

    callbacks.onMovePerson({ x: 1, y: 2 });
    find<HTMLButtonElement>(root, "button").click();

    expect(sent).toEqual([{ type: "move_person", x: 1, y: 2 }, { type: "remove_person" }]);
  });
});
