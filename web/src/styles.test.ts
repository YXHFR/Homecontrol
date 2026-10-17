/// <reference types="node" />
/**
 * Utseendet: färgerna är variabler på :root med ett mörkt läge via
 * prefers-color-scheme, och texten har tillräcklig kontrast (WCAG AA) i
 * båda lägena.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

// Vitest ger tom text för CSS-filer, så filen läses direkt (testerna körs från web/).
const css = readFileSync(join(process.cwd(), "src", "styles.css"), "utf8");

/** Variablerna i det första :root-blocket som står direkt efter `after`. */
function variables(after: RegExp): Map<string, string> {
  const start = css.search(after);
  if (start < 0) {
    throw new Error(`Hittade inte ${after}`);
  }
  const rest = css.slice(start);
  const open = rest.indexOf(":root");
  const body = rest.slice(rest.indexOf("{", open) + 1, rest.indexOf("}", open));
  const found = new Map<string, string>();
  for (const match of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    found.set(match[1], match[2].trim());
  }
  return found;
}

const light = variables(/^/);
const darkOnly = variables(/@media\s*\(prefers-color-scheme:\s*dark\)/);
const dark = new Map([...light, ...darkOnly]);

type Rgba = [number, number, number, number];

function parse(value: string): Rgba {
  const hex = /^#([0-9a-f]{6})$/i.exec(value);
  if (hex !== null) {
    const n = parseInt(hex[1], 16);
    return [n >> 16, (n >> 8) & 255, n & 255, 1];
  }
  const rgba = /^rgba?\(([^)]+)\)$/.exec(value);
  if (rgba !== null) {
    const [r, g, b, a = "1"] = rgba[1].split(",").map((part) => part.trim());
    return [Number(r), Number(g), Number(b), Number(a)];
  }
  throw new Error(`Kan inte läsa färgen ${value}`);
}

/** Färgen `top` ovanpå den täckande färgen `below`. */
function over(top: Rgba, below: Rgba): Rgba {
  const a = top[3];
  return [0, 1, 2].map((i) => top[i] * a + below[i] * (1 - a)).concat(1) as Rgba;
}

function luminance([r, g, b]: Rgba): number {
  const channel = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** Kontrasten mellan två variabler; en genomskinlig bakgrund ligger på `base`. */
function contrast(theme: Map<string, string>, fg: string, bg: string, base = "--surface"): number {
  const color = (name: string) => {
    const value = theme.get(name);
    if (value === undefined) {
      throw new Error(`${name} saknas`);
    }
    return parse(value);
  };
  const background = over(color(bg), color(base));
  const foreground = over(color(fg), background);
  const [high, low] = [luminance(foreground), luminance(background)].sort((x, y) => y - x);
  return (high + 0.05) / (low + 0.05);
}

/** Text och bakgrund som står ihop i styles.css. */
const TEXT_PAIRS: [string, string][] = [
  ["--text", "--bg"],
  ["--text", "--surface"],
  ["--text", "--surface-2"],
  ["--text-2", "--bg"],
  ["--text-2", "--surface"],
  ["--text-2", "--surface-2"],
  ["--text-3", "--bg"],
  ["--text-3", "--surface"],
  ["--text-3", "--surface-2"],
  // Hälsoraden och felmeddelanden.
  ["--bad-ink", "--bad-soft"],
  ["--text", "--warn-soft"],
  // Toppraden i läget Flytta, och Klar i den.
  ["--accent-ink", "--accent"],
  ["--accent", "--accent-ink"],
  // Meddelanden längst ned på kartan.
  ["--bg", "--text"],
  ["--on-bad", "--bad"],
];

/** Prickar och linjer som bär betydelse: minst 3:1 mot ytan de står på. */
const GRAPHIC_PAIRS: [string, string][] = [
  ["--ok", "--surface"],
  ["--warn", "--surface"],
  ["--bad", "--surface"],
  ["--unk", "--surface"],
  ["--accent-strong", "--plan-bg"],
  ["--lamp-off", "--plan-bg"],
  ["--wall", "--plan-bg"],
  ["--focus", "--bg"],
];

describe("utseendet", () => {
  it("har färger, avstånd och hörn som variabler på :root", () => {
    for (const name of ["--bg", "--surface", "--text", "--accent", "--line", "--space-4", "--radius", "--mono"]) {
      expect(light.has(name), name).toBe(true);
    }
  });

  it("byter färgerna i mörkt läge via prefers-color-scheme", () => {
    for (const name of ["--bg", "--surface", "--text", "--text-2", "--text-3", "--plan-bg", "--wall"]) {
      expect(darkOnly.get(name), name).toBeDefined();
      expect(darkOnly.get(name), name).not.toBe(light.get(name));
    }
  });

  it("har bara en accentfärg, bärnstensgul", () => {
    const [r, g, b] = parse(light.get("--accent") ?? "");
    // Bärnsten: mycket rött, en del grönt och lite blått.
    expect(r).toBeGreaterThan(200);
    expect(g).toBeGreaterThan(120);
    expect(b).toBeLessThan(60);
  });

  it("har tryckytor på minst 44 punkter", () => {
    expect(parseFloat(light.get("--touch") ?? "0")).toBeGreaterThanOrEqual(44);
  });

  for (const [name, theme] of [
    ["ljust", light],
    ["mörkt", dark],
  ] as const) {
    it(`har text med minst 4,5:1 i kontrast i ${name} läge (WCAG AA)`, () => {
      const failing = TEXT_PAIRS.map(([fg, bg]) => ({ fg, bg, ratio: contrast(theme, fg, bg) })).filter(
        (pair) => pair.ratio < 4.5,
      );
      expect(failing).toEqual([]);
    });

    it(`har prickar och linjer med minst 3:1 i kontrast i ${name} läge`, () => {
      const failing = GRAPHIC_PAIRS.map(([fg, bg]) => ({ fg, bg, ratio: contrast(theme, fg, bg) })).filter(
        (pair) => pair.ratio < 3,
      );
      expect(failing).toEqual([]);
    });
  }
});
