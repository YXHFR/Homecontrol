/**
 * Zonerna: delar av ett rum som betyder något särskilt för närvaron (se #36).
 * Här finns hur varje sorts zon ser ut och de ändringar som kartredigeraren
 * gör i zonerna. Precis som i editing.ts returnerar ändringarna ett nytt
 * dokument och lämnar det gamla orört.
 */
import { polygonArea, selfIntersecting, toPoint, toXY, uniqueId, updateRoom } from "./editing";
import type { FloorModel, HomeDocument, RoomModel, ZoneModel } from "./homefile";
import type { Point, ZoneKind } from "./protocol";

/** Mindre zoner än så här (i kvadratmeter) är nästan säkert en felklickning. */
export const MIN_ZONE_AREA = 0.04;

export const ZONE_KINDS: readonly ZoneKind[] = ["vanlig", "stillhet", "ignorera", "entré"];

export interface ZoneStyle {
  /** Namnet på knappen och i listan över sorter. */
  label: string;
  /** Det nya zoner av sorten heter, följt av en siffra. */
  defaultName: string;
  /** Vad sorten betyder, i klartext. */
  description: string;
  color: string;
}

export const ZONE_STYLES: Record<ZoneKind, ZoneStyle> = {
  vanlig: {
    label: "Vanlig zon",
    defaultName: "Zon",
    description: "En plats i rummet som systemet ska känna till, till exempel skrivbordet.",
    color: "#2e9d5b",
  },
  stillhet: {
    label: "Stillhetszon",
    defaultName: "Stillhet",
    description:
      "Där man sitter eller ligger still länge, till exempel soffan eller sängen. " +
      "Närvaron hålls kvar även när radarn tappar personen.",
    color: "#7c5cc9",
  },
  ignorera: {
    label: "Ignorera-zon",
    defaultName: "Ignorera",
    description:
      "Störkällor som radarn inte ska bry sig om, till exempel en fläkt, en gardin eller en växt.",
    color: "#7a7a7a",
  },
  entré: {
    label: "Entrézon",
    defaultName: "Entré",
    description: "Vid en dörr. Den som försvinner ur sikte här har gått ut.",
    color: "#0e8f8a",
  },
};

/** Om punkten är i ytan. Punkter på kanten räknas som inne, som i kärnan (home.py). */
export function inPolygon(point: Point, polygon: Point[]): boolean {
  const tolerance = 1e-9;
  let inside = false;
  for (let i = 0; i < polygon.length; i++) {
    const a = polygon[i];
    const b = polygon[(i + 1) % polygon.length];
    const cross = (b.x - a.x) * (point.y - a.y) - (b.y - a.y) * (point.x - a.x);
    const onEdge =
      Math.abs(cross) <= tolerance &&
      Math.min(a.x, b.x) - tolerance <= point.x &&
      point.x <= Math.max(a.x, b.x) + tolerance &&
      Math.min(a.y, b.y) - tolerance <= point.y &&
      point.y <= Math.max(a.y, b.y) + tolerance;
    if (onEdge) {
      return true;
    }
    // Strålmetoden: räkna hur många sidor en stråle åt höger från punkten korsar.
    if (a.y > point.y !== b.y > point.y) {
      const crossingX = a.x + ((point.y - a.y) * (b.x - a.x)) / (b.y - a.y);
      if (point.x < crossingX) {
        inside = !inside;
      }
    }
  }
  return inside;
}

/**
 * Rummet på våningen som alla hörnen ligger i, eller null. Hörnen avrundas
 * först som när de sparas, så att kärnan godtar det som godtas här.
 */
export function roomAround(floor: FloorModel, corners: Point[]): RoomModel | null {
  const saved = corners.map((corner) => toPoint(toXY(corner)));
  return (
    floor.rooms.find((room) => {
      const polygon = room.polygon.map(toPoint);
      return saved.every((corner) => inPolygon(corner, polygon));
    }) ?? null
  );
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
    return "Rita zonen inne i ett rum. Alla hörn ska ligga i samma rum.";
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
  const usable =
    given !== undefined && uniqueId(document, given.zoneId) === given.zoneId ? given : undefined;
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
