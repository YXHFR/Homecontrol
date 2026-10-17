import { expect, test, type Page } from "@playwright/test";
import { groundFloor, mapCanvas, onMap, openEditor, savedHome, waitUntilSaved } from "./helpers";

/** Det första hemmeddelandet som sidan får från kärnans realtidsström. */
async function liveHome(page: Page): Promise<{
  floors: { rooms: { id: string; zones: { id: string; name: string; kind: string }[] }[] }[];
}> {
  return page.evaluate(
    () =>
      new Promise((resolve, reject) => {
        const socket = new WebSocket(`ws://${location.host}/api/live`);
        socket.addEventListener("message", (event) => {
          const message = JSON.parse(String(event.data)) as { type: string };
          if (message.type === "home") {
            socket.close();
            resolve(message as never);
          }
        });
        socket.addEventListener("error", () => reject(new Error("Ingen kontakt med realtidsströmmen")));
      }),
  );
}

test("en zon ritas i ett rum, byter namn och sort, når realtidsströmmen och tas bort", async ({
  page,
}) => {
  await openEditor(page);
  const map = mapCanvas(page);
  // Ett nytt rum i tom yta bredvid exempelhemmet, att rita zonen i.
  await page.getByRole("button", { name: "Zooma ut" }).click();
  await page.getByRole("button", { name: "Zooma ut" }).click();
  await page.getByRole("button", { name: "Rum, rektangel" }).click();
  const from = await onMap(map, 0.8, 0.66);
  const to = await onMap(map, 0.97, 0.9);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(to.x, to.y, { steps: 5 });
  await page.mouse.up();
  await waitUntilSaved(page);
  const roomId = groundFloor(await savedHome(page)).rooms.at(-1)?.id;

  await page.getByRole("button", { name: "Ignorera-zon" }).click();
  await expect(page.locator(".instruction")).toContainText("Störkällor");
  const corners: [number, number][] = [
    [0.84, 0.7],
    [0.93, 0.7],
    [0.93, 0.82],
    [0.84, 0.82],
  ];
  for (const [fx, fy] of corners) {
    const point = await onMap(map, fx, fy);
    await page.mouse.click(point.x, point.y);
  }
  const first = await onMap(map, ...corners[0]);
  await page.mouse.click(first.x, first.y);
  await waitUntilSaved(page);

  const zoneOf = async () =>
    groundFloor(await savedHome(page)).rooms.find((room) => room.id === roomId)?.zones?.[0];
  const zone = await zoneOf();
  expect(zone).toMatchObject({ kind: "ignorera" });
  expect(zone?.polygon).toHaveLength(4);
  await expect(page.getByRole("button", { name: "Ta bort zonen" })).toBeVisible();

  const name = page.getByRole("textbox", { name: "Namn" });
  await name.fill("Fläkt");
  await name.press("Enter");
  await waitUntilSaved(page);
  await page.getByRole("combobox", { name: "Sort" }).selectOption({ label: "Stillhetszon" });
  await waitUntilSaved(page);
  expect(await zoneOf()).toMatchObject({ id: zone?.id, name: "Fläkt", kind: "stillhet" });

  // Realtidsströmmen får zonen av kärnan.
  const live = await liveHome(page);
  const liveZones = live.floors.flatMap((floor) => floor.rooms).find((r) => r.id === roomId);
  expect(liveZones?.zones).toEqual([
    expect.objectContaining({ id: zone?.id, name: "Fläkt", kind: "stillhet" }),
  ]);

  // Välj bort zonen och välj den igen på kartan.
  await page.getByRole("button", { name: "Välj och flytta" }).click();
  const outside = await onMap(map, 0.5, 0.03);
  await page.mouse.click(outside.x, outside.y);
  await expect(page.locator(".selection")).toContainText("Inget valt");
  const middle = await onMap(map, 0.885, 0.76);
  await page.mouse.click(middle.x, middle.y);
  await expect(page.getByRole("textbox", { name: "Namn" })).toHaveValue("Fläkt");

  await page.getByRole("button", { name: "Ta bort zonen" }).click();
  await waitUntilSaved(page);
  expect(await zoneOf()).toBeUndefined();

  // Städa: välj rummet utanför där zonen var och ta bort det.
  const inRoom = await onMap(map, 0.95, 0.87);
  await page.mouse.click(inRoom.x, inRoom.y);
  await page.getByRole("button", { name: "Ta bort rummet" }).click();
  await waitUntilSaved(page);
});
