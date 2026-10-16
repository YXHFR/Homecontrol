/**
 * Hemmet som kartredigeraren läser och sparar: samma form som exportfilen
 * (se core/src/homecontrol/homefile.py och home.py).
 *
 * Det redigeraren inte själv ändrar (till exempel nodernas förmågor) följer
 * med oförändrat, så typerna här beskriver bara det som redigeraren läser
 * eller skriver.
 */
import type { WallKind, ZoneKind } from "./protocol";

export type XY = [number, number];

export interface PlacedItem {
  position: XY;
}

/** Ett rumskort (ESP32) med radar. */
export interface NodeModel extends PlacedItem {
  id: string;
  name: string;
  /** Monteringshöjd över golvet i meter. */
  height: number;
  /** Åt vilket håll radarn tittar, i grader medurs från höger (x-axeln), 0–359. */
  rotation?: number;
  /** Radarns sidled är spegelvänd. */
  mirrored?: boolean;
  capabilities?: string[];
}

export interface LampModel extends PlacedItem {
  id: string;
  name: string;
  /** Zonen i rummet som lampan hör till. */
  zone_id?: string | null;
  profile?: string;
  /** Hur mycket lampan lyser upp rummets ljussensor vid full styrka, i lux (lampkalibreringen). */
  lux_contribution?: number;
}

/** En Wi-Fi-sändare, till exempel routern. */
export interface SignalSourceModel extends PlacedItem {
  id: string;
  name: string;
  height: number;
}

export interface RoomSettings {
  /** Under den här ljusnivån (lux) räknas rummet som mörkt. */
  dark_below_lux: number;
  /** Över den här ljusnivån (lux) räknas rummet som ljust av dagsljus. */
  bright_above_lux: number;
  /** Så länge rummet ska ha varit tomt innan lamporna släcks, i sekunder. */
  off_after_empty_seconds: number;
  /**
   * Hur mycket Wi-Fi-rörelse väger mot radarn, från 0 till 1 (se RoomSettings i
   * core/src/homecontrol/home.py). Under 1 startar Wi-Fi aldrig närvaro ensam.
   */
  wifi_weight: number;
  /** Så länge lamporna dimras som varning innan de släcks, i sekunder. 0 stänger av varningen. */
  dim_before_off_seconds: number;
  /** Ljusstyrkan under varningsdimringen, i procent. */
  dim_percent: number;
  /** Så länge dagsljuset ska ha varit över ljusgränsen i sträck innan lamporna släcks, i sekunder. */
  daylight_off_after_seconds: number;
  /** Rummet har inga fönster och räknas alltid som mörkt. */
  windowless: boolean;
}

export interface RoomModel {
  id: string;
  name: string;
  /** Hörnen i ordning, i meter med origo uppe till vänster. */
  polygon: XY[];
  /** En vägg per sida: väggen från hörn i till hörn i + 1. */
  walls: WallKind[];
  settings?: RoomSettings;
  zones?: ZoneModel[];
  nodes?: NodeModel[];
  lamps?: LampModel[];
}

export interface ZoneModel {
  id: string;
  name: string;
  kind: ZoneKind;
  /** Hörnen i ordning. Alla ligger i rummet. */
  polygon: XY[];
}

export interface PlanImage {
  /** Bildens övre vänstra hörn, i meter. */
  position: XY;
  meters_per_pixel: number;
}

export interface FloorModel {
  id: string;
  name: string;
  level: number;
  rooms: RoomModel[];
  signal_sources?: SignalSourceModel[];
  plan_image?: PlanImage | null;
}

export interface HomeModel {
  name: string;
  floors: FloorModel[];
}

export interface HomeDocument {
  format: "homecontrol-hem";
  version: number;
  home: HomeModel;
}
