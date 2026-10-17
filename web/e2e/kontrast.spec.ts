import { expect, type Page, test } from "@playwright/test";
import { openPage } from "./helpers";

/**
 * Kontrasten på den riktiga sidan, så som webbläsaren ritar den: all synlig
 * text mot bakgrunden den står på, i ljust och mörkt läge på en 375 punkter
 * bred telefon. WCAG AA kräver 4,5:1 för vanlig text och 3:1 för stor text.
 */

interface Failure {
  text: string;
  ratio: number;
  needed: number;
}

/** Texten på sidan som har för låg kontrast. */
function lowContrast(page: Page): Promise<Failure[]> {
  return page.evaluate(() => {
    type Rgba = [number, number, number, number];
    const parse = (value: string): Rgba | null => {
      const match = /^rgba?\(([^)]+)\)$/.exec(value);
      if (match === null) {
        return null;
      }
      const parts = match[1].split(/[\s,/]+/).filter(Boolean).map(Number);
      return [parts[0], parts[1], parts[2], parts[3] ?? 1];
    };
    const over = (top: Rgba, below: Rgba): Rgba => {
      const a = top[3];
      return [top[0] * a + below[0] * (1 - a), top[1] * a + below[1] * (1 - a), top[2] * a + below[2] * (1 - a), 1];
    };
    const luminance = ([r, g, b]: Rgba) => {
      const channel = (v: number) => {
        const s = v / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
    };
    /** Bakgrunden bakom elementet: alla bakgrunder från roten och inåt, lagda på varandra. */
    const backgroundOf = (element: Element): Rgba => {
      const chain: Element[] = [];
      for (let node: Element | null = element; node !== null; node = node.parentElement) {
        chain.unshift(node);
      }
      let color: Rgba = [255, 255, 255, 1];
      for (const node of chain) {
        const parsed = parse(getComputedStyle(node).backgroundColor);
        if (parsed !== null && parsed[3] > 0) {
          color = over(parsed, color);
        }
      }
      return color;
    };
    const opacityOf = (element: Element): number => {
      let opacity = 1;
      for (let node: Element | null = element; node !== null; node = node.parentElement) {
        opacity *= Number(getComputedStyle(node).opacity);
      }
      return opacity;
    };

    const failures: { text: string; ratio: number; needed: number }[] = [];
    for (const element of document.querySelectorAll<HTMLElement>("body *")) {
      const own = [...element.childNodes]
        .filter((child) => child.nodeType === Node.TEXT_NODE)
        .map((child) => child.textContent?.trim() ?? "")
        .join(" ")
        .trim();
      if (own === "" || element.closest("[disabled], [aria-disabled='true'], [hidden]") !== null) {
        continue;
      }
      const box = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      if (box.width === 0 || box.height === 0 || style.visibility === "hidden" || element.offsetParent === null) {
        continue;
      }
      const fg = parse(style.color);
      if (fg === null) {
        continue;
      }
      const background = backgroundOf(element);
      const foreground = over([fg[0], fg[1], fg[2], fg[3] * opacityOf(element)], background);
      const [high, low] = [luminance(foreground), luminance(background)].sort((x, y) => y - x);
      const ratio = (high + 0.05) / (low + 0.05);
      const size = parseFloat(style.fontSize);
      const large = size >= 24 || (size >= 18.66 && Number(style.fontWeight) >= 700);
      const needed = large ? 3 : 4.5;
      if (ratio < needed) {
        failures.push({ text: own.slice(0, 40), ratio: Math.round(ratio * 100) / 100, needed });
      }
    }
    return failures;
  });
}

test.describe("på en 375 punkter bred telefon", () => {
  test.use({ viewport: { width: 375, height: 740 } });

  for (const scheme of ["light", "dark"] as const) {
    test(`har texten tillräcklig kontrast (WCAG AA) i ${scheme === "light" ? "ljust" : "mörkt"} läge`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await openPage(page);
      const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);

      // Rummets kort, med hälsoraden och simuleringen.
      expect(await lowContrast(page), "rummets kort").toEqual([]);
      expect(await overflow()).toBeLessThanOrEqual(0);

      // Ett rumskorts kort och toppraden i accentfärgen medan det flyttas.
      await page.locator(".sheet li button").first().click();
      expect(await lowContrast(page), "rumskortets kort").toEqual([]);
      await page.getByRole("button", { name: "Flytta", exact: true }).click();
      await expect(page.locator("#app")).toHaveAttribute("data-mode", "move");
      expect(await lowContrast(page), "Flytta").toEqual([]);
      expect(await overflow()).toBeLessThanOrEqual(0);
      await page.locator(".topbar").getByRole("button", { name: "Avbryt" }).click();
      await expect(page.locator("#app")).toHaveAttribute("data-mode", "view");

      // Ritverktygen.
      await page.getByRole("button", { name: "Tillbaka till rummet" }).click();
      await page.getByRole("button", { name: "Ändra rummet" }).click();
      await expect(page.locator("#app")).toHaveAttribute("data-mode", "edit");
      expect(await lowContrast(page), "ritverktygen").toEqual([]);
      expect(await overflow()).toBeLessThanOrEqual(0);
      await page.getByRole("button", { name: "Klar" }).click();
    });
  }
});
