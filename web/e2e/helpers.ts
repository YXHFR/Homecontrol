import { expect, type Locator, type Page } from "@playwright/test";
import { deflateSync } from "node:zlib";

export interface HomeDocument {
  home: {
    floors: {
      id: string;
      level: number;
      rooms: { id: string; name: string; polygon: [number, number][] }[];
      plan_image?: { position: [number, number]; meters_per_pixel: number } | null;
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

export async function openEditor(page: Page): Promise<void> {
  if (page.url().endsWith("/#redigera")) {
    await page.reload(); // samma adress laddas inte om av goto
  } else {
    await page.goto("/#redigera");
  }
  await expect(page.locator("#app")).toHaveAttribute("data-ready", "true");
  savesSeen.set(page, 0);
}

/** Väntar tills kärnan har tagit emot nästa ändring. */
export async function waitUntilSaved(page: Page): Promise<void> {
  const next = (savesSeen.get(page) ?? 0) + 1;
  await expect(page.locator(".message"), "kärnan sa nej").toBeHidden();
  await expect(page.locator("#app")).toHaveAttribute("data-saves", String(next));
  await expect(page.getByRole("status")).toHaveText("Alla ändringar är sparade.");
  savesSeen.set(page, next);
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
