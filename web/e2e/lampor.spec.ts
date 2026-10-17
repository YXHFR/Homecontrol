import { expect, type Page, test } from "@playwright/test";
import { atMeters, groundFloor, mapCanvas, openPage, savedHome } from "./helpers";

interface LiveState {
  lamps: { id: string; on: boolean | null }[];
  rooms: { id: string; manual: boolean; status: string }[];
  simulation: { daylight: number | null; persons: { id: string }[] } | null;
}

/** Läget som kärnan skickar i realtidsströmmen just nu. */
async function liveState(page: Page): Promise<LiveState> {
  return page.evaluate(
    () =>
      new Promise<LiveState>((resolve, reject) => {
        const socket = new WebSocket(`ws://${location.host}/api/live`);
        socket.addEventListener("message", (event) => {
          const message = JSON.parse(String(event.data)) as { type: string } & LiveState;
          if (message.type === "state") {
            socket.close();
            resolve(message);
          }
        });
        socket.addEventListener("error", () => reject(new Error("Ingen kontakt med kärnan")));
      }),
  );
}

/** Skickar ett meddelande till kärnan i realtidsströmmen, som sidan själv gör. */
async function sendLive(page: Page, message: Record<string, unknown>): Promise<void> {
  await page.evaluate(
    (body) =>
      new Promise<void>((resolve, reject) => {
        const socket = new WebSocket(`ws://${location.host}/api/live`);
        socket.addEventListener("open", () => {
          socket.send(JSON.stringify(body));
          socket.close();
          resolve();
        });
        socket.addEventListener("error", () => reject(new Error("Ingen kontakt med kärnan")));
      }),
    message,
  );
}

const lampOn = async (page: Page, id: string) => (await liveState(page)).lamps.find((l) => l.id === id)?.on;
const manual = async (page: Page, id: string) => (await liveState(page)).rooms.find((r) => r.id === id)?.manual;

test("köksets taklampa tänds och släcks med den stora knappen i lampans kort och med snabbknappen", async ({
  page,
}) => {
  const floor = groundFloor(await savedHome(page));
  await openPage(page);
  const map = mapCanvas(page);
  expect(await lampOn(page, "kok-tak")).toBe(false);

  try {
    // Lampans kort: PÅ och sedan AV. Köket väljs inte först; lampan tar med sig sitt rum.
    const lamp = await atMeters(map, floor, { x: 4, y: 7.5 });
    await page.mouse.click(lamp.x, lamp.y);
    await expect(page.locator(".card-head h2")).toHaveText("Taklampa");
    const power = page.locator(".power.big");
    await expect(power).toHaveAttribute("aria-pressed", "false");
    await power.click();
    await expect(power).toHaveAttribute("aria-pressed", "true");
    await expect.poll(() => lampOn(page, "kok-tak")).toBe(true);
    await power.click();
    await expect(power).toHaveAttribute("aria-pressed", "false");
    await expect.poll(() => lampOn(page, "kok-tak")).toBe(false);

    // ‹ Rummet leder till lampans rum. Där tänder och släcker snabbknappen samma lampa.
    await page.getByRole("button", { name: "Tillbaka till rummet" }).click();
    await expect(page.locator(".card-head h2")).toHaveText("Kök");
    const quick = page.locator(".lamp-row", { hasText: "Taklampa" }).getByRole("button", { name: "Taklampa" });
    await quick.click();
    await expect(quick).toHaveAttribute("aria-pressed", "true");
    await expect.poll(() => lampOn(page, "kok-tak")).toBe(true);
    await quick.click();
    await expect(quick).toHaveAttribute("aria-pressed", "false");
    await expect.poll(() => lampOn(page, "kok-tak")).toBe(false);
    // Att styra lampan för hand pausar automatiken i köket.
    expect(await manual(page, "kok")).toBe(true);
  } finally {
    await sendLive(page, { type: "resume_automation", room_id: "kok" });
    await expect.poll(() => manual(page, "kok")).toBe(false);
  }
});

test("ett tryck på en tänd lampa på kartan släcker den inte och pausar inte automatiken", async ({ page }) => {
  const floor = groundFloor(await savedHome(page));
  const hall = floor.rooms.find((room) => room.id === "hall");
  const node = hall?.nodes?.[0];
  if (node === undefined) {
    throw new Error("Exempelhemmets hall saknar rumskort");
  }
  await openPage(page);
  const map = mapCanvas(page);
  const before = await liveState(page);
  const daylight = page.getByRole("slider", { name: "Dagsljus" });

  try {
    // Mörkt ute och en person framför hallens rumskort: automatiken tänder hallens taklampa.
    await daylight.fill("0");
    await expect.poll(async () => (await liveState(page)).simulation?.daylight).toBe(0);
    const angle = (node.rotation * Math.PI) / 180;
    await sendLive(page, {
      type: "add_person",
      floor_id: floor.id,
      x: node.position[0] + 1.0 * Math.cos(angle),
      y: node.position[1] + 1.0 * Math.sin(angle),
    });
    await expect.poll(() => lampOn(page, "hall-tak"), { timeout: 15_000 }).toBe(true);
    expect(await manual(page, "hall")).toBe(false);

    // Tre tryck på lampan på kartan öppnar dess kort men ändrar inget.
    const lamp = await atMeters(map, floor, { x: 2.5, y: 3.0 });
    for (let i = 0; i < 3; i++) {
      await page.mouse.click(lamp.x, lamp.y);
    }
    await expect(page.locator(".card-head h2")).toHaveText("Taklampa");
    await expect(page.locator(".power.big")).toHaveAttribute("aria-pressed", "true");
    // Ge kärnan tid att svara på något sidan kanske skickade.
    await page.waitForTimeout(1_000);
    expect(await lampOn(page, "hall-tak")).toBe(true);
    expect(await manual(page, "hall")).toBe(false);
  } finally {
    for (const person of (await liveState(page)).simulation?.persons ?? []) {
      await sendLive(page, { type: "remove_person", person_id: person.id });
    }
    await daylight.fill(String(before.simulation?.daylight ?? 0));
    // Hallen räknas som upptagen en stund efter att personen gått; nästa test börjar med en tom hall.
    await expect
      .poll(async () => (await liveState(page)).rooms.find((r) => r.id === "hall")?.status, { timeout: 20_000 })
      .toBe("tomt");
  }
});
