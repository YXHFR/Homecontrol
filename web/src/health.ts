/**
 * Hälsoraden i toppen: hela läget i en enda mening, till exempel "Allt
 * fungerar", "Rumskortet svarar inte" eller "+1 ny enhet". Det värsta som är
 * fel visas, och raden pekar ut saken det gäller, så att ett tryck kan
 * centrera kartan på den och öppna dess kort. Modulen beror inte på Konva
 * eller sidan, så att den går att pröva för sig.
 */
import type { ConnectionStatus } from "./connection";
import type { FloorModel } from "./homefile";
import type { StateMessage } from "./protocol";

/** Grön, gul, röd, grå (okänt) eller en ny enhet. */
export type HealthLevel = "ok" | "warn" | "bad" | "unk" | "new";

/** Det hälsoraden handlar om. */
export type HealthTarget =
  /** Ett rumskort eller en lampa på kartan. */
  | { kind: "item"; id: string }
  /** En ny enhet som väntar på att placeras. */
  | { kind: "new"; id: string }
  /** Zigbee-stickan, som inte finns på kartan utan i rummets enhetslista. */
  | { kind: "zigbee" };

export interface HealthSummary {
  level: HealthLevel;
  text: string;
  target: HealthTarget | null;
}

/** En enhet som kärnan har hittat men som inte är placerad på kartan. */
export interface NewDevice {
  id: string;
  name: string;
  kind: "node" | "lamp";
}

export interface HealthInput {
  connection: ConnectionStatus;
  state: StateMessage | null;
  /** Våningen som visas, för att peka ut saker som finns på kartan. */
  floor: FloorModel | null;
  newDevices: NewDevice[];
}

/** Om kärnan når Zigbee-lamporna. Null när den inte styr några. */
export function zigbeeProblem(state: StateMessage | null): string | null {
  const zigbee = state?.zigbee;
  if (zigbee === null || zigbee === undefined) {
    return null;
  }
  if (!zigbee.mqtt_connected) {
    return "MQTT-brokern svarar inte, så lamporna nås inte.";
  }
  if (zigbee.bridge_online === false) {
    return "Zigbee2MQTT är offline, så lamporna nås inte.";
  }
  return null;
}

function placedOn(floor: FloorModel | null): Set<string> {
  return new Set(
    floor?.rooms.flatMap((room) => [
      ...(room.nodes ?? []).map((node) => node.id),
      ...(room.lamps ?? []).map((lamp) => lamp.id),
    ]),
  );
}

/** Hela läget i en mening, och saken det gäller. */
export function healthSummary(input: HealthInput): HealthSummary {
  const { connection, state, floor, newDevices } = input;
  if (state === null) {
    return connection === "closed"
      ? { level: "bad", text: "Kärnan nås inte", target: null }
      : { level: "unk", text: "Ansluter …", target: null };
  }
  // Medan sidan ansluter igen är läget från förut inte längre säkert.
  if (connection !== "open") {
    return { level: "bad", text: "Kärnan nås inte", target: null };
  }
  if (zigbeeProblem(state) !== null) {
    return { level: "bad", text: "Zigbee-stickan svarar inte", target: { kind: "zigbee" } };
  }
  const onMap = placedOn(floor);
  const pick = (ids: string[]) => {
    const id = ids.find((candidate) => onMap.has(candidate)) ?? ids[0];
    return onMap.has(id) ? ({ kind: "item", id } as const) : null;
  };

  const silentCards = (state.cards ?? []).filter((card) => card.placed && !card.online);
  if (silentCards.length > 0) {
    return {
      level: "bad",
      text:
        silentCards.length === 1
          ? "Rumskortet svarar inte"
          : `${silentCards.length} rumskort svarar inte`,
      target: pick(silentCards.map((card) => card.id)),
    };
  }
  const silentLamps = state.lamps.filter((lamp) => !lamp.responding);
  if (silentLamps.length > 0) {
    return {
      level: "bad",
      text:
        silentLamps.length === 1 ? "Lampan svarar inte" : `${silentLamps.length} lampor svarar inte`,
      target: pick(silentLamps.map((lamp) => lamp.id)),
    };
  }
  if (state.database !== null && !state.database.connected) {
    return { level: "warn", text: "Databasen svarar inte", target: null };
  }
  const warnings = (state.cards ?? []).filter((card) => card.placed && card.problems.length > 0);
  if (warnings.length > 0) {
    return {
      level: "warn",
      text: "Rumskortet har en varning",
      target: pick(warnings.map((card) => card.id)),
    };
  }
  if (newDevices.length > 0) {
    return {
      level: "new",
      text: newDevices.length === 1 ? "+1 ny enhet" : `+${newDevices.length} nya enheter`,
      target: { kind: "new", id: newDevices[0].id },
    };
  }
  return { level: "ok", text: "Allt fungerar", target: null };
}
