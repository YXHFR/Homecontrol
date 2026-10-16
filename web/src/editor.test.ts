import { describe, expect, it, vi } from "vitest";
import { mountEditor, type EditorDependencies } from "./editor";
import type { EditorScene, EditorViewCallbacks } from "./editorView";
import { SaveError, type HomeDocument } from "./homefile";

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

async function setup(options: { refuse?: string } = {}) {
  const saved: HomeDocument[] = [];
  const scenes: EditorScene[] = [];
  let callbacks: EditorViewCallbacks | undefined;
  const deps: EditorDependencies = {
    api: {
      load: () => Promise.resolve(home()),
      save: (document) => {
        if (options.refuse !== undefined) {
          return Promise.reject(new SaveError(options.refuse));
        }
        saved.push(document);
        return Promise.resolve();
      },
      uploadPlanImage: vi.fn(() => Promise.resolve()),
      deletePlanImage: vi.fn(() => Promise.resolve()),
      planImageUrl: (floorId) => `/bild/${floorId}`,
    },
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
  const settled = () => vi.waitFor(() => expect(root.dataset.saved).toBe("true"));
  const drawRectangle = async () => {
    click("Rum, rektangel");
    map.onPointerDown({ x: 4, y: 0 });
    map.onPointerMove({ x: 6, y: 1 });
    map.onPointerUp({ x: 6, y: 2 });
    await settled();
  };
  return { root, saved, scenes, deps, map, click, settled, drawRectangle };
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
    const { root, saved, drawRectangle, settled } = await setup();

    await drawRectangle();

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

  it("hörn nära ett befintligt hörn hamnar exakt på det", async () => {
    const { saved, map, click, settled } = await setup();
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

    expect(saved[0].home.floors[0].rooms[1].polygon[0]).toEqual([3, 0]);
  });

  it("ett rum som kärnan inte godtar försvinner och felet visas", async () => {
    const { root, scenes, drawRectangle } = await setup({ refuse: "Rummen delar en vägg" });

    await drawRectangle();

    expect(text(root, ".message")).toBe("Rummen delar en vägg");
    expect(text(root, ".save-status")).toBe("Ändringen sparades inte.");
    expect(scenes.at(-1)?.floor.rooms).toHaveLength(1);
  });

  it("för små rum sparas inte", async () => {
    const { root, saved, map, click } = await setup();
    click("Rum, rektangel");
    map.onPointerDown({ x: 4, y: 4 });
    map.onPointerUp({ x: 4.1, y: 4.1 });

    expect(saved).toEqual([]);
    expect(text(root, ".message")).toMatch(/för litet/);
  });

  it("en uppladdad bild läggs på våningen och skalas med två punkter", async () => {
    const { root, saved, deps, map, settled } = await setup();
    const input = root.querySelector<HTMLInputElement>('input[type="file"]');
    if (input === null) {
      throw new Error("Inget filfält");
    }
    const file = new File(["bild"], "plan.png", { type: "image/png" });
    Object.defineProperty(input, "files", { value: [file] });
    input.dispatchEvent(new Event("change"));
    await vi.waitFor(() => expect(saved).toHaveLength(1));
    await settled();

    expect(deps.api.uploadPlanImage).toHaveBeenCalledWith("plan", file);
    expect(saved[0].home.floors[0].plan_image).toEqual({
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

    expect(saved[1].home.floors[0].plan_image).toEqual({
      position: [0, 0],
      meters_per_pixel: 0.02,
    });
  });
});
