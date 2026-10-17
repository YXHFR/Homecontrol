import type { Point, Room, WallKind } from "./protocol";

/** Luft runt rummen i meter, så att det finns plats att stå utanför. */
export const MARGIN_METERS = 2;

/** Omvandlar mellan hemmets meter och skärmens pixlar. */
export interface Transform {
  scale: number;
  toScreen(point: Point): Point;
  toHome(point: Point): Point;
}

interface Bounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

/** Rummets hörn som punkter. */
export function corners(room: Pick<Room, "polygon">): Point[] {
  return room.polygon.map(([x, y]) => ({ x, y }));
}

export interface WallSegment {
  from: Point;
  to: Point;
  kind: WallKind;
}

/** Rummets väggar, en per sida i polygonen. */
export function wallSegments(room: Room): WallSegment[] {
  const points = corners(room);
  return points.map((from, i) => ({
    from,
    to: points[(i + 1) % points.length],
    kind: room.walls[i] ?? "gips",
  }));
}

function bounds(rooms: Pick<Room, "polygon">[]): Bounds {
  const points = rooms.flatMap(corners);
  if (points.length === 0) {
    return { minX: 0, minY: 0, maxX: 1, maxY: 1 };
  }
  return {
    minX: Math.min(...points.map((p) => p.x)),
    minY: Math.min(...points.map((p) => p.y)),
    maxX: Math.max(...points.map((p) => p.x)),
    maxY: Math.max(...points.map((p) => p.y)),
  };
}

/** Skalar rummen, med marginal, så att de får plats i en yta på width × height pixlar. */
export function fitTransform(rooms: Room[], width: number, height: number): Transform {
  const b = bounds(rooms);
  const originX = b.minX - MARGIN_METERS;
  const originY = b.minY - MARGIN_METERS;
  const scale = Math.min(
    width / (b.maxX - b.minX + 2 * MARGIN_METERS),
    height / (b.maxY - b.minY + 2 * MARGIN_METERS),
  );
  return {
    scale,
    toScreen: (p) => ({ x: (p.x - originX) * scale, y: (p.y - originY) * scale }),
    toHome: (p) => ({ x: p.x / scale + originX, y: p.y / scale + originY }),
  };
}

/** Avståndet mellan låtsaspersoner som väntar bredvid varandra, i meter. */
const PARKING_GAP_METERS = 1;

/**
 * Där en ny låtsasperson väntar utanför rummen innan någon drar in den.
 * Person nummer `index` ställer sig under de som redan väntar, i en kö längs
 * rummens högra kant som börjar om uppifrån när den når botten.
 */
export function parkingSpot(rooms: Pick<Room, "polygon">[], index = 0): Point {
  const b = bounds(rooms);
  const places = Math.max(1, Math.floor((b.maxY - b.minY) / PARKING_GAP_METERS));
  return {
    x: b.maxX + MARGIN_METERS / 2,
    y: b.minY + PARKING_GAP_METERS / 2 + (index % places) * PARKING_GAP_METERS,
  };
}
