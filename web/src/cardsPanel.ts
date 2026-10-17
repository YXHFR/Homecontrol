/**
 * Texterna om ett riktigt rumskort i dess kort: signalstyrkan och hur kortet mår.
 */
import type { Card } from "./protocol";

/** Signalstyrka i klartext, som i en mobiltelefon. */
export function signalText(dbm: number): string {
  const quality = dbm >= -60 ? "bra" : dbm >= -70 ? "godkänd" : "svag";
  return `Wi-Fi ${Math.round(dbm)} dBm (${quality})`;
}

/** Hur kortet mår i några ord. */
export function cardStatus(card: Card): string {
  if (!card.online) {
    return "Svarar inte";
  }
  if (card.problems.length > 0) {
    return "Varning";
  }
  return card.calibrating ? "Kalibrerar" : "Svarar";
}

/** Klockslaget, som "14:02", i webbläsarens tidszon. */
export function clockTime(ms: number): string {
  return new Date(ms).toLocaleTimeString("sv-SE", { hour: "2-digit", minute: "2-digit" });
}
