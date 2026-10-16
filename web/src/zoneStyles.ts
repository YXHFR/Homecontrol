/**
 * Hur varje sorts zon ser ut och vad den betyder i klartext. Både livevyn och
 * kartredigeraren ritar zoner, så modulen beror inte på någon av dem.
 */
import type { ZoneKind } from "./protocol";

export const ZONE_KINDS: readonly ZoneKind[] = ["vanlig", "stillhet", "ignorera", "entré"];

export interface ZoneStyle {
  /** Namnet på knappen och i listan över sorter. */
  label: string;
  /** Det nya zoner av sorten heter, följt av en siffra. */
  defaultName: string;
  /** Vad sorten betyder, i klartext. */
  description: string;
  /** Som "#rrggbb"; kartorna lägger till två hexsiffror för genomskinlighet. */
  color: string;
}

export const ZONE_STYLES: Record<ZoneKind, ZoneStyle> = {
  vanlig: {
    label: "Vanlig zon",
    defaultName: "Zon",
    description: "En plats i rummet som systemet ska känna till, till exempel skrivbordet.",
    color: "#2e9d5b",
  },
  stillhet: {
    label: "Stillhetszon",
    defaultName: "Stillhet",
    description:
      "Där man sitter eller ligger still länge, till exempel soffan eller sängen. " +
      "Närvaron hålls kvar även när radarn tappar personen.",
    color: "#7c5cc9",
  },
  ignorera: {
    label: "Ignorera-zon",
    defaultName: "Ignorera",
    description:
      "Störkällor som radarn inte ska bry sig om, till exempel en fläkt, en gardin eller en växt.",
    color: "#7a7a7a",
  },
  entré: {
    label: "Entrézon",
    defaultName: "Entré",
    description: "Vid en dörr. Den som försvinner ur sikte här har gått ut.",
    color: "#0e8f8a",
  },
};
