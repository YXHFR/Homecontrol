import { expect, test } from "@playwright/test";
import {
  atMeters,
  groundFloor,
  onMap,
  openEditor,
  png,
  savedHome,
  waitUntilSaved,
} from "./helpers";

test("ett rum som dras ut som rektangel sparas, byter namn, flyttas och tas bort", async ({ page }) => {
  await openEditor(page);
  const before = groundFloor(await savedHome(page)).rooms.length;
  const map = page.locator(".editor-stage canvas").first();

  await page.getByRole("button", { name: "Rum, rektangel" }).click();
  await expect(page.locator(".instruction")).toContainText("dra till hörnet snett emot");
  // Zooma ut så att det finns tom yta bredvid exempelhemmet.
  await page.getByRole("button", { name: "−" }).click();
  await page.getByRole("button", { name: "−" }).click();
  const from = await onMap(map, 0.82, 0.8);
  const to = await onMap(map, 0.96, 0.96);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(to.x, to.y, { steps: 5 });
  await page.mouse.up();
  await waitUntilSaved(page);

  const added = groundFloor(await savedHome(page)).rooms;
  expect(added).toHaveLength(before + 1);
  const room = added[added.length - 1];
  expect(room.polygon).toHaveLength(4);
  const name = page.getByRole("textbox", { name: "Namn" });
  await expect(name).toHaveValue(room.name);

  await name.fill("Förråd");
  await name.press("Enter");
  await waitUntilSaved(page);
  expect(groundFloor(await savedHome(page)).rooms.at(-1)?.name).toBe("Förråd");

  // Rummet finns i en nyöppnad redigerare, för det är kärnan som har det.
  const other = await page.context().newPage();
  await openEditor(other);
  await expect(other.locator("#app")).toHaveAttribute("data-rooms", String(before + 1));
  const allRooms = (await savedHome(page)).home.floors.flatMap((floor) => floor.rooms).length;
  await expect(other.locator(".summary")).toContainText(`Rum${allRooms}`);
  await other.close();

  // Välj bort rummet, välj det igen på kartan och ta bort det.
  await page.getByRole("button", { name: "Välj och flytta" }).click();
  const outside = await onMap(map, 0.5, 0.03);
  await page.mouse.click(outside.x, outside.y);
  await expect(page.locator(".selection")).toContainText("Inget valt");
  const middle = await onMap(map, 0.89, 0.88);
  await page.mouse.click(middle.x, middle.y);
  await expect(page.getByRole("textbox", { name: "Namn" })).toHaveValue("Förråd");

  // Dra det valda rummet åt vänster.
  const xBefore = groundFloor(await savedHome(page)).rooms.at(-1)?.polygon[0][0] ?? 0;
  await page.mouse.move(middle.x, middle.y);
  await page.mouse.down();
  await page.mouse.move(middle.x - 60, middle.y, { steps: 6 });
  await page.mouse.up();
  await waitUntilSaved(page);
  expect(groundFloor(await savedHome(page)).rooms.at(-1)?.polygon[0][0]).toBeLessThan(xBefore);

  await page.getByRole("button", { name: "Ta bort rummet" }).click();
  await waitUntilSaved(page);
  expect(groundFloor(await savedHome(page)).rooms).toHaveLength(before);
});

test("ett rum med fri form stängs genom att klicka på första hörnet", async ({ page }) => {
  await openEditor(page);
  const before = groundFloor(await savedHome(page)).rooms.length;
  const map = page.locator(".editor-stage canvas").first();
  await page.getByRole("button", { name: "−" }).click();
  await page.getByRole("button", { name: "−" }).click();

  await page.getByRole("button", { name: "Rum, fri form" }).click();
  await expect(page.locator(".instruction")).toContainText("klicka på det första hörnet");
  // Ett L-format rum.
  const corners: [number, number][] = [
    [0.8, 0.8],
    [0.96, 0.8],
    [0.96, 0.96],
    [0.88, 0.96],
    [0.88, 0.88],
    [0.8, 0.88],
  ];
  for (const [fx, fy] of corners) {
    const point = await onMap(map, fx, fy);
    await page.mouse.click(point.x, point.y);
  }
  const first = await onMap(map, ...corners[0]);
  await page.mouse.click(first.x, first.y);
  await waitUntilSaved(page);

  const rooms = groundFloor(await savedHome(page)).rooms;
  expect(rooms).toHaveLength(before + 1);
  expect(rooms.at(-1)?.polygon).toHaveLength(6);

  await page.getByRole("button", { name: "Ta bort rummet" }).click();
  await waitUntilSaved(page);
});

test("en bild av planritningen laddas upp och skalas med två punkter", async ({ page }) => {
  await openEditor(page);
  const map = page.locator(".editor-stage canvas").first();

  await page.locator('input[type="file"]').setInputFiles({
    name: "planritning.png",
    mimeType: "image/png",
    buffer: png(400, 300),
  });
  await waitUntilSaved(page);
  const uploaded = groundFloor(await savedHome(page)).plan_image;
  expect(uploaded?.meters_per_pixel).toBeCloseTo(20 / 400);
  await expect(page.getByRole("button", { name: "Skala bilden" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  const a = await onMap(map, 0.3, 0.5);
  const b = await onMap(map, 0.6, 0.5);
  await page.mouse.click(a.x, a.y);
  await page.mouse.click(b.x, b.y);
  const shown = await page.locator(".actions p").textContent();
  const metersBefore = Number(/är ([\d,]+) m/.exec(shown ?? "")?.[1].replace(",", "."));
  await page.getByRole("spinbutton", { name: "Avstånd i meter" }).fill("3");
  await page.getByRole("button", { name: "Skala", exact: true }).click();
  await waitUntilSaved(page);

  const scaled = groundFloor(await savedHome(page)).plan_image;
  expect(scaled?.meters_per_pixel).toBeCloseTo(((20 / 400) * 3) / metersBefore, 3);
  const image = await page.request.get(
    `/api/floors/${groundFloor(await savedHome(page)).id}/plan-image`,
  );
  expect(image.headers()["content-type"]).toBe("image/png");

  await page.getByRole("button", { name: "Ta bort bilden" }).click();
  await waitUntilSaved(page);
  expect(groundFloor(await savedHome(page)).plan_image).toBeNull();
});

test("ett rumskort placeras i ett rum, vrids med knappar och ring, får höjd och spegling", async ({
  page,
}) => {
  await openEditor(page);
  const floor = groundFloor(await savedHome(page));
  const room = floor.rooms[0];
  const nodesBefore = room.nodes?.length ?? 0;
  const map = page.locator(".editor-stage canvas").first();
  // En bit in från rummets övre vänstra hörn, där inget annat är placerat.
  const [left, top] = room.polygon[0];
  const spot = { x: left + 1, y: top + 1 };

  await page.getByRole("button", { name: "Rumskort (ESP32)" }).click();
  await expect(page.locator(".instruction")).toContainText("ringen eller knapparna");
  const at = await atMeters(map, floor, spot);
  await page.mouse.click(at.x, at.y);
  await waitUntilSaved(page);

  const placedNodes = () =>
    savedHome(page).then((home) => groundFloor(home).rooms.find((r) => r.id === room.id)?.nodes);
  const nodes = (await placedNodes()) ?? [];
  expect(nodes).toHaveLength(nodesBefore + 1);
  const node = nodes[nodes.length - 1];
  expect(node.position[0]).toBeCloseTo(spot.x, 1);
  expect(node.position[1]).toBeCloseTo(spot.y, 1);
  expect(node.rotation).toBe(0);
  const current = async () => (await placedNodes())?.find((n) => n.id === node.id);

  await page.getByRole("button", { name: "Vrid 15° medurs" }).click();
  await waitUntilSaved(page);
  expect((await current())?.rotation).toBe(15);

  // Med Välj och flytta syns ringen runt det valda kortet. Dra handtaget rakt nedåt.
  await page.getByRole("button", { name: "Välj och flytta" }).click();
  const ringRadius = 46;
  const handle = {
    x: at.x + ringRadius * Math.cos((15 * Math.PI) / 180),
    y: at.y + ringRadius * Math.sin((15 * Math.PI) / 180),
  };
  await page.mouse.move(handle.x, handle.y);
  await page.mouse.down();
  await page.mouse.move(at.x + 10, at.y + 40, { steps: 4 });
  await page.mouse.move(at.x, at.y + ringRadius, { steps: 4 });
  await page.mouse.up();
  await waitUntilSaved(page);
  expect((await current())?.rotation).toBeGreaterThanOrEqual(85);
  expect((await current())?.rotation).toBeLessThanOrEqual(95);

  const height = page.getByRole("spinbutton", { name: "Höjd över golvet (m)" });
  await height.fill("2.5");
  await height.press("Enter");
  await waitUntilSaved(page);
  await page.getByRole("checkbox", { name: "Spegelvänd sidled" }).check();
  await waitUntilSaved(page);
  expect(await current()).toMatchObject({ height: 2.5, mirrored: true });

  // Dra kortet en bit åt höger.
  await page.mouse.move(at.x, at.y);
  await page.mouse.down();
  await page.mouse.move(at.x + 30, at.y, { steps: 5 });
  await page.mouse.up();
  await waitUntilSaved(page);
  expect((await current())?.position[0]).toBeGreaterThan(node.position[0]);

  await page.getByRole("button", { name: "Ta bort rumskortet" }).click();
  await waitUntilSaved(page);
  expect(await current()).toBeUndefined();
});
