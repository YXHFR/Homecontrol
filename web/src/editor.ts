/**
 * Kartredigeraren: verktygen till vänster, kartan i mitten och egenskaperna
 * till höger (se #34). På en smal skärm staplas de. Väggar, rumskort,
 * routrar och lampor ändras med placing.ts och visas med editorItems.ts och
 * itemPanel.ts (se #35). Zonerna (#36) har sina delar i zones.ts och
 * zonePanel.ts. En iPhone-skanning läses med roomplan.ts och läggs in med
 * scanPanel.ts (#86).
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
import { button, element } from "./dom";
import {
  createKonvaEditorView,
  type EditorViewCallbacks,
  type EditorViewFactory,
  type Selection,
} from "./editorView";
import { coreApi, CoreError, type HomeApi } from "./homeApi";
import type { FloorModel, HomeDocument } from "./homefile";
import { itemProperties, type PanelActions, roomSettingsForm } from "./itemPanel";
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

export interface EditorDependencies {
  api: HomeApi;
  createView: EditorViewFactory;
  prepareImage(file: File): Promise<PreparedImage>;
}

/** Större bilder förminskas; mer än så här syns ändå inte på en skärm. */
const MAX_IMAGE_SIDE = 4000;
/** Så ofta redigeraren frågar kärnan om nya rumskort och lampor. */
const FOUND_REFRESH_MS = 10_000;
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

/** En ändring av hemmet, som görs på det sparade hemmet när den står på tur. */
type Edit = (document: HomeDocument) => HomeDocument;

/** Något som ska skickas till kärnan: en ändring av hemmet eller av en bild. */
type Change =
  /** `failed` körs om kärnan säger nej, innan kartan visar hemmet igen. */
  | { kind: "edit"; edit: Edit; failed?: () => void }
  | { kind: "image"; send: () => Promise<void>; done?: () => void };

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
  walls: "Väggar och dörrar",
  zone: "Zon", // visas inte: zonerna har en knapp per sort (zonePanel.ts)
  node: `${ITEM_LABELS.node} (ESP32)`,
  router: ITEM_LABELS.router,
  lamp: ITEM_LABELS.lamp,
};

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function formatNumber(value: number, decimals = 1): string {
  return value.toLocaleString("sv-SE", { maximumFractionDigits: decimals });
}

/** Om det valda finns i hemmet. En ny sort som går att välja måste läggas till här. */
function stillThere(document: HomeDocument, selection: Selection): boolean {
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
  /** Det valda rummet, zonen, rumskortet, routern eller lampan. Bara en sak väljs åt gången. */
  let selection: Selection = null;
  /** Sorten av zon som ritas med verktyget Zon. */
  let zoneKind: ZoneKind = "vanlig";
  /** Punkterna som ska skalas mellan, när användaren har klickat ut två. */
  let scalePoints: { a: Point; b: Point } | null = null;
  /** Ändras när en ny bild laddas upp, så att webbläsaren inte visar den gamla. */
  let imageVersion = Date.now();
  /** Nya kort som kärnan har hittat, och kortet som placeras med nästa klick. */
  let newCards: Card[] = [];
  let cardToPlace: Card | null = null;
  /** Riktiga lampor som kärnan har hittat men som inte är placerade, och den som placeras nu. */
  let newLamps: FoundLamp[] = [];
  let lampToPlace: FoundLamp | null = null;
  /** Ändringarna skickas en i taget. */
  let queue: Promise<void> = Promise.resolve();
  let savedCount = 0;
  /** En iPhone-skanning som visas på kartan men inte är inlagd än. */
  let scanImport: ScanImport | null = null;

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
  /** Riktiga rumskort som kärnan har hittat men som inte är placerade än. */
  const newCardsList = element("div", { className: "new-cards", hidden: true });
  newCardsList.setAttribute("aria-label", "Nya rumskort");
  const newLampsList = element("div", { className: "new-lamps", hidden: true });
  newLampsList.setAttribute("aria-label", "Nya lampor");
  const toolbox = element(
    "aside",
    { className: "toolbox" },
    element("h2", {}, "Planritning"),
    uploadButton,
    fileInput,
    toolButton("scale"),
    removeImageButton,
    scanButton,
    scanInput,
    element("h2", {}, "Rita"),
    toolButton("select"),
    toolButton("polygon"),
    toolButton("rectangle"),
    toolButton("walls"),
    ...zoneToolbox.nodes,
    element("h2", {}, "Placera"),
    toolButton("node"),
    newCardsList,
    toolButton("router"),
    toolButton("lamp"),
    newLampsList,
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
  const selectionPanel = element("div", { className: "selection" });
  const summary = element("dl", { className: "summary" });
  const properties = element(
    "aside",
    { className: "properties" },
    element("section", {}, element("h2", {}, "Instruktion"), instruction, draftActions),
    element("section", {}, element("h2", {}, "Valt på kartan"), selectionPanel),
    element("section", {}, element("h2", {}, "Hemmet"), summary),
  );

  root.replaceChildren(
    element("h1", {}, "Homecontrol – kartredigerare"),
    element("div", { className: "editor" }, toolbox, map, properties),
  );

  const currentFloor = (): FloorModel | null =>
    shown?.home.floors.find((floor) => floor.id === floorId) ?? null;

  /** Hemmet som det blir med skanningen inlagd, och våningen den hamnar på. */
  const scanPreview = (): { document: HomeDocument; floorId: string } | null =>
    scanImport === null || shown === null
      ? null
      : importScan(shown, placeScan(scanImport.scan, scanImport.placement), scanImport.target);

  const showMessage = (text: string | null) => {
    message.textContent = text ?? "";
    message.hidden = text === null;
  };

  // --- Ritning av allt utom kartan ---
  const renderTools = () => {
    for (const [tool, node] of toolButtons) {
      node.setAttribute("aria-pressed", String(tool === drawing.tool));
    }
    zoneToolbox.render(drawing.tool === "zone" ? zoneKind : null);
    // Medan en bild laddas upp eller tas bort visar kartan fortfarande den
    // gamla, så den går inte att skala eller ta bort förrän kärnan är klar.
    const imageChanging = waiting.some((change) => change.kind === "image");
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
    const waiting = newCards.filter((card) => shown === null || findItem(shown, card.id) === null);
    newCardsList.hidden = waiting.length === 0;
    newCardsList.replaceChildren(
      element("p", { className: "muted" }, "Nya rumskort som kärnan har hittat:"),
      ...waiting.map((card) => {
        const node = button(
          `Placera ${card.name}`,
          () => {
            chooseTool("node");
            cardToPlace = card;
            render();
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
    const waiting = newLamps.filter((lamp) => shown === null || findItem(shown, lamp.id) === null);
    newLampsList.hidden = waiting.length === 0;
    newLampsList.replaceChildren(
      element("p", { className: "muted" }, "Nya lampor som kärnan har hittat:"),
      ...waiting.map((lamp) => {
        const node = button(
          `Placera ${lamp.name}`,
          () => {
            chooseTool("lamp");
            lampToPlace = lamp;
            render();
          },
          "tool",
        );
        node.dataset.lamp = lamp.id;
        node.setAttribute("aria-pressed", String(lampToPlace?.id === lamp.id));
        return node;
      }),
    );
  };

  /** Läser om vilka lampor kärnan har hittat. Ett fel här stoppar inte redigeraren. */
  const refreshLamps = () => {
    deps.api.lamps().then(
      (lamps) => {
        newLamps = lamps.filter((lamp) => !lamp.placed);
        renderTools();
      },
      () => undefined,
    );
  };

  /** Läser om vilka kort kärnan har hittat. Ett fel här stoppar inte redigeraren. */
  const refreshCards = () => {
    deps.api.cards().then(
      (cards) => {
        newCards = cards.filter((card) => !card.placed);
        renderTools();
      },
      () => undefined,
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
        ...scanForm(scanImport, shown?.home.floors ?? [], {
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

  const panelActions: PanelActions = {
    commit: (edit) => commit(edit),
    showMessage: (text) => showMessage(text),
    deselect: () => {
      selection = null;
    },
  };

  const renderSelection = () => {
    const zone =
      selection?.kind === "zone" && shown !== null
        ? zoneProperties(shown, selection.id, {
            commit,
            deselect: () => (selection = null),
            showMessage,
          })
        : null;
    if (zone !== null) {
      selectionPanel.replaceChildren(...zone);
      return;
    }
    const item =
      selection?.kind === "item" && shown !== null ? findItem(shown, selection.id) : null;
    if (item !== null) {
      selectionPanel.replaceChildren(...itemProperties(item, panelActions));
      return;
    }
    const room =
      selection?.kind === "room" && shown !== null ? findRoom(shown, selection.id) : null;
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
      } else if (next !== room.name && shown !== null) {
        commit((document) => renameRoom(document, room.id, next));
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
          if (shown !== null) {
            selection = null;
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
          selection = null;
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
    const preview = scanPreview();
    const floor =
      preview?.document.home.floors.find((candidate) => candidate.id === preview.floorId) ??
      currentFloor();
    if (floor === null) {
      return;
    }
    view.render({
      floor,
      planImage:
        floor.plan_image == null
          ? null
          : { url: `${deps.api.planImageUrl(floor.id)}?v=${imageVersion}`, plan: floor.plan_image },
      selection,
      tool: drawing.tool,
      draftColor: drawing.tool === "zone" ? ZONE_STYLES[zoneKind].color : null,
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

  /** Räknar fram det som visas och glömmer det valda om det inte längre finns. */
  const refreshShown = () => {
    shown = saved;
    for (const change of waiting) {
      if (shown !== null && change.kind === "edit") {
        shown = change.edit(shown);
      }
    }
    if (shown !== null && !stillThere(shown, selection)) {
      selection = null;
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
    let succeeded = false;
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
      succeeded = true;
      savedCount++;
      root.dataset.saves = String(savedCount);
    } catch (error) {
      // Resten av kön byggde på den här ändringen och skickas inte heller.
      waiting = [];
      if (change.kind === "edit") {
        change.failed?.();
      }
      changedView = true;
      showMessage(errorText(error));
      saveStatus.textContent =
        error instanceof CoreError && error.conflict
          ? "Hemmet har lästs om."
          : "Ändringen sparades inte.";
      try {
        await reload();
      } catch {
        // Kärnan går inte att nå; visa det som senast sparades. Det kan vara
        // inaktuellt, så nästa ändring får skickas utan giltig revision. Då
        // nekar kärnan den (409) och hemmet läses om i stället för att skrivas över.
        revision = "";
      }
    }
    if (changedView) {
      refreshShown();
      render();
    }
    if (succeeded && change.kind === "image") {
      change.done?.();
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
  const commit = (edit: Edit, failed?: () => void) => enqueue({ kind: "edit", edit, failed });

  // --- Verktyg och ritande ---
  const chooseTool = (tool: ToolName) => {
    drawing.setTool(tool);
    scalePoints = null;
    cardToPlace = null;
    lampToPlace = null;
    scanImport = null;
    if (tool !== "select") {
      selection = null;
    }
    render();
  };

  // cancelDrawing och handle är vanliga funktioner, eftersom knapparna som
  // ritas ovanför behöver dem innan de har definierats här.
  function cancelDrawing() {
    drawing.cancel();
    scalePoints = null;
    scanImport = null;
    render();
  }

  /** Visar skanningen vriden, flyttad eller på väg till en annan våning. */
  function changeScan(next: ScanImport) {
    const turned = next.placement.quarterTurns !== scanImport?.placement.quarterTurns;
    scanImport = next;
    render();
    if (turned) {
      view.fit(); // vriden kan skanningen bli högre än kartan
    }
  }

  /**
   * Lägger in skanningen som visas och visar våningen den hamnade på. Säger
   * kärnan nej visas skanningen igen, så att den inte behöver läsas in,
   * vridas och flyttas en gång till.
   */
  function applyScan() {
    const current = scanImport;
    if (current === null || shown === null) {
      return;
    }
    const placed = placeScan(current.scan, current.placement);
    const previousFloor = floorId;
    scanImport = null;
    selection = null;
    floorId = importScan(shown, placed, current.target).floorId;
    // Ändringen görs om på det sparade hemmet; utan konflikter blir våningens id detsamma.
    commit(
      (document) => importScan(document, placed, current.target).document,
      () => {
        scanImport = current;
        floorId = previousFloor;
      },
    );
    view.fit();
  }

  /**
   * Drar punkten till ett hörn som redan finns om den är nära, för en zon
   * annars till en vägg som är nära, och annars till hela centimeter. Punkter utanför planet (till vänster om eller ovanför origo)
   * flyttas in till kanten.
   */
  const snap = (point: Point): Point => {
    const radius = SNAP_PIXELS * view.metersPerPixel();
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
    if (outcome?.kind === "room") {
      const problem = roomProblem(outcome.corners);
      const floor = currentFloor();
      if (problem !== null) {
        showMessage(problem);
      } else if (shown !== null && floor !== null) {
        const { roomId, name } = addRoom(shown, floor.id, outcome.corners);
        selection = { kind: "room", id: roomId };
        commit(
          (document) => addRoom(document, floor.id, outcome.corners, { roomId, name }).document,
        );
        return;
      }
    } else if (outcome?.kind === "zone") {
      const floor = currentFloor();
      const plan =
        shown === null || floor === null ? null : planZone(shown, floor, zoneKind, outcome.corners);
      if (plan !== null && "problem" in plan) {
        showMessage(plan.problem);
      } else if (plan !== null) {
        selection = { kind: "zone", id: plan.zoneId };
        commit(plan.edit);
        return;
      }
    } else if (outcome?.kind === "scale") {
      scalePoints = outcome;
    } else if (outcome?.kind === "place") {
      place(outcome.item, outcome.point);
      return;
    }
    render();
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
    const found = foundToPlace(kind);
    const placed =
      shown === null || floor === null ? null : placeItem(shown, kind, floor.id, point, found);
    if (placed === null || floor === null) {
      showMessage("Klicka inne i ett rum. Rumskort och lampor hör till rummet de sitter i.");
      render();
      return;
    }
    selection = { kind: "item", id: placed.id };
    if (found !== undefined) {
      cardToPlace = null;
      lampToPlace = null;
      newCards = newCards.filter((other) => other.id !== found.id);
      newLamps = newLamps.filter((other) => other.id !== found.id);
      drawing.setTool("select");
    }
    // Ändringen görs om på det sparade hemmet när den står på tur. Samma id
    // gör att den nya saken förblir vald.
    // Bara en riktig lampa har en profil; ett rumskort har ingen.
    const profile = found !== undefined && "profile" in found ? found.profile : undefined;
    const given = { id: placed.id, name: placed.name, profile };
    commit((document) => placeItem(document, kind, floor.id, point, given)?.document ?? document);
  };

  const applyScale = (a: Point, b: Point, meters: number) => {
    const floor = currentFloor();
    if (shown === null || floor?.plan_image == null) {
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
    commit((document) => {
      const plan = document.home.floors.find((f) => f.id === floor.id)?.plan_image;
      // Punkterna klickades på bilden som visades; har den bytts gäller de inte.
      if (plan == null || JSON.stringify(plan) !== JSON.stringify(shownPlan)) {
        return document;
      }
      return setPlanImage(document, floor.id, scalePlanImage(plan, a, b, meters));
    });
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
      },
      done: () => {
        // Nästa steg är att skala bilden, om användaren inte redan ritar något annat.
        if (drawing.draft.kind === "none") {
          chooseTool("scale");
        }
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
      if (shown !== null) {
        // Prova att lägga in skanningen innan den visas, så att ett fel syns här och inte vid ritningen.
        importScan(shown, placeScan(scan, next.placement), next.target);
      }
      chooseTool("select");
      showMessage(null);
      scanImport = next;
      render();
      view.fit();
    } catch (error) {
      showMessage(
        error instanceof ScanError
          ? error.message
          : `Kunde inte läsa skanningen: ${errorText(error)}`,
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
  const unlessPreviewingScan = (callbacks: EditorViewCallbacks): EditorViewCallbacks => {
    const guarded: Record<string, unknown> = {};
    for (const [name, handler] of Object.entries(callbacks) as [
      string,
      (...a: never[]) => void,
    ][]) {
      guarded[name] = (...args: never[]) => {
        if (scanImport === null) {
          handler(...args);
        }
      };
    }
    return guarded as unknown as EditorViewCallbacks;
  };

  const view = deps.createView(
    stageContainer,
    unlessPreviewingScan({
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
        const snapped =
          drawing.tool === "scale" || isPlacingTool(drawing.tool) ? point : snap(point);
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
      onSelect: (next) => {
        selection = next;
        render();
      },
      onSelectItem: (itemId) => {
        selection = { kind: "item", id: itemId };
        render();
      },
      onMoveItem: (itemId, point) => {
        selection = { kind: "item", id: itemId };
        commit((document) => moveItem(document, itemId, point));
      },
      onRotateNode: (nodeId, rotation) => {
        commit((document) =>
          updateItem(document, nodeId, { rotation: normalizeRotation(rotation) }),
        );
      },
      onClickWall: (roomId, index) => {
        commit((document) => cycleWall(document, roomId, index));
      },
      onMoveRoom: (roomId, dx, dy) => {
        if (shown !== null && (dx !== 0 || dy !== 0)) {
          commit((document) => moveRoom(document, roomId, dx, dy));
        }
      },
    }),
  );

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
  // Nya kort och lampor dyker upp medan redigeraren är öppen, till exempel
  // när ett kort just har installerats eller en lampa parats ihop.
  window.setInterval(() => {
    refreshCards();
    refreshLamps();
  }, FOUND_REFRESH_MS);
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
      refreshCards();
      refreshLamps();
      root.dataset.ready = "true";
    },
    (error: unknown) => {
      saveStatus.textContent = "Kunde inte läsa hemmet. Ladda om sidan för att försöka igen.";
      showMessage(errorText(error));
    },
  );
}
