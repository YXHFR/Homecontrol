import { expect, type Locator, type Page } from "@playwright/test";
import { deflateSync } from "node:zlib";

export interface HomeDocument {
  home: {
    floors: {
      id: string;
      name: string;
      level: number;
      rooms: {
        id: string;
        name: string;
        polygon: [number, number][];
        zones?: { id: string; name: string; kind: string; polygon: [number, number][] }[];
        nodes?: {
          id: string;
          name: string;
          position: [number, number];
          height: number;
          rotation: number;
          mirrored: boolean;
        }[];
      }[];
      plan_image?: { position: [number, number]; meters_per_pixel: number } | null;
      furniture?: { name: string; polygon: [number, number][] }[];
    }[];
  };
}

/** Hemmet som kärnan har sparat just nu. */
export async function savedHome(page: Page): Promise<HomeDocument> {
  const response = await page.request.get("/api/home");
  expect(response.ok()).toBe(true);
  return (await response.json()) as HomeDocument;
}

export function groundFloor(document: HomeDocument) {
  return document.home.floors.find((floor) => floor.level === 0) ?? document.home.floors[0];
}

/** Hur många ändringar sidan har sparat sedan den öppnades. */
const savesSeen = new WeakMap<Page, number>();

/** Öppnar sidan och väntar tills den har läst hemmet. */
export async function openPage(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.locator("#app")).toHaveAttribute("data-ready", "true");
  savesSeen.set(page, 0);
}

/** Öppnar sidan och ritverktygen (Ändra rummet). */
export async function openEditor(page: Page): Promise<void> {
  await openPage(page);
  await page.getByRole("button", { name: "Ändra rummet" }).click();
  await expect(page.locator("#app")).toHaveAttribute("data-mode", "edit");
}

/** Väntar tills kärnan har tagit emot nästa ändring. */
export async function waitUntilSaved(page: Page): Promise<void> {
  const next = (savesSeen.get(page) ?? 0) + 1;
  await expect(page.locator(".message"), "kärnan sa nej").toBeHidden();
  await expect(page.locator("#app")).toHaveAttribute("data-saves", String(next));
  await expect(page.locator("#app")).toHaveAttribute("data-busy", "false");
  await expect(page.locator(".save-status")).toHaveText("Alla ändringar är sparade.");
  savesSeen.set(page, next);
}

/** Kartan på sidan. */
export function mapCanvas(page: Page): Locator {
  return page.locator(".map-stage canvas").first();
}

/** En punkt på kartan, som andel av kartans bredd och höjd (0–1). */
export async function onMap(
  map: Locator,
  fx: number,
  fy: number,
): Promise<{ x: number; y: number }> {
  const box = await map.boundingBox();
  if (box === null) {
    throw new Error("Kartan syns inte");
  }
  return { x: box.x + box.width * fx, y: box.y + box.height * fy };
}

/**
 * Var en punkt i hemmets meter syns på kartan, när kartan visar hela
 * våningen som när den öppnas (samma uträkning som `fit` i mapView.ts).
 */
export async function atMeters(
  map: Locator,
  floor: HomeDocument["home"]["floors"][number],
  point: { x: number; y: number },
): Promise<{ x: number; y: number }> {
  const box = await map.boundingBox();
  if (box === null) {
    throw new Error("Kartan syns inte");
  }
  const corners = floor.rooms.flatMap((room) => room.polygon);
  const xs = corners.map(([x]) => x);
  const ys = corners.map(([, y]) => y);
  const [minX, maxX, minY, maxY] = [
    Math.min(...xs),
    Math.max(...xs),
    Math.min(...ys),
    Math.max(...ys),
  ];
  const margin = 1.5;
  const perMeter = Math.min(
    box.width / (maxX - minX + 2 * margin),
    box.height / (maxY - minY + 2 * margin),
  );
  return {
    x: box.x + (box.width - (maxX + minX) * perMeter) / 2 + point.x * perMeter,
    y: box.y + (box.height - (maxY + minY) * perMeter) / 2 + point.y * perMeter,
  };
}

/** En enfärgad PNG-bild, width × height bildpunkter. */
export function png(width: number, height: number): Buffer {
  const crcTable = Array.from({ length: 256 }, (_, n) => {
    let c = n;
    for (let k = 0; k < 8; k++) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    return c >>> 0;
  });
  const crc = (data: Buffer) => {
    let c = 0xffffffff;
    for (const byte of data) {
      c = crcTable[(c ^ byte) & 0xff] ^ (c >>> 8);
    }
    return (c ^ 0xffffffff) >>> 0;
  };
  const chunk = (type: string, data: Buffer) => {
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const sum = Buffer.alloc(4);
    sum.writeUInt32BE(crc(body));
    return Buffer.concat([length, body, sum]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header.set([8, 2, 0, 0, 0], 8); // 8 bitar, RGB
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(width * 3, 0xdd)]);
  const pixels = Buffer.concat(Array.from({ length: height }, () => row));
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(pixels)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}
