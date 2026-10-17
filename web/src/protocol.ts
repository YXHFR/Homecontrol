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
  /** Hur mycket lampan lyser upp rummets ljussensor vid full styrka, i lux (lampkalibreringen). */
  lux_contribution: number;
}

/** Ett rumskort på kartan, riktigt eller simulerat. Ett riktigt kort har kortets namn som id. */
export interface MapNode {
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
  nodes: MapNode[];
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
  /** Tidsfarten är låst till 1×, eftersom riktiga sensorer eller lampor används. */
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

/** Ett riktigt rumskort (se cards_message i core/src/homecontrol/cards.py). */
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

export interface RoomState {
  id: string;
  status: Occupancy;
  /** Uppmätt ljusnivå i lux, eller null om ingen har mätt den. */
  lux: number | null;
  /** Ljusnivån med rummets egna lampors ljus avräknat; den som reglerna använder. */
  adjusted_lux: number | null;
  /** Någon har styrt en lampa i rummet för hand, och automatiken pausar. */
  manual: boolean;
  /** Varför rummets lampor är som de är, i klartext. */
  explanation: string;
}

export interface LampState {
  id: string;
  /** Null när lampans läge är okänt. */
  on: boolean | null;
  /** Ljusstyrkan i procent när lampan lyser, lägre under en dimring. */
  brightness: number;
  /** False när lampan inte svarar, till exempel när strömbrytaren är av. */
  responding: boolean;
  /** Samma förklaring som för lampans rum; lamporna i ett rum styrs som en grupp. */
  explanation: string;
}

/** Något som hände med lamporna, för listan "Varför hände det?". */
export interface LampEvent {
  /** Löpnummer från 1; ett högre nummer är en senare händelse. */
  number: number;
  /** Klockslaget som Unix-tid i sekunder. */
  at: number;
  room_id: string;
  text: string;
}

export interface StateMessage {
  type: "state";
  /** Spåren från alla källor, även de som låtsaspersonerna ger. */
  tracks: Track[];
  rooms: RoomState[];
  zones: { id: string; status: Occupancy }[];
  lamps: LampState[];
  /** De senaste händelserna, äldst först. */
  events: LampEvent[];
  /** Simuleringsläget, eller null om kärnan inte har någon simulering. */
  simulation: Simulation | null;
  /** De riktiga rumskorten, eller null om kärnan inte ansluter några. */
  cards: Card[] | null;
  /** Lampkalibreringen som pågår eller den senaste, eller null om ingen har körts. */
  calibration: Calibration | null;
  /**
   * Kärnans kontakt med databasen, eller null om ingen databas är inställd.
   * Utan kontakt styr kärnan med sin kopia av hemmet, och ändringar sparas inte.
   */
  database: { connected: boolean } | null;
  /** MQTT-brokern och Zigbee2MQTT, eller null om kärnan inte styr några Zigbee-lampor. */
  zigbee: ZigbeeStatus | null;
}

/** Om kärnan når Zigbee-lamporna (se core/src/homecontrol/zigbee.py). */
export interface ZigbeeStatus {
  /** Kärnan har en anslutning till MQTT-brokern. */
  mqtt_connected: boolean;
  /**
   * Det Zigbee2MQTT senast sa om sig själv (`bridge/state`), eller null när
   * kärnan inte är ansluten till brokern eller inte har hört något än.
   */
  bridge_online: boolean | null;
  /** När läget senast ändrades, som ISO 8601 i UTC, till exempel "2026-10-17T19:42:00Z". */
  since: string;
}

/** Hur lampkalibreringen går (se core/src/homecontrol/calibration.py). */
export interface Calibration {
  room_id: string;
  phase: "pågår" | "klar" | "avbruten";
  /** Vad som händer just nu, varför kalibreringen avbröts eller vad den kom fram till. */
  text: string;
  /** Mätningen som pågår, från 1, eller antalet mätningar när kalibreringen är klar. */
  step: number;
  steps: number;
  /** Varje lampas eget ljus vid sensorn i lux, när kalibreringen är klar. */
  results: Record<string, number>;
}

export type ServerMessage = HomeMessage | StateMessage;

/**
 * En riktig lampa som kärnan har hittat (se core/src/homecontrol/lampdevices.py).
 * Den används först när den är placerad på kartan med samma id.
 */
export interface FoundLamp {
  id: string;
  name: string;
  /** Lampprofilen som passar lampan, till exempel "ikea-tradfri". */
  profile: string;
  placed: boolean;
}

export type ClientMessage =
  | { type: "set_lamp"; lamp_id: string; on: boolean }
  | { type: "resume_automation"; room_id: string }
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
    events?: unknown;
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
    Array.isArray(message.events) &&
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
