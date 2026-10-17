/**
 * Kartans färger. Kartan ritas på en canvas, som inte kan läsa CSS-variabler
 * själv, så färgerna läses från :root i styles.css. Då följer kartan ljust och
 * mörkt läge precis som resten av sidan.
 */

/** Färgerna som kartan använder, med värdet i ljust läge som reserv (till exempel i testerna). */
const FALLBACK = {
  bg: "#fafafa",
  surface: "#ffffff",
  text: "#09090b",
  text2: "#52525b",
  text3: "#65656d",
  line: "#e4e4e7",
  lineStrong: "#d4d4d8",
  accent: "#f59e0b",
  accentStrong: "#b45309",
  ok: "#16a34a",
  warn: "#a16207",
  bad: "#dc2626",
  unk: "#8a8a93",
  planBg: "#ffffff",
  planOutside: "#f4f4f5",
  planGrid: "#ececee",
  wall: "#18181b",
  roomFill: "#fcfcfc",
  roomOccupied: "#f1f1f3",
  roomSelected: "#ececef",
  furn: "rgba(9, 9, 11, 0.06)",
  furnEdge: "#a1a1aa",
  lampOff: "#8f8f98",
  person: "#09090b",
  cone: "rgba(9, 9, 11, 0.05)",
  coneSelected: "rgba(9, 9, 11, 0.1)",
  draft: "#09090b",
};

export type MapColors = typeof FALLBACK;

/** CSS-variabeln för varje färg: `planBg` heter `--plan-bg`. */
function variableName(key: string): string {
  return `--${key.replace(/[A-Z0-9]/g, (letter) => `-${letter.toLowerCase()}`)}`;
}

/** Färgerna som gäller just nu, i ljust eller mörkt läge. */
export function mapColors(): MapColors {
  const style = getComputedStyle(document.documentElement);
  const colors = { ...FALLBACK };
  for (const key of Object.keys(FALLBACK) as (keyof MapColors)[]) {
    const value = style.getPropertyValue(variableName(key)).trim();
    if (value !== "") {
      colors[key] = value;
    }
  }
  return colors;
}

/** Anropar `listener` när telefonen eller datorn byter mellan ljust och mörkt läge. */
export function onColorSchemeChange(listener: () => void): void {
  const query = window.matchMedia?.("(prefers-color-scheme: dark)");
  query?.addEventListener?.("change", listener);
}
