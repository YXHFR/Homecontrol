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

export interface Room {
  id: string;
  name: string;
  /** Hörnen i ordning som [x, y]. */
  polygon: [number, number][];
  /** En vägg per sida: väggen från hörn i till hörn i + 1. */
  walls: WallKind[];
}

export interface Lamp {
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

export interface StateMessage {
  type: "state";
  targets: Target[];
  rooms: { id: string; occupied: boolean; lux: number | null }[];
  lamps: { id: string; on: boolean; explanation: string }[];
}

export type ServerMessage = HomeMessage | StateMessage;

export type ClientMessage =
  | { type: "move_person"; floor_id: string; x: number; y: number }
  | { type: "remove_person" }
  | { type: "set_light_level"; room_id: string; lux: number };

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
  };
  if (message.type === "home" && Array.isArray(message.floors) && message.floors.length > 0) {
    return data as HomeMessage;
  }
  if (
    message.type === "state" &&
    Array.isArray(message.targets) &&
    Array.isArray(message.rooms) &&
    Array.isArray(message.lamps)
  ) {
    return data as StateMessage;
  }
  return null;
}
