/**
 * Ändringar som kartredigeraren gör i hemmet. Alla funktioner är rena: de
 * returnerar ett nytt dokument och lämnar det gamla orört, så att ett
 * misslyckat sparande kan ångras genom att visa det gamla igen.
 */
import type { FloorModel, HomeDocument, PlanImage, RoomModel, XY } from "./homefile";
import type { Point } from "./protocol";

/** Samma gräns som i kärnan (home.py). Origo är uppe till vänster, så inget är negativt. */
export const MAX_COORDINATE = 1000;
/** Mindre rum än så här är nästan säkert en felklickning. */
export const MIN_ROOM_AREA = 0.25;

/** Avrundar till hela centimeter, så att filen blir läsbar. */
export function roundCm(value: number): number {
  return Math.round(value * 100) / 100;
}

function clampToPlan(value: number): number {
  return Math.min(Math.max(roundCm(value), 0), MAX_COORDINATE);
}

export function toXY(point: Point): XY {
  return [clampToPlan(point.x), clampToPlan(point.y)];
}

export function toPoint([x, y]: XY): Point {
  return { x, y };
}

export function distance(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/** Ytans area i kvadratmeter. */
export function polygonArea(points: Point[]): number {
  let twice = 0;
  points.forEach((a, i) => {
    const b = points[(i + 1) % points.length];
    twice += a.x * b.y - b.x * a.y;
  });
  return Math.abs(twice) / 2;
}

function crosses(a: Point, b: Point, c: Point, d: Point): boolean {
  const side = (p: Point, q: Point, r: Point) =>
    (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
  return side(a, b, c) * side(a, b, d) < 0 && side(c, d, a) * side(c, d, b) < 0;
}

/** Om två sidor som inte sitter ihop korsar varandra, som i en åtta. */
export function selfIntersecting(points: Point[]): boolean {
  const n = points.length;
  for (let i = 0; i < n; i++) {
    for (let j = i + 2; j < n; j++) {
      if (i === 0 && j === n - 1) {
        continue; // första och sista sidan möts i första hörnet
      }
      if (crosses(points[i], points[(i + 1) % n], points[j], points[(j + 1) % n])) {
        return true;
      }
    }
  }
  return false;
}

/** Varför ytan inte kan bli ett rum, eller null om den kan det. */
export function roomProblem(points: Point[]): string | null {
  if (points.length < 3) {
    return "Ett rum behöver minst tre hörn.";
  }
  if (selfIntersecting(points)) {
    return "Väggarna korsar varandra. Klicka ut hörnen i ordning runt rummet.";
  }
  if (polygonArea(points) < MIN_ROOM_AREA) {
    return "Rummet blev för litet. Rita det lite större.";
  }
  return null;
}

/** Hörnen i en rektangel mellan två motsatta hörn, medurs från övre vänstra. */
export function rectangleCorners(a: Point, b: Point): Point[] {
  const [left, right] = [Math.min(a.x, b.x), Math.max(a.x, b.x)];
  const [top, bottom] = [Math.min(a.y, b.y), Math.max(a.y, b.y)];
  return [
    { x: left, y: top },
    { x: right, y: top },
    { x: right, y: bottom },
    { x: left, y: bottom },
  ];
}

function allIds(document: HomeDocument): Set<string> {
  const ids = new Set<string>();
  const add = (items: unknown[] | undefined) => {
    for (const item of items ?? []) {
      if (
        typeof item === "object" &&
        item !== null &&
        "id" in item &&
        typeof item.id === "string"
      ) {
        ids.add(item.id);
      }
    }
  };
  for (const floor of document.home.floors) {
    add([floor]);
    add(floor.signal_sources);
    add(floor.rooms);
    for (const room of floor.rooms) {
      add(room.zones);
      add(room.nodes);
      add(room.lamps);
    }
  }
  return ids;
}

/** Ett id som kärnan godtar (a–z, siffror, - och _) och som inte redan används. */
export function uniqueId(document: HomeDocument, name: string): string {
  const base =
    name
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "") // å → a, ä → a, ö → o
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 50) || "rum";
  const taken = allIds(document);
  let id = base;
  for (let n = 2; taken.has(id); n++) {
    id = `${base}-${n}`;
  }
  return id;
}

function nextRoomName(document: HomeDocument): string {
  const names = new Set(document.home.floors.flatMap((floor) => floor.rooms.map((r) => r.name)));
  let n = names.size + 1;
  while (names.has(`Rum ${n}`)) {
    n++;
  }
  return `Rum ${n}`;
}

function updateFloors(
  document: HomeDocument,
  update: (floor: FloorModel) => FloorModel,
): HomeDocument {
  return { ...document, home: { ...document.home, floors: document.home.floors.map(update) } };
}

function updateRoom(
  document: HomeDocument,
  roomId: string,
  update: (room: RoomModel) => RoomModel,
): HomeDocument {
  return updateFloors(document, (floor) => ({
    ...floor,
    rooms: floor.rooms.map((room) => (room.id === roomId ? update(room) : room)),
  }));
}

export function findRoom(document: HomeDocument, roomId: string | null): RoomModel | null {
  for (const floor of document.home.floors) {
    const room = floor.rooms.find((candidate) => candidate.id === roomId);
    if (room !== undefined) {
      return room;
    }
  }
  return null;
}

/** Lägger till ett rum med gipsväggar och ett påhittat namn. */
export function addRoom(
  document: HomeDocument,
  floorId: string,
  corners: Point[],
): { document: HomeDocument; roomId: string } {
  const name = nextRoomName(document);
  const roomId = uniqueId(document, name);
  const polygon = corners.map(toXY);
  const room: RoomModel = { id: roomId, name, polygon, walls: polygon.map(() => "gips") };
  return {
    roomId,
    document: updateFloors(document, (floor) =>
      floor.id === floorId ? { ...floor, rooms: [...floor.rooms, room] } : floor,
    ),
  };
}

/** Byter namn. Id:t behålls, så det som pekar på rummet fortsätter att fungera. */
export function renameRoom(document: HomeDocument, roomId: string, name: string): HomeDocument {
  return updateRoom(document, roomId, (room) => ({ ...room, name }));
}

export function deleteRoom(document: HomeDocument, roomId: string): HomeDocument {
  return updateFloors(document, (floor) => ({
    ...floor,
    rooms: floor.rooms.filter((room) => room.id !== roomId),
  }));
}

/**
 * Flyttar rummet och allt i det (zoner, noder, lampor) lika mycket. Flytten
 * kortas av så att inget hamnar utanför planet.
 */
export function moveRoom(
  document: HomeDocument,
  roomId: string,
  dx: number,
  dy: number,
): HomeDocument {
  return updateRoom(document, roomId, (room) => {
    const points = [
      ...room.polygon,
      ...(room.zones ?? []).flatMap((zone) => zone.polygon),
      ...(room.nodes ?? []).map((node) => node.position),
      ...(room.lamps ?? []).map((lamp) => lamp.position),
    ];
    const xs = points.map(([x]) => x);
    const ys = points.map(([, y]) => y);
    const allowedDx = Math.min(Math.max(dx, -Math.min(...xs)), MAX_COORDINATE - Math.max(...xs));
    const allowedDy = Math.min(Math.max(dy, -Math.min(...ys)), MAX_COORDINATE - Math.max(...ys));
    const shift = ([x, y]: XY): XY => [clampToPlan(x + allowedDx), clampToPlan(y + allowedDy)];
    const moved: RoomModel = { ...room, polygon: room.polygon.map(shift) };
    if (room.zones !== undefined) {
      moved.zones = room.zones.map((zone) => ({ ...zone, polygon: zone.polygon.map(shift) }));
    }
    if (room.nodes !== undefined) {
      moved.nodes = room.nodes.map((node) => ({ ...node, position: shift(node.position) }));
    }
    if (room.lamps !== undefined) {
      moved.lamps = room.lamps.map((lamp) => ({ ...lamp, position: shift(lamp.position) }));
    }
    return moved;
  });
}

export function setPlanImage(
  document: HomeDocument,
  floorId: string,
  planImage: PlanImage | null,
): HomeDocument {
  return updateFloors(document, (floor) =>
    floor.id === floorId ? { ...floor, plan_image: planImage } : floor,
  );
}

/**
 * Skalar om bilden så att avståndet mellan `a` och `b` (två punkter på
 * bilden, i meter med bildens nuvarande skala) blir `meters`. Bildens övre
 * vänstra hörn ligger kvar.
 */
export function scalePlanImage(plan: PlanImage, a: Point, b: Point, meters: number): PlanImage {
  const pixels = distance(a, b) / plan.meters_per_pixel;
  return { ...plan, meters_per_pixel: meters / pixels };
}

export interface HomeSummary {
  rooms: number;
  zones: number;
  nodes: number;
  signalSources: number;
  lamps: number;
  /** Rummens sammanlagda golvyta i kvadratmeter. */
  area: number;
}

export function summarize(document: HomeDocument): HomeSummary {
  const floors = document.home.floors;
  const rooms = floors.flatMap((floor) => floor.rooms);
  return {
    rooms: rooms.length,
    zones: rooms.reduce((sum, room) => sum + (room.zones?.length ?? 0), 0),
    nodes: rooms.reduce((sum, room) => sum + (room.nodes?.length ?? 0), 0),
    signalSources: floors.reduce((sum, floor) => sum + (floor.signal_sources?.length ?? 0), 0),
    lamps: rooms.reduce((sum, room) => sum + (room.lamps?.length ?? 0), 0),
    area: rooms.reduce((sum, room) => sum + polygonArea(room.polygon.map(toPoint)), 0),
  };
}
