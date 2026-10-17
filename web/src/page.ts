/**
 * Homecontrol är en enda sida: toppraden med hälsoraden, kartan över rummet
 * och ett kort för det som är valt (ett bottenark på mobilen och en
 * sidopanel på datorn, med samma innehåll).
 *
 * - Ett tryck på en sak på kartan öppnar dess kort. Inget flyttas förrän
 *   man trycker på Flytta i kortet; då går bara den saken att dra, och varje
 *   släpp sparas direkt med "Sparat · Ångra".
 * - Ändra rummet öppnar ritverktygen (editMode.ts).
 * - Hemmet läses och sparas genom HomeStore (REST), och spåren, lamporna och
 *   korten kommer från kärnans realtidsström (WebSocket). Medan något dras
 *   eller sparas tar sidan inte emot nya hem från strömmen, så att inget
 *   hoppar tillbaka; spåren och lamporna visas som vanligt.
 *
 * Sidan visar en våning och ett rum, men hemmet har listor av våningar och
 * rum, så att fler kan visas senare.
 *
 * Attributen data-ready, data-busy, data-saves, data-rooms och data-mode på
 * roten finns för webbläsartesterna, som väntar på dem.
 */
import { coreCalibrationApi, createCalibrationPanel, type CalibrationApi } from "./calibrationPanel";
import {
  LiveConnection,
  liveUrl,
  type ConnectionHandlers,
  type ConnectionStatus,
} from "./connection";
import { hasDoorbell } from "./door";
import { button, element } from "./dom";
import { inPolygon, toPoint } from "./editing";
import { createEditMode, type PreparedImage, shrinkImage, stillThere } from "./editMode";
import { parkingSpot } from "./geometry";
import { healthSummary, type HealthTarget, type NewDevice } from "./health";
import { coreApi, type HomeApi } from "./homeApi";
import type { FloorModel, HomeDocument, NodeModel, RoomModel, XY } from "./homefile";
import { errorText, type Edit, HomeStore } from "./homeStore";
import {
  createKonvaMapView,
  type MapCallbacks,
  type MapScene,
  type MapView,
  type MapViewFactory,
  type Selection,
} from "./mapView";
import { findItem, moveItem, normalizeRotation, placeItem, roomAt, updateItem } from "./placing";
import type { Card, ClientMessage, Floor, FoundLamp, HomeMessage, Point, StateMessage } from "./protocol";
import {
  type CardActions,
  type CardData,
  lampCard,
  moveCard,
  newDeviceCard,
  nodeCard,
  roomCard,
  routerCard,
} from "./sheetCards";
import { luxText } from "./roomPanels";
import { createSimulationControls } from "./simulationPanel";
import { tracksOnFloor } from "./trackStyles";

export interface Connection {
  start(): void;
  send(message: ClientMessage): void;
}

/** Det sidan behöver utifrån. Testerna byter ut det. */
export interface PageDependencies {
  api: HomeApi;
  createMap: MapViewFactory;
  prepareImage(file: File): Promise<PreparedImage>;
  connect(handlers: ConnectionHandlers): Connection;
  calibration: CalibrationApi;
  /** Om det finns en dörrklocka, så att länken till Dörren ska visas. */
  hasDoorbell(): Promise<boolean>;
}

const defaultDependencies: PageDependencies = {
  api: coreApi,
  createMap: createKonvaMapView,
  prepareImage: shrinkImage,
  connect: (handlers) => new LiveConnection(liveUrl(window.location), handlers),
  calibration: coreCalibrationApi,
  hasDoorbell: () => hasDoorbell(),
};

/** Så ofta sidan frågar kärnan om nya rumskort och lampor. */
const FOUND_REFRESH_MS = 10_000;
/** Så länge "Sparat · Ångra" visas efter en flytt. */
export const UNDO_MS = 8000;
/** Så länge "Räknar om …" visas som längst, om inga nya spår kommer. */
const RECALCULATING_MS = 6000;
/** Så långt en piltangent flyttar, i meter, och hur länge sidan väntar med att spara. */
const NUDGE_METERS = 0.05;
const NUDGE_SAVE_MS = 600;
/** Så många grader Skift+pil vrider ett rumskort. */
const TURN_DEGREES = 15;

export const DATABASE_DOWN =
  "Databasen svarar inte. Lamporna styrs som vanligt, men ändringar kan inte sparas just nu. " +
  "Kärnan försöker ansluta igen av sig själv.";

/** Texten när någon annan hann ändra hemmet (409). */
export const CONFLICT_TEXT = "Någon annan ändrade – ladda om";

/** Våningen som visas: entréplanet, eller den första om inget plan är 0. */
export function startFloor<T extends { id: string; level: number }>(floors: T[]): T | undefined {
  return floors.find((floor) => floor.level === 0) ?? floors[0];
}

/** Var en sak står och hur den är vriden, för att kunna ställa tillbaka den. */
interface Placement {
  position: XY;
  rotation: number | undefined;
}

function placementOf(document: HomeDocument | null, itemId: string): Placement | null {
  const found = document === null ? null : findItem(document, itemId);
  if (found === null) {
    return null;
  }
  return {
    position: found.item.position,
    rotation: found.kind === "node" ? ((found.item as NodeModel).rotation ?? 0) : undefined,
  };
}

/** Ställer tillbaka saken där den stod och som den var vriden. */
function restore(itemId: string, placement: Placement): Edit {
  return (document) => {
    const moved = moveItem(document, itemId, toPoint(placement.position));
    return placement.rotation === undefined
      ? moved
      : updateItem(moved, itemId, { rotation: placement.rotation });
  };
}

/** En punkt mitt i rummet, där en ny enhet hamnar innan den flyttas dit den sitter. */
function middleOf(floor: FloorModel, room: RoomModel): Point {
  const corners = room.polygon.map(toPoint);
  const middle = {
    x: corners.reduce((sum, p) => sum + p.x, 0) / corners.length,
    y: corners.reduce((sum, p) => sum + p.y, 0) / corners.length,
  };
  if (roomAt(floor, middle)?.id === room.id) {
    return middle;
  }
  // Ett L-format rum kan ha mitten utanför sig; ta då en punkt strax innanför första hörnet.
  const inside = corners.map((corner) => ({
    x: corner.x + (middle.x - corner.x) * 0.2,
    y: corner.y + (middle.y - corner.y) * 0.2,
  }));
  return inside.find((point) => inPolygon(point, corners)) ?? corners[0];
}

type Mode = "view" | "move" | "edit";
type SheetHeight = "low" | "half" | "high";
const SHEET_HEIGHTS: SheetHeight[] = ["low", "half", "high"];

export function mountPage(root: HTMLElement, deps: PageDependencies = defaultDependencies): void {
  // --- Läget ---
  let connectionStatus: ConnectionStatus = "connecting";
  let state: StateMessage | null = null;
  /** Hemmet som kärnan senast skickade i realtidsströmmen, och ett som väntar medan sidan är låst. */
  let liveHome: HomeMessage | null = null;
  let waitingHome: HomeMessage | null = null;
  let floorId: string | null = null;
  /** Rummet vars kort visas när inget är valt: det första, eller det man tryckte på. */
  let roomId: string | null = null;
  let selection: Selection = null;
  /** En ny enhet vars kort visas. */
  let newDeviceId: string | null = null;
  let mode: Mode = "view";
  /** Saken som flyttas, och var den stod när Flytta trycktes. */
  let moving: { itemId: string; before: Placement } | null = null;
  /** Något dras på kartan just nu. */
  let dragging = false;
  /** En flytt med piltangenterna som sparas när tangenterna har släppts en stund. */
  let nudge: { itemId: string; point: Point; timer: number } | null = null;
  let foundCards: Card[] = [];
  let foundLamps: FoundLamp[] = [];
  const lastHeard = new Map<string, number>();
  let imageVersion = Date.now();
  let recalculating: { since: number; timer: number } | null = null;
  let toastTimer = 0;
  let personsAdded = 0;
  let loadError: string | null = null;

  // --- Toppraden ---
  const healthDot = element("span", { className: "dot" });
  const healthText = element("span", { className: "text" }, "Ansluter …");
  const health = button("", () => openHealthTarget(), "health");
  health.replaceChildren(healthDot, healthText);
  health.setAttribute("aria-live", "polite");
  const brand = element(
    "h1",
    { className: "brand" },
    element("span", { className: "brand-mark" }),
    element("span", { className: "word" }, "Homecontrol"),
  );
  const simBadge = element("span", { className: "badge", hidden: true }, "Simulering");
  const doorLink = element("a", { className: "btn ghost door-link", href: "#dorren", hidden: true }, "Dörren");
  const simButton = button("Sim", () => {
    const simulation = state?.simulation;
    if (simulation != null) {
      send({ type: "set_simulation", enabled: !simulation.enabled });
    }
  }, "sim");
  simButton.hidden = true;
  simButton.setAttribute("aria-label", "Simuleringsläge");
  const modeTitle = element("span", { className: "title" });
  const cancelButton = button("Avbryt", () => cancelMove(), "cancel");
  const doneButton = button("Klar", () => finishMode(), "primary done");
  const topbar = element("header", { className: "topbar" });

  const databaseWarning = element("p", { className: "database-warning", hidden: true }, DATABASE_DOWN);
  databaseWarning.setAttribute("role", "alert");

  // --- Kartan ---
  const stageContainer = element("div", { className: "map-stage" });
  const zoomOut = button("−", () => view.zoom(0.8));
  zoomOut.setAttribute("aria-label", "Zooma ut");
  const zoomIn = button("+", () => view.zoom(1.25));
  zoomIn.setAttribute("aria-label", "Zooma in");
  const fitButton = button("⌖", () => view.fit());
  fitButton.setAttribute("aria-label", "Visa hela rummet");
  const sizeText = element("span", { className: "size mono" });
  const mapControls = element("div", { className: "map-controls" }, sizeText, zoomOut, zoomIn, fitButton);
  const recalcMessage = element(
    "div",
    { className: "float-message recalculating", hidden: true },
    element("span", { className: "spinner" }),
    "Räknar om …",
  );
  recalcMessage.setAttribute("role", "status");
  const toastText = element("span", { className: "text" });
  const toastAction = button("", () => undefined);
  const toast = element("div", { className: "toast", hidden: true }, toastText, toastAction);
  toast.setAttribute("aria-live", "polite");
  const simulationControls = createSimulationControls(send, () => addPerson());
  const mapwrap = element("div", { className: "mapwrap" }, stageContainer);

  // --- Arket ---
  const sheetBody = element("div", { className: "sheet-body" });
  const sheetHandle = button("", () => cycleSheet(), "sheet-handle");
  sheetHandle.setAttribute("aria-label", "Ändra arkets höjd");
  const sheet = element("aside", { className: "sheet" }, sheetHandle, sheetBody);
  sheet.setAttribute("aria-label", "Kort");
  sheet.dataset.height = "low";

  root.classList.add("page");
  root.replaceChildren(
    topbar,
    databaseWarning,
    element("div", { className: "main" }, mapwrap, sheet),
  );

  const store = new HomeStore(deps.api, {
    changed: () => render(),
    busy: (busy) => {
      root.dataset.busy = String(busy);
      root.dataset.saves = String(store.savedCount);
      editMode.busy(busy);
      if (!busy) {
        takeWaitingHome();
      }
    },
    failed: (error, conflict) => {
      if (mode === "edit") {
        editMode.failed(error, conflict);
        return;
      }
      if (conflict) {
        showToast(CONFLICT_TEXT, "bad", { label: "Ladda om", run: () => window.location.reload() });
      } else {
        showToast(errorText(error), "bad");
      }
    },
  });

  const calibration = createCalibrationPanel(deps.calibration);

  // --- Det som visas ---
  /** Hemmet som visas: det sparade, med en flytt med piltangenterna ovanpå. */
  const shown = (): HomeDocument | null => {
    const document = store.shown;
    return document !== null && nudge !== null ? moveItem(document, nudge.itemId, nudge.point) : document;
  };

  const currentFloor = (): FloorModel | null => {
    const floors = shown()?.home.floors ?? [];
    return floors.find((floor) => floor.id === floorId) ?? startFloor(floors) ?? null;
  };

  const currentRoom = (): RoomModel | null => {
    const rooms = currentFloor()?.rooms ?? [];
    return rooms.find((room) => room.id === roomId) ?? rooms[0] ?? null;
  };

  /** Våningen som realtidsströmmen beskriver, för lampkalibreringen. */
  const liveFloor = (): Floor | null =>
    liveHome?.floors.find((floor) => floor.id === currentFloor()?.id) ?? null;

  const virtualLamps = (): Set<string> =>
    new Set(
      liveHome?.floors.flatMap((floor) => floor.lamps.filter((lamp) => lamp.virtual).map((lamp) => lamp.id)),
    );

  const databaseDown = () => state?.database != null && !state.database.connected;

  /** Riktiga kort som kärnan har hittat men som inte är placerade. */
  const unplacedCards = (): Card[] => {
    const document = store.shown;
    const cards = new Map<string, Card>();
    for (const card of [...foundCards, ...(state?.cards ?? [])]) {
      if (!card.placed && (document === null || findItem(document, card.id) === null)) {
        cards.set(card.id, card);
      }
    }
    return [...cards.values()];
  };

  const unplacedLamps = (): FoundLamp[] => {
    const document = store.shown;
    return foundLamps.filter((lamp) => !lamp.placed && (document === null || findItem(document, lamp.id) === null));
  };

  const newDevices = (): NewDevice[] => [
    ...unplacedCards().map((card) => ({ id: card.id, name: card.name, kind: "node" as const })),
    ...unplacedLamps().map((lamp) => ({ id: lamp.id, name: lamp.name, kind: "lamp" as const })),
  ];

  // --- Hämtning av nya kort och lampor ---
  const refreshFound = () => {
    deps.api.cards().then(
      (cards) => {
        foundCards = cards;
        render();
      },
      () => undefined,
    );
    deps.api.lamps().then(
      (lamps) => {
        foundLamps = lamps;
        render();
      },
      () => undefined,
    );
  };

  // --- Ändra rummet ---
  const editMode = createEditMode({
    api: deps.api,
    store,
    prepareImage: deps.prepareImage,
    view: () => view,
    floor: currentFloor,
    setFloorId: (id) => {
      floorId = id;
    },
    selection: () => selection,
    setSelection: (next) => {
      selection = next;
    },
    newCards: unplacedCards,
    newLamps: unplacedLamps,
    placedFound: (id) => {
      foundCards = foundCards.filter((card) => card.id !== id);
      foundLamps = foundLamps.filter((lamp) => lamp.id !== id);
    },
    render: () => render(),
    imageUploaded: () => {
      imageVersion = Date.now();
    },
  });

  // --- Meddelanden ---
  function showToast(
    text: string,
    level: "info" | "bad" = "info",
    action: { label: string; run: () => void } | null = null,
    ms = UNDO_MS,
  ) {
    toastText.textContent = text;
    toast.dataset.level = level;
    toastAction.hidden = action === null;
    toastAction.textContent = action?.label ?? "";
    toastAction.onclick = () => {
      hideToast();
      action?.run();
    };
    toast.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(hideToast, ms);
  }

  function hideToast() {
    toast.hidden = true;
    window.clearTimeout(toastTimer);
  }

  const showMessage = (text: string) => {
    if (mode === "edit") {
      editMode.showMessage(text);
    } else {
      showToast(text, "bad");
    }
  };

  function send(message: ClientMessage) {
    connection.send(message);
  }

  // --- Realtidsströmmen ---
  /** Sidan är låst för nya hem medan något dras eller sparas. */
  const frozen = () => dragging || nudge !== null || store.busy;

  function takeWaitingHome() {
    if (waitingHome === null || frozen()) {
      return;
    }
    liveHome = waitingHome;
    waitingHome = null;
    // Kärnan har ett nytt hem, till exempel från ett annat fönster. Läs det.
    store.reload().catch(() => undefined);
    render();
  }

  const showState = (next: StateMessage) => {
    state = next;
    const now = Date.now();
    for (const card of next.cards ?? []) {
      if (card.online) {
        lastHeard.set(card.id, now);
      }
    }
    if (recalculating !== null && now - recalculating.since > 500) {
      const floor = currentFloor();
      if (floor !== null && tracksOnFloor(next, floor.id).length > 0) {
        stopRecalculating();
      }
    }
    renderLive();
  };

  const connection = deps.connect({
    onStatus: (next) => {
      connectionStatus = next;
      root.dataset.status = next;
      renderHealth();
    },
    onMessage: (message) => {
      if (message.type === "home") {
        waitingHome = message;
        takeWaitingHome();
      } else {
        showState(message);
      }
    },
  });

  // --- Hälsoraden ---
  const healthNow = () =>
    healthSummary({ connection: connectionStatus, state, floor: currentFloor(), newDevices: newDevices() });

  function renderHealth() {
    const summary = healthNow();
    healthText.textContent = summary.text;
    healthDot.dataset.level = summary.level;
    health.dataset.level = summary.level;
  }

  /** Centrerar kartan på saken som hälsoraden handlar om och öppnar dess kort. */
  function openHealthTarget() {
    const target: HealthTarget | null = healthNow().target;
    if (mode !== "view") {
      return;
    }
    if (target?.kind === "item") {
      selectItem(target.id);
      const found = findItem(shown() ?? emptyHome(), target.id);
      if (found !== null) {
        view.centerOn(toPoint(found.item.position));
      }
    } else if (target?.kind === "new") {
      selection = null;
      newDeviceId = target.id;
      setSheet("half");
      render();
    } else {
      selection = null;
      newDeviceId = null;
      setSheet("half");
      render();
    }
  }

  const emptyHome = (): HomeDocument => ({ format: "homecontrol-hem", version: 1, home: { name: "", floors: [] } });

  // --- Val och lägen ---
  function selectItem(itemId: string) {
    // Sakens rum blir det valda rummet, så att ‹ Rummet leder dit.
    const room = findItem(shown() ?? emptyHome(), itemId)?.room;
    if (room) {
      roomId = room.id;
    }
    selection = { kind: "item", id: itemId };
    newDeviceId = null;
    setSheet("half");
    render();
  }

  const backToRoom = () => {
    selection = null;
    newDeviceId = null;
    render();
  };

  function startMove(itemId: string) {
    const before = placementOf(store.shown, itemId);
    if (before === null || databaseDown()) {
      return;
    }
    mode = "move";
    moving = { itemId, before };
    selection = { kind: "item", id: itemId };
    newDeviceId = null;
    hideToast();
    render();
  }

  /** Klar: slutar flytta eller ändra. Det som har flyttats är redan sparat. */
  function finishMode() {
    if (mode === "move") {
      flushNudge();
      mode = "view";
      moving = null;
    } else if (mode === "edit") {
      editMode.reset();
      mode = "view";
      selection = null;
      setSheet("low");
    }
    render();
  }

  /** Avbryt: ställer tillbaka saken där den stod när Flytta trycktes. */
  function cancelMove() {
    if (mode !== "move" || moving === null) {
      return;
    }
    const { itemId, before } = moving;
    if (nudge !== null) {
      window.clearTimeout(nudge.timer);
      nudge = null;
    }
    mode = "view";
    moving = null;
    if (JSON.stringify(placementOf(store.shown, itemId)) !== JSON.stringify(before)) {
      store.commit(restore(itemId, before), {
        done: () => {
          showToast("Flytten är ångrad.", "info", null, 3000);
          startRecalculatingIfNode(itemId);
        },
      });
    }
    render();
  }

  function openEdit() {
    mode = "edit";
    selection = null;
    newDeviceId = null;
    hideToast();
    // På mobilen ska kartan synas när man ritar; arket dras upp vid behov.
    setSheet("low");
    render();
  }

  // --- Flytta ---
  /** Sparar en flytt och visar "Sparat · Ångra". En PUT per släpp. */
  function saveMove(itemId: string, edit: Edit) {
    const before = placementOf(store.shown, itemId);
    store.commit(edit, {
      done: () => {
        startRecalculatingIfNode(itemId);
        if (before !== null) {
          showToast("Sparat", "info", {
            label: "Ångra",
            run: () => saveUndo(itemId, before),
          });
        }
      },
    });
  }

  function saveUndo(itemId: string, before: Placement) {
    store.commit(restore(itemId, before), {
      done: () => {
        startRecalculatingIfNode(itemId);
        showToast("Ångrat", "info", null, 3000);
      },
    });
  }

  function startRecalculatingIfNode(itemId: string) {
    if (findItem(store.shown ?? emptyHome(), itemId)?.kind !== "node") {
      return;
    }
    stopRecalculating();
    // Kärnan räknar om med den nya placeringen och glömmer kortets gamla
    // spår, så prickarna är borta en kort stund.
    recalculating = { since: Date.now(), timer: window.setTimeout(() => stopRecalculating(), RECALCULATING_MS) };
    recalcMessage.hidden = false;
  }

  function stopRecalculating() {
    if (recalculating !== null) {
      window.clearTimeout(recalculating.timer);
      recalculating = null;
    }
    recalcMessage.hidden = true;
  }

  const rotateMoving = (degrees: number) => {
    if (moving === null) {
      return;
    }
    const itemId = moving.itemId;
    flushNudge();
    saveMove(itemId, (document) => {
      const found = findItem(document, itemId);
      if (found?.kind !== "node") {
        return document;
      }
      const rotation = (found.item as NodeModel).rotation ?? 0;
      return updateItem(document, itemId, { rotation: normalizeRotation(rotation + degrees) });
    });
  };

  function nudgeMoving(dx: number, dy: number) {
    if (moving === null) {
      return;
    }
    const itemId = moving.itemId;
    const found = findItem(shown() ?? emptyHome(), itemId);
    if (found === null) {
      return;
    }
    if (nudge !== null) {
      window.clearTimeout(nudge.timer);
    }
    const from = toPoint(found.item.position);
    nudge = {
      itemId,
      point: { x: Math.round((from.x + dx) * 100) / 100, y: Math.round((from.y + dy) * 100) / 100 },
      timer: window.setTimeout(() => flushNudge(), NUDGE_SAVE_MS),
    };
    render();
  }

  function flushNudge() {
    if (nudge === null) {
      return;
    }
    const { itemId, point, timer } = nudge;
    window.clearTimeout(timer);
    nudge = null;
    saveMove(itemId, (document) => moveItem(document, itemId, point));
  }

  // --- Simulering ---
  function addPerson() {
    const floor = currentFloor();
    if (floor !== null) {
      const spot = parkingSpot(floor.rooms, personsAdded++);
      send({ type: "add_person", floor_id: floor.id, ...spot });
    }
  }

  // --- Ny enhet ---
  function placeNew(device: NewDevice) {
    const document = store.shown;
    const floor = currentFloor();
    const room = currentRoom();
    if (document === null || floor === null || room === null) {
      return;
    }
    const point = middleOf(floor, room);
    const found =
      device.kind === "node"
        ? unplacedCards().find((card) => card.id === device.id)
        : unplacedLamps().find((lamp) => lamp.id === device.id);
    const placed = placeItem(document, device.kind, floor.id, point, found);
    if (placed === null) {
      showMessage("Enheten kunde inte placeras i rummet.");
      return;
    }
    foundCards = foundCards.filter((card) => card.id !== device.id);
    foundLamps = foundLamps.filter((lamp) => lamp.id !== device.id);
    const profile = found !== undefined && "profile" in found ? found.profile : undefined;
    const given = { id: placed.id, name: placed.name, profile };
    store.commit((saved) => placeItem(saved, device.kind, floor.id, point, given)?.document ?? saved);
    // Enheten hamnar mitt i rummet och är redan upplåst för flytt.
    startMove(placed.id);
  }

  // --- Kortens handlingar ---
  const cardActions: CardActions = {
    send,
    select: (itemId) => {
      selectItem(itemId);
      const found = findItem(shown() ?? emptyHome(), itemId);
      if (found !== null) {
        view.centerOn(toPoint(found.item.position));
      }
    },
    selectNew: (deviceId) => {
      selection = null;
      newDeviceId = deviceId;
      render();
    },
    back: backToRoom,
    move: startMove,
    editRoom: openEdit,
    calibrate: (id) => {
      calibration.open(id);
      setSheet("high");
      renderSheet(true);
      calibration.element.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
    },
    place: placeNew,
    commit: (edit) => store.commit(edit),
    showMessage,
  };

  // --- Kartans händelser ---
  const viewCallbacks: MapCallbacks = {
    onPointerDown: () => undefined,
    onPointerMove: () => undefined,
    onPointerUp: () => undefined,
    onSelect: (next) => {
      if (mode !== "view") {
        return;
      }
      // Ett tryck på golvet visar rummets kort.
      if (next?.kind === "room") {
        roomId = next.id;
      }
      selection = null;
      newDeviceId = null;
      render();
    },
    onSelectItem: (itemId) => {
      if (mode === "view") {
        selectItem(itemId);
      }
    },
    onDragStart: () => {
      dragging = true;
    },
    onMoveItem: (itemId, point) => {
      dragging = false;
      if (mode === "move" && moving?.itemId === itemId) {
        flushNudge();
        saveMove(itemId, (document) => moveItem(document, itemId, point));
      } else {
        takeWaitingHome();
      }
    },
    onRotateNode: (nodeId, rotation) => {
      dragging = false;
      if (mode === "move" && moving?.itemId === nodeId) {
        flushNudge();
        saveMove(nodeId, (document) => updateItem(document, nodeId, { rotation: normalizeRotation(rotation) }));
      }
    },
    onClickWall: () => undefined,
    onMoveRoom: () => undefined,
    onMovePerson: (personId, point, done) => {
      const floor = currentFloor();
      if (floor !== null && state?.simulation?.enabled === true) {
        send({ type: "move_person", person_id: personId, floor_id: floor.id, ...point });
      }
      if (done) {
        dragging = false;
        takeWaitingHome();
      }
    },
  };

  /** Med ritverktygen går kartans händelser till Ändra rummet. */
  const dispatch = <K extends keyof MapCallbacks>(name: K): MapCallbacks[K] =>
    ((...args: unknown[]) => {
      const handlers = mode === "edit" && name in editMode.callbacks ? editMode.callbacks : viewCallbacks;
      if (name === "onDragStart") {
        dragging = true;
      } else if (mode === "edit" && (name === "onMoveItem" || name === "onRotateNode" || name === "onMoveRoom")) {
        dragging = false;
      }
      (handlers[name as keyof typeof handlers] as (...a: unknown[]) => void)(...args);
    }) as MapCallbacks[K];

  const view: MapView = deps.createMap(stageContainer, {
    onPointerDown: dispatch("onPointerDown"),
    onPointerMove: dispatch("onPointerMove"),
    onPointerUp: dispatch("onPointerUp"),
    onSelect: dispatch("onSelect"),
    onSelectItem: dispatch("onSelectItem"),
    onDragStart: dispatch("onDragStart"),
    onMoveItem: dispatch("onMoveItem"),
    onRotateNode: dispatch("onRotateNode"),
    onClickWall: dispatch("onClickWall"),
    onMoveRoom: dispatch("onMoveRoom"),
    onMovePerson: dispatch("onMovePerson"),
  });

  // --- Arkets höjd (mobil) ---
  function setSheet(height: SheetHeight) {
    sheet.dataset.height = height;
    sheet.style.height = "";
  }

  function cycleSheet() {
    const now = SHEET_HEIGHTS.indexOf(sheet.dataset.height as SheetHeight);
    setSheet(SHEET_HEIGHTS[(now + 1) % SHEET_HEIGHTS.length]);
  }

  // Arket dras upp och ned i handtaget och stannar på närmaste av tre höjder.
  let sheetDrag: { startY: number; startHeight: number; moved: boolean } | null = null;
  sheetHandle.addEventListener("pointerdown", (event) => {
    sheetDrag = { startY: event.clientY, startHeight: sheet.getBoundingClientRect().height, moved: false };
    sheetHandle.setPointerCapture?.(event.pointerId);
  });
  sheetHandle.addEventListener("pointermove", (event) => {
    if (sheetDrag === null) {
      return;
    }
    const delta = sheetDrag.startY - event.clientY;
    if (Math.abs(delta) > 6) {
      sheetDrag.moved = true;
      sheet.classList.add("dragging");
      sheet.style.height = `${Math.max(80, sheetDrag.startHeight + delta)}px`;
    }
  });
  sheetHandle.addEventListener("pointerup", () => {
    const drag = sheetDrag;
    sheetDrag = null;
    sheet.classList.remove("dragging");
    if (drag === null || !drag.moved) {
      return;
    }
    const height = sheet.getBoundingClientRect().height;
    const total = sheet.parentElement?.getBoundingClientRect().height ?? window.innerHeight;
    const share = height / Math.max(1, total);
    setSheet(share < 0.35 ? "low" : share < 0.72 ? "half" : "high");
    // Klicket som följer på dragningen ska inte byta höjd igen.
    sheetHandle.addEventListener("click", (event) => event.stopImmediatePropagation(), {
      capture: true,
      once: true,
    });
  });

  // --- Ritning ---
  const sceneNow = (): MapScene | null => {
    const editing = mode === "edit" ? editMode.scene() : null;
    const preview = editing?.preview ?? null;
    const floor =
      preview?.document.home.floors.find((candidate) => candidate.id === preview.floorId) ?? currentFloor();
    if (floor === null) {
      return null;
    }
    const simulation = state?.simulation;
    return {
      floor,
      planImage:
        floor.plan_image == null
          ? null
          : { url: `${deps.api.planImageUrl(floor.id)}?v=${imageVersion}`, plan: floor.plan_image },
      selection,
      mode,
      movingId: moving?.itemId ?? null,
      tool: editing?.tool ?? "select",
      draftColor: editing?.draftColor ?? null,
      draft: editing?.draft ?? { kind: "none" },
      live: {
        state,
        virtualLamps: virtualLamps(),
        persons: simulation?.enabled ? simulation.persons.filter((p) => p.floor_id === floor.id) : [],
      },
    };
  };

  const renderMap = () => {
    const scene = sceneNow();
    if (scene !== null) {
      view.render(scene);
    }
    const room = currentRoom();
    if (room !== null) {
      const xs = room.polygon.map(([x]) => x);
      const ys = room.polygon.map(([, y]) => y);
      const format = (value: number) => value.toLocaleString("sv-SE", { maximumFractionDigits: 1 });
      sizeText.textContent = `${format(Math.max(...xs) - Math.min(...xs))} × ${format(Math.max(...ys) - Math.min(...ys))} m`;
    } else {
      sizeText.textContent = "";
    }
  };

  const renderTopbar = () => {
    root.dataset.mode = mode;
    topbar.classList.toggle("mode", mode !== "view");
    if (mode === "move" && moving !== null) {
      const name = findItem(store.shown ?? emptyHome(), moving.itemId)?.item.name ?? "";
      modeTitle.textContent = `Flyttar ${name}`;
      topbar.replaceChildren(modeTitle, cancelButton, doneButton);
    } else if (mode === "edit") {
      modeTitle.textContent = "Ändrar rummet";
      topbar.replaceChildren(modeTitle, doneButton);
    } else {
      topbar.replaceChildren(brand, health, simBadge, doorLink, simButton);
    }
    const simulation = state?.simulation ?? null;
    simButton.hidden = simulation === null;
    simButton.setAttribute("aria-pressed", String(simulation?.enabled === true));
    simBadge.hidden = simulation?.enabled !== true;
    mapwrap.dataset.simulation = String(simulation?.enabled === true);
    databaseWarning.hidden = !databaseDown();
  };

  const cardData = (floor: FloorModel): CardData => ({
    floor,
    state,
    virtualLamps: virtualLamps(),
    databaseDown: databaseDown(),
    newDevices: newDevices(),
    lastHeard,
  });

  /** Det arket visar senast; samma igen byggs inte om, så att ett tryck inte tappas. */
  let shownSheet = "";
  /** Arket byggs om när fältet som har fokus har lämnats. */
  let sheetWaiting = false;

  /**
   * Kortet som arket ska visa, och det som kortet visar. Kortet byggs bara om
   * när det som det visar har ändrats, så att ett tryck inte tappas för att
   * knappen byttes ut mitt i trycket.
   */
  const sheetCard = (floor: FloorModel): { key: unknown[]; build: () => HTMLElement[] } => {
    const document = shown();
    const data = cardData(floor);
    const common = [mode, selection, newDeviceId, data.databaseDown, loadError];
    if (mode === "move" && moving !== null && document !== null) {
      const found = findItem(document, moving.itemId);
      if (found !== null) {
        return { key: [...common, moving, found.item], build: () => moveCard(found, rotateMoving) };
      }
    }
    if (newDeviceId !== null) {
      const device = data.newDevices.find((candidate) => candidate.id === newDeviceId);
      if (device !== undefined) {
        return { key: [...common, device], build: () => newDeviceCard(device, data, cardActions) };
      }
      newDeviceId = null;
    }
    if (selection?.kind === "item" && document !== null) {
      const found = findItem(document, selection.id);
      const lampState = state?.lamps.find((lamp) => lamp.id === selection?.id);
      const card = state?.cards?.find((candidate) => candidate.id === selection?.id);
      const key = [...common, found?.item, found?.room?.name, lampState, card, data.virtualLamps.has(selection.id)];
      if (found?.kind === "node") {
        // "Senast hört" ändras bara när kortet slutar svara, och då ändras kortet ändå.
        return { key, build: () => nodeCard(found, data, cardActions) };
      }
      if (found?.kind === "lamp") {
        return { key, build: () => lampCard(found, data, cardActions) };
      }
      if (found?.kind === "router") {
        return { key, build: () => routerCard(found, data, cardActions) };
      }
    }
    const room = currentRoom();
    if (room === null) {
      return {
        key: [...common, "inga rum"],
        build: () => [
          element(
            "p",
            { className: "help" },
            "Hemmet har inga rum än. Tryck på Ändra rummet och rita rummet, eller läs in en iPhone-skanning.",
          ),
          button("Ändra rummet", openEdit, "link-row edit-room"),
        ],
      };
    }
    const persons = tracksOnFloor(state, floor.id).filter((track) =>
      inPolygon(track, room.polygon.map(toPoint)),
    ).length;
    const simulation = state?.simulation ?? null;
    return {
      key: [
        ...common,
        room,
        floor.signal_sources,
        persons,
        // Ljusnivån ändras ofta; den skrivs in utan att kortet byggs om (se renderSheet).
        { ...state?.rooms.find((r) => r.id === room.id), lux: null, adjusted_lux: null },
        state?.lamps,
        state?.cards,
        state?.zigbee,
        simulation === null ? null : [simulation.enabled, simulation.persons.map((p) => p.id), simulation.speed_locked],
        data.newDevices,
      ],
      build: () => roomCard(room, data, cardActions),
    };
  };

  function renderSheet(force = false) {
    if (mode === "edit") {
      if (sheetBody.firstElementChild !== editMode.panel) {
        sheetBody.replaceChildren(editMode.panel);
      }
      shownSheet = "";
      return;
    }
    if (loadError !== null) {
      sheetBody.replaceChildren(element("p", { className: "message", role: "alert" }, loadError));
      return;
    }
    const floor = currentFloor();
    if (floor === null) {
      return;
    }
    calibration.show(liveFloor(), state?.calibration ?? null);
    const card = sheetCard(floor);
    const key = JSON.stringify(card.key);
    if (!force && key === shownSheet) {
      showLux();
      return;
    }
    // Ett fält som någon skriver i byts inte ut; arket byggs om när fältet lämnas.
    const active = document.activeElement;
    if (
      !force &&
      active instanceof HTMLElement &&
      sheetBody.contains(active) &&
      (active instanceof HTMLInputElement || active instanceof HTMLSelectElement) &&
      active.type !== "checkbox" &&
      active.type !== "range"
    ) {
      sheetWaiting = true;
      return;
    }
    sheetWaiting = false;
    shownSheet = key;
    // Öppna delar (till exempel Ljusregler) förblir öppna när kortet byggs om.
    const open = new Set(
      [...sheetBody.querySelectorAll("details[open]")].map((details) => details.className),
    );
    sheetBody.replaceChildren(...card.build(), calibration.element);
    for (const details of sheetBody.querySelectorAll("details")) {
      if (open.has(details.className)) {
        details.open = true;
      }
    }
    showLux();
  }

  /** Skriver in rummets ljusnivå, som ändras för ofta för att bygga om kortet varje gång. */
  function showLux() {
    const value = sheetBody.querySelector<HTMLElement>(".lux-value");
    const roomState = state?.rooms.find((room) => room.id === value?.dataset.room);
    if (value !== null && roomState !== undefined) {
      value.textContent = luxText(roomState.lux, roomState.adjusted_lux);
    }
  }

  sheetBody.addEventListener("focusout", () => {
    if (sheetWaiting) {
      queueMicrotask(() => renderSheet());
    }
  });

  /** Ser till att det valda och rummet finns kvar i hemmet. */
  const keepValid = () => {
    const document = store.shown;
    if (document === null) {
      return;
    }
    if (!stillThere(document, selection)) {
      selection = null;
    }
    if (moving !== null && findItem(document, moving.itemId) === null) {
      mode = "view";
      moving = null;
    }
    const floors = document.home.floors;
    if (floors.length > 0 && !floors.some((floor) => floor.id === floorId)) {
      floorId = startFloor(floors)?.id ?? null;
    }
    const rooms = currentFloor()?.rooms ?? [];
    if (!rooms.some((room) => room.id === roomId)) {
      roomId = rooms[0]?.id ?? null;
    }
  };

  /** Ritar om allt. */
  function render() {
    keepValid();
    if (mode === "edit") {
      editMode.render();
    }
    if (mode === "edit") {
      if (editMode.toolbar.parentElement !== mapwrap) {
        mapwrap.append(editMode.toolbar);
      }
    } else {
      editMode.toolbar.remove();
    }
    for (const part of [simulationControls.strip, recalcMessage, mapControls, toast]) {
      if (part.parentElement !== mapwrap) {
        mapwrap.append(part);
      }
    }
    renderTopbar();
    renderHealth();
    renderMap();
    renderSheet();
    simulationControls.show(state?.simulation ?? null);
    root.dataset.rooms = String(currentFloor()?.rooms.length ?? 0);
  }

  /** Ritar om det som läget från kärnan ändrar, upp till tio gånger i sekunden. */
  function renderLive() {
    renderTopbar();
    renderHealth();
    renderMap();
    renderSheet();
    simulationControls.show(state?.simulation ?? null);
  }

  // --- Tangenterna ---
  window.addEventListener("keydown", (event) => {
    const target = event.target;
    if (
      target instanceof HTMLInputElement ||
      target instanceof HTMLSelectElement ||
      target instanceof HTMLTextAreaElement
    ) {
      return;
    }
    if (mode === "edit") {
      if (editMode.keydown(event)) {
        event.preventDefault();
      }
      return;
    }
    if (mode === "move" && moving !== null) {
      const arrows: Record<string, [number, number]> = {
        ArrowLeft: [-1, 0],
        ArrowRight: [1, 0],
        ArrowUp: [0, -1],
        ArrowDown: [0, 1],
      };
      const direction = arrows[event.key];
      if (direction === undefined) {
        if (event.key === "Escape") {
          cancelMove();
        }
        return;
      }
      event.preventDefault();
      if (event.shiftKey) {
        // Skift+pil vrider ett rumskort: vänster och upp moturs, höger och ned medurs.
        if (findItem(store.shown ?? emptyHome(), moving.itemId)?.kind === "node") {
          rotateMoving(direction[0] + direction[1] < 0 ? -TURN_DEGREES : TURN_DEGREES);
        }
        return;
      }
      nudgeMoving(direction[0] * NUDGE_METERS, direction[1] * NUDGE_METERS);
      return;
    }
    if (event.key === "Escape" && (selection !== null || newDeviceId !== null)) {
      backToRoom();
    }
  });

  // --- Start ---
  render();
  connection.start();
  void deps.hasDoorbell().then((found) => {
    doorLink.hidden = !found;
  });
  // Nya kort och lampor dyker upp medan sidan är öppen, till exempel när
  // ett kort just har installerats eller en lampa parats ihop.
  window.setInterval(refreshFound, FOUND_REFRESH_MS);
  store.load().then(
    () => {
      const floors = store.shown?.home.floors ?? [];
      floorId = startFloor(floors)?.id ?? null;
      root.dataset.busy = "false";
      root.dataset.saves = "0";
      render();
      refreshFound();
      root.dataset.ready = "true";
    },
    (error: unknown) => {
      loadError = `Kunde inte läsa hemmet. Ladda om sidan för att försöka igen. (${errorText(error)})`;
      render();
    },
  );
}
