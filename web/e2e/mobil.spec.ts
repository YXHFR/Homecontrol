import { expect, test } from "@playwright/test";
import { groundFloor, mapCanvas, onMap, openEditor, openPage, savedHome, waitUntilSaved } from "./helpers";

test("på mobilen ligger ritverktygen längst ned och ett rum kan ritas med fingret", async ({ page }) => {
  await openEditor(page);
  const before = groundFloor(await savedHome(page)).rooms.length;
  const map = mapCanvas(page);

  // Verktygsraden ligger under kartan, utan att sidan blir bredare än skärmen.
  const [toolsBox, mapBox] = await Promise.all([
    page.getByRole("navigation", { name: "Verktyg" }).boundingBox(),
    map.boundingBox(),
  ]);
  if (toolsBox === null || mapBox === null) {
    throw new Error("Verktygen eller kartan syns inte");
  }
  expect(toolsBox.y).toBeGreaterThanOrEqual(mapBox.y + mapBox.height - 1);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);

  await page.getByRole("button", { name: "Zooma ut" }).tap();
  await page.getByRole("button", { name: "Zooma ut" }).tap();
  await page.getByRole("button", { name: "Rum, fri form" }).tap();
  for (const [fx, fy] of [
    [0.7, 0.72],
    [0.95, 0.72],
    [0.95, 0.86],
    [0.7, 0.86],
    [0.7, 0.72],
  ] as const) {
    const point = await onMap(map, fx, fy);
    await page.touchscreen.tap(point.x, point.y);
  }
  await waitUntilSaved(page);

  expect(groundFloor(await savedHome(page)).rooms).toHaveLength(before + 1);
  await page.getByRole("button", { name: "Ta bort rummet" }).tap();
  await waitUntilSaved(page);
});

test.describe("på en 375 punkter bred telefon", () => {
  test.use({ viewport: { width: 375, height: 740 } });

  /** Hur många punkter sidan är bredare än skärmen. */
  const overflow = (page: import("@playwright/test").Page) =>
    page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);

  for (const scheme of ["light", "dark"] as const) {
    test(`skrollar sidan inte i sidled, i ${scheme === "light" ? "ljust" : "mörkt"} läge, och knapparna går att träffa`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await openPage(page);
      expect(await overflow(page)).toBeLessThanOrEqual(0);

      // Bakgrunden följer telefonens läge.
      const background = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
      expect(background).toBe(scheme === "light" ? "rgb(250, 250, 250)" : "rgb(10, 10, 11)");

      // Arkets tre höjder.
      const handle = page.getByRole("button", { name: "Ändra arkets höjd" });
      for (const height of ["half", "high", "low"]) {
        await handle.tap();
        await expect(page.locator(".sheet")).toHaveAttribute("data-height", height);
        expect(await overflow(page)).toBeLessThanOrEqual(0);
      }

      // Tryckytorna i toppraden och kortet är minst 44 punkter höga.
      const small = await page.evaluate(() =>
        [...document.querySelectorAll<HTMLElement>(".topbar button, .sheet button, .map-controls button")]
          .filter((node) => node.offsetParent !== null)
          .map((node) => ({ name: node.getAttribute("aria-label") ?? node.textContent, ...node.getBoundingClientRect().toJSON() }))
          .filter((box: { height: number; width: number }) => box.height < 44 || box.width < 44)
          .map((box: { name: string | null; width: number; height: number }) => `${box.name} ${box.width}×${box.height}`),
      );
      expect(small).toEqual([]);
    });
  }

  test("skrollar inte i sidled medan ett rumskort flyttas eller rummet ändras", async ({ page }) => {
    await openPage(page);
    await page.locator(".sheet li button").first().tap();
    await page.getByRole("button", { name: "Flytta", exact: true }).tap();
    await expect(page.locator("#app")).toHaveAttribute("data-mode", "move");
    expect(await overflow(page)).toBeLessThanOrEqual(0);
    await page.locator(".topbar").getByRole("button", { name: "Avbryt" }).tap();
    await expect(page.locator("#app")).toHaveAttribute("data-mode", "view");

    await page.getByRole("button", { name: "Tillbaka till rummet" }).tap();
    await page.getByRole("button", { name: "Ändra rummet" }).tap();
    await expect(page.locator("#app")).toHaveAttribute("data-mode", "edit");
    expect(await overflow(page)).toBeLessThanOrEqual(0);
    await page.getByRole("button", { name: "Klar" }).tap();
  });
});
