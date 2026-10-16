import { expect, test } from "@playwright/test";
import { groundFloor, onMap, openEditor, savedHome, waitUntilSaved } from "./helpers";

test("på mobilen staplas panelerna och ett rum kan ritas med fingret", async ({ page }) => {
  await openEditor(page);
  const before = groundFloor(await savedHome(page)).rooms.length;
  const toolbox = page.getByRole("complementary", { name: "Verktyg" });
  const map = page.locator(".editor-stage canvas").first();

  // Verktygen ligger ovanför kartan och egenskaperna under, utan att sidan blir bredare än skärmen.
  const [toolsBox, mapBox, propertiesBox] = await Promise.all([
    toolbox.boundingBox(),
    map.boundingBox(),
    page.locator(".properties").boundingBox(),
  ]);
  if (toolsBox === null || mapBox === null || propertiesBox === null) {
    throw new Error("Panelerna syns inte");
  }
  expect(toolsBox.y).toBeLessThan(mapBox.y);
  expect(propertiesBox.y).toBeGreaterThan(mapBox.y + mapBox.height - 1);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);

  await page.getByRole("button", { name: "−" }).tap();
  await page.getByRole("button", { name: "−" }).tap();
  await page.getByRole("button", { name: "Rum, fri form" }).tap();
  // Som en användare: rulla så att hela kartan syns innan man ritar.
  await map.evaluate((canvas) => canvas.scrollIntoView({ block: "end" }));
  for (const [fx, fy] of [
    [0.7, 0.82],
    [0.95, 0.82],
    [0.95, 0.96],
    [0.7, 0.96],
    [0.7, 0.82],
  ] as const) {
    const point = await onMap(map, fx, fy);
    await page.touchscreen.tap(point.x, point.y);
  }
  await waitUntilSaved(page);

  expect(groundFloor(await savedHome(page)).rooms).toHaveLength(before + 1);
  await page.getByRole("button", { name: "Ta bort rummet" }).tap();
  await waitUntilSaved(page);
});
