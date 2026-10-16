/**
 * Kartredigeraren: verktygen till vänster, kartan i mitten och egenskaperna
 * till höger (se #34). På en smal skärm staplas de.
 *
 * Varje ändring sparas direkt i kärnan, som kontrollerar hemmet och visar det
 * i livevyn. Säger kärnan nej visas varför, och kartan läses om från kärnan,
 * så att den aldrig visar något som inte är sparat.
 *
 * Attributen data-ready, data-busy, data-saves och data-rooms på roten finns
 * för webbläsartesterna, som väntar på dem.
 */
import "./editor.css";
import {
  addRoom,
  deleteRoom,
  distance,
  findRoom,
  moveRoom,
  polygonArea,
  renameRoom,
  roomProblem,
  scalePlanImage,
  setPlanImage,
  summarize,
  toPoint,
  toXY,
} from "./editing";
import { createKonvaEditorView, type EditorViewFactory } from "./editorView";
import { coreApi, CoreError, type HomeApi } from "./homeApi";
import type { FloorModel, HomeDocument } from "./homefile";
import type { Point } from "./protocol";
import { Drawing, INSTRUCTIONS, type Outcome, type ToolName } from "./tools";

/** En bild som är klar att skickas till kärnan, och hur många bildpunkter bred den är. */
export interface PreparedImage {
  blob: Blob;
  width: number;
}

export interface EditorDependencies {
  api: HomeApi;
  createView: EditorViewFactory;
  prepareImage(file: File): Promise<PreparedImage>;
}

/** Större bilder förminskas; mer än så här syns ändå inte på en skärm. */
const MAX_IMAGE_SIDE = 4000;
/** Så nära (i skärmpunkter) ett klick måste vara ett hörn för att träffa det. */
const SNAP_PIXELS = 12;

/**
 * Ritar om bilden i webbläsaren innan den skickas. Det gör den mindre och tar
 * bort det som telefonen har lagt till i filen, till exempel var bilden togs.
 */
export async function shrinkImage(file: File): Promise<PreparedImage> {
  const bitmap = await createImageBitmap(file);
  const factor = Math.min(1, MAX_IMAGE_SIDE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(bitmap.width * factor));
  canvas.height = Math.max(1, Math.round(bitmap.height * factor));
  canvas.getContext("2d")?.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  // En ritning med tunna linjer blir suddig som JPEG, så PNG får förbli PNG.
  const type = file.type === "image/png" ? "image/png" : "image/jpeg";
  const blob = await new Promise<Blob>((resolve, reject) =>
    canvas.toBlob(
      (result) => (result === null ? reject(new Error("toBlob gav ingen bild")) : resolve(result)),
      type,
      0.9,
    ),
  );
  return { blob, width: canvas.width };
}

/** En ändring av hemmet, som görs på det sparade hemmet när den står på tur. */
type Edit = (document: HomeDocument) => HomeDocument;

/** Något som ska skickas till kärnan: en ändring av hemmet eller av en bild. */
type Change = { kind: "edit"; edit: Edit } | { kind: "image"; send: () => Promise<void> };

const defaultDependencies: EditorDependencies = {
  api: coreApi,
  createView: createKonvaEditorView,
  prepareImage: shrinkImage,
};

const TOOL_LABELS: Record<ToolName, string> = {
  select: "Välj och flytta",
  polygon: "Rum, fri form",
  rectangle: "Rum, rektangel",
  scale: "Skala bilden",
};

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Partial<HTMLElementTagNameMap[K]> = {},
  ...children: (Node | string)[]
): HTMLElementTagNameMap[K] {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

function button(label: string, onClick: () => void, className = ""): HTMLButtonElement {
  const node = element("button", { type: "button", className }, label);
  node.addEventListener("click", onClick);
  return node;
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function formatNumber(value: number, decimals = 1): string {
  return value.toLocaleString("sv-SE", { maximumFractionDigits: decimals });
}

export function mountEditor(
  root: HTMLElement,
  deps: EditorDependencies = defaultDependencies,
): void {
  const drawing = new Drawing();
  /** Det som kärnan senast tog emot, och dess revision. */
  let saved: HomeDocument | null = null;
  let revision = "";
  /** Ändringar som väntar på att skickas till kärnan, i den ordning de gjordes. */
  let waiting: Change[] = [];
  /** Det som visas: det sparade med de väntande ändringarna ovanpå. */
  let shown: HomeDocument | null = null;
  let floorId: string | null = null;
  let selectedRoomId: string | null = null;
  /** Punkterna som ska skalas mellan, när användaren har klickat ut två. */
  let scalePoints: { a: Point; b: Point } | null = null;
  /** Ändras när en ny bild laddas upp, så att webbläsaren inte visar den gamla. */
  let imageVersion = Date.now();
  /** Ändringarna skickas en i taget. */
  let queue: Promise<void> = Promise.resolve();
  let savedCount = 0;

  // --- Verktygen ---
  const toolButtons = new Map<ToolName, HTMLButtonElement>();
  const toolButton = (tool: ToolName) => {
    const node = button(TOOL_LABELS[tool], () => chooseTool(tool), "tool");
    node.dataset.tool = tool;
    toolButtons.set(tool, node);
    return node;
  };
  const fileInput = element("input", { type: "file", accept: "image/png,image/jpeg,image/webp" });
  fileInput.hidden = true;
  const uploadButton = button("Ladda upp bild", () => fileInput.click(), "tool");
  const removeImageButton = button("Ta bort bilden", () => removeImage(), "tool");
  const toolbox = element(
    "aside",
    { className: "toolbox" },
    element("h2", {}, "Planritning"),
    uploadButton,
    fileInput,
    toolButton("scale"),
    removeImageButton,
    element("h2", {}, "Rita"),
    toolButton("select"),
    toolButton("polygon"),
    toolButton("rectangle"),
    element("h2", {}, "Kartan"),
    element(
      "div",
      { className: "zoom" },
      button("+", () => view.zoom(1.25), "tool"),
      button("−", () => view.zoom(0.8), "tool"),
      button("Visa allt", () => view.fit(), "tool"),
    ),
  );
  toolbox.setAttribute("aria-label", "Verktyg");

  // --- Kartan ---
  const floorPicker = element("nav", { className: "floors", hidden: true });
  floorPicker.setAttribute("aria-label", "Våningar");
  const stageContainer = element("div", { className: "editor-stage" });
  const saveStatus = element("p", { className: "save-status" }, "Läser hemmet …");
  saveStatus.setAttribute("role", "status");
  const message = element("p", { className: "message", hidden: true });
  message.setAttribute("role", "alert");
  const map = element(
    "section",
    { className: "map" },
    floorPicker,
    stageContainer,
    saveStatus,
    message,
  );

  // --- Egenskaperna ---
  const instruction = element("p", { className: "instruction" });
  const draftActions = element("div", { className: "actions" });
  const selection = element("div", { className: "selection" });
  const summary = element("dl", { className: "summary" });
  const properties = element(
    "aside",
    { className: "properties" },
    element("section", {}, element("h2", {}, "Instruktion"), instruction, draftActions),
    element("section", {}, element("h2", {}, "Valt rum"), selection),
    element("section", {}, element("h2", {}, "Hemmet"), summary),
  );

  root.replaceChildren(
    element("h1", {}, "Homecontrol – kartredigerare"),
    element("div", { className: "editor" }, toolbox, map, properties),
  );

  const currentFloor = (): FloorModel | null =>
    shown?.home.floors.find((floor) => floor.id === floorId) ?? null;

  const showMessage = (text: string | null) => {
    message.textContent = text ?? "";
    message.hidden = text === null;
  };

  // --- Ritning av allt utom kartan ---
  const renderTools = () => {
    for (const [tool, node] of toolButtons) {
      node.setAttribute("aria-pressed", String(tool === drawing.tool));
    }
    const hasImage = currentFloor()?.plan_image != null;
    const scaleButton = toolButtons.get("scale");
    if (scaleButton !== undefined) {
      scaleButton.disabled = !hasImage;
    }
    removeImageButton.disabled = !hasImage;
  };

  const renderInstruction = () => {
    const draft = drawing.draft;
    instruction.textContent = INSTRUCTIONS[drawing.tool];
    draftActions.replaceChildren();
    if (draft.kind === "polygon") {
      const done = button("Stäng rummet", () => handle(drawing.finish()));
      done.disabled = draft.points.length < 3;
      draftActions.append(
        done,
        button("Ångra hörnet", () => {
          drawing.undo();
          render();
        }),
        button("Avbryt", cancelDrawing),
      );
    }
    if (scalePoints !== null) {
      const { a, b } = scalePoints;
      const meters = element("input", {
        type: "number",
        min: "0.1",
        max: "1000",
        step: "0.01",
        required: true,
        name: "meters",
      });
      const form = element(
        "form",
        { className: "scale-form" },
        element("label", {}, "Avstånd i meter ", meters),
        element("button", { type: "submit" }, "Skala"),
        button("Avbryt", cancelDrawing),
      );
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        applyScale(a, b, Number(meters.value));
      });
      draftActions.append(
        element(
          "p",
          {},
          `Avståndet mellan punkterna är ${formatNumber(distance(a, b), 2)} m med bildens nuvarande skala.`,
        ),
        form,
      );
      queueMicrotask(() => meters.focus());
    }
  };

  const renderSelection = () => {
    const room = selectedRoomId === null || shown === null ? null : findRoom(shown, selectedRoomId);
    if (room === null) {
      selection.replaceChildren(
        element("p", { className: "muted" }, "Inget valt. Välj ett rum på kartan."),
      );
      return;
    }
    const name = element("input", { type: "text", value: room.name, maxLength: 100, name: "name" });
    name.addEventListener("change", () => {
      const next = name.value.trim();
      if (next === "") {
        name.value = room.name;
        showMessage("Rummet måste ha ett namn.");
      } else if (next !== room.name && shown !== null) {
        commit((document) => renameRoom(document, room.id, next));
      }
    });
    name.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        name.blur();
      }
    });
    selection.replaceChildren(
      element("label", {}, "Namn ", name),
      element(
        "p",
        { className: "muted" },
        `${formatNumber(polygonArea(room.polygon.map(toPoint)))} m², ${room.polygon.length} hörn`,
      ),
      button(
        "Ta bort rummet",
        () => {
          if (shown !== null) {
            selectedRoomId = null;
            commit((document) => deleteRoom(document, room.id));
          }
        },
        "danger",
      ),
    );
  };

  const renderSummary = () => {
    if (shown === null) {
      return;
    }
    const s = summarize(shown);
    const rows: [string, string][] = [
      ["Våningar", String(shown.home.floors.length)],
      ["Rum", String(s.rooms)],
      ["Golvyta", `${formatNumber(s.area)} m²`],
      ["Zoner", String(s.zones)],
      ["Rumskort", String(s.nodes)],
      ["Routrar", String(s.signalSources)],
      ["Lampor", String(s.lamps)],
    ];
    summary.replaceChildren(
      ...rows.flatMap(([term, value]) => [element("dt", {}, term), element("dd", {}, value)]),
    );
  };

  const renderFloors = () => {
    const floors = shown?.home.floors ?? [];
    floorPicker.replaceChildren(
      ...floors.map((floor) => {
        const node = button(floor.name, () => {
          floorId = floor.id;
          selectedRoomId = null;
          cancelDrawing();
        });
        node.setAttribute("aria-pressed", String(floor.id === floorId));
        return node;
      }),
    );
    // Redigeraren visar en våning åt gången; väljaren behövs bara om det finns flera.
    floorPicker.hidden = floors.length < 2;
  };

  const renderMap = () => {
    const floor = currentFloor();
    if (floor === null) {
      return;
    }
    view.render({
      floor,
      planImage:
        floor.plan_image == null
          ? null
          : { url: `${deps.api.planImageUrl(floor.id)}?v=${imageVersion}`, plan: floor.plan_image },
      selectedRoomId,
      tool: drawing.tool,
      draft: drawing.draft,
    });
  };

  const render = () => {
    renderTools();
    renderInstruction();
    renderSelection();
    renderSummary();
    renderFloors();
    renderMap();
    root.dataset.rooms = String(currentFloor()?.rooms.length ?? 0);
  };

  // --- Sparande ---
  const setBusy = (busy: boolean) => {
    root.dataset.busy = String(busy);
  };

  /** Räknar fram det som visas och glömmer ett valt rum som inte längre finns. */
  const refreshShown = () => {
    shown = saved;
    for (const change of waiting) {
      if (shown !== null && change.kind === "edit") {
        shown = change.edit(shown);
      }
    }
    if (selectedRoomId !== null && shown !== null && findRoom(shown, selectedRoomId) === null) {
      selectedRoomId = null;
    }
  };

  const reload = async () => {
    const latest = await deps.api.load();
    saved = latest.document;
    revision = latest.revision;
  };

  const run = async (change: Change) => {
    if (!waiting.includes(change)) {
      return; // en tidigare ändring misslyckades, och den här byggde på den
    }
    let changedView = change.kind === "image";
    try {
      if (change.kind === "edit" && saved !== null) {
        // Ändringen görs på det kärnan har nu, inte på det som visades när
        // användaren gjorde den. Då försvinner inget som hann sparas emellan.
        const next = change.edit(saved);
        revision = await deps.api.save(next, revision);
        saved = next;
      } else if (change.kind === "image") {
        await change.send();
        await reload(); // kärnan har ändrat hemmet; läs det som blev
      }
      waiting = waiting.filter((other) => other !== change);
      savedCount++;
      root.dataset.saves = String(savedCount);
    } catch (error) {
      // Resten av kön byggde på den här ändringen och skickas inte heller.
      waiting = [];
      changedView = true;
      showMessage(errorText(error));
      saveStatus.textContent =
        error instanceof CoreError && error.conflict
          ? "Hemmet har lästs om."
          : "Ändringen sparades inte.";
      try {
        await reload();
      } catch {
        // Kärnan går inte att nå; visa det som senast sparades.
      }
    }
    if (changedView) {
      refreshShown();
      render();
    }
    if (waiting.length === 0) {
      if (message.hidden) {
        saveStatus.textContent = "Alla ändringar är sparade.";
      }
      setBusy(false);
    }
  };

  const enqueue = (change: Change) => {
    waiting.push(change);
    refreshShown();
    showMessage(null);
    saveStatus.textContent = "Sparar …";
    setBusy(true);
    render();
    queue = queue.then(() => run(change));
  };

  /** Ändrar hemmet. `edit` görs på det sparade hemmet när ändringen står på tur. */
  const commit = (edit: Edit) => enqueue({ kind: "edit", edit });

  // --- Verktyg och ritande ---
  const chooseTool = (tool: ToolName) => {
    drawing.setTool(tool);
    scalePoints = null;
    if (tool !== "select") {
      selectedRoomId = null;
    }
    render();
  };

  // cancelDrawing och handle är vanliga funktioner, eftersom knapparna som
  // ritas ovanför behöver dem innan de har definierats här.
  function cancelDrawing() {
    drawing.cancel();
    scalePoints = null;
    render();
  }

  /**
   * Drar punkten till ett hörn som redan finns om den är nära, annars till hela
   * centimeter. Punkter utanför planet (till vänster om eller ovanför origo)
   * flyttas in till kanten.
   */
  const snap = (point: Point): Point => {
    const radius = SNAP_PIXELS * view.metersPerPixel();
    const corners = currentFloor()?.rooms.flatMap((room) => room.polygon.map(toPoint)) ?? [];
    let best: Point | null = null;
    for (const corner of corners) {
      if (
        distance(point, corner) <= radius &&
        (best === null || distance(point, corner) < distance(point, best))
      ) {
        best = corner;
      }
    }
    return best ?? toPoint(toXY(point));
  };

  function handle(outcome: Outcome) {
    if (outcome?.kind === "room") {
      const problem = roomProblem(outcome.corners);
      const floor = currentFloor();
      if (problem !== null) {
        showMessage(problem);
      } else if (shown !== null && floor !== null) {
        const { roomId, name } = addRoom(shown, floor.id, outcome.corners);
        selectedRoomId = roomId;
        commit(
          (document) => addRoom(document, floor.id, outcome.corners, { roomId, name }).document,
        );
        return;
      }
    } else if (outcome?.kind === "scale") {
      scalePoints = outcome;
    }
    render();
  }

  const applyScale = (a: Point, b: Point, meters: number) => {
    const floor = currentFloor();
    if (shown === null || floor?.plan_image == null) {
      return;
    }
    if (!(meters > 0) || distance(a, b) === 0) {
      showMessage("Skriv in ett avstånd större än noll, mellan två olika punkter.");
      return;
    }
    const plan = scalePlanImage(floor.plan_image, a, b, meters);
    if (plan.meters_per_pixel > 1) {
      showMessage("Så stor kan bilden inte bli. Kontrollera avståndet.");
      return;
    }
    drawing.cancel();
    scalePoints = null;
    commit((document) => setPlanImage(document, floor.id, plan));
    view.fit();
  };

  const uploadImage = async (file: File) => {
    const floor = currentFloor();
    if (floor === null) {
      return;
    }
    let image: PreparedImage;
    try {
      image = await deps.prepareImage(file);
    } catch {
      showMessage("Kunde inte läsa bilden. Välj en PNG-, JPEG- eller WebP-fil.");
      return;
    }
    enqueue({
      kind: "image",
      send: async () => {
        // Kärnan lägger bilden på våningen och sparar hemmet i samma steg.
        revision = await deps.api.uploadPlanImage(floor.id, image.blob, image.width, revision);
        imageVersion = Date.now();
        chooseTool("scale");
      },
    });
  };

  const removeImage = () => {
    const floor = currentFloor();
    if (floor === null) {
      return;
    }
    if (drawing.tool === "scale") {
      chooseTool("select");
    }
    enqueue({
      kind: "image",
      send: async () => {
        revision = await deps.api.deletePlanImage(floor.id, revision);
      },
    });
  };

  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    fileInput.value = "";
    if (file !== undefined) {
      void uploadImage(file);
    }
  });

  const view = deps.createView(stageContainer, {
    onPointerDown: (point) => {
      // Med Välj sköter kartan klicket själv. Att rita om nu skulle byta ut
      // rummet under pekaren mitt i klicket, och då kommer klicket aldrig fram.
      if (drawing.tool === "select") {
        return;
      }
      if (drawing.tool === "scale" && currentFloor()?.plan_image == null) {
        return;
      }
      if (drawing.tool === "scale") {
        scalePoints = null; // ett nytt klick börjar om med två nya punkter
      }
      const snapped = drawing.tool === "scale" ? point : snap(point);
      handle(drawing.pointerDown(snapped, SNAP_PIXELS * view.metersPerPixel()));
    },
    onPointerMove: (point) => {
      if (drawing.draft.kind !== "none") {
        drawing.pointerMove(drawing.tool === "scale" ? point : snap(point));
        renderMap();
      }
    },
    onPointerUp: (point) => {
      if (drawing.tool === "rectangle") {
        handle(drawing.pointerUp(snap(point)));
      }
    },
    onSelectRoom: (roomId) => {
      selectedRoomId = roomId;
      render();
    },
    onMoveRoom: (roomId, dx, dy) => {
      if (shown !== null && (dx !== 0 || dy !== 0)) {
        commit((document) => moveRoom(document, roomId, dx, dy));
      }
    },
  });

  document.addEventListener("keydown", (event) => {
    if (event.target instanceof HTMLInputElement) {
      return;
    }
    if (event.key === "Escape") {
      cancelDrawing();
    } else if (event.key === "Backspace" && drawing.draft.kind !== "none") {
      drawing.undo();
      render();
    } else if (event.key === "Enter") {
      handle(drawing.finish());
    }
  });

  chooseTool("select");
  deps.api.load().then(
    (latest) => {
      saved = latest.document;
      revision = latest.revision;
      refreshShown();
      const floors = latest.document.home.floors;
      floorId = (floors.find((floor) => floor.level === 0) ?? floors[0]).id;
      saveStatus.textContent = "Alla ändringar är sparade.";
      setBusy(false);
      render();
      root.dataset.ready = "true";
    },
    (error: unknown) => {
      saveStatus.textContent = "Kunde inte läsa hemmet. Ladda om sidan för att försöka igen.";
      showMessage(errorText(error));
    },
  );
}
