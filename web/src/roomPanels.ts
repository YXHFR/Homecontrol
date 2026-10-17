/**
 * Texterna i rummets och lampornas kort: lampans läge, vad ett tryck på
 * PÅ/AV gör och ljusnivån. Modulen beror inte på Konva, så att den går att
 * pröva utan en rityta.
 */
import type { LampState } from "./protocol";

/** Lampans läge i klartext: "tänd", "dimrad 50 %", "släckt", "svarar inte" eller "okänt läge". */
export function lampStateText(state: LampState | undefined): string {
  if (state?.responding === false) {
    return "svarar inte";
  }
  if (state === undefined || state.on === null) {
    return "okänt läge";
  }
  if (!state.on) {
    return "släckt";
  }
  return state.brightness < 100 ? `dimrad ${state.brightness} %` : "tänd";
}

/** Vad ett tryck på PÅ/AV gör: släcker en lampa som lyser, annars tänder den. */
export function shouldTurnOn(state: LampState | undefined): boolean {
  return state?.on !== true;
}

function formatLux(lux: number): string {
  return lux.toLocaleString("sv-SE", { maximumFractionDigits: 1 });
}

/** Ljusnivån, och den justerade nivån när rummets lampor lyser upp sensorn. */
export function luxText(lux: number | null, adjusted: number | null): string {
  if (lux === null) {
    return "ljusnivån okänd";
  }
  // Skillnader som försvinner när värdet visas med en decimal syns inte.
  if (adjusted === null || Math.abs(adjusted - lux) < 0.05) {
    return `${formatLux(lux)} lux`;
  }
  return `${formatLux(lux)} lux (${formatLux(adjusted)} utan lampornas eget ljus)`;
}
