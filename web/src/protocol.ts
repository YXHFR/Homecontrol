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

/** Ett rumskort på kartan. Ett riktigt kort har samma id som kortets namn, homecontrol-<rum>. */
export interface CardNode {
  id: string;
  name: string;
  room_id: string;
  x: number;
  y: number;
}

export interface Floor {
  id: string;
  name: string;
  /** 0 är entréplanet, 1 en trappa upp och -1 källaren. */
  level: number;
  rooms: Room[];
  nodes: CardNode[];
  lamps: Lamp[];
}

export interface HomeMessage {
  type: "home";
  name: string;
  floors: Floor[];
}

/** En punkt på en viss våning. */
export interface Target extends Point {
  floor_id: string;
}

/** Ett anonymt spår av en person, som kärnans sammanvägning har byggt av det radarerna ser. */
export interface Track extends Target {
  /** Radarn har tappat spåret, men kärnan håller det kvar en stund. */
  held: boolean;
}

/** Läget i ett rum eller en zon. Okänt när en sensor där har slutat svara. */
export type Occupancy = "upptaget" | "tomt" | "okänt";

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
  /** Tidsfarten är låst till 1×, eftersom riktiga rumskort används. */
  speed_locked: boolean;
  /** Dagsljuset i alla rum, i lux. */
  daylight: number | null;
  persons: Person[];
}

/** En ny version av kortets firmware, som användaren kan godkänna. */
export interface CardUpdate {
  version: string;
  in_progress: boolean;
  /** Hur långt uppdateringen har kommit, 0–100, om kortet säger det. */
  progress: number | null;
}

/** Ett riktigt rumskort (se cards_message i core/src/homecontrol/live.py). */
export interface Card {
  /** Kortets namn, homecontrol-<rum>. Ett placerat kort är en nod med samma id. */
  id: string;
  name: string;
  /** Kortet är placerat på kartan. Annars väntar det på att placeras i kartredigeraren. */
  placed: boolean;
  online: boolean;
  /** Varningar i klartext. Tom när allt fungerar. */
  problems: string[];
  calibrating: boolean;
  wifi_signal: number | null;
  firmware_version: string | null;
  update: CardUpdate | null;
}

export interface StateMessage {
  type: "state";
  /** Spåren från alla källor, även de som låtsaspersonerna ger. */
  tracks: Track[];
  rooms: { id: string; status: Occupancy; lux: number | null }[];
  zones: { id: string; status: Occupancy }[];
  lamps: { id: string; on: boolean; explanation: string }[];
  /** Simuleringsläget, eller null om kärnan inte har någon simulering. */
  simulation: Simulation | null;
  /** De riktiga rumskorten, eller null om kärnan inte ansluter några. */
  cards: Card[] | null;
}

export type ServerMessage = HomeMessage | StateMessage;

export type ClientMessage =
  | { type: "set_simulation"; enabled: boolean }
  | { type: "add_person"; floor_id: string; x: number; y: number }
  | { type: "move_person"; person_id: string; floor_id: string; x: number; y: number }
  | { type: "remove_person"; person_id: string }
  | { type: "set_daylight"; lux: number }
  | { type: "set_speed"; speed: Speed }
  /** Användaren har godkänt att kortet uppdateras. */
  | { type: "install_update"; card: string };

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
    tracks?: unknown;
    zones?: unknown;
    simulation?: unknown;
    cards?: unknown;
  };
  if (message.type === "home" && Array.isArray(message.floors) && message.floors.length > 0) {
    return data as HomeMessage;
  }
  if (
    message.type === "state" &&
    Array.isArray(message.tracks) &&
    Array.isArray(message.rooms) &&
    Array.isArray(message.zones) &&
    Array.isArray(message.lamps) &&
    (message.simulation === null || isSimulation(message.simulation)) &&
    (message.cards === null || Array.isArray(message.cards))
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
