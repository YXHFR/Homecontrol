import { expect, type Page, test } from "@playwright/test";
import { groundFloor, openPage, savedHome } from "./helpers";

/**
 * Hälsoraden och kartan när något inte svarar. Simuleringen har inga riktiga
 * rumskort eller Zigbee-lampor, så testerna ändrar kärnans lägesmeddelanden på
 * vägen till sidan, som om hallens rumskort var ett riktigt kort som tystnat.
 */

type Change = (state: Record<string, unknown>) => Record<string, unknown>;

/** Låter `change` ändra varje lägesmeddelande från kärnan innan sidan får det. */
async function changeLiveState(page: Page, current: () => Change): Promise<void> {
  await page.routeWebSocket("**/api/live", (socket) => {
    const core = socket.connectToServer();
    core.onMessage((message) => {
      const parsed = JSON.parse(String(message)) as { type: string } & Record<string, unknown>;
      socket.send(JSON.stringify(parsed.type === "state" ? current()(parsed) : parsed));
    });
  });
}

/**
 * Kärnan skickar läget bara när något ändras. Dagsljuset ändras en lux fram
 * och tillbaka, så att sidan får ett nytt (ändrat) läge; `restore` ställer
 * tillbaka det som det var.
 */
async function nudger(page: Page): Promise<{ nudge(): Promise<void>; restore(): Promise<void> }> {
  const send = (lux: number) =>
    page.evaluate(
      (value) =>
        new Promise<void>((resolve, reject) => {
          const socket = new WebSocket(`ws://${location.host}/api/live`);
          socket.addEventListener("open", () => {
            socket.send(JSON.stringify({ type: "set_daylight", lux: value }));
            socket.close();
            resolve();
          });
          socket.addEventListener("error", () => reject(new Error("Ingen kontakt med kärnan")));
        }),
      lux,
    );
  const value = await page.getByRole("slider", { name: "Dagsljus" }).inputValue();
  const original = Number(value);
  let shifted = false;
  return {
    async nudge() {
      shifted = !shifted;
      await send(shifted ? original + 1 : original);
    },
    async restore() {
      await send(original);
    },
  };
}

/** Ett riktigt kort för rumskortet `id`, placerat på kartan. */
function card(id: string, online: boolean) {
  return {
    id,
    name: "Homecontrol hall",
    placed: true,
    online,
    problems: [],
    calibrating: false,
    wifi_signal: online ? -60 : null,
    firmware_version: "1.0.0",
    update: null,
  };
}

/** Fyllnadsfärgerna på cirklarna i kartans grupp för saken `id`. */
async function circleFills(page: Page, id: string): Promise<string[]> {
  return page.evaluate((itemId) => {
    interface KonvaNode {
      getClassName(): string;
      fill(): unknown;
      find(selector: string): KonvaNode[];
    }
    const konva = (window as unknown as { Konva?: { stages: { findOne(s: string): KonvaNode | undefined }[] } })
      .Konva;
    const group = konva?.stages.map((stage) => stage.findOne(`#${itemId}`)).find((node) => node !== undefined);
    if (group === undefined) {
      throw new Error(`${itemId} finns inte på kartan`);
    }
    return group
      .find("Circle")
      .map((circle) => circle.fill())
      .filter((fill): fill is string => typeof fill === "string");
  }, id);
}

/** Färgen för "fungerar inte" i det läge sidan visas i. */
function badColor(page: Page): Promise<string> {
  return page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--bad").trim());
}

test("ett rumskort som inte svarar syns i hälsoraden och på kartan, och ett tryck öppnar dess kort", async ({
  page,
}) => {
  const hall = groundFloor(await savedHome(page)).rooms.find((room) => room.id === "hall");
  const node = hall?.nodes?.[0];
  if (node === undefined) {
    throw new Error("Exempelhemmets hall saknar rumskort");
  }
  let online: boolean | null = null;
  await changeLiveState(page, () => (state) =>
    online === null ? state : { ...state, cards: [card(node.id, online)] },
  );
  await openPage(page);
  const health = page.locator(".topbar .health");

  // Allt svarar i simuleringen, och kortet ritas utan röd prick.
  await expect(health).toHaveText("Allt fungerar");
  await expect(health).toHaveAttribute("data-level", "ok");
  const bad = await badColor(page);
  expect(bad).not.toBe("");
  expect(await circleFills(page, node.id)).not.toContain(bad);

  // Kortet tystnar.
  const daylight = await nudger(page);
  online = false;
  await daylight.nudge();
  await expect(health).toHaveText("Rumskortet svarar inte");
  await expect(health).toHaveAttribute("data-level", "bad");
  await expect.poll(() => circleFills(page, node.id)).toContain(bad);

  // Ett tryck på hälsoraden öppnar kortet och säger vad som är fel.
  await health.click();
  await expect(page.locator(".notice")).toContainText("Rumskortet svarar inte");
  await expect(page.getByRole("button", { name: "Flytta", exact: true })).toBeVisible();

  // Kortet svarar igen.
  online = true;
  await daylight.nudge();
  await expect(health).toHaveText("Allt fungerar");
  await expect.poll(() => circleFills(page, node.id)).not.toContain(bad);
  await daylight.restore();
});

test("hälsoraden säger Zigbee-stickan svarar inte när kärnan inte når MQTT-brokern", async ({ page }) => {
  let zigbee: { mqtt_connected: boolean; bridge_online: boolean | null; since: string } | null = null;
  await changeLiveState(page, () => (state) => (zigbee === null ? state : { ...state, zigbee }));
  await openPage(page);
  const health = page.locator(".topbar .health");
  await expect(health).toHaveText("Allt fungerar");

  const daylight = await nudger(page);
  zigbee = { mqtt_connected: false, bridge_online: null, since: "2026-10-17T19:42:00Z" };
  await daylight.nudge();
  await expect(health).toHaveText("Zigbee-stickan svarar inte");
  await expect(health).toHaveAttribute("data-level", "bad");

  zigbee = { mqtt_connected: true, bridge_online: true, since: "2026-10-17T19:43:00Z" };
  await daylight.nudge();
  await expect(health).toHaveText("Allt fungerar");
  await daylight.restore();
});
