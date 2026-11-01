import { describe, expect, it } from "vitest";
import { healthSummary, type HealthInput, zigbeeProblem } from "./health";
import type { FloorModel } from "./homefile";
import { lampHealth, nodeHealth } from "./mapItems";
import type { Card, StateMessage } from "./protocol";

const SINCE = "2026-10-17T19:42:00Z";

const floor: FloorModel = {
  id: "plan",
  name: "Entréplan",
  level: 0,
  rooms: [
    {
      id: "vardagsrum",
      name: "Vardagsrum",
      polygon: [
        [0, 0],
        [5, 0],
        [5, 4],
        [0, 4],
      ],
      walls: ["gips", "gips", "gips", "gips"],
      nodes: [{ id: "kort-1", name: "Kort 1", position: [1, 1], height: 2 }],
      lamps: [{ id: "lampa-1", name: "Lampa 1", position: [2, 2] }],
    },
  ],
};

function card(changes: Partial<Card> = {}): Card {
  return {
    id: "kort-1",
    name: "Kort 1",
    placed: true,
    online: true,
    problems: [],
    calibrating: false,
    wifi_signal: null,
    firmware_version: "0.4.0",
    ...changes,
  };
}

function state(changes: Partial<StateMessage> = {}): StateMessage {
  return {
    type: "state",
    tracks: [],
    rooms: [],
    zones: [],
    lamps: [{ id: "lampa-1", on: false, brightness: 100, explanation: "", responding: true }],
    events: [],
    simulation: null,
    cards: [card()],
    calibration: null,
    database: { connected: true },
    zigbee: { mqtt_connected: true, bridge_online: true, since: SINCE },
    ...changes,
  };
}

const input = (changes: Partial<HealthInput> = {}): HealthInput => ({
  connection: "open",
  state: state(),
  floor,
  newDevices: [],
  ...changes,
});

describe("hälsoraden", () => {
  it("säger Allt fungerar när allt svarar", () => {
    expect(healthSummary(input())).toEqual({ level: "ok", text: "Allt fungerar", target: null });
  });

  it("säger Ansluter … innan kärnan har skickat något, och Kärnan nås inte när anslutningen är stängd", () => {
    expect(healthSummary(input({ state: null, connection: "connecting" })).text).toBe("Ansluter …");
    expect(healthSummary(input({ state: null, connection: "closed" }))).toMatchObject({
      level: "bad",
      text: "Kärnan nås inte",
    });
    // Läget från förut gäller inte medan sidan ansluter igen.
    expect(healthSummary(input({ connection: "connecting" })).text).toBe("Kärnan nås inte");
  });

  it("pekar ut ett rumskort som inte svarar, så att ett tryck kan öppna dess kort", () => {
    expect(healthSummary(input({ state: state({ cards: [card({ online: false })] }) }))).toEqual({
      level: "bad",
      text: "Rumskortet svarar inte",
      target: { kind: "item", id: "kort-1" },
    });
  });

  it("räknar flera rumskort som inte svarar, och pekar på ett som finns på kartan", () => {
    const summary = healthSummary(
      input({
        state: state({
          cards: [card({ id: "annat", online: false }), card({ online: false })],
        }),
      }),
    );
    expect(summary.text).toBe("2 rumskort svarar inte");
    expect(summary.target).toEqual({ kind: "item", id: "kort-1" });
  });

  it("bryr sig inte om ett kort som inte är placerat när det inte svarar", () => {
    expect(
      healthSummary(input({ state: state({ cards: [card({ placed: false, online: false })] }) })).text,
    ).toBe("Allt fungerar");
  });

  it("pekar inte ut något på kartan när kortet som inte svarar inte finns där", () => {
    const summary = healthSummary(
      input({ state: state({ cards: [card({ id: "på-annan-våning", online: false })] }) }),
    );
    expect(summary.text).toBe("Rumskortet svarar inte");
    expect(summary.target).toBeNull();
  });

  it("använder lampornas responding", () => {
    const silent = state({
      lamps: [{ id: "lampa-1", on: null, brightness: 100, explanation: "", responding: false }],
    });
    expect(healthSummary(input({ state: silent }))).toEqual({
      level: "bad",
      text: "Lampan svarar inte",
      target: { kind: "item", id: "lampa-1" },
    });
  });

  it("säger Zigbee-stickan svarar inte när MQTT eller Zigbee2MQTT är nere, före allt annat", () => {
    const noBroker = state({
      zigbee: { mqtt_connected: false, bridge_online: null, since: SINCE },
      cards: [card({ online: false })],
    });
    expect(healthSummary(input({ state: noBroker }))).toEqual({
      level: "bad",
      text: "Zigbee-stickan svarar inte",
      target: { kind: "zigbee" },
    });
    const bridgeDown = state({ zigbee: { mqtt_connected: true, bridge_online: false, since: SINCE } });
    expect(healthSummary(input({ state: bridgeDown })).text).toBe("Zigbee-stickan svarar inte");
  });

  it("säger inget om Zigbee när kärnan inte styr några Zigbee-lampor, eller inte vet om bryggan", () => {
    expect(zigbeeProblem(state({ zigbee: null }))).toBeNull();
    expect(zigbeeProblem(state({ zigbee: { mqtt_connected: true, bridge_online: null, since: SINCE } }))).toBeNull();
    expect(zigbeeProblem(null)).toBeNull();
  });

  it("använder database: varnar när databasen är nere men inte när kärnan saknar databas", () => {
    expect(healthSummary(input({ state: state({ database: { connected: false } }) }))).toEqual({
      level: "warn",
      text: "Databasen svarar inte",
      target: null,
    });
    expect(healthSummary(input({ state: state({ database: null }) })).text).toBe("Allt fungerar");
  });

  it("visar det värsta först: ett tyst rumskort före en databas som är nere och en ny enhet", () => {
    const summary = healthSummary(
      input({
        state: state({ cards: [card({ online: false })], database: { connected: false } }),
        newDevices: [{ id: "ny", name: "Ny", kind: "node" }],
      }),
    );
    expect(summary.text).toBe("Rumskortet svarar inte");
  });

  it("säger +1 ny enhet, och hur många när de är fler", () => {
    expect(healthSummary(input({ newDevices: [{ id: "ny", name: "Ny", kind: "lamp" }] }))).toEqual({
      level: "new",
      text: "+1 ny enhet",
      target: { kind: "new", id: "ny" },
    });
    expect(
      healthSummary(
        input({
          newDevices: [
            { id: "a", name: "A", kind: "lamp" },
            { id: "b", name: "B", kind: "node" },
          ],
        }),
      ).text,
    ).toBe("+2 nya enheter");
  });

  it("varnar för ett rumskort med problem", () => {
    expect(healthSummary(input({ state: state({ cards: [card({ problems: ["Radarn tyst"] })] }) }))).toEqual({
      level: "warn",
      text: "Rumskortet har en varning",
      target: { kind: "item", id: "kort-1" },
    });
  });
});

describe("hälsan på kartan", () => {
  it("ritar ett rumskort som inte svarar rött och ett simulerat utan prick", () => {
    expect(nodeHealth(card({ online: false }))).toBe("bad");
    expect(nodeHealth(card({ problems: ["x"] }))).toBe("warn");
    expect(nodeHealth(card())).toBe("ok");
    expect(nodeHealth(undefined)).toBeNull();
  });

  it("ritar en lampa som inte svarar röd och en med okänt läge grå", () => {
    expect(lampHealth({ id: "l", on: null, brightness: 0, explanation: "", responding: false })).toBe("bad");
    expect(lampHealth({ id: "l", on: null, brightness: 0, explanation: "", responding: true })).toBe("unk");
    expect(lampHealth({ id: "l", on: true, brightness: 0, explanation: "", responding: true })).toBeNull();
  });
});
