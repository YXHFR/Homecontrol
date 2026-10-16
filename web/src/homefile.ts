/**
 * Hemmet som kartredigeraren läser och sparar: samma form som exportfilen
 * (se core/src/homecontrol/homefile.py och home.py).
 *
 * Redigeraren ändrar rummen, zonerna och planritningen. Allt annat (noder,
 * lampor, inställningar …) följer med oförändrat, så typerna här beskriver
 * bara det som redigeraren läser.
 */
import type { WallKind, ZoneKind } from "./protocol";

export type XY = [number, number];

export interface PlacedItem {
  position: XY;
}

export interface RoomModel {
  id: string;
  name: string;
  /** Hörnen i ordning, i meter med origo uppe till vänster. */
  polygon: XY[];
  /** En vägg per sida: väggen från hörn i till hörn i + 1. */
  walls: WallKind[];
  zones?: ZoneModel[];
  nodes?: PlacedItem[];
  lamps?: LampModel[];
}

export interface ZoneModel {
  id: string;
  name: string;
  kind: ZoneKind;
  /** Hörnen i ordning. Alla ligger i rummet. */
  polygon: XY[];
}

export interface LampModel extends PlacedItem {
  /** Zonen i rummet som lampan hör till, om någon. */
  zone_id?: string | null;
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
  signal_sources?: unknown[];
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
