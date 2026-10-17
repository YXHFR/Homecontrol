/**
 * Läser en iPhone-skanning (Apple RoomPlan, CapturedRoom som JSON) och gör
 * den till rum, väggar, dörröppningar, fönster och möbler i hemmet (se #86).
 *
 * Skanningen läses här i webbläsaren och inte i kärnan. Kartredigeraren gör
 * redan alla ändringar som rena funktioner på hemmet och visar resultatet
 * innan det sparas, så att användaren kan vrida och flytta skanningen först.
 * Själva filen lämnar aldrig webbläsaren; kärnan får bara det färdiga hemmet,
 * som den kontrollerar som vanligt.
 *
 * RoomPlans koordinater är meter, höger hand med y uppåt. Sett ovanifrån är x
 * åt höger och z mot betraktaren, alltså nedåt på kartan. Därför blir
 * skanningens (x, z) hemmets (x, y), utan spegling. Varje yta har sina mått
 * (bredd, höjd, djup) och en 4 × 4-matris som flyttar och vrider den från
 * ytans egna koordinater, där x går längs ytan och y uppåt.
 *
 * Formatet är inte fullständigt dokumenterat och skiljer sig mellan
 * iOS-versioner och appar. Läsaren tar därför det den känner igen, hoppar
 * över resten och säger på svenska vad som inte gick att läsa.
 */
import { polygonArea, roomProblem, toXY, uniqueId, updateFloors } from "./editing";
import type { FloorModel, FurnitureModel, HomeDocument, RoomModel, XY } from "./homefile";
import type { Point, WallKind } from "./protocol";

/** Filen gick inte att läsa som en skanning alls. Meddelandet säger varför, på svenska. */
export class ScanError extends Error {}

export interface ScannedRoom {
  name: string;
  /** Hörnen i ordning, i meter. */
  polygon: Point[];
  /** En vägg per sida, som i hemmet: väggen från hörn i till hörn i + 1. */
  walls: WallKind[];
  /** Takhöjden i meter, om skanningen har väggar längs rummet. */
  ceilingHeight: number | null;
}

export interface ScannedFurniture {
  name: string;
  polygon: Point[];
}

/** Det som lästes ur en skanning, i meter med samma riktningar som på kartan. */
export interface Scan {
  rooms: ScannedRoom[];
  furniture: ScannedFurniture[];
  /** Hur många dörröppningar och fönster som hamnade i någon vägg. */
  doorways: number;
  windows: number;
  /** Det som inte gick att läsa, i klartext. */
  problems: string[];
}

/** Var skanningen ska ligga: vriden ett antal kvarts varv medurs, med övre vänstra hörnet i `position`. */
export interface Placement {
  quarterTurns: number;
  position: Point;
}

/** Var skanningen hamnar om användaren inte flyttar den: en meter från planets hörn. */
export const DEFAULT_PLACEMENT: Placement = { quarterTurns: 0, position: { x: 1, y: 1 } };

/** Hur långt från en sida i golvet en vägg, dörr eller ett fönster får ligga och ändå höra till den. */
const SIDE_TOLERANCE = 0.4;
/** Kortare väggbitar än så här (meter) mellan två dörrar eller fönster räknas till grannen. */
const MIN_PIECE = 0.05;
/** Hur mycket en vägg, dörr eller ett fönster får luta mot en sida i golvet och ändå höra till den. */
const MAX_ANGLE_DEGREES = 20;
/** Så nära (meter) två väggars ändar måste vara för att räknas som ett hörn, när golv saknas. */
const CORNER_TOLERANCE = 0.5;

const FURNITURE_NAMES: Record<string, string> = {
  bathtub: "Badkar",
  bed: "Säng",
  chair: "Stol",
  dishwasher: "Diskmaskin",
  fireplace: "Öppen spis",
  oven: "Ugn",
  refrigerator: "Kylskåp",
  sink: "Handfat",
  sofa: "Soffa",
  stairs: "Trappa",
  storage: "Förvaring",
  stove: "Spis",
  table: "Bord",
  television: "Tv",
  toilet: "Toalett",
  washerDryer: "Tvättmaskin",
};

const ROOM_NAMES: Record<string, string> = {
  bathroom: "Badrum",
  bedroom: "Sovrum",
  diningRoom: "Matsal",
  kitchen: "Kök",
  livingRoom: "Vardagsrum",
};

// --- Att läsa fälten tolerant ---

type Json = Record<string, unknown>;

function asObject(value: unknown): Json | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Json)
    : null;
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** En lista med minst `length` tal, eller null. */
function numbers(value: unknown, length: number): number[] | null {
  return Array.isArray(value) && value.length >= length && value.every(finite)
    ? (value as number[])
    : null;
}

/** En lista ur skanningen, eller en tom lista om fältet saknas eller är något annat. */
function list(data: Json, key: string): unknown[] {
  const value = data[key];
  return Array.isArray(value) ? value : [];
}

/**
 * Namnet på en sort. Swift skriver en sort som {"door": {"isOpen": false}} eller
 * {"sofa": {}}, men en del appar skriver bara "door".
 */
export function categoryName(value: unknown): string | null {
  if (typeof value === "string") {
    return value;
  }
  const object = asObject(value);
  const keys = object === null ? [] : Object.keys(object);
  return keys.length === 1 ? keys[0] : null;
}

/** En 4 × 4-matris med kolumnerna efter varandra, som simd_float4x4 skrivs. */
type Matrix = number[];

/**
 * Matrisen i ett fält, som 16 tal eller 4 kolumner med 4 tal. Kolumnvis är
 * det vanliga (translationen i tal 13–15). Har någon skrivit den radvis, med
 * translationen sist i varje rad, vänds den.
 */
export function readMatrix(value: unknown): Matrix | null {
  let flat: number[] | null = null;
  if (Array.isArray(value) && value.length === 4 && value.every((c) => numbers(c, 4) !== null)) {
    flat = (value as number[][]).flatMap((column) => column.slice(0, 4));
  } else {
    flat = numbers(value, 16);
  }
  if (flat === null || flat.length !== 16) {
    return null;
  }
  const lastRowIsZero = [3, 7, 11].every((i) => Math.abs(flat[i]) < 1e-9);
  const rowMajor = !lastRowIsZero && [12, 13, 14].every((i) => Math.abs(flat[i]) < 1e-9);
  return rowMajor
    ? [0, 1, 2, 3].flatMap((column) => [0, 4, 8, 12].map((row) => flat[row + column]))
    : flat;
}

/** Var en punkt i ytans egna koordinater hamnar på kartan (skanningens x och z). */
function onPlan(matrix: Matrix, [x, y, z]: [number, number, number]): Point {
  return {
    x: matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12],
    y: matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14],
  };
}

const IDENTITY: Matrix = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

interface Surface {
  /** Var ytan står sedd ovanifrån: en sträcka. */
  start: Point;
  end: Point;
  /** Ytans höjd i meter. */
  height: number;
  story: number;
}

/** En vägg, dörr, ett fönster eller en öppning som en sträcka ovanifrån, eller varför det inte gick. */
function readSurface(value: unknown): Surface | string {
  const data = asObject(value);
  if (data === null) {
    return "är inte ett objekt";
  }
  const dimensions = numbers(data.dimensions, 2);
  const matrix = readMatrix(data.transform);
  if (dimensions === null) {
    return "saknar mått";
  }
  if (matrix === null) {
    return "saknar läge (transform)";
  }
  const half = dimensions[0] / 2;
  return {
    start: onPlan(matrix, [-half, 0, 0]),
    end: onPlan(matrix, [half, 0, 0]),
    height: dimensions[1],
    story: finite(data.story) ? data.story : 0,
  };
}

/** Golvets hörn på kartan, eller varför de inte gick att läsa. */
function readFloorPolygon(value: unknown): { polygon: Point[]; story: number } | string {
  const data = asObject(value);
  if (data === null) {
    return "är inte ett objekt";
  }
  const matrix = readMatrix(data.transform) ?? IDENTITY;
  const story = finite(data.story) ? data.story : 0;
  const corners = Array.isArray(data.polygonCorners)
    ? data.polygonCorners.map((corner) => numbers(corner, 2))
    : [];
  if (corners.length >= 3 && corners.every((corner) => corner !== null)) {
    const local = (corners as number[][]).map((c): [number, number, number] => [
      c[0],
      c[1],
      c[2] ?? 0,
    ]);
    let polygon = local.map((corner) => onPlan(matrix, corner));
    if (polygonArea(polygon) < 1e-6) {
      // Hörnen står redan i skanningens koordinater, men i golvets plan (x, y).
      polygon = local.map(([x, y]) => ({ x, y: -y }));
    }
    return { polygon, story };
  }
  // Utan hörn är golvet en rektangel efter måtten.
  const dimensions = numbers(data.dimensions, 2);
  if (dimensions === null) {
    return "saknar både hörn och mått";
  }
  const [w, d] = [dimensions[0] / 2, dimensions[1] / 2];
  const flat: [number, number][] = [
    [-w, -d],
    [w, -d],
    [w, d],
    [-w, d],
  ];
  let polygon = flat.map(([x, y]) => onPlan(matrix, [x, y, 0]));
  if (polygonArea(polygon) < 1e-6) {
    polygon = flat.map(([x, z]) => onPlan(matrix, [x, 0, z]));
  }
  return { polygon, story };
}

// --- Geometri ---

function sub(a: Point, b: Point): Point {
  return { x: a.x - b.x, y: a.y - b.y };
}

function length(v: Point): number {
  return Math.hypot(v.x, v.y);
}

/** Var längs sidan a–b (i meter från a) en sträcka ligger, om den ligger längs sidan. */
function alongSide(a: Point, b: Point, surface: Surface): [number, number] | null {
  const side = sub(b, a);
  const sideLength = length(side);
  const own = sub(surface.end, surface.start);
  if (sideLength < 1e-6 || length(own) < 1e-6) {
    return null;
  }
  const cos = Math.abs(side.x * own.x + side.y * own.y) / (sideLength * length(own));
  if (cos < Math.cos((MAX_ANGLE_DEGREES * Math.PI) / 180)) {
    return null;
  }
  const unit = { x: side.x / sideLength, y: side.y / sideLength };
  const offset = (p: Point) => Math.abs(unit.x * (p.y - a.y) - unit.y * (p.x - a.x));
  if (offset(surface.start) > SIDE_TOLERANCE || offset(surface.end) > SIDE_TOLERANCE) {
    return null;
  }
  const t = (p: Point) => unit.x * (p.x - a.x) + unit.y * (p.y - a.y);
  const [from, to] = [t(surface.start), t(surface.end)].sort((p, q) => p - q);
  const [start, end] = [Math.max(0, from), Math.min(sideLength, to)];
  return end - start > MIN_PIECE ? [start, end] : null;
}

interface Piece {
  start: number;
  end: number;
  kind: WallKind;
}

/**
 * Delar sidan a–b i väggbitar: dörröppning där en dörr eller öppning är,
 * glas där ett fönster är och gips (det vanligaste) i övrigt. Skanningen
 * säger inget om materialet, så det får användaren ändra efteråt.
 */
function splitSide(
  sideLength: number,
  doorways: [number, number][],
  windows: [number, number][],
): Piece[] {
  const cuts = [0, sideLength, ...doorways.flat(), ...windows.flat()].sort((p, q) => p - q);
  const inside = (spans: [number, number][], at: number) =>
    spans.some(([start, end]) => start <= at && at <= end);
  const pieces: Piece[] = [];
  for (let i = 0; i + 1 < cuts.length; i++) {
    const [start, end] = [cuts[i], cuts[i + 1]];
    if (end - start < 1e-9) {
      continue;
    }
    const middle = (start + end) / 2;
    const kind: WallKind = inside(doorways, middle)
      ? "dörröppning"
      : inside(windows, middle)
        ? "glas"
        : "gips";
    const last = pieces.at(-1);
    if (last !== undefined && last.kind === kind) {
      last.end = end;
    } else {
      pieces.push({ start, end, kind });
    }
  }
  // En smal väggbit mellan två öppningar, eller vid ett hörn, blir en del av grannen.
  for (let i = 0; i < pieces.length && pieces.length > 1; ) {
    const piece = pieces[i];
    if (piece.end - piece.start >= MIN_PIECE) {
      i++;
      continue;
    }
    if (i > 0) {
      pieces[i - 1].end = piece.end;
    } else {
      pieces[i + 1].start = piece.start;
    }
    pieces.splice(i, 1);
  }
  return pieces;
}

function median(values: number[]): number | null {
  if (values.length === 0) {
    return null;
  }
  const sorted = [...values].sort((p, q) => p - q);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

/**
 * Rum av väggarna, när skanningen saknar golv (före iOS 17). Väggar vars
 * ändar möts sätts ihop runt rummet, och varje hörn hamnar mitt emellan.
 */
function roomsFromWalls(walls: Surface[]): { polygons: Point[][]; unused: number } {
  const left = [...walls];
  const polygons: Point[][] = [];
  let unused = 0;
  while (left.length > 0) {
    const first = left.shift() as Surface;
    const chain: Point[] = [first.start];
    let end = first.end;
    for (;;) {
      const next = left.findIndex(
        (wall) =>
          length(sub(wall.start, end)) < CORNER_TOLERANCE ||
          length(sub(wall.end, end)) < CORNER_TOLERANCE,
      );
      if (next === -1) {
        break;
      }
      const [wall] = left.splice(next, 1);
      const [near, far] =
        length(sub(wall.start, end)) < length(sub(wall.end, end))
          ? [wall.start, wall.end]
          : [wall.end, wall.start];
      chain.push({ x: (end.x + near.x) / 2, y: (end.y + near.y) / 2 });
      end = far;
    }
    if (chain.length >= 3 && length(sub(end, chain[0])) < CORNER_TOLERANCE) {
      chain[0] = { x: (end.x + chain[0].x) / 2, y: (end.y + chain[0].y) / 2 };
      polygons.push(chain);
    } else {
      unused += chain.length;
    }
  }
  return { polygons, unused };
}

/**
 * Vinkeln (radianer) som vrider skanningen så att väggarna går rakt åt
 * sidorna och uppåt. Telefonen bestämmer själv vilket håll som är x i
 * skanningen, så utan det här hamnar huset snett på kartan. Längre väggar
 * väger mer, och vinklar räknas modulo 90°, så att väggar i rät vinkel
 * mot varandra drar åt samma håll.
 */
export function straighteningAngle(walls: { start: Point; end: Point }[]): number {
  let [sumX, sumY] = [0, 0];
  for (const { start, end } of walls) {
    const angle = Math.atan2(end.y - start.y, end.x - start.x);
    const weight = length(sub(end, start));
    sumX += weight * Math.cos(4 * angle);
    sumY += weight * Math.sin(4 * angle);
  }
  return sumX === 0 && sumY === 0 ? 0 : -Math.atan2(sumY, sumX) / 4;
}

function rotate(point: Point, angle: number): Point {
  const [cos, sin] = [Math.cos(angle), Math.sin(angle)];
  return { x: point.x * cos - point.y * sin, y: point.x * sin + point.y * cos };
}

function counted(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

// --- Att läsa skanningen ---

/**
 * Läser skanningen ur filens text. Ger ett `ScanError` om det inte är en
 * RoomPlan-skanning alls. Det som inte gick att läsa i en skanning står i
 * `problems`.
 */
export function readScan(text: string): Scan {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new ScanError(
      "Filen är inte en JSON-fil. Exportera skanningen som JSON (inte USDZ) och försök igen.",
    );
  }
  const data = asObject(parsed);
  if (data === null) {
    throw new ScanError("Filen ser inte ut som en RoomPlan-skanning.");
  }
  // En skanning av flera rum (CapturedStructure) kan ha rummen för sig.
  const parts = [
    data,
    ...list(data, "rooms")
      .map(asObject)
      .filter((room) => room !== null),
  ];
  const gather = (key: string) =>
    data[key] !== undefined ? list(data, key) : parts.slice(1).flatMap((room) => list(room, key));
  const problems: string[] = [];

  const surfaces = (key: string, label: string): Surface[] =>
    gather(key).flatMap((value, i) => {
      const surface = readSurface(value);
      if (typeof surface === "string") {
        problems.push(`${label} ${i + 1} ${surface} och hoppades över.`);
        return [];
      }
      if (asObject(value)?.curve != null) {
        problems.push(`${label} ${i + 1} är rundad i skanningen och blev rak.`);
      }
      return [surface];
    });
  const walls = surfaces("walls", "Vägg");
  const doorways = [...surfaces("doors", "Dörr"), ...surfaces("openings", "Öppning")];
  const windows = surfaces("windows", "Fönster");

  const floors = gather("floors").flatMap((value, i) => {
    const floor = readFloorPolygon(value);
    if (typeof floor === "string") {
      problems.push(`Golv ${i + 1} ${floor} och hoppades över.`);
      return [];
    }
    return [floor];
  });
  if (walls.length === 0 && floors.length === 0) {
    throw new ScanError(
      "Filen har inga väggar eller golv som går att läsa. Är det en RoomPlan-skanning?",
    );
  }

  // En skanning kan ha flera våningsplan. Bara det första läses, som en våning i hemmet.
  const story = floors[0]?.story ?? walls[0]?.story ?? 0;
  const otherStories =
    floors.some((floor) => floor.story !== story) || walls.some((wall) => wall.story !== story);
  if (otherStories) {
    problems.push("Skanningen har flera våningsplan. Bara det första lästes in.");
  }
  const onStory = <T extends { story: number }>(items: T[]) =>
    items.filter((item) => item.story === story);

  let polygons = onStory(floors).map((floor) => floor.polygon);
  if (polygons.length === 0) {
    const fromWalls = roomsFromWalls(onStory(walls));
    polygons = fromWalls.polygons;
    problems.push(
      "Skanningen har inga golv (de kom i iOS 17), så rummen ritades efter väggarna. Kontrollera hörnen.",
    );
    if (fromWalls.unused > 0) {
      problems.push(
        `${counted(fromWalls.unused, "vägg", "väggar")} gick inte att sätta ihop till ett rum.`,
      );
    }
  }

  // Vrid allt så att väggarna går rakt på kartan.
  const angle = straighteningAngle(
    onStory(walls).length > 0
      ? onStory(walls)
      : polygons.flatMap((polygon) =>
          polygon.map((start, i) => ({ start, end: polygon[(i + 1) % polygon.length] })),
        ),
  );
  const straight = (surface: Surface): Surface => ({
    ...surface,
    start: rotate(surface.start, angle),
    end: rotate(surface.end, angle),
  });
  const straightWalls = onStory(walls).map(straight);
  const straightDoorways = onStory(doorways).map(straight);
  const straightWindows = onStory(windows).map(straight);
  const usedDoorways = new Set<Surface>();
  const usedWindows = new Set<Surface>();
  const usedWalls = new Set<Surface>();

  // Avdelningarnas mittpunkter står i skanningens koordinater, som golven innan de vreds.
  const names = roomNames(gather("sections"), polygons);
  const rooms: ScannedRoom[] = [];
  polygons.forEach((original, index) => {
    const outline = original.map((point) => rotate(point, angle));
    const problem = roomProblem(outline);
    if (problem !== null) {
      problems.push(`Rum ${index + 1} hoppades över: ${problem}`);
      return;
    }
    const polygon: Point[] = [];
    const kinds: WallKind[] = [];
    const heights: number[] = [];
    outline.forEach((a, i) => {
      const b = outline[(i + 1) % outline.length];
      const spans = (surfaces: Surface[], used: Set<Surface>) =>
        surfaces.flatMap((surface) => {
          const span = alongSide(a, b, surface);
          if (span !== null) {
            used.add(surface);
          }
          return span === null ? [] : [span];
        });
      for (const wall of straightWalls) {
        if (alongSide(a, b, wall) !== null) {
          usedWalls.add(wall);
          heights.push(wall.height);
        }
      }
      const side = sub(b, a);
      const sideLength = length(side);
      for (const piece of splitSide(
        sideLength,
        spans(straightDoorways, usedDoorways),
        spans(straightWindows, usedWindows),
      )) {
        const at = piece.start / sideLength;
        polygon.push({ x: a.x + side.x * at, y: a.y + side.y * at });
        kinds.push(piece.kind);
      }
    });
    const height = median(heights);
    rooms.push({
      name: names[index],
      polygon,
      walls: kinds,
      ceilingHeight: height !== null && height > 0 ? height : null,
    });
  });

  const missedWalls = straightWalls.length - usedWalls.size;
  if (floors.length > 0 && missedWalls > 0) {
    problems.push(
      `${counted(missedWalls, "vägg", "väggar")} låg inte längs något golv och kom inte med.`,
    );
  }
  const missedDoorways = straightDoorways.length - usedDoorways.size;
  if (missedDoorways > 0) {
    problems.push(
      `${counted(missedDoorways, "dörr eller öppning", "dörrar eller öppningar")} låg inte vid någon vägg och kom inte med.`,
    );
  }
  const missedWindows = straightWindows.length - usedWindows.size;
  if (missedWindows > 0) {
    problems.push(
      `${counted(missedWindows, "fönster", "fönster")} låg inte vid någon vägg och kom inte med.`,
    );
  }

  const furniture = readFurniture(gather("objects"), story, angle, problems);
  return {
    rooms,
    furniture,
    doorways: usedDoorways.size,
    windows: usedWindows.size,
    problems,
  };
}

/** Rummens namn efter skanningens avdelningar (sections), till exempel "Sovrum", annars "Rum 1". */
function roomNames(sections: unknown[], polygons: Point[][]): string[] {
  const labelled = sections.flatMap((value) => {
    const data = asObject(value);
    const center = numbers(data?.center, 3);
    const label = categoryName(data?.label);
    const name = label === null ? undefined : ROOM_NAMES[label];
    return center === null || name === undefined
      ? []
      : [{ name, at: { x: center[0], y: center[2] } }];
  });
  const used = new Map<string, number>();
  return polygons.map((polygon, i) => {
    const section = labelled.find(({ at }) => inside(at, polygon));
    const base = section?.name ?? `Rum ${i + 1}`;
    const count = (used.get(base) ?? 0) + 1;
    used.set(base, count);
    return count === 1 ? base : `${base} ${count}`;
  });
}

function inside(point: Point, polygon: Point[]): boolean {
  let result = false;
  polygon.forEach((a, i) => {
    const b = polygon[(i + 1) % polygon.length];
    if (a.y > point.y !== b.y > point.y) {
      if (point.x < a.x + ((point.y - a.y) * (b.x - a.x)) / (b.y - a.y)) {
        result = !result;
      }
    }
  });
  return result;
}

/** Möblerna som rektanglar ovanifrån. RoomPlan ger varje möbel som en låda. */
function readFurniture(
  objects: unknown[],
  story: number,
  angle: number,
  problems: string[],
): ScannedFurniture[] {
  const unknown = new Set<string>();
  const furniture = objects.flatMap((value, i) => {
    const data = asObject(value);
    const dimensions = numbers(data?.dimensions, 3);
    const matrix = readMatrix(data?.transform);
    if (data === null || dimensions === null || matrix === null) {
      problems.push(`Möbel ${i + 1} saknar mått eller läge och hoppades över.`);
      return [];
    }
    if (finite(data.story) && data.story !== story) {
      return [];
    }
    const category = categoryName(data.category);
    let name = category === null ? undefined : FURNITURE_NAMES[category];
    if (name === undefined) {
      unknown.add(category ?? "utan sort");
      name = "Möbel";
    }
    const [w, d] = [dimensions[0] / 2, dimensions[2] / 2];
    const corners: [number, number][] = [
      [-w, -d],
      [w, -d],
      [w, d],
      [-w, d],
    ];
    const polygon = corners.map(([x, z]) => rotate(onPlan(matrix, [x, 0, z]), angle));
    if (polygonArea(polygon) < 1e-4) {
      problems.push(`Möbel ${i + 1} (${name}) har ingen yta ovanifrån och hoppades över.`);
      return [];
    }
    return [{ name, polygon }];
  });
  if (unknown.size > 0) {
    problems.push(`Okända sorters möbler visas som "Möbel": ${[...unknown].join(", ")}.`);
  }
  return furniture;
}

// --- Att lägga skanningen i hemmet ---

/** Vrider skanningen ett antal kvarts varv medurs och flyttar den så att övre vänstra hörnet hamnar i `position`. */
export function placeScan(scan: Scan, placement: Placement): Scan {
  const turns = ((Math.round(placement.quarterTurns) % 4) + 4) % 4;
  // Med y nedåt vrider (x, y) → (−y, x) ett kvarts varv medurs på skärmen.
  const turn = (point: Point): Point => {
    let p = point;
    for (let i = 0; i < turns; i++) {
      p = { x: -p.y, y: p.x };
    }
    return p;
  };
  const all = [...scan.rooms, ...scan.furniture].flatMap((item) => item.polygon.map(turn));
  if (all.length === 0) {
    return scan;
  }
  const [minX, minY] = [Math.min(...all.map((p) => p.x)), Math.min(...all.map((p) => p.y))];
  const target = {
    x: Math.max(0, placement.position.x),
    y: Math.max(0, placement.position.y),
  };
  const move = (point: Point): Point => {
    const p = turn(point);
    return { x: p.x - minX + target.x, y: p.y - minY + target.y };
  };
  return {
    ...scan,
    rooms: scan.rooms.map((room) => ({ ...room, polygon: room.polygon.map(move) })),
    furniture: scan.furniture.map((item) => ({ ...item, polygon: item.polygon.map(move) })),
  };
}

/** Avrundar hörnen till hela centimeter och tar bort sidor som då blev noll långa. */
function roundedOutline(polygon: Point[], walls: WallKind[]): { polygon: XY[]; walls: WallKind[] } {
  const points = polygon.map(toXY);
  const kept: { point: XY; wall: WallKind }[] = [];
  points.forEach((point, i) => {
    const previous = kept.at(-1)?.point;
    if (previous !== undefined && previous[0] === point[0] && previous[1] === point[1]) {
      return; // sidan före var noll lång; väggen efter hörnet gäller
    }
    kept.push({ point, wall: walls[i] });
  });
  const [first, last] = [kept[0], kept.at(-1)];
  if (
    kept.length > 1 &&
    last !== undefined &&
    first.point[0] === last.point[0] &&
    first.point[1] === last.point[1]
  ) {
    kept.pop();
  }
  return { polygon: kept.map((k) => k.point), walls: kept.map((k) => k.wall) };
}

/** Var skanningen ska in: som en ny våning, eller i stället för det som finns på en våning. */
export type ScanTarget = { kind: "new" } | { kind: "replace"; floorId: string };

/**
 * Lägger in en placerad skanning i hemmet. Som ny våning hamnar den ett
 * plan ovanför den översta. I stället för en våning ersätts våningens rum
 * och möbler, med allt i rummen (zoner, rumskort och lampor). Våningens
 * namn, routrar och planritning finns kvar.
 */
export function importScan(
  document: HomeDocument,
  scan: Scan,
  target: ScanTarget,
): { document: HomeDocument; floorId: string } {
  let next = document;
  let floorId: string;
  if (target.kind === "new") {
    const name = "Skannad våning";
    floorId = uniqueId(document, name);
    const level = Math.max(...document.home.floors.map((floor) => floor.level)) + 1;
    const floor: FloorModel = { id: floorId, name, level, rooms: [] };
    next = { ...document, home: { ...document.home, floors: [...document.home.floors, floor] } };
  } else {
    floorId = target.floorId;
    next = updateFloors(next, (floor) =>
      floor.id === floorId ? { ...floor, rooms: [], furniture: [] } : floor,
    );
  }
  const rooms: RoomModel[] = [];
  for (const scanned of scan.rooms) {
    const outline = roundedOutline(scanned.polygon, scanned.walls);
    if (outline.polygon.length < 3) {
      continue;
    }
    // Id:t räknas fram mot hemmet med de rum som redan är tillagda.
    const withRooms = updateFloors(next, (floor) =>
      floor.id === floorId ? { ...floor, rooms } : floor,
    );
    const room: RoomModel = {
      id: uniqueId(withRooms, scanned.name),
      name: scanned.name,
      ...outline,
    };
    if (scanned.ceilingHeight !== null) {
      room.ceiling_height = Math.round(scanned.ceilingHeight * 100) / 100;
    }
    rooms.push(room);
  }
  const furniture: FurnitureModel[] = scan.furniture.map((item) => ({
    name: item.name,
    polygon: item.polygon.map(toXY),
  }));
  next = updateFloors(next, (floor) =>
    floor.id === floorId ? { ...floor, rooms, furniture } : floor,
  );
  return { document: next, floorId };
}
