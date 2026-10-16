import { describe, expect, it, vi } from "vitest";
import { mountEditor, type EditorDependencies } from "./editor";
import type { EditorScene, EditorViewCallbacks } from "./editorView";
import { setPlanImage } from "./editing";
import { CoreError, type HomeApi } from "./homeApi";
import type { HomeDocument } from "./homefile";

function home(): HomeDocument {
  return {
    format: "homecontrol-hem",
    version: 1,
    home: {
      name: "Hemmet",
      floors: [
        {
          id: "plan",
          name: "Plan 1",
          level: 0,
          rooms: [
            {
              id: "kok",
              name: "Kök",
              polygon: [
                [0, 0],
                [3, 0],
                [3, 2],
                [0, 2],
              ],
              walls: ["gips", "gips", "gips", "gips"],
              lamps: [{ position: [1, 1] }],
            },
          ],
        },
      ],
    },
  };
}

/** En låtsad kärna som håller hemmet och dess revision, och kan säga nej på beställning. */
class FakeCore {
  document = home();
  revision = 1;
  images = new Map<string, Blob>();
  saved: HomeDocument[] = [];
  /** Svar att ge på de kommande anropen som ändrar något, i tur och ordning. */
  failures: (CoreError | null)[] = [];

  private change(revision: string, apply: () => void): Promise<string> {
    const failure = this.failures.shift() ?? null;
    if (failure !== null) {
      return Promise.reject(failure);
    }
    if (revision !== String(this.revision)) {
      return Promise.reject(new CoreError("Hemmet har ändrats i ett annat fönster.", true));
    }
    apply();
    this.revision++;
    return Promise.resolve(String(this.revision));
  }

  api: HomeApi = {
    load: () =>
      Promise.resolve({
        document: structuredClone(this.document),
        revision: String(this.revision),
      }),
    save: (document, revision) =>
      this.change(revision, () => {
        this.document = structuredClone(document);
        this.saved.push(this.document);
      }),
    uploadPlanImage: (floorId, image, width, revision) =>
      this.change(revision, () => {
        this.images.set(floorId, image);
        // Samma regel som kärnan (editor.py): ungefär 20 meter bred.
        this.document = setPlanImage(this.document, floorId, {
          position: [0, 0],
          meters_per_pixel: Math.min(1, 20 / width),
        });
      }),
    deletePlanImage: (floorId, revision) =>
      this.change(revision, () => {
        this.images.delete(floorId);
        this.document = setPlanImage(this.document, floorId, null);
      }),
    planImageUrl: (floorId) => `/bild/${floorId}`,
  };
}

async function setup() {
  const core = new FakeCore();
  const scenes: EditorScene[] = [];
  let callbacks: EditorViewCallbacks | undefined;
  const deps: EditorDependencies = {
    api: core.api,
    createView: (_container, cb) => {
      callbacks = cb;
      return {
        render: (scene) => scenes.push(scene),
        metersPerPixel: () => 0.01,
        zoom: () => undefined,
        fit: () => undefined,
        destroy: () => undefined,
      };
    },
    prepareImage: (file) => Promise.resolve({ blob: file, width: 500 }),
  };
  const root = document.createElement("main");
  document.body.replaceChildren(root);
  mountEditor(root, deps);
  await vi.waitFor(() => expect(root.dataset.ready).toBe("true"));
  if (callbacks === undefined) {
    throw new Error("Kartan skapades inte");
  }
  const map = callbacks;
  const click = (label: string) => {
    const button = [...root.querySelectorAll("button")].find((b) => b.textContent === label);
    if (button === undefined) {
      throw new Error(`Hittade ingen knapp ${label}`);
    }
    button.click();
  };
  const settled = () => vi.waitFor(() => expect(root.dataset.busy).toBe("false"));
  const drawRectangle = (x = 4) => {
    click("Rum, rektangel");
    map.onPointerDown({ x, y: 0 });
    map.onPointerMove({ x: x + 2, y: 1 });
    map.onPointerUp({ x: x + 2, y: 2 });
  };
  return { root, core, scenes, map, click, settled, drawRectangle };
}

const text = (root: HTMLElement, selector: string) =>
  root.querySelector(selector)?.textContent ?? "";

describe("kartredigeraren", () => {
  it("visar hemmets sammanfattning och instruktionen för valt verktyg", async () => {
    const { root, click } = await setup();

    expect(text(root, ".summary")).toContain("Rum1");
    expect(text(root, ".summary")).toContain("Lampor1");
    expect(text(root, ".summary")).toContain("Golvyta6 m²");
    expect(text(root, ".instruction")).toMatch(/Klicka på ett rum/);

    click("Rum, fri form");
    expect(text(root, ".instruction")).toMatch(/första hörnet/);
    // Utan bild finns inget att skala.
    expect(root.querySelector<HTMLButtonElement>('[data-tool="scale"]')?.disabled).toBe(true);
  });

  it("ett utdraget rum sparas, väljs och kan byta namn och tas bort", async () => {
    const { root, core, drawRectangle, settled } = await setup();
    const saved = core.saved;

    drawRectangle();
    await settled();

    const added = saved[0].home.floors[0].rooms[1];
    expect(added.polygon).toEqual([
      [4, 0],
      [6, 0],
      [6, 2],
      [4, 2],
    ]);
    const name = root.querySelector<HTMLInputElement>('input[name="name"]');
    expect(name?.value).toBe(added.name);
    expect(text(root, ".summary")).toContain("Rum2");

    if (name === null) {
      throw new Error("Inget namnfält");
    }
    name.value = "Förråd";
    name.dispatchEvent(new Event("change"));
    await settled();
    expect(saved[1].home.floors[0].rooms[1]).toMatchObject({ id: added.id, name: "Förråd" });

    [...root.querySelectorAll("button")].find((b) => b.textContent === "Ta bort rummet")?.click();
    await settled();
    expect(saved[2].home.floors[0].rooms.map((r) => r.id)).toEqual(["kok"]);
    expect(text(root, ".selection")).toMatch(/Inget valt/);
  });

  it("en zon ritas i ett rum, väljs, byter namn och sort och tas bort", async () => {
    const { root, core, scenes, map, click, settled } = await setup();
    const zones = () => core.document.home.floors[0].rooms[0].zones ?? [];

    click("Stillhetszon");
    expect(text(root, ".instruction")).toMatch(/Stillhetszon: Där man sitter/);
    expect(root.querySelector('[data-zone-kind="stillhet"]')?.getAttribute("aria-pressed")).toBe(
      "true",
    );
    for (const point of [
      { x: 0.5, y: 0.5 },
      { x: 2, y: 0.5 },
      { x: 2, y: 1.5 },
      { x: 0.5, y: 1.5 },
    ]) {
      map.onPointerDown(point);
    }
    expect(scenes.at(-1)?.draft.kind).toBe("polygon");
    click("Stäng zonen");
    await settled();

    expect(zones()).toEqual([
      {
        id: "stillhet-1",
        name: "Stillhet 1",
        kind: "stillhet",
        polygon: [
          [0.5, 0.5],
          [2, 0.5],
          [2, 1.5],
          [0.5, 1.5],
        ],
      },
    ]);
    expect(text(root, ".summary")).toContain("Zoner1");
    // Den nya zonen är vald.
    expect(text(root, ".properties")).toContain("Vald zon");
    expect(text(root, ".selection")).toContain("Ligger i Kök.");

    const name = root.querySelector<HTMLInputElement>('input[name="name"]');
    if (name === null) {
      throw new Error("Inget namnfält");
    }
    name.value = "Soffa";
    name.dispatchEvent(new Event("change"));
    await settled();
    expect(zones()[0]).toMatchObject({ id: "stillhet-1", name: "Soffa" });

    const kind = root.querySelector<HTMLSelectElement>('select[name="kind"]');
    if (kind === null) {
      throw new Error("Ingen väljare för sort");
    }
    kind.value = "entré";
    kind.dispatchEvent(new Event("change"));
    await settled();
    expect(zones()[0]).toMatchObject({ id: "stillhet-1", kind: "entré" });

    // Välj bort zonen och välj den igen på kartan.
    click("Välj och flytta");
    map.onSelect(null);
    expect(text(root, ".selection")).toMatch(/Inget valt/);
    map.onSelect({ kind: "zone", id: "stillhet-1" });
    expect(scenes.at(-1)?.selection).toEqual({ kind: "zone", id: "stillhet-1" });
    expect(root.querySelector<HTMLInputElement>('input[name="name"]')?.value).toBe("Soffa");

    click("Ta bort zonen");
    await settled();
    expect(zones()).toEqual([]);
    expect(text(root, ".selection")).toMatch(/Inget valt/);
  });

  it("ett hörn nära väggen hamnar på väggen, så att en entrézon kan ligga längs den", async () => {
    const { core, map, click, settled } = await setup();
    click("Entrézon");
    // metersPerPixel är 0,01, så allt inom 12 cm från väggen dras till den.
    for (const point of [
      { x: -0.04, y: 0.5 },
      { x: 0.8, y: 0.5 },
      { x: 0.8, y: 1.5 },
      { x: 0.05, y: 1.5 },
      { x: -0.04, y: 0.5 },
    ]) {
      map.onPointerDown(point);
    }
    await settled();

    expect(core.document.home.floors[0].rooms[0].zones?.[0].polygon).toEqual([
      [0, 0.5],
      [0.8, 0.5],
      [0.8, 1.5],
      [0, 1.5],
    ]);
  });

  it("en zon som kärnan nekar försvinner och är inte längre vald", async () => {
    const { root, core, scenes, map, click, settled } = await setup();
    core.failures = [new CoreError("Zonen går utanför rummet")];
    click("Vanlig zon");
    for (const point of [
      { x: 0.5, y: 0.5 },
      { x: 2, y: 0.5 },
      { x: 2, y: 1.5 },
    ]) {
      map.onPointerDown(point);
    }
    click("Stäng zonen");
    await settled();

    expect(text(root, ".message")).toBe("Zonen går utanför rummet");
    expect(scenes.at(-1)?.floor.rooms[0].zones ?? []).toEqual([]);
    expect(scenes.at(-1)?.selection).toBeNull();
    expect(text(root, ".selection")).toMatch(/Inget valt/);
  });

  it("en zon utanför rummen sparas inte", async () => {
    const { root, core, map, click } = await setup();
    click("Vanlig zon");
    for (const point of [
      { x: 2, y: 1 },
      { x: 5, y: 1 },
      { x: 5, y: 1.5 },
    ]) {
      map.onPointerDown(point);
    }
    click("Stäng zonen");

    expect(core.saved).toEqual([]);
    expect(text(root, ".message")).toMatch(/inne i ett rum/);
  });

  it("hörn nära ett befintligt hörn hamnar exakt på det", async () => {
    const { core, map, click, settled } = await setup();
    click("Rum, fri form");

    for (const point of [
      { x: 3.05, y: 0.04 }, // kökets hörn (3, 0)
      { x: 5, y: 0 },
      { x: 5, y: 2 },
      { x: 3, y: 2 },
      { x: 3, y: 0 },
    ]) {
      map.onPointerDown(point);
    }
    await settled();

    expect(core.saved[0].home.floors[0].rooms[1].polygon[0]).toEqual([3, 0]);
  });

  it("ett rum som kärnan inte godtar försvinner och felet visas", async () => {
    const { root, core, scenes, drawRectangle, settled } = await setup();
    core.failures = [new CoreError("Rummen delar en vägg")];

    drawRectangle();
    await settled();

    expect(text(root, ".message")).toBe("Rummen delar en vägg");
    expect(text(root, ".save-status")).toBe("Ändringen sparades inte.");
    expect(scenes.at(-1)?.floor.rooms).toHaveLength(1);
  });

  it("ändringar som byggde på en misslyckad ändring skickas inte", async () => {
    const { root, core, scenes, drawRectangle, settled } = await setup();
    core.failures = [new CoreError("Kunde inte nå databasen.")];

    drawRectangle(4); // misslyckas
    drawRectangle(8); // byggde på den första och får inte sparas ensam
    await settled();

    expect(core.saved).toEqual([]);
    expect(scenes.at(-1)?.floor.rooms).toHaveLength(1);
    expect(text(root, ".message")).toBe("Kunde inte nå databasen.");

    // Nästa ändring utgår från det kärnan har och går bra.
    drawRectangle(4);
    await settled();
    expect(core.saved[0].home.floors[0].rooms).toHaveLength(2);
    expect(text(root, ".save-status")).toBe("Alla ändringar är sparade.");
  });

  it("en ändring från ett annat fönster skrivs inte över utan visas", async () => {
    const { root, core, scenes, drawRectangle, settled } = await setup();
    // Ett annat fönster byter namn på köket.
    core.document = structuredClone(core.document);
    core.document.home.floors[0].rooms[0].name = "Köket";
    core.revision++;

    drawRectangle(4); // nekas: hemmet har ändrats
    drawRectangle(8); // byggde på den nekade och skickas inte
    await settled();

    expect(core.saved).toEqual([]);
    expect(text(root, ".save-status")).toBe("Hemmet har lästs om.");
    expect(scenes.at(-1)?.floor.rooms.map((room) => room.name)).toEqual(["Köket"]);

    // En ny ändring görs på det nya hemmet, så namnbytet finns kvar.
    drawRectangle(4);
    await settled();
    expect(core.document.home.floors[0].rooms.map((room) => room.name)).toEqual(["Köket", "Rum 2"]);
  });

  it("en ändring som görs medan bilden laddas upp tar inte bort bilden", async () => {
    const { root, core, scenes, drawRectangle, settled } = await setup();
    // Bilden når inte fram förrän testet släpper den.
    let arrive: (value?: unknown) => void = () => undefined;
    const arrived = new Promise((resolve) => (arrive = resolve));
    const upload = core.api.uploadPlanImage;
    core.api.uploadPlanImage = async (...args) => {
      await arrived;
      return upload(...args);
    };
    const input = root.querySelector<HTMLInputElement>('input[type="file"]');
    Object.defineProperty(input, "files", { value: [new File(["bild"], "plan.png")] });
    input?.dispatchEvent(new Event("change"));
    await vi.waitFor(() => expect(root.dataset.busy).toBe("true"));

    drawRectangle(); // medan bilden fortfarande är på väg
    arrive();
    await settled();

    const floor = core.document.home.floors[0];
    expect(floor.plan_image).not.toBeNull();
    expect(floor.plan_image).toBeDefined();
    expect(floor.rooms).toHaveLength(2);
    expect(scenes.at(-1)?.floor.rooms).toHaveLength(2);
    expect(scenes.at(-1)?.planImage).not.toBeNull();
  });

  it("för små rum sparas inte", async () => {
    const { root, core, map, click } = await setup();
    click("Rum, rektangel");
    map.onPointerDown({ x: 4, y: 4 });
    map.onPointerUp({ x: 4.1, y: 4.1 });

    expect(core.saved).toEqual([]);
    expect(text(root, ".message")).toMatch(/för litet/);
  });

  it("en uppladdad bild läggs på våningen och skalas med två punkter", async () => {
    const { root, core, map, settled } = await setup();
    const input = root.querySelector<HTMLInputElement>('input[type="file"]');
    if (input === null) {
      throw new Error("Inget filfält");
    }
    const file = new File(["bild"], "plan.png", { type: "image/png" });
    Object.defineProperty(input, "files", { value: [file] });
    input.dispatchEvent(new Event("change"));
    await vi.waitFor(() => expect(core.images.get("plan")).toBe(file));
    await settled();

    expect(core.document.home.floors[0].plan_image).toEqual({
      position: [0, 0],
      meters_per_pixel: 0.04,
    });
    expect(text(root, ".instruction")).toMatch(/två punkter/);

    // 4 meter med bildens nuvarande skala är 100 bildpunkter.
    map.onPointerDown({ x: 1, y: 1 });
    map.onPointerDown({ x: 5, y: 1 });
    expect(text(root, ".actions")).toContain("4 m");
    const meters = root.querySelector<HTMLInputElement>('input[name="meters"]');
    if (meters === null) {
      throw new Error("Inget fält för avståndet");
    }
    meters.value = "2";
    root.querySelector("form")?.requestSubmit();
    await settled();

    expect(core.saved[0].home.floors[0].plan_image).toEqual({
      position: [0, 0],
      meters_per_pixel: 0.02,
    });

    [...root.querySelectorAll("button")].find((b) => b.textContent === "Ta bort bilden")?.click();
    await settled();
    expect(core.document.home.floors[0].plan_image).toBeNull();
    expect(core.images.size).toBe(0);
  });

  it("en bild som inte går att ladda upp ger ett fel och ändrar inget", async () => {
    const { root, core, settled } = await setup();
    core.failures = [new CoreError("Bilden ska vara PNG, JPEG eller WebP")];
    const input = root.querySelector<HTMLInputElement>('input[type="file"]');
    Object.defineProperty(input, "files", { value: [new File(["x"], "a.png")] });
    input?.dispatchEvent(new Event("change"));
    await vi.waitFor(() => expect(text(root, ".message")).toMatch(/PNG/));
    await settled();

    expect(core.document.home.floors[0].plan_image).toBeUndefined();
    expect(root.querySelector<HTMLButtonElement>('[data-tool="scale"]')?.disabled).toBe(true);
  });
  it("medan en bild laddas upp går den inte att skala eller ta bort", async () => {
    const { root, core, settled } = await setup();
    let arrive: (value?: unknown) => void = () => undefined;
    const arrived = new Promise((resolve) => (arrive = resolve));
    const upload = core.api.uploadPlanImage;
    core.api.uploadPlanImage = async (...args) => {
      await arrived;
      return upload(...args);
    };
    const input = root.querySelector<HTMLInputElement>('input[type="file"]');
    Object.defineProperty(input, "files", { value: [new File(["bild"], "plan.png")] });
    input?.dispatchEvent(new Event("change"));
    await vi.waitFor(() => expect(root.dataset.busy).toBe("true"));

    const disabled = (selector: string) =>
      root.querySelector<HTMLButtonElement>(selector)?.disabled;
    expect(disabled('[data-tool="scale"]')).toBe(true);
    arrive();
    await settled();
    expect(disabled('[data-tool="scale"]')).toBe(false);
    expect(text(root, ".instruction")).toMatch(/två punkter/);
  });

  it("går hemmet inte att läsa om efter ett fel skrivs inget över sedan", async () => {
    const { root, core, drawRectangle, settled } = await setup();
    core.failures = [new CoreError("Kunde inte nå databasen.")];
    const load = core.api.load;
    core.api.load = () => Promise.reject(new CoreError("Får ingen kontakt."));
    drawRectangle(4);
    await settled();

    // Ett annat fönster ändrar under tiden, och kontakten kommer tillbaka.
    core.document = structuredClone(core.document);
    core.document.home.floors[0].rooms[0].name = "Köket";
    core.revision++;
    core.api.load = load;
    drawRectangle(8);
    await settled();

    expect(core.saved).toEqual([]);
    expect(text(root, ".save-status")).toBe("Hemmet har lästs om.");
    expect(core.document.home.floors[0].rooms.map((room) => room.name)).toEqual(["Köket"]);
  });
});
