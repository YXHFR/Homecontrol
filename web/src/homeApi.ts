/**
 * Hur kartredigeraren läser och sparar hemmet i kärnan (se core/src/homecontrol/editor.py).
 *
 * Varje ändring ger hemmet en ny revision. Redigeraren skickar med den
 * revision den utgår från, så att den aldrig skriver över en ändring som
 * någon annan (till exempel ett annat fönster) hann göra först.
 */
import type { HomeDocument } from "./homefile";

/** Kärnan sa nej eller gick inte att nå. Meddelandet går att visa för användaren. */
export class CoreError extends Error {
  constructor(
    message: string,
    /** Hemmet hade ändrats någon annanstans. Det som visas måste läsas om. */
    readonly conflict = false,
  ) {
    super(message);
  }
}

export interface LoadedHome {
  document: HomeDocument;
  revision: string;
}

/** Det kartredigeraren behöver av kärnan. Testerna byter ut det. */
export interface HomeApi {
  load(): Promise<LoadedHome>;
  /** Sparar hemmet och ger den nya revisionen. */
  save(document: HomeDocument, revision: string): Promise<string>;
  /** Byter bilden av planritningen, `width` bildpunkter bred, och ger den nya revisionen. */
  uploadPlanImage(floorId: string, image: Blob, width: number, revision: string): Promise<string>;
  deletePlanImage(floorId: string, revision: string): Promise<string>;
  planImageUrl(floorId: string): string;
}

const CONFLICT = 409;

async function ensureOk(request: Promise<Response>): Promise<Response> {
  let response: Response;
  try {
    response = await request;
  } catch {
    throw new CoreError("Får ingen kontakt med Homecontrol. Försök igen om en stund.");
  }
  if (response.ok) {
    return response;
  }
  let detail = `Något gick fel i Homecontrol (felkod ${response.status}). Försök igen om en stund.`;
  try {
    const body: unknown = await response.json();
    // Kärnans egna fel har en text; FastAPIs kontroller ger en lista, som inte visas.
    if (
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof body.detail === "string"
    ) {
      detail = body.detail;
    }
  } catch {
    // Inget begripligt svar; texten ovan får räcka.
  }
  throw new CoreError(detail, response.status === CONFLICT);
}

function revisionOf(response: Response): string {
  return response.headers.get("ETag") ?? "";
}

function planImagePath(floorId: string): string {
  return `/api/floors/${encodeURIComponent(floorId)}/plan-image`;
}

export const coreApi: HomeApi = {
  async load() {
    const response = await ensureOk(fetch("/api/home", { cache: "no-store" }));
    return { document: (await response.json()) as HomeDocument, revision: revisionOf(response) };
  },
  async save(document, revision) {
    const response = await ensureOk(
      fetch("/api/home", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "If-Match": revision },
        body: JSON.stringify(document),
      }),
    );
    return revisionOf(response);
  },
  async uploadPlanImage(floorId, image, width, revision) {
    const response = await ensureOk(
      fetch(`${planImagePath(floorId)}?width=${width}`, {
        method: "PUT",
        headers: { "If-Match": revision },
        body: image,
      }),
    );
    return revisionOf(response);
  },
  async deletePlanImage(floorId, revision) {
    const response = await ensureOk(
      fetch(planImagePath(floorId), { method: "DELETE", headers: { "If-Match": revision } }),
    );
    return revisionOf(response);
  },
  planImageUrl: planImagePath,
};
