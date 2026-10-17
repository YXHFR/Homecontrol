import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { openEditor, savedHome, waitUntilSaved } from "./helpers";

/** Den påhittade tvåan som också README pekar på. */
const EXAMPLE_SCAN = fileURLToPath(new URL("../exempel/roomplan-exempel.json", import.meta.url));

test("en iPhone-skanning blir en ny våning med rum, dörröppningar, fönster och möbler", async ({
  page,
}) => {
  // Testerna delar hem, så våningen som läggs till tas bort igen efteråt.
  const before = await page.request.get("/api/home");
  const original: unknown = await before.json();
  try {
    await openEditor(page);
    await page.locator('input[accept=".json,application/json"]').setInputFiles(EXAMPLE_SCAN);
    await expect(page.locator(".instruction")).toContainText("Så här blir skanningen");
    await expect(page.locator(".edit-panel")).toContainText(
      "2 rum, 2 dörröppningar, 2 fönster och 6 möbler",
    );
    await page.getByRole("button", { name: "Vrid 90°" }).click();
    await page.getByRole("spinbutton", { name: "Från vänster" }).fill("2");
    await page.getByRole("spinbutton", { name: "Från vänster" }).press("Tab");
    await page.getByRole("button", { name: "Läs in", exact: true }).click();
    await waitUntilSaved(page);

    const floors = (await savedHome(page)).home.floors;
    const scanned = floors.find((floor) => floor.name === "Skannad våning");
    expect(scanned?.level).toBe(Math.max(...floors.map((floor) => floor.level)));
    expect(scanned?.rooms.map((room) => room.name)).toEqual(["Vardagsrum", "Sovrum"]);
    expect(scanned?.furniture).toHaveLength(6);
    // Vriden ett kvarts varv är vardagsrummet 4 m brett, och det börjar 2 m från vänster.
    const xs = scanned?.rooms[0].polygon.map(([x]) => x) ?? [];
    expect(Math.min(...xs)).toBeCloseTo(2, 1);
    expect(Math.max(...xs) - Math.min(...xs)).toBeCloseTo(4, 1);
    // Den nya våningen visas på kartan.
    await expect(page.locator("#app")).toHaveAttribute("data-rooms", "2");
  } finally {
    const now = await page.request.get("/api/home");
    await page.request.put("/api/home", {
      data: original,
      headers: { "If-Match": now.headers().etag ?? "" },
    });
  }
});
