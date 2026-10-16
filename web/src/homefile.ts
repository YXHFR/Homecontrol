/**
 * Hemmet som kartredigeraren läser och sparar: samma form som exportfilen
 * (se core/src/homecontrol/homefile.py och home.py).
 *
 * Redigeraren ändrar bara rummen och planritningen. Allt annat (zoner, noder,
 * lampor, inställningar …) följer med oförändrat, så typerna här beskriver
 * bara det som redigeraren läser.
 */
import type { WallKind } from "./protocol";

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
  zones?: { polygon: XY[] }[];
  nodes?: PlacedItem[];
  lamps?: PlacedItem[];
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

/** Kärnan sa nej. Meddelandet går att visa för användaren. */
export class SaveError extends Error {}

/** Det kartredigeraren behöver av kärnan. Testerna byter ut det. */
export interface HomeApi {
  load(): Promise<HomeDocument>;
  save(document: HomeDocument): Promise<void>;
  uploadPlanImage(floorId: string, image: Blob): Promise<void>;
  deletePlanImage(floorId: string): Promise<void>;
  planImageUrl(floorId: string): string;
}

async function check(response: Response): Promise<Response> {
  if (response.ok) {
    return response;
  }
  let detail = `Kärnan svarade ${response.status}`;
  try {
    const body: unknown = await response.json();
    if (typeof body === "object" && body !== null && "detail" in body) {
      detail = String(body.detail);
    }
  } catch {
    // Inget begripligt svar; statuskoden får räcka.
  }
  throw new SaveError(detail);
}

function planImagePath(floorId: string): string {
  return `/api/floors/${encodeURIComponent(floorId)}/plan-image`;
}

export const coreApi: HomeApi = {
  async load() {
    const response = await check(await fetch("/api/home", { cache: "no-store" }));
    return (await response.json()) as HomeDocument;
  },
  async save(document) {
    await check(
      await fetch("/api/home", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(document),
      }),
    );
  },
  async uploadPlanImage(floorId, image) {
    await check(await fetch(planImagePath(floorId), { method: "PUT", body: image }));
  },
  async deletePlanImage(floorId) {
    await check(await fetch(planImagePath(floorId), { method: "DELETE" }));
  },
  planImageUrl: planImagePath,
};
