/**
 * Zonerna: delar av ett rum som betyder något särskilt för närvaron (se #36).
 * Här finns de ändringar som ritverktygen gör i zonerna. Precis som i
 * editing.ts returnerar ändringarna ett nytt dokument och lämnar det gamla
 * orört. Hur zonerna ser ut står i zoneStyles.ts.
 */
import {
  allIds,
  distance,
  inPolygon,
  polygonArea,
  polygonWithin,
  selfIntersecting,
  toPoint,
  toXY,
  uniqueId,
  updateRoom,
} from "./editing";
import type { FloorModel, HomeDocument, RoomModel, ZoneModel } from "./homefile";
import type { Point, ZoneKind } from "./protocol";
import { ZONE_STYLES } from "./zoneStyles";

/** Mindre zoner än så här (i kvadratmeter) är nästan säkert en felklickning. */
export const MIN_ZONE_AREA = 0.04;

/**
 * Rummet på våningen som ytan ligger i, eller null. Hörnen avrundas
 * först som när de sparas, så att kärnan godtar det som godtas här.
 */
export function roomAround(floor: FloorModel, corners: Point[]): RoomModel | null {
  const saved = corners.map((corner) => toPoint(toXY(corner)));
  return floor.rooms.find((room) => polygonWithin(saved, room.polygon.map(toPoint))) ?? null;
}

/**
 * Den närmaste punkten på en vägg på våningen, om den är högst `radius` meter
 * bort. Ett hörn som ska ligga längs väggen, som i en entrézon vid dörren,
 * hamnar då på väggen och inte en centimeter utanför.
 */
export function nearestOnWall(floor: FloorModel, point: Point, radius: number): Point | null {
  let best: Point | null = null;
  for (const room of floor.rooms) {
    const corners = room.polygon.map(toPoint);
    corners.forEach((a, i) => {
      const b = corners[(i + 1) % corners.length];
      const length2 = (b.x - a.x) ** 2 + (b.y - a.y) ** 2;
      const t =
        length2 === 0
          ? 0
          : Math.min(
              1,
              Math.max(
                0,
                ((point.x - a.x) * (b.x - a.x) + (point.y - a.y) * (b.y - a.y)) / length2,
              ),
            );
      const onWall = { x: a.x + t * (b.x - a.x), y: a.y + t * (b.y - a.y) };
      // Hörnen sparas i hela centimeter. På en sned vägg kan den avrundade
      // punkten hamna utanför rummet; då är det bättre att inte dra alls.
      const saved = toPoint(toXY(onWall));
      if (
        inPolygon(saved, corners) &&
        distance(point, saved) <= radius &&
        (best === null || distance(point, saved) < distance(point, best))
      ) {
        best = saved;
      }
    });
  }
  return best;
}

/** Varför ytan inte kan bli en zon i något rum på våningen, eller null om den kan det. */
export function zoneProblem(floor: FloorModel, corners: Point[]): string | null {
  if (corners.length < 3) {
    return "En zon behöver minst tre hörn.";
  }
  if (selfIntersecting(corners)) {
    return "Zonens kanter korsar varandra. Klicka ut hörnen i ordning runt zonen.";
  }
  if (polygonArea(corners) < MIN_ZONE_AREA) {
    return "Zonen blev för liten. Rita den lite större.";
  }
  if (roomAround(floor, corners) === null) {
    return "Rita zonen inne i ett rum, utan att den går ut genom någon vägg.";
  }
  return null;
}

export function findZone(
  document: HomeDocument,
  zoneId: string | null,
): { room: RoomModel; zone: ZoneModel } | null {
  for (const floor of document.home.floors) {
    for (const room of floor.rooms) {
      const zone = room.zones?.find((candidate) => candidate.id === zoneId);
      if (zone !== undefined) {
        return { room, zone };
      }
    }
  }
  return null;
}

/**
 * Det som ska hända när en zon har ritats: antingen varför den inte kan
 * sparas, eller den nya zonens id och ändringen som lägger till den. Ändringen
 * görs på det sparade hemmet när den står på tur och ger zonen samma id och
 * namn som den fick när den ritades.
 */
export function planZone(
  document: HomeDocument,
  floor: FloorModel,
  kind: ZoneKind,
  corners: Point[],
): { problem: string } | { zoneId: string; edit: (document: HomeDocument) => HomeDocument } {
  const problem = zoneProblem(floor, corners);
  const room = roomAround(floor, corners);
  if (problem !== null || room === null) {
    return { problem: problem ?? "Rita zonen inne i ett rum." };
  }
  const { zoneId, name } = addZone(document, room.id, kind, corners);
  return {
    zoneId,
    edit: (saved) => addZone(saved, room.id, kind, corners, { zoneId, name }).document,
  };
}

function nextZoneName(document: HomeDocument, kind: ZoneKind): string {
  const names = new Set(
    document.home.floors.flatMap((floor) =>
      floor.rooms.flatMap((room) => (room.zones ?? []).map((zone) => zone.name)),
    ),
  );
  const base = ZONE_STYLES[kind].defaultName;
  let n = 1;
  while (names.has(`${base} ${n}`)) {
    n++;
  }
  return `${base} ${n}`;
}

/**
 * Lägger till en zon i rummet med ett påhittat namn, eller med det id och
 * namn som anges (när samma zon läggs till igen i ett nyare hem).
 */
export function addZone(
  document: HomeDocument,
  roomId: string,
  kind: ZoneKind,
  corners: Point[],
  given?: { zoneId: string; name: string },
): { document: HomeDocument; zoneId: string; name: string } {
  // Ett givet id som redan används (någon annan hann lägga till något) byts ut.
  const usable = given !== undefined && !allIds(document).has(given.zoneId) ? given : undefined;
  const name = usable?.name ?? nextZoneName(document, kind);
  const zoneId = usable?.zoneId ?? uniqueId(document, name);
  const zone: ZoneModel = { id: zoneId, name, kind, polygon: corners.map(toXY) };
  return {
    zoneId,
    name,
    document: updateRoom(document, roomId, (room) => ({
      ...room,
      zones: [...(room.zones ?? []), zone],
    })),
  };
}

function updateZone(
  document: HomeDocument,
  zoneId: string,
  update: (zone: ZoneModel) => ZoneModel,
): HomeDocument {
  const found = findZone(document, zoneId);
  if (found === null) {
    return document;
  }
  return updateRoom(document, found.room.id, (room) => ({
    ...room,
    zones: (room.zones ?? []).map((zone) => (zone.id === zoneId ? update(zone) : zone)),
  }));
}

/** Byter namn. Id:t behålls, så lampor som hör till zonen fortsätter att göra det. */
export function renameZone(document: HomeDocument, zoneId: string, name: string): HomeDocument {
  return updateZone(document, zoneId, (zone) => ({ ...zone, name }));
}

export function setZoneKind(document: HomeDocument, zoneId: string, kind: ZoneKind): HomeDocument {
  return updateZone(document, zoneId, (zone) => ({ ...zone, kind }));
}

/** Tar bort zonen. Lampor som hörde till den hör sedan till hela rummet. */
export function deleteZone(document: HomeDocument, zoneId: string): HomeDocument {
  const found = findZone(document, zoneId);
  if (found === null) {
    return document;
  }
  return updateRoom(document, found.room.id, (room) => {
    const next: RoomModel = {
      ...room,
      zones: (room.zones ?? []).filter((zone) => zone.id !== zoneId),
    };
    if (room.lamps !== undefined) {
      next.lamps = room.lamps.map((lamp) =>
        lamp.zone_id === zoneId ? { ...lamp, zone_id: null } : lamp,
      );
    }
    return next;
  });
}
