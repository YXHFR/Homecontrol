/**
 * Guiden för lampkalibreringen i livevyn: mäter hur mycket varje lampa i ett rum
 * själv lyser upp rummets ljussensor, så att reglerna kan räkna bort lampornas
 * eget ljus (se core/src/homecontrol/calibration.py).
 *
 * Guiden öppnas från ett rum i panelen "Just nu". Den förklarar vad som händer,
 * startar mätningen i kärnan och visar hur den går och vad den kom fram till.
 * Mätningen sköts helt av kärnan, så guiden kan stängas och öppnas igen.
 */
import { button, element } from "./dom";
import { CoreError, ensureOk } from "./homeApi";
import type { Calibration, Floor } from "./protocol";

/** Det guiden behöver av kärnan. Testerna byter ut det. */
export interface CalibrationApi {
  /** Startar kalibreringen i rummet. Ett `CoreError` säger varför det inte gick. */
  start(roomId: string): Promise<void>;
  /** Avbryter kalibreringen som pågår. Lamporna ställs tillbaka. */
  cancel(): Promise<void>;
}

export const coreCalibrationApi: CalibrationApi = {
  async start(roomId) {
    await ensureOk(fetch(`/api/rooms/${encodeURIComponent(roomId)}/calibration`, { method: "POST" }));
  },
  async cancel() {
    await ensureOk(fetch("/api/calibration", { method: "DELETE" }));
  },
};

export interface CalibrationPanel {
  /** Panelen som läggs in på sidan. Den är dold tills guiden öppnas eller en kalibrering pågår. */
  element: HTMLElement;
  /** Öppnar guiden för ett rum. */
  open(roomId: string): void;
  /** Visar våningen och kalibreringen från kärnan. */
  show(floor: Floor | null, calibration: Calibration | null): void;
}

function formatLux(lux: number): string {
  return `${lux.toLocaleString("sv-SE", { maximumFractionDigits: 1 })} lux`;
}

export function createCalibrationPanel(api: CalibrationApi): CalibrationPanel {
  const heading = element("h2", {}, "Lampkalibrering");
  const body = element("div", {});
  const panel = element("section", { className: "calibration", hidden: true }, heading, body);
  panel.setAttribute("aria-label", "Lampkalibrering");

  let roomId: string | null = null;
  let floor: Floor | null = null;
  let calibration: Calibration | null = null;
  /** Kärnans svar när mätningen inte gick att starta. */
  let problem: string | null = null;
  /** Det som visas, så att guiden bara byggs om när något har ändrats. */
  let shown = "";

  const start = (id: string) => {
    problem = null;
    api.start(id).catch((error: unknown) => {
      problem = error instanceof CoreError ? error.message : "Mätningen gick inte att starta.";
      render();
    });
  };
  const close = () => {
    roomId = null;
    problem = null;
    render();
  };

  const render = () => {
    // En kalibrering som pågår visas alltid, även om ingen har öppnat guiden
    // här, och guiden står kvar med resultatet när den är klar.
    if (calibration?.phase === "pågår") {
      roomId = calibration.room_id;
    }
    const id = roomId;
    const room = floor?.rooms.find((r) => r.id === id);
    const state = JSON.stringify([id, room, floor?.lamps, calibration, problem]);
    if (state === shown) {
      return;
    }
    shown = state;
    panel.hidden = room === undefined;
    if (room === undefined || floor === null) {
      body.replaceChildren();
      return;
    }
    heading.textContent = `Lampkalibrering: ${room.name}`;
    const lamps = floor.lamps.filter((lamp) => lamp.room_id === room.id);
    const latest = calibration?.room_id === room.id ? calibration : null;

    if (latest?.phase === "pågår") {
      const progress = element("progress", { max: latest.steps, value: latest.step - 1 });
      progress.setAttribute("aria-label", "Hur långt mätningen har kommit");
      body.replaceChildren(
        element("p", {}, `Mätning ${latest.step} av ${latest.steps}: ${latest.text}`),
        progress,
        element(
          "p",
          { className: "help" },
          "Lamporna tänds och släcks en i taget. Rör inte lamporna och stå inte i vägen för " +
            "rumskortet. Automatiken pausar under tiden.",
        ),
        element(
          "div",
          { className: "controls" },
          button("Avbryt", () => void api.cancel().catch(() => undefined), "cancel"),
        ),
      );
      return;
    }

    const results =
      latest?.phase === "klar"
        ? element(
            "ul",
            { className: "calibration-results" },
            ...lamps.map((lamp) =>
              element("li", {}, `${lamp.name}: ${formatLux(latest.results[lamp.id] ?? lamp.lux_contribution)}`),
            ),
          )
        : null;
    const intro =
      latest === null
        ? [
            element(
              "p",
              {},
              "Kalibreringen mäter hur mycket varje lampa själv lyser upp ljussensorn i rummet, " +
                "så att lampornas eget ljus aldrig tas för dagsljus och släcker dem.",
            ),
            element(
              "ol",
              {},
              element("li", {}, "Gör det när ljuset är jämnt, helst när det är mörkt ute."),
              element("li", {}, "Stå inte mellan lamporna och rumskortet, och skugga inte sensorn."),
              element(
                "li",
                {},
                `Lamporna släcks och tänds sedan en i taget. Det tar ungefär ${minutes(lamps.length)}. ` +
                  "Efteråt blir lamporna som de var.",
              ),
            ),
            element(
              "ul",
              { className: "calibration-results" },
              ...lamps.map((lamp) =>
                element(
                  "li",
                  {},
                  `${lamp.name}: ${lamp.lux_contribution > 0 ? formatLux(lamp.lux_contribution) : "inte mätt än"}`,
                ),
              ),
            ),
          ]
        : [element("p", { className: latest.phase === "avbruten" ? "problem" : "" }, latest.text)];
    const startLabel = latest === null ? "Starta mätningen" : latest.phase === "klar" ? "Kalibrera igen" : "Försök igen";
    body.replaceChildren(
      ...intro,
      ...(results === null ? [] : [results]),
      ...(problem === null ? [] : [element("p", { className: "problem" }, problem)]),
      element(
        "div",
        { className: "controls" },
        button(startLabel, () => start(room.id), "start"),
        " ",
        button("Stäng", close, "close"),
      ),
    );
  };

  return {
    element: panel,
    open(id) {
      roomId = id;
      problem = null;
      render();
    },
    show(nextFloor, nextCalibration) {
      floor = nextFloor;
      calibration = nextCalibration;
      render();
    },
  };
}

/** Ungefär hur länge kalibreringen tar: en mätning utan lampor och två per lampa. */
function minutes(lamps: number): string {
  // Varje mätning tar ungefär en kvarts minut (se STABLE_SECONDS i kärnan).
  const total = Math.max(1, Math.round(((1 + 2 * lamps) * 17) / 60));
  return total === 1 ? "en minut" : `${total} minuter`;
}
