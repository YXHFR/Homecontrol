/**
 * Ändra rummet: ritverktygen på sidan. Verktygsraden ligger till vänster på
 * datorn och längst ned på mobilen, och instruktionen och egenskaperna för
 * det valda står i arket. Väggar, rumskort, routrar och lampor ändras med
 * placing.ts och visas med mapItems.ts och itemPanel.ts. Zonerna har sina
 * delar i zones.ts och zonePanel.ts. En iPhone-skanning läses med roomplan.ts
 * och läggs in med scanPanel.ts.
 *
 * Varje ändring sparas direkt i kärnan genom HomeStore. Säger kärnan nej
 * visas varför, och kartan läses om från kärnan, så att den aldrig visar
 * något som inte är sparat.
 */
import { button, element } from "./dom";
import {
  addRoom,
  cornersOnFloor,
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
import type { HomeApi } from "./homeApi";
import type { FloorModel, HomeDocument } from "./homefile";
import { errorText, type Edit, type HomeStore } from "./homeStore";
import { itemProperties, type PanelActions, roomSettingsForm } from "./itemPanel";
import type { MapCallbacks, MapView, Selection } from "./mapView";
import {
  cycleWall,
  findItem,
  ITEM_LABELS,
  type ItemKind,
  moveItem,
  normalizeRotation,
  placeItem,
  updateItem,
} from "./placing";
import type { Card, FoundLamp, Point, ZoneKind } from "./protocol";
import { DEFAULT_PLACEMENT, importScan, placeScan, readScan, ScanError } from "./roomplan";
import { SCAN_INSTRUCTION, scanForm, type ScanImport } from "./scanPanel";
import {
  type Draft,
  Drawing,
  INSTRUCTIONS,
  isPlacingTool,
  mapHandlesClicks,
  type Outcome,
  type ToolName,
} from "./tools";
import { zoneInstruction, zoneProperties, zoneTools } from "./zonePanel";
import { findZone, nearestOnWall, planZone } from "./zones";
import { ZONE_STYLES } from "./zoneStyles";

/** En bild som är klar att skickas till kärnan, och hur många bildpunkter bred den är. */
export interface PreparedImage {
  blob: Blob;
  width: number;
}

/** Större bilder förminskas; mer än så här syns ändå inte på en skärm. */
const MAX_IMAGE_SIDE = 4000;
/** Större filer än så här är inte en skanning (en stor lägenhet ger några hundra kilobyte). */
const MAX_SCAN_BYTES = 20_000_000;
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

const TOOL_LABELS: Record<ToolName, string> = {
  select: "Välj och flytta",
  polygon: "Rum, fri form",
  rectangle: "Rum, rektangel",
  scale: "Skala bilden",
  walls: "Väggar och dörrar",
  zone: "Zon", // visas inte: zonerna har en knapp per sort (zonePanel.ts)
  node: `${ITEM_LABELS.node} (ESP32)`,
  router: ITEM_LABELS.router,
  lamp: ITEM_LABELS.lamp,
};

function formatNumber(value: number, decimals = 1): string {
  return value.toLocaleString("sv-SE", { maximumFractionDigits: decimals });
}

/** Om det valda finns i hemmet. En ny sort som går att välja måste läggas till här. */
export function stillThere(document: HomeDocument, selection: Selection): boolean {
  if (selection === null) {
    return true;
  }
  switch (selection.kind) {
    case "room":
      return findRoom(document, selection.id) !== null;
    case "zone":
      return findZone(document, selection.id) !== null;
    case "item":
      return findItem(document, selection.id) !== null;
    default: {
      const unknown: never = selection.kind;
      throw new Error(`Okänd sort att välja: ${String(unknown)}`);
    }
  }
}

/** Det Ändra rummet behöver av sidan. */
export interface EditContext {
  api: HomeApi;
  store: HomeStore;
  prepareImage(file: File): Promise<PreparedImage>;
  view(): MapView;
  /** Våningen som visas. */
  floor(): FloorModel | null;
  setFloorId(floorId: string): void;
  selection(): Selection;
  setSelection(selection: Selection): void;
  /** Riktiga rumskort och lampor som kärnan har hittat men som inte är placerade. */
  newCards(): Card[];
  newLamps(): FoundLamp[];
  /** Ett nytt kort eller en ny lampa har placerats och ska inte längre visas som ny. */
  placedFound(id: string): void;
  /** Ritar om sidan. */
  render(): void;
  /** Ändras när en ny bild laddas upp, så att webbläsaren inte visar den gamla. */
  imageUploaded(): void;
}

/** Det som Ändra rummet vill visa på kartan. */
export interface EditScene {
  tool: ToolName;
  draft: Draft;
  draftColor: string | null;
  /** Hemmet som det blir med skanningen inlagd, och våningen den hamnar på, medan den visas. */
  preview: { document: HomeDocument; floorId: string } | null;
}

export type EditCallbacks = Pick<
  MapCallbacks,
  | "onPointerDown"
  | "onPointerMove"
  | "onPointerUp"
  | "onSelect"
  | "onSelectItem"
  | "onMoveItem"
  | "onRotateNode"
  | "onClickWall"
  | "onMoveRoom"
>;

export interface EditMode {
  /** Verktygsraden. */
  toolbar: HTMLElement;
  /** Instruktionen, det valda och hemmet, för arket. */
  panel: HTMLElement;
  callbacks: EditCallbacks;
  scene(): EditScene;
  /** Ritar verktygsraden och panelen. */
  render(): void;
  showMessage(text: string | null): void;
  /** Kön har börjat eller slutat spara. */
  busy(busy: boolean): void;
  /** En ändring sparades inte. */
  failed(error: unknown, conflict: boolean): void;
  /** Tangenterna Escape, Backspace och Enter medan något ritas. Sant om tangenten användes. */
  keydown(event: KeyboardEvent): boolean;
  /** Slutar rita och väljer verktyget Välj, till exempel när Ändra rummet stängs. */
  reset(): void;
}

export function createEditMode(ctx: EditContext): EditMode {
  const drawing = new Drawing();
  const { store } = ctx;
  /** Sorten av zon som ritas med verktyget Zon. */
  let zoneKind: ZoneKind = "vanlig";
  /** Punkterna som ska skalas mellan, när användaren har klickat ut två. */
  let scalePoints: { a: Point; b: Point } | null = null;
  /** Kortet eller lampan som placeras med nästa klick. */
  let cardToPlace: Card | null = null;
  let lampToPlace: FoundLamp | null = null;
  /** En iPhone-skanning som visas på kartan men inte är inlagd än. */
  let scanImport: ScanImport | null = null;

  const commit = (edit: Edit, failed?: () => void) => {
    showMessage(null);
    store.commit(edit, { failed });
  };

  // --- Verktygen ---
  const toolButtons = new Map<ToolName, HTMLButtonElement>();
  const zoneToolbox = zoneTools((kind) => {
    zoneKind = kind;
    chooseTool("zone");
  });
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
  const scanInput = element("input", { type: "file", accept: ".json,application/json" });
  scanInput.hidden = true;
  const scanButton = button("Läs in iPhone-skanning", () => scanInput.click(), "tool");
  const newCardsList = element("div", { className: "new-cards", hidden: true });
  newCardsList.setAttribute("aria-label", "Nya rumskort");
  const newLampsList = element("div", { className: "new-lamps", hidden: true });
  newLampsList.setAttribute("aria-label", "Nya lampor");
  const group = (title: string, ...nodes: HTMLElement[]) =>
    element(
      "div",
      { className: "group" },
      element("span", { className: "group-title" }, title),
      ...nodes,
    );
  const toolbar = element(
    "nav",
    { className: "toolbar" },
    group("Rita", toolButton("select"), toolButton("polygon"), toolButton("rectangle"), toolButton("walls")),
    group("Zoner", ...zoneToolbox.nodes.filter((node) => node.tagName === "BUTTON")),
    group(
      "Placera",
      toolButton("node"),
      newCardsList,
      toolButton("router"),
      toolButton("lamp"),
      newLampsList,
    ),
    group(
      "Planritning",
      uploadButton,
      fileInput,
      toolButton("scale"),
      removeImageButton,
      scanButton,
      scanInput,
    ),
  );
  toolbar.setAttribute("aria-label", "Verktyg");

  // --- Panelen i arket ---
  const instruction = element("p", { className: "instruction" });
  const draftActions = element("div", { className: "actions" });
  const selectionPanel = element("div", { className: "selection" });
  const summary = element("dl", { className: "summary" });
  const saveStatus = element("p", { className: "save-status" }, "Alla ändringar är sparade.");
  saveStatus.setAttribute("role", "status");
  const message = element("p", { className: "message", hidden: true });
  message.setAttribute("role", "alert");
  const panel = element(
    "div",
    { className: "edit-panel" },
    element("section", {}, element("h3", {}, "Instruktion"), instruction, draftActions),
    message,
    saveStatus,
    element("section", {}, element("h3", {}, "Valt på kartan"), selectionPanel),
    element("section", {}, element("h3", {}, "Hemmet"), summary),
  );

  const shown = () => store.shown;
  const currentFloor = () => ctx.floor();

  /**
   * En våning som skanningen skulle ersätta kan ha försvunnit, till exempel
   * om den togs bort i ett annat fönster. Då läses skanningen in som en ny våning.
   */
  const keepScanTarget = () => {
    const target = scanImport?.target;
    const floors = shown()?.home.floors ?? [];
    if (scanImport !== null && target?.kind === "replace" && !floors.some((f) => f.id === target.floorId)) {
      scanImport = { ...scanImport, target: { kind: "new" } };
    }
  };

  /** Hemmet som det blir med skanningen inlagd, och våningen den hamnar på. */
  const scanPreview = (): { document: HomeDocument; floorId: string } | null => {
    keepScanTarget();
    const document = shown();
    return scanImport === null || document === null
      ? null
      : importScan(document, placeScan(scanImport.scan, scanImport.placement), scanImport.target);
  };

  function showMessage(text: string | null) {
    message.textContent = text ?? "";
    message.hidden = text === null;
  }

  const renderTools = () => {
    for (const [tool, node] of toolButtons) {
      node.setAttribute("aria-pressed", String(tool === drawing.tool));
    }
    zoneToolbox.render(drawing.tool === "zone" ? zoneKind : null);
    // Medan en bild laddas upp eller tas bort visar kartan fortfarande den
    // gamla, så den går inte att skala eller ta bort förrän kärnan är klar.
    const imageChanging = store.imageChanging;
    const hasImage = currentFloor()?.plan_image != null && !imageChanging;
    const scaleButton = toolButtons.get("scale");
    if (scaleButton !== undefined) {
      scaleButton.disabled = !hasImage;
    }
    removeImageButton.disabled = !hasImage;
    uploadButton.disabled = imageChanging;
    renderNewCards();
    renderNewLamps();
  };

  const renderNewCards = () => {
    const document = shown();
    const waiting = ctx.newCards().filter((card) => document === null || findItem(document, card.id) === null);
    newCardsList.hidden = waiting.length === 0;
    newCardsList.replaceChildren(
      element("p", { className: "muted" }, "Nya rumskort som kärnan har hittat:"),
      ...waiting.map((card) => {
        const node = button(
          `Placera ${card.name}`,
          () => {
            chooseTool("node");
            cardToPlace = card;
            ctx.render();
          },
          "tool",
        );
        node.dataset.card = card.id;
        node.setAttribute("aria-pressed", String(cardToPlace?.id === card.id));
        return node;
      }),
    );
  };

  const renderNewLamps = () => {
    const document = shown();
    const waiting = ctx.newLamps().filter((lamp) => document === null || findItem(document, lamp.id) === null);
    newLampsList.hidden = waiting.length === 0;
    newLampsList.replaceChildren(
      element("p", { className: "muted" }, "Nya lampor som kärnan har hittat:"),
      ...waiting.map((lamp) => {
        const node = button(
          `Placera ${lamp.name}`,
          () => {
            chooseTool("lamp");
            lampToPlace = lamp;
            ctx.render();
          },
          "tool",
        );
        node.dataset.lamp = lamp.id;
        node.setAttribute("aria-pressed", String(lampToPlace?.id === lamp.id));
        return node;
      }),
    );
  };

  const instructionText = (): string => {
    if (lampToPlace !== null) {
      return (
        `Klicka där ${lampToPlace.name} sitter, inne i ett rum. Kärnan börjar styra lampan ` +
        "när den är placerad."
      );
    }
    if (cardToPlace !== null) {
      return (
        `Klicka där ${cardToPlace.name} sitter, inne i ett rum. Kärnan börjar använda kortet ` +
        "när det är placerat. Vrid det sedan så att det tittar åt rätt håll."
      );
    }
    return drawing.tool === "zone" ? zoneInstruction(zoneKind) : INSTRUCTIONS[drawing.tool];
  };

  const renderInstruction = () => {
    const draft = drawing.draft;
    instruction.textContent = instructionText();
    draftActions.replaceChildren();
    if (scanImport !== null) {
      instruction.textContent = SCAN_INSTRUCTION;
      draftActions.replaceChildren(
        ...scanForm(scanImport, shown()?.home.floors ?? [], {
          change: changeScan,
          apply: applyScan,
          cancel: cancelDrawing,
        }),
      );
      return;
    }
    if (draft.kind === "polygon") {
      const done = button(drawing.tool === "zone" ? "Stäng zonen" : "Stäng rummet", () =>
        handle(drawing.finish()),
      );
      done.disabled = draft.points.length < 3;
      draftActions.append(
        done,
        button("Ångra hörnet", () => {
          drawing.undo();
          ctx.render();
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

  const panelActions: PanelActions = {
    commit: (edit) => commit(edit),
    showMessage: (text) => showMessage(text),
    deselect: () => ctx.setSelection(null),
  };

  const renderSelection = () => {
    const document = shown();
    const selection = ctx.selection();
    const zone =
      selection?.kind === "zone" && document !== null
        ? zoneProperties(document, selection.id, {
            commit,
            deselect: () => ctx.setSelection(null),
            showMessage,
          })
        : null;
    if (zone !== null) {
      selectionPanel.replaceChildren(...zone);
      return;
    }
    const item = selection?.kind === "item" && document !== null ? findItem(document, selection.id) : null;
    if (item !== null) {
      selectionPanel.replaceChildren(...itemProperties(item, panelActions));
      return;
    }
    const room = selection?.kind === "room" && document !== null ? findRoom(document, selection.id) : null;
    if (room === null) {
      selectionPanel.replaceChildren(
        element(
          "p",
          { className: "muted" },
          "Inget valt. Välj ett rum, en zon, ett rumskort, en router eller en lampa på kartan.",
        ),
      );
      return;
    }
    const name = element("input", { type: "text", value: room.name, maxLength: 100, name: "name" });
    name.addEventListener("change", () => {
      const next = name.value.trim();
      if (next === "") {
        name.value = room.name;
        showMessage("Rummet måste ha ett namn.");
      } else if (next !== room.name) {
        commit((saved) => renameRoom(saved, room.id, next));
      }
    });
    name.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        name.blur();
      }
    });
    selectionPanel.replaceChildren(
      element("label", {}, "Namn ", name),
      element(
        "p",
        { className: "muted" },
        `${formatNumber(polygonArea(room.polygon.map(toPoint)))} m², ${room.polygon.length} hörn`,
      ),
      roomSettingsForm(room, panelActions),
      button(
        "Ta bort rummet",
        () => {
          ctx.setSelection(null);
          commit((saved) => deleteRoom(saved, room.id));
        },
        "danger",
      ),
    );
  };

  const renderSummary = () => {
    const document = shown();
    if (document === null) {
      return;
    }
    const s = summarize(document);
    const rows: [string, string][] = [
      ["Våningar", String(document.home.floors.length)],
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

  // --- Verktyg och ritande ---
  const chooseTool = (tool: ToolName) => {
    drawing.setTool(tool);
    scalePoints = null;
    cardToPlace = null;
    lampToPlace = null;
    scanImport = null;
    if (tool !== "select") {
      ctx.setSelection(null);
    }
    ctx.render();
  };

  // cancelDrawing och handle är vanliga funktioner, eftersom knapparna som
  // ritas ovanför behöver dem innan de har definierats här.
  function cancelDrawing() {
    drawing.cancel();
    scalePoints = null;
    scanImport = null;
    ctx.render();
  }

  /** Visar skanningen vriden, flyttad eller på väg till en annan våning. */
  function changeScan(next: ScanImport) {
    const turned = next.placement.quarterTurns !== scanImport?.placement.quarterTurns;
    scanImport = next;
    ctx.render();
    if (turned) {
      ctx.view().fit(); // vriden kan skanningen bli högre än kartan
    }
  }

  /**
   * Lägger in skanningen som visas och visar våningen den hamnade på. Säger
   * kärnan nej visas skanningen igen, så att den inte behöver läsas in,
   * vridas och flyttas en gång till.
   */
  function applyScan() {
    const current = scanImport;
    const document = shown();
    if (current === null || document === null) {
      return;
    }
    const placed = placeScan(current.scan, current.placement);
    let nextFloor: string;
    try {
      nextFloor = importScan(document, placed, current.target).floorId;
    } catch (error) {
      showMessage(errorText(error));
      return;
    }
    const previousFloor = currentFloor()?.id ?? null;
    scanImport = null;
    ctx.setSelection(null);
    ctx.setFloorId(nextFloor);
    // Ändringen görs om på det sparade hemmet; utan konflikter blir våningens id detsamma.
    commit(
      (saved) => importScan(saved, placed, current.target).document,
      () => {
        scanImport = current;
        if (previousFloor !== null) {
          ctx.setFloorId(previousFloor);
        }
      },
    );
    ctx.view().fit();
  }

  /**
   * Drar punkten till ett hörn som redan finns om den är nära, för en zon
   * annars till en vägg som är nära, och annars till hela centimeter.
   */
  const snap = (point: Point): Point => {
    const radius = SNAP_PIXELS * ctx.view().metersPerPixel();
    const floor = currentFloor();
    const corners = floor === null ? [] : cornersOnFloor(floor);
    let best: Point | null = null;
    for (const corner of corners) {
      if (
        distance(point, corner) <= radius &&
        (best === null || distance(point, corner) < distance(point, best))
      ) {
        best = corner;
      }
    }
    if (best === null && floor !== null && drawing.tool === "zone") {
      best = nearestOnWall(floor, point, radius);
    }
    return best ?? toPoint(toXY(point));
  };

  function handle(outcome: Outcome) {
    const document = shown();
    if (outcome?.kind === "room") {
      const problem = roomProblem(outcome.corners);
      const floor = currentFloor();
      if (problem !== null) {
        showMessage(problem);
      } else if (document !== null && floor !== null) {
        const { roomId, name } = addRoom(document, floor.id, outcome.corners);
        ctx.setSelection({ kind: "room", id: roomId });
        commit((saved) => addRoom(saved, floor.id, outcome.corners, { roomId, name }).document);
        return;
      }
    } else if (outcome?.kind === "zone") {
      const floor = currentFloor();
      const plan =
        document === null || floor === null ? null : planZone(document, floor, zoneKind, outcome.corners);
      if (plan !== null && "problem" in plan) {
        showMessage(plan.problem);
      } else if (plan !== null) {
        ctx.setSelection({ kind: "zone", id: plan.zoneId });
        commit(plan.edit);
        return;
      }
    } else if (outcome?.kind === "scale") {
      scalePoints = outcome;
    } else if (outcome?.kind === "place") {
      place(outcome.item, outcome.point);
      return;
    }
    ctx.render();
  }

  /** Det riktiga kortet eller den riktiga lampan som placeras med nästa klick, om någon. */
  const foundToPlace = (kind: ItemKind): Card | FoundLamp | undefined => {
    if (kind === "node") {
      return cardToPlace ?? undefined;
    }
    return kind === "lamp" ? (lampToPlace ?? undefined) : undefined;
  };

  /**
   * Placerar ett rumskort, en router eller en lampa och väljer den. Ett nytt
   * riktigt kort får kortets namn som id, och en ny riktig lampa lampans id
   * och profil; då börjar kärnan använda dem.
   */
  const place = (kind: ItemKind, point: Point) => {
    const floor = currentFloor();
    const document = shown();
    const found = foundToPlace(kind);
    const placed =
      document === null || floor === null ? null : placeItem(document, kind, floor.id, point, found);
    if (placed === null || floor === null) {
      showMessage("Klicka inne i ett rum. Rumskort och lampor hör till rummet de sitter i.");
      ctx.render();
      return;
    }
    ctx.setSelection({ kind: "item", id: placed.id });
    if (found !== undefined) {
      cardToPlace = null;
      lampToPlace = null;
      ctx.placedFound(found.id);
      drawing.setTool("select");
    }
    // Ändringen görs om på det sparade hemmet när den står på tur. Samma id
    // gör att den nya saken förblir vald.
    // Bara en riktig lampa har en profil; ett rumskort har ingen.
    const profile = found !== undefined && "profile" in found ? found.profile : undefined;
    const given = { id: placed.id, name: placed.name, profile };
    commit((saved) => placeItem(saved, kind, floor.id, point, given)?.document ?? saved);
  };

  const applyScale = (a: Point, b: Point, meters: number) => {
    const floor = currentFloor();
    if (shown() === null || floor?.plan_image == null) {
      return;
    }
    if (!(meters > 0) || distance(a, b) === 0) {
      showMessage("Skriv in ett avstånd större än noll, mellan två olika punkter.");
      return;
    }
    const shownPlan = floor.plan_image;
    if (scalePlanImage(shownPlan, a, b, meters).meters_per_pixel > 1) {
      showMessage("Så stor kan bilden inte bli. Kontrollera avståndet.");
      return;
    }
    drawing.cancel();
    scalePoints = null;
    commit((saved) => {
      const plan = saved.home.floors.find((f) => f.id === floor.id)?.plan_image;
      // Punkterna klickades på bilden som visades; har den bytts gäller de inte.
      if (plan == null || JSON.stringify(plan) !== JSON.stringify(shownPlan)) {
        return saved;
      }
      return setPlanImage(saved, floor.id, scalePlanImage(plan, a, b, meters));
    });
    ctx.view().fit();
  };

  const uploadImage = async (file: File) => {
    const floor = currentFloor();
    if (floor === null) {
      return;
    }
    let image: PreparedImage;
    try {
      image = await ctx.prepareImage(file);
    } catch {
      showMessage("Kunde inte läsa bilden. Välj en PNG-, JPEG- eller WebP-fil.");
      return;
    }
    showMessage(null);
    store.sendImage(
      async (revision) => {
        // Kärnan lägger bilden på våningen och sparar hemmet i samma steg.
        const next = await ctx.api.uploadPlanImage(floor.id, image.blob, image.width, revision);
        ctx.imageUploaded();
        return next;
      },
      () => {
        // Nästa steg är att skala bilden, om användaren inte redan ritar något annat.
        if (drawing.draft.kind === "none") {
          chooseTool("scale");
        }
      },
    );
  };

  const removeImage = () => {
    const floor = currentFloor();
    if (floor === null) {
      return;
    }
    if (drawing.tool === "scale") {
      chooseTool("select");
    }
    showMessage(null);
    store.sendImage((revision) => ctx.api.deletePlanImage(floor.id, revision));
  };

  const readScanFile = async (file: File) => {
    if (file.size > MAX_SCAN_BYTES) {
      showMessage(
        `Filen är ${formatNumber(file.size / 1e6)} MB. En skanning är mycket mindre än så; ` +
          "välj JSON-filen från skanningen.",
      );
      return;
    }
    let text: string;
    try {
      text = await file.text();
    } catch {
      showMessage("Kunde inte läsa filen. Försök igen.");
      return;
    }
    try {
      const scan = readScan(text);
      if (scan.rooms.length === 0) {
        showMessage(["Skanningen gav inga rum.", ...scan.problems].join(" "));
        return;
      }
      const next: ScanImport = { scan, placement: DEFAULT_PLACEMENT, target: { kind: "new" } };
      const document = shown();
      if (document !== null) {
        // Prova att lägga in skanningen innan den visas, så att ett fel syns här och inte vid ritningen.
        importScan(document, placeScan(scan, next.placement), next.target);
      }
      chooseTool("select");
      showMessage(null);
      scanImport = next;
      ctx.render();
      ctx.view().fit();
    } catch (error) {
      showMessage(
        error instanceof ScanError ? error.message : `Kunde inte läsa skanningen: ${errorText(error)}`,
      );
    }
  };

  scanInput.addEventListener("change", () => {
    const file = scanInput.files?.[0];
    scanInput.value = "";
    if (file !== undefined) {
      void readScanFile(file);
    }
  });

  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    fileInput.value = "";
    if (file !== undefined) {
      void uploadImage(file);
    }
  });

  /**
   * Kartans klick och drag gör inget medan kartan visar en skanning, som inte
   * går att ändra förrän den är inlagd.
   */
  const unlessPreviewingScan = (callbacks: EditCallbacks): EditCallbacks => {
    const guarded: Record<string, unknown> = {};
    for (const [name, handler] of Object.entries(callbacks) as [string, (...a: never[]) => void][]) {
      guarded[name] = (...args: never[]) => {
        if (scanImport === null) {
          handler(...args);
        }
      };
    }
    return guarded as unknown as EditCallbacks;
  };

  const callbacks = unlessPreviewingScan({
    onPointerDown: (point) => {
      // Med Välj och väggverktyget sköter kartan klicket själv. Att rita om nu
      // skulle byta ut det som är under pekaren mitt i klicket, och då kommer
      // klicket aldrig fram.
      if (mapHandlesClicks(drawing.tool)) {
        return;
      }
      if (drawing.tool === "scale" && currentFloor()?.plan_image == null) {
        return;
      }
      if (drawing.tool === "scale") {
        scalePoints = null; // ett nytt klick börjar om med två nya punkter
      }
      // Hörnen dras till befintliga hörn; det som placeras ut hamnar där man klickar.
      const snapped = drawing.tool === "scale" || isPlacingTool(drawing.tool) ? point : snap(point);
      handle(drawing.pointerDown(snapped, SNAP_PIXELS * ctx.view().metersPerPixel()));
    },
    onPointerMove: (point) => {
      if (drawing.draft.kind !== "none") {
        drawing.pointerMove(drawing.tool === "scale" ? point : snap(point));
        ctx.render();
      }
    },
    onPointerUp: (point) => {
      if (drawing.tool === "rectangle") {
        handle(drawing.pointerUp(snap(point)));
      }
    },
    onSelect: (next) => {
      ctx.setSelection(next);
      ctx.render();
    },
    onSelectItem: (itemId) => {
      ctx.setSelection({ kind: "item", id: itemId });
      ctx.render();
    },
    onMoveItem: (itemId, point) => {
      ctx.setSelection({ kind: "item", id: itemId });
      commit((saved) => moveItem(saved, itemId, point));
    },
    onRotateNode: (nodeId, rotation) => {
      commit((saved) => updateItem(saved, nodeId, { rotation: normalizeRotation(rotation) }));
    },
    onClickWall: (roomId, index) => {
      commit((saved) => cycleWall(saved, roomId, index));
    },
    onMoveRoom: (roomId, dx, dy) => {
      if (dx !== 0 || dy !== 0) {
        commit((saved) => moveRoom(saved, roomId, dx, dy));
      }
    },
  });

  return {
    toolbar,
    panel,
    callbacks,
    scene: () => ({
      tool: drawing.tool,
      draft: drawing.draft,
      draftColor: drawing.tool === "zone" ? ZONE_STYLES[zoneKind].color : null,
      preview: scanPreview(),
    }),
    render() {
      keepScanTarget();
      renderTools();
      renderInstruction();
      renderSelection();
      renderSummary();
    },
    showMessage,
    busy(busy) {
      if (busy) {
        saveStatus.textContent = "Sparar …";
      } else if (message.hidden) {
        saveStatus.textContent = "Alla ändringar är sparade.";
      }
    },
    failed(error, conflict) {
      showMessage(errorText(error));
      saveStatus.textContent = conflict ? "Hemmet har lästs om." : "Ändringen sparades inte.";
    },
    keydown(event) {
      if (event.key === "Escape") {
        cancelDrawing();
        return true;
      }
      if (event.key === "Backspace" && drawing.draft.kind !== "none") {
        drawing.undo();
        ctx.render();
        return true;
      }
      if (event.key === "Enter" && drawing.draft.kind !== "none") {
        handle(drawing.finish());
        return true;
      }
      return false;
    },
    reset() {
      drawing.setTool("select");
      scalePoints = null;
      cardToPlace = null;
      lampToPlace = null;
      scanImport = null;
      showMessage(null);
    },
  };
}
