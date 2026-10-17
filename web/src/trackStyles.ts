/**
 * Hur kartan visar spåren och läget i rummen. Modulen beror inte på Konva,
 * så att den går att pröva utan en rityta.
 */
import type { Occupancy, StateMessage, Track } from "./protocol";

/** Spårens färg när ingen annan anges (se --person i styles.css). */
export const TRACK_COLOR = "#09090b";

export interface TrackStyle {
  fill?: string;
  stroke?: string;
  strokeWidth?: number;
  dash?: number[];
}

/**
 * Ett spår som radarn ser är en fylld prick. Ett spår som radarn har tappat
 * men som kärnan håller kvar är en streckad ring, så att det syns att
 * kärnan minns personen fast ingen ser den just nu.
 */
export function trackStyle(track: Track, color = TRACK_COLOR): TrackStyle {
  return track.held ? { stroke: color, strokeWidth: 2, dash: [3, 3] } : { fill: color };
}

/** Spåren på en våning. */
export function tracksOnFloor(state: StateMessage | null, floorId: string): Track[] {
  return (state?.tracks ?? []).filter((track) => track.floor_id === floorId);
}

/** Läget i ett rum i klartext, för rummets kort. */
export const OCCUPANCY_TEXT: Record<Occupancy, string> = {
  upptaget: "Upptaget",
  tomt: "Tomt",
  okänt: "Okänt läge – en sensor svarar inte",
};
