import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { hasDoorbell, mountDoor, POLL_MS, timeLeft, type DoorApi, type DoorState } from "./door";

class FakeDoor implements DoorApi {
  current: DoorState | null = { camera: true, open_seconds: 0 };
  shows = 0;
  async state() {
    return this.current;
  }
  async show() {
    this.shows += 1;
    this.current = { camera: true, open_seconds: 120 };
    return this.current;
  }
  streamUrl() {
    return "/api/dorr/strom";
  }
}

async function flush() {
  await vi.advanceTimersByTimeAsync(0);
}

function image(root: HTMLElement): HTMLImageElement {
  return root.querySelector("img.door-stream") as HTMLImageElement;
}

function showButton(root: HTMLElement): HTMLButtonElement {
  const found = [...root.querySelectorAll("button")].find((b) => b.textContent === "Visa dörren");
  if (found === undefined) {
    throw new Error("Ingen knapp Visa dörren");
  }
  return found;
}

describe("sidan Dörren", () => {
  let root: HTMLElement;
  let close: () => void = () => {};

  beforeEach(() => {
    vi.useFakeTimers();
    root = document.createElement("main");
  });
  afterEach(() => {
    close();
    vi.useRealTimers();
  });

  it("visar ingen ström utan ring", async () => {
    close = mountDoor(root, new FakeDoor());
    await flush();

    expect(image(root).hidden).toBe(true);
    expect(image(root).getAttribute("src")).toBeNull();
    expect(root.textContent).toContain("Kameran är avstängd.");
  });

  it("visar strömmen i 2 minuter efter Visa dörren och tar sedan bort den", async () => {
    const door = new FakeDoor();
    close = mountDoor(root, door);
    await flush();

    showButton(root).click();
    await flush();

    expect(door.shows).toBe(1);
    expect(image(root).hidden).toBe(false);
    expect(image(root).src).toContain("/api/dorr/strom");
    expect(root.textContent).toContain("Kameran visas i 2:00 till.");

    door.current = { camera: true, open_seconds: 0 };
    await vi.advanceTimersByTimeAsync(120_000);

    expect(image(root).hidden).toBe(true);
    expect(image(root).getAttribute("src")).toBeNull();
  });

  it("börjar visa strömmen av sig själv när någon har ringt", async () => {
    const door = new FakeDoor();
    close = mountDoor(root, door);
    await flush();

    door.current = { camera: true, open_seconds: 115 };
    await vi.advanceTimersByTimeAsync(POLL_MS);

    expect(image(root).hidden).toBe(false);
    expect(root.textContent).toContain("1:55");
  });

  it("säger till när ingen dörrklocka är inställd", async () => {
    const door = new FakeDoor();
    door.current = null;
    close = mountDoor(root, door);
    await flush();

    expect(root.textContent).toContain("Ingen dörrklocka är inställd");
    expect(showButton(root).hidden).toBe(true);
    expect(await hasDoorbell(door)).toBe(false);
  });

  it("slutar hämta strömmen när sidan stängs", async () => {
    const door = new FakeDoor();
    close = mountDoor(root, door);
    await flush();
    showButton(root).click();
    await flush();

    close();

    expect(image(root).getAttribute("src")).toBeNull();
  });
});

describe("timeLeft", () => {
  it("skriver minuter och sekunder", () => {
    expect(timeLeft(120)).toBe("2:00");
    expect(timeLeft(65)).toBe("1:05");
    expect(timeLeft(0.2)).toBe("0:01");
    expect(timeLeft(-3)).toBe("0:00");
  });
});
