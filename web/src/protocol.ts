/**
 * Meddelandena i realtidsströmmen mellan kärnan och livevyn (se core/src/homecontrol/live.py).
 * Koordinater är meter i hemmets plan.
 */

export interface Point {
  x: number;
  y: number;
}

export interface Room {
  id: string;
  name: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Lamp {
  id: string;
  name: string;
  room_id: string;
  x: number;
  y: number;
}

export interface HomeMessage {
  type: "home";
  rooms: Room[];
  lamps: Lamp[];
}

export interface StateMessage {
  type: "state";
  targets: Point[];
  rooms: { id: string; occupied: boolean; lux: number | null }[];
  lamps: { id: string; on: boolean; explanation: string }[];
}

export type ServerMessage = HomeMessage | StateMessage;

export type ClientMessage =
  | { type: "move_person"; x: number; y: number }
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
  const message = data as { type?: unknown; rooms?: unknown; lamps?: unknown; targets?: unknown };
  if (message.type === "home" && Array.isArray(message.rooms) && Array.isArray(message.lamps)) {
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
