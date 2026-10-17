import { expect, type Page, test } from "@playwright/test";
import { atMeters, groundFloor, mapCanvas, openPage, savedHome } from "./helpers";

interface LiveSimulation {
  enabled: boolean;
  daylight: number | null;
  persons: { id: string; floor_id: string; x: number; y: number }[];
}

/** Simuleringen som kärnan skickar i realtidsströmmen just nu. */
async function liveSimulation(page: Page): Promise<LiveSimulation> {
  return page.evaluate(
    () =>
      new Promise<LiveSimulation>((resolve, reject) => {
        const socket = new WebSocket(`ws://${location.host}/api/live`);
        socket.addEventListener("message", (event) => {
          const message = JSON.parse(String(event.data)) as { type: string; simulation?: LiveSimulation };
          if (message.type === "state" && message.simulation != null) {
            socket.close();
            resolve(message.simulation);
          }
        });
        socket.addEventListener("error", () => reject(new Error("Ingen kontakt med kärnan")));
      }),
  );
}

async function hallNode(page: Page) {
  const floor = groundFloor(await savedHome(page));
  const hall = floor.rooms.find((room) => room.id === "hall");
  const node = hall?.nodes?.[0];
  if (node === undefined) {
    throw new Error("Exempelhemmets hall saknar rumskort");
  }
  const current = async () =>
    groundFloor(await savedHome(page))
      .rooms.flatMap((room) => room.nodes ?? [])
      .find((candidate) => candidate.id === node.id);
  return { floor, node, current };
}

test("ett rumskort flyttas och vrids med Flytta; Ångra och Avbryt ställer tillbaka", async ({ page }) => {
  const { floor, node, current } = await hallNode(page);
  await openPage(page);
  const map = mapCanvas(page);
  const app = page.locator("#app");

  // Ett tryck på rumskortet öppnar dess kort, men flyttar inget.
  const at = await atMeters(map, floor, { x: node.position[0], y: node.position[1] });
  await page.mouse.click(at.x, at.y);
  await expect(page.locator(".card-head h2")).toHaveText(node.name);
  await expect(app).toHaveAttribute("data-mode", "view");

  await page.getByRole("button", { name: "Flytta", exact: true }).click();
  await expect(app).toHaveAttribute("data-mode", "move");
  await expect(page.locator(".topbar .title")).toHaveText(`Flyttar ${node.name}`);
  await expect(page.locator(".topbar").getByRole("button", { name: "Avbryt" })).toBeVisible();
  await expect(page.locator(".topbar").getByRole("button", { name: "Klar" })).toBeVisible();

  // +15° sparas direkt, och Ångra ställer tillbaka vridningen.
  await page.getByRole("button", { name: "Vrid 15° medurs" }).click();
  await expect.poll(async () => (await current())?.rotation).toBe((node.rotation + 15) % 360);
  const toast = page.locator(".toast");
  await expect(toast).toContainText("Sparat");
  await toast.getByRole("button", { name: "Ångra" }).click();
  await expect.poll(async () => (await current())?.rotation).toBe(node.rotation);

  // Piltangenterna flyttar 5 cm åt gången.
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await expect
    .poll(async () => (await current())?.position[0])
    .toBeCloseTo(node.position[0] + 0.1, 5);

  // Dra kortet en bit nedåt; släppet sparas direkt.
  const now = (await current())?.position ?? node.position;
  const from = await atMeters(map, floor, { x: now[0], y: now[1] });
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(from.x, from.y + 30, { steps: 5 });
  await page.mouse.up();
  await expect.poll(async () => (await current())?.position[1]).toBeGreaterThan(node.position[1] + 0.05);

  // Avbryt ställer tillbaka allt till hur det var innan Flytta trycktes.
  await page.locator(".topbar").getByRole("button", { name: "Avbryt" }).click();
  await expect(app).toHaveAttribute("data-mode", "view");
  await expect.poll(async () => (await current())?.position).toEqual(node.position);
  expect((await current())?.rotation).toBe(node.rotation);
});

test("en låtsasperson läggs till, dras in i hallen och tas bort; dagsljuset ändras", async ({ page }) => {
  const { floor, node } = await hallNode(page);
  await openPage(page);
  const map = mapCanvas(page);
  expect((await liveSimulation(page)).enabled).toBe(true);
  const hallCard = page.locator(".card-head", { hasText: "Hall" });
  await expect(hallCard).toHaveAttribute("data-status", "tomt");

  // Dagsljuset skickas till kärnan och visas bredvid reglaget.
  const daylight = page.getByRole("slider", { name: "Dagsljus" });
  const before = (await liveSimulation(page)).daylight ?? 0;
  const next = before === 200 ? 100 : 200;
  await daylight.fill(String(next));
  await expect(page.locator(".sim-strip output")).toHaveText(`${next} lux`);
  await expect.poll(async () => (await liveSimulation(page)).daylight).toBe(next);

  // + Person lägger personen bredvid rummen.
  await page.getByRole("button", { name: "Lägg till person" }).click();
  await expect(page.locator(".persons li")).toHaveCount(1);
  await expect.poll(async () => (await liveSimulation(page)).persons.length).toBe(1);
  const person = (await liveSimulation(page)).persons[0];

  // Dra personen till en och en halv meter rakt fram från hallens rumskort.
  const angle = (node.rotation * Math.PI) / 180;
  const target = {
    x: node.position[0] + 1.5 * Math.cos(angle),
    y: node.position[1] + 1.5 * Math.sin(angle),
  };
  const from = await atMeters(map, floor, person);
  const to = await atMeters(map, floor, target);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(to.x, to.y, { steps: 8 });
  await page.mouse.up();
  await expect(hallCard).toHaveAttribute("data-status", "upptaget");
  const moved = (await liveSimulation(page)).persons[0];
  expect(moved.x).toBeCloseTo(target.x, 0);
  expect(moved.y).toBeCloseTo(target.y, 0);
  // Att dra en person är inte att flytta något i hemmet.
  await expect(page.locator("#app")).toHaveAttribute("data-mode", "view");

  await page.getByRole("button", { name: /Ta bort person/ }).click();
  await expect(page.locator(".persons li")).toHaveCount(0);
  await daylight.fill(String(before));
  await expect.poll(async () => (await liveSimulation(page)).daylight).toBe(before);
});

test("sidan har ingen Kartredigerare, våningsväljare eller händelselista", async ({ page }) => {
  await openPage(page);
  await expect(page.getByText("Varför hände det")).toHaveCount(0);
  await expect(page.getByRole("link", { name: /Kartredigerare/ })).toHaveCount(0);
  await expect(page.getByRole("combobox")).toHaveCount(0);
  // Den gamla sidan för kartredigeraren finns inte längre.
  const response = await page.request.get("/editor.html");
  expect(response.status() === 404 || !(await response.text()).includes("Kartredigerare")).toBe(true);
});
