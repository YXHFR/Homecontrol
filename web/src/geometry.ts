import type { Point, Room } from "./protocol";

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

function bounds(rooms: Room[]): Bounds {
  if (rooms.length === 0) {
    return { minX: 0, minY: 0, maxX: 1, maxY: 1 };
  }
  return {
    minX: Math.min(...rooms.map((room) => room.x)),
    minY: Math.min(...rooms.map((room) => room.y)),
    maxX: Math.max(...rooms.map((room) => room.x + room.width)),
    maxY: Math.max(...rooms.map((room) => room.y + room.height)),
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

/** Där låtsaspersonen väntar utanför rummen innan någon drar in den. */
export function parkingSpot(rooms: Room[]): Point {
  const b = bounds(rooms);
  return { x: b.maxX + MARGIN_METERS / 2, y: (b.minY + b.maxY) / 2 };
}
