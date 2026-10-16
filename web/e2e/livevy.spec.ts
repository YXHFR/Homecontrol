import { expect, test } from "@playwright/test";
import { groundFloor, savedHome } from "./helpers";

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

  await page.goto("/");
  await expect(page.locator("#app")).toHaveAttribute("data-ready", "true");
  const hallNow = page.locator(".rooms-now li", { hasText: "Hall:" });
  await expect(hallNow).toHaveAttribute("data-status", "tomt");

  // Livevyn drar personer med musen; här skickas samma meddelande som dragningen ger.
  await page.evaluate(
    ({ floorId, x, y }) =>
      new Promise<void>((resolve, reject) => {
        const socket = new WebSocket(`ws://${location.host}/api/live`);
        socket.addEventListener("open", () => {
          socket.send(JSON.stringify({ type: "add_person", floor_id: floorId, x, y }));
          socket.close();
          resolve();
        });
        socket.addEventListener("error", () => reject(new Error("Ingen kontakt med livevyn")));
      }),
    { floorId: groundFloor(await savedHome(page)).id, x, y },
  );
  await expect(hallNow).toHaveAttribute("data-status", "upptaget");

  // Personen försvinner mitt i hallen: rummet räknas som upptaget i cirka 10 sekunder till.
  await page.getByRole("button", { name: /Ta bort person/ }).click();
  await expect(page.locator(".simulation li")).toHaveCount(0);
  await expect(hallNow).toHaveAttribute("data-status", "upptaget");
  await expect(hallNow).toHaveAttribute("data-status", "tomt", { timeout: 15_000 });
});
