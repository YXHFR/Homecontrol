/**
 * Ändringar av det som placeras i hemmet: väggarnas material, rumskort
 * (noder), routrar (signalkällor), lampor och rummens inställningar.
 *
 * Precis som i editing.ts är alla funktioner rena och returnerar ett nytt
 * dokument. En ändring som pekar på något som inte längre finns (någon
 * hann ta bort det) lämnar dokumentet som det var.
 */
import { distance, toPoint, toXY, uniqueId, updateFloors } from "./editing";
import type {
  FloorModel,
  HomeDocument,
  LampModel,
  NodeModel,
  RoomModel,
  RoomSettings,
  SignalSourceModel,
} from "./homefile";
import type { Point, WallKind } from "./protocol";

/** I den här ordningen växlar väggverktyget material vid varje klick. */
export const WALL_KINDS: WallKind[] = ["gips", "betong", "trä", "glas", "dörröppning"];

/** Radarns synfält (LD2450): så långt den ser, i meter … */
export const RADAR_RANGE = 6;
/** … och så många grader åt varje sida om riktningen den tittar åt. */
export const RADAR_HALF_ANGLE = 60;

/** Samma gräns som i kärnan (home.py). */
export const MAX_HEIGHT = 20;
export const MAX_LUX = 200_000;
export const MAX_DELAY_SECONDS = 24 * 60 * 60;

/** Samma standardvärden som i kärnan (home.py). */
export const DEFAULT_SETTINGS: RoomSettings = {
  dark_below_lux: 30,
  bright_above_lux: 100,
  off_after_empty_seconds: 180,
};

/** Det som placeras ut med ett klick: ett rumskort, en router eller en lampa. */
export type ItemKind = "node" | "router" | "lamp";

/** Höjden ett nyplacerat rumskort och en ny router får, i meter. */
const DEFAULT_NODE_HEIGHT = 2;
const DEFAULT_ROUTER_HEIGHT = 1;

/** Vad sakerna heter i gränssnittet och i påhittade namn ("Lampa 1"). */
export const ITEM_LABELS: Record<ItemKind, string> = {
  node: "Rumskort",
  router: "Router",
  lamp: "Lampa",
};

export interface FoundItem {
  kind: ItemKind;
  item: NodeModel | SignalSourceModel | LampModel;
  floor: FloorModel;
  /** Rummet som noden eller lampan hör till. Routrar hör till våningen. */
  room: RoomModel | null;
}

// --- Väggar ---

export function nextWallKind(kind: WallKind): WallKind {
  return WALL_KINDS[(WALL_KINDS.indexOf(kind) + 1) % WALL_KINDS.length];
}

/** Om sträckorna a–b och c–d ligger på samma linje och delar en bit (som i home.py). */
export function sharesStretch(a: Point, b: Point, c: Point, d: Point, tolerance = 1e-6): boolean {
  const [dx, dy] = [b.x - a.x, b.y - a.y];
  const length = Math.hypot(dx, dy);
  if (length < tolerance) {
    return false;
  }
  for (const p of [c, d]) {
    if (Math.abs(dx * (p.y - a.y) - dy * (p.x - a.x)) / length > tolerance) {
      return false;
    }
  }
  const alongC = ((c.x - a.x) * dx + (c.y - a.y) * dy) / length;
  const alongD = ((d.x - a.x) * dx + (d.y - a.y) * dy) / length;
  const shared = Math.min(length, Math.max(alongC, alongD)) - Math.max(0, Math.min(alongC, alongD));
  return shared > tolerance;
}

interface WallRef {
  roomId: string;
  index: number;
  start: Point;
  end: Point;
}

function wallsOf(floor: FloorModel): WallRef[] {
  return floor.rooms.flatMap((room) =>
    room.polygon.map((corner, index) => ({
      roomId: room.id,
      index,
      start: toPoint(corner),
      end: toPoint(room.polygon[(index + 1) % room.polygon.length]),
    })),
  );
}

/**
 * Byter väggen till nästa material. En vägg som delas med ett grannrum är
 * samma vägg, så grannrummets sida byts också (kärnan godtar inte att två
 * rum säger olika saker om samma vägg).
 */
export function cycleWall(document: HomeDocument, roomId: string, index: number): HomeDocument {
  const floor = document.home.floors.find((f) => f.rooms.some((room) => room.id === roomId));
  const room = floor?.rooms.find((r) => r.id === roomId);
  if (floor === undefined || room === undefined || index >= room.walls.length) {
    return document;
  }
  const kind = nextWallKind(room.walls[index]);
  const walls = wallsOf(floor);
  const clicked = walls.find((wall) => wall.roomId === roomId && wall.index === index);
  if (clicked === undefined) {
    return document;
  }
  // Alla väggar som hänger ihop med den klickade, även genom en tredje vägg.
  const shared = [clicked];
  for (let i = 0; i < shared.length; i++) {
    const wall = shared[i];
    for (const other of walls) {
      if (
        !shared.includes(other) &&
        other.roomId !== wall.roomId &&
        sharesStretch(wall.start, wall.end, other.start, other.end)
      ) {
        shared.push(other);
      }
    }
  }
  return updateFloor(document, floor.id, (f) => ({
    ...f,
    rooms: f.rooms.map((r) => {
      const indices = new Set(shared.filter((w) => w.roomId === r.id).map((w) => w.index));
      return indices.size === 0
        ? r
        : { ...r, walls: r.walls.map((wall, i) => (indices.has(i) ? kind : wall)) };
    }),
  }));
}

// --- Rum ---

/** Om punkten är i rummet. Punkter på väggen räknas som inne. */
export function insideRoom(point: Point, room: RoomModel): boolean {
  const corners = room.polygon.map(toPoint);
  let inside = false;
  for (let i = 0; i < corners.length; i++) {
    const a = corners[i];
    const b = corners[(i + 1) % corners.length];
    const along = distance(a, point) + distance(point, b) - distance(a, b);
    if (along < 1e-9) {
      return true; // på väggen
    }
    if (a.y > point.y !== b.y > point.y) {
      const crossingX = a.x + ((point.y - a.y) * (b.x - a.x)) / (b.y - a.y);
      if (point.x < crossingX) {
        inside = !inside;
      }
    }
  }
  return inside;
}

/** Rummet som punkten är i, eller null. Ligger den i flera (på en delad vägg) gäller det första. */
export function roomAt(floor: FloorModel, point: Point): RoomModel | null {
  return floor.rooms.find((room) => insideRoom(point, room)) ?? null;
}

export function roomSettings(room: RoomModel): RoomSettings {
  return room.settings ?? DEFAULT_SETTINGS;
}

/** Varför inställningarna inte går att spara, eller null om de går. */
export function settingsProblem(settings: RoomSettings): string | null {
  const { dark_below_lux: dark, bright_above_lux: bright } = settings;
  const delay = settings.off_after_empty_seconds;
  if (![dark, bright, delay].every(Number.isFinite)) {
    return "Fyll i alla tre värdena.";
  }
  if (dark < 0 || bright > MAX_LUX) {
    return `Ljusgränserna ska vara mellan 0 och ${MAX_LUX.toLocaleString("sv-SE")} lux.`;
  }
  if (dark >= bright) {
    return "Mörkergränsen måste vara lägre än ljusgränsen.";
  }
  if (delay <= 0 || delay > MAX_DELAY_SECONDS) {
    return "Fördröjningen ska vara mer än 0 och högst ett dygn.";
  }
  return null;
}

export function setRoomSettings(
  document: HomeDocument,
  roomId: string,
  settings: RoomSettings,
): HomeDocument {
  return updateRooms(document, (room) => (room.id === roomId ? { ...room, settings } : room));
}

// --- Noder, routrar och lampor ---

export function findItem(document: HomeDocument, itemId: string | null): FoundItem | null {
  for (const floor of document.home.floors) {
    const router = floor.signal_sources?.find((source) => source.id === itemId);
    if (router !== undefined) {
      return { kind: "router", item: router, floor, room: null };
    }
    for (const room of floor.rooms) {
      const node = room.nodes?.find((n) => n.id === itemId);
      if (node !== undefined) {
        return { kind: "node", item: node, floor, room };
      }
      const lamp = room.lamps?.find((l) => l.id === itemId);
      if (lamp !== undefined) {
        return { kind: "lamp", item: lamp, floor, room };
      }
    }
  }
  return null;
}

function nextName(document: HomeDocument, kind: ItemKind): string {
  const floors = document.home.floors;
  const names = new Set(
    floors.flatMap((floor) => [
      ...(floor.signal_sources ?? []).map((source) => source.name),
      ...floor.rooms.flatMap((room) => [
        ...(room.nodes ?? []).map((node) => node.name),
        ...(room.lamps ?? []).map((lamp) => lamp.name),
      ]),
    ]),
  );
  let n = 1;
  while (names.has(`${ITEM_LABELS[kind]} ${n}`)) {
    n++;
  }
  return `${ITEM_LABELS[kind]} ${n}`;
}

/**
 * Placerar en ny sak i punkten. Rumskort och lampor hör till rummet de
 * placeras i och kan bara placeras i ett rum; då returneras null utanför.
 *
 * `given` är id och namn från när saken placerades i det som visades, så
 * att samma sak får samma id när ändringen görs på ett nyare hem.
 */
export function placeItem(
  document: HomeDocument,
  kind: ItemKind,
  floorId: string,
  point: Point,
  given?: { id: string; name: string },
): { document: HomeDocument; id: string; name: string } | null {
  const floor = document.home.floors.find((f) => f.id === floorId);
  if (floor === undefined) {
    return null;
  }
  const usable = given !== undefined && findItem(document, given.id) === null ? given : undefined;
  const name = usable?.name ?? nextName(document, kind);
  const id = usable?.id ?? uniqueId(document, name);
  const position = toXY(point);
  if (kind === "router") {
    const router: SignalSourceModel = { id, name, position, height: DEFAULT_ROUTER_HEIGHT };
    return {
      id,
      name,
      document: updateFloor(document, floorId, (f) => ({
        ...f,
        signal_sources: [...(f.signal_sources ?? []), router],
      })),
    };
  }
  const room = roomAt(floor, toPoint(position));
  if (room === null) {
    return null;
  }
  const add = (r: RoomModel): RoomModel =>
    kind === "node"
      ? {
          ...r,
          nodes: [
            ...(r.nodes ?? []),
            {
              id,
              name,
              position,
              height: DEFAULT_NODE_HEIGHT,
              rotation: 0,
              mirrored: false,
              capabilities: ["radar", "csi", "lux"],
            },
          ],
        }
      : { ...r, lamps: [...(r.lamps ?? []), { id, name, position }] };
  return {
    id,
    name,
    document: updateRooms(document, (r) => (r.id === room.id ? add(r) : r)),
  };
}

/** Ändrar fälten i en sak (namn, höjd, vridning, spegling …). */
export function updateItem(
  document: HomeDocument,
  itemId: string,
  changes: Partial<NodeModel> | Partial<SignalSourceModel> | Partial<LampModel>,
): HomeDocument {
  const change = <T extends { id: string }>(items: T[] | undefined): T[] | undefined =>
    items?.map((item) => (item.id === itemId ? { ...item, ...changes, id: itemId } : item));
  return updateFloors(document, (floor) => ({
    ...floor,
    signal_sources: change(floor.signal_sources),
    rooms: floor.rooms.map((room) => ({
      ...room,
      nodes: change(room.nodes),
      lamps: change(room.lamps),
    })),
  }));
}

export function deleteItem(document: HomeDocument, itemId: string): HomeDocument {
  const keep = <T extends { id: string }>(items: T[] | undefined): T[] | undefined =>
    items?.filter((item) => item.id !== itemId);
  return updateFloors(document, (floor) => ({
    ...floor,
    signal_sources: keep(floor.signal_sources),
    rooms: floor.rooms.map((room) => ({
      ...room,
      nodes: keep(room.nodes),
      lamps: keep(room.lamps),
    })),
  }));
}

/**
 * Kopplar ett rumskort eller en lampa till ett annat rum på samma våning.
 * Lampans zon hör till det gamla rummet och släpps.
 */
export function moveItemToRoom(
  document: HomeDocument,
  itemId: string,
  roomId: string,
): HomeDocument {
  const found = findItem(document, itemId);
  if (
    found === null ||
    found.room === null ||
    found.room.id === roomId ||
    !found.floor.rooms.some((room) => room.id === roomId)
  ) {
    return document;
  }
  const { kind } = found;
  const item = kind === "lamp" ? { ...found.item, zone_id: null } : found.item;
  const without = deleteItem(document, itemId);
  return updateRooms(without, (room) => {
    if (room.id !== roomId) {
      return room;
    }
    return kind === "node"
      ? { ...room, nodes: [...(room.nodes ?? []), item as NodeModel] }
      : { ...room, lamps: [...(room.lamps ?? []), item as LampModel] };
  });
}

/**
 * Flyttar saken till punkten. Ett rumskort eller en lampa som släpps i ett
 * annat rum hör sedan till det rummet; utanför alla rum behåller den sitt.
 */
export function moveItem(document: HomeDocument, itemId: string, point: Point): HomeDocument {
  const found = findItem(document, itemId);
  if (found === null) {
    return document;
  }
  const position = toXY(point);
  const moved = updateItem(document, itemId, { position });
  const target = found.room === null ? null : roomAt(found.floor, toPoint(position));
  return target === null ? moved : moveItemToRoom(moved, itemId, target.id);
}

/** Vridningen i hela grader, 0–359, åt vilket håll den än har snurrats. */
export function normalizeRotation(degrees: number): number {
  const whole = Math.round(degrees) % 360;
  return whole < 0 ? whole + 360 : whole;
}

/** Riktningen från `from` mot `to`, i grader medurs från höger (som rumskortens vridning). */
export function bearing(from: Point, to: Point): number {
  return normalizeRotation((Math.atan2(to.y - from.y, to.x - from.x) * 180) / Math.PI);
}

// --- Hjälpfunktioner ---

function updateFloor(
  document: HomeDocument,
  floorId: string,
  update: (floor: FloorModel) => FloorModel,
): HomeDocument {
  return updateFloors(document, (floor) => (floor.id === floorId ? update(floor) : floor));
}

function updateRooms(document: HomeDocument, update: (room: RoomModel) => RoomModel): HomeDocument {
  return updateFloors(document, (floor) => ({ ...floor, rooms: floor.rooms.map(update) }));
}
