/**
 * Meddelandena i realtidsströmmen mellan kärnan och livevyn (se core/src/homecontrol/live.py).
 * Koordinater är meter i en vånings plan, med origo uppe till vänster och y nedåt.
 */

export interface Point {
  x: number;
  y: number;
}

/** Vad en vägg är gjord av, eller att den är en dörröppning. */
export type WallKind = "gips" | "betong" | "trä" | "glas" | "dörröppning";

/** Vad en zon betyder för närvaron (se ZoneKind i core/src/homecontrol/home.py). */
export type ZoneKind = "vanlig" | "stillhet" | "ignorera" | "entré";

export interface Zone {
  id: string;
  name: string;
  kind: ZoneKind;
  /** Hörnen i ordning som [x, y]. De ligger alla i zonens rum. */
  polygon: [number, number][];
}

export interface Room {
  id: string;
  name: string;
  /** Hörnen i ordning som [x, y]. */
  polygon: [number, number][];
  /** En vägg per sida: väggen från hörn i till hörn i + 1. */
  walls: WallKind[];
  zones: Zone[];
}

export interface Lamp {
  id: string;
  name: string;
  room_id: string;
  x: number;
  y: number;
  /** Lampan finns bara i simuleringen; ingen riktig lampa styrs av den. */
  virtual: boolean;
}

export interface Floor {
  id: string;
  name: string;
  /** 0 är entréplanet, 1 en trappa upp och -1 källaren. */
  level: number;
  rooms: Room[];
  lamps: Lamp[];
}

export interface HomeMessage {
  type: "home";
  name: string;
  floors: Floor[];
}

/** En person som någon källa ser, på en viss våning. */
export interface Target extends Point {
  floor_id: string;
}

/** Tidsfarterna som simuleringen kan köra i. Vid 60× går en minut på en sekund. */
export const SPEEDS = [1, 10, 60] as const;
export type Speed = (typeof SPEEDS)[number];

/** Högst så här många låtsaspersoner (samma gräns som MAX_PERSONS i kärnans simulation.py). */
export const MAX_PERSONS = 10;

/** En låtsasperson i simuleringen. Id:t behövs för att flytta eller ta bort den. */
export interface Person extends Target {
  id: string;
}

export interface Simulation {
  enabled: boolean;
  speed: Speed;
  /** Dagsljuset i alla rum, i lux. */
  daylight: number | null;
  persons: Person[];
}

export interface StateMessage {
  type: "state";
  /** Allt som kärnan ser, från alla källor, även låtsaspersonerna. */
  targets: Target[];
  rooms: { id: string; occupied: boolean; lux: number | null }[];
  lamps: { id: string; on: boolean; explanation: string }[];
  /** Simuleringsläget, eller null om kärnan inte har någon simulering. */
  simulation: Simulation | null;
}

export type ServerMessage = HomeMessage | StateMessage;

export type ClientMessage =
  | { type: "set_simulation"; enabled: boolean }
  | { type: "add_person"; floor_id: string; x: number; y: number }
  | { type: "move_person"; person_id: string; floor_id: string; x: number; y: number }
  | { type: "remove_person"; person_id: string }
  | { type: "set_daylight"; lux: number }
  | { type: "set_speed"; speed: Speed };

/** Tolkar ett meddelande från kärnan. Okända eller trasiga meddelanden ger null. */
export function parseServerMessage(text: string): ServerMessage | null {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return null;
  }
  if (typeof data !== "object" || data === null) {
    return null;
  }
  const message = data as {
    type?: unknown;
    floors?: unknown;
    rooms?: unknown;
    lamps?: unknown;
    targets?: unknown;
    simulation?: unknown;
  };
  if (message.type === "home" && Array.isArray(message.floors) && message.floors.length > 0) {
    return data as HomeMessage;
  }
  if (
    message.type === "state" &&
    Array.isArray(message.targets) &&
    Array.isArray(message.rooms) &&
    Array.isArray(message.lamps) &&
    (message.simulation === null || isSimulation(message.simulation))
  ) {
    return data as StateMessage;
  }
  return null;
}

function isSimulation(data: unknown): data is Simulation {
  if (typeof data !== "object" || data === null) {
    return false;
  }
  const simulation = data as { enabled?: unknown; speed?: unknown; persons?: unknown };
  return (
    typeof simulation.enabled === "boolean" &&
    (SPEEDS as readonly unknown[]).includes(simulation.speed) &&
    Array.isArray(simulation.persons)
  );
}
