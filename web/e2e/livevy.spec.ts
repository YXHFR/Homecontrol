import { expect, test } from "@playwright/test";
import { atMeters, groundFloor, mapCanvas, openPage, savedHome } from "./helpers";

test("en simulerad person i hallen ger ett spår, och när den går hålls spåret kvar en stund", async ({
  page,
}) => {
  const hall = groundFloor(await savedHome(page)).rooms.find((room) => room.id === "hall");
  const node = hall?.nodes?.[0];
  if (node === undefined) {
    throw new Error("Exempelhemmets hall saknar rumskort");
  }
  // En och en halv meter rakt fram från hallens rumskort, mitt i radarns synfält.
  const angle = (node.rotation * Math.PI) / 180;
  const x = node.position[0] + 1.5 * Math.cos(angle);
  const y = node.position[1] + 1.5 * Math.sin(angle);

  await openPage(page);
  // Hallen är det första rummet, så dess kort visas när inget är valt.
  const hallCard = page.locator(".card-head", { hasText: "Hall" });
  await expect(hallCard).toHaveAttribute("data-status", "tomt");

  // Sidan drar personer med musen; här skickas samma meddelande som dragningen ger.
  await page.evaluate(
    ({ floorId, x, y }) =>
      new Promise<void>((resolve, reject) => {
        const socket = new WebSocket(`ws://${location.host}/api/live`);
        socket.addEventListener("open", () => {
          socket.send(JSON.stringify({ type: "add_person", floor_id: floorId, x, y }));
          socket.close();
          resolve();
        });
        socket.addEventListener("error", () => reject(new Error("Ingen kontakt med kärnan")));
      }),
    { floorId: groundFloor(await savedHome(page)).id, x, y },
  );
  await expect(hallCard).toHaveAttribute("data-status", "upptaget");

  // Personen försvinner mitt i hallen: rummet räknas som upptaget i cirka 10 sekunder till.
  await page.getByRole("button", { name: /Ta bort person/ }).click();
  await expect(page.locator(".persons li")).toHaveCount(0);
  await expect(hallCard).toHaveAttribute("data-status", "upptaget");
  await expect(hallCard).toHaveAttribute("data-status", "tomt", { timeout: 15_000 });
});

test("ett tryck på lampan på kartan öppnar kortet utan att tända; snabbknappen styr den för hand", async ({
  page,
}) => {
  const floor = groundFloor(await savedHome(page));
  const kitchen = floor.rooms.find((room) => room.id === "kok");
  if (kitchen === undefined) {
    throw new Error("Exempelhemmet saknar kök");
  }
  await openPage(page);
  const map = mapCanvas(page);

  // Ett tryck på köksgolvet visar köket.
  const floorSpot = await atMeters(map, floor, { x: 2.5, y: 6.5 });
  await page.mouse.click(floorSpot.x, floorSpot.y);
  await expect(page.locator(".card-head h2")).toHaveText("Kök");
  const automation = page.locator(".automation");
  await expect(automation).toHaveAttribute("data-manual", "false");

  // Ett tryck på lampan öppnar lampans kort och ändrar inget.
  const lamp = await atMeters(map, floor, { x: 4, y: 7.5 });
  await page.mouse.click(lamp.x, lamp.y);
  await expect(page.locator(".card-head h2")).toHaveText("Taklampa");
  await expect(page.locator(".power.big")).toHaveAttribute("aria-pressed", "false");
  await page.getByRole("button", { name: "Tillbaka till rummet" }).click();
  await expect(automation).toHaveAttribute("data-manual", "false");

  // Snabbknappen i rummets kort tänder lampan för hand och pausar automatiken.
  const quick = page.locator(".lamp-row", { hasText: "Taklampa" }).getByRole("button", { name: "Taklampa" });
  await quick.click();
  await expect(quick).toHaveAttribute("aria-pressed", "true");
  await expect(automation).toHaveAttribute("data-manual", "true");
  await expect(automation).toContainText("Manuellt läge");

  // Automatiken tar över igen direkt, utan att vänta tills köket varit tomt.
  await automation.getByRole("button", { name: "Automatik igen" }).click();
  await expect(automation).toHaveAttribute("data-manual", "false");
});

test("lampkalibreringen mäter hallens taklampa och visar dess ljus", async ({ page }) => {
  await openPage(page);
  // Kalibreringen följer kärnans klocka, så i 60× tar den ungefär en sekund.
  await page.getByRole("button", { name: "60×" }).click();

  await page.getByRole("button", { name: "Mät lampornas ljus" }).click();
  const guide = page.getByRole("region", { name: "Lampkalibrering" });
  await expect(guide.getByRole("heading")).toHaveText("Lampkalibrering: Hall");
  await guide.getByRole("button", { name: "Starta kalibreringen" }).click();

  await expect(guide).toContainText("Lampkalibreringen i Hall är klar", { timeout: 15_000 });
  // Den virtuella taklampan sitter drygt två meter från hallens rumskort.
  await expect(guide.locator("li")).toHaveText([/^Taklampa: 1\d(,\d)? lux$/]);
  await expect(guide.getByRole("button", { name: "Kalibrera igen" })).toBeVisible();

  await page.getByRole("button", { name: "1×" }).click();
});
