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
import {
  addFloor,
  distance,
  inPolygon,
  polygonArea,
  roomProblem,
  roundCm,
  toPoint,
  toXY,
  uniqueId,
  updateFloors,
} from "./editing";
import type { FurnitureModel, HomeDocument, RoomModel, XY } from "./homefile";
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
/** Kortare (meter) eller mindre (kvadratmeter) än så här är ingenting, bara avrundningsfel. */
const EPSILON = 1e-6;
/** Mindre möbler än så här (kvadratmeter ovanifrån) hoppas över. */
const MIN_FURNITURE_AREA = 1e-4;
/** Högre väggar än så här är fel i skanningen (samma gräns som MAX_HEIGHT i kärnans home.py). */
const MAX_CEILING_HEIGHT = 20;
/**
 * Högst så här många saker av varje slag (väggar, golv, möbler …) och hörn per golv läses.
 * En riktig skanning har några hundra; en enorm fil skulle annars kunna låsa webbläsaren.
 */
export const MAX_ITEMS = 2000;

// Tabellerna är Map och inte vanliga objekt, så att en sort som "constructor"
// eller "__proto__" i filen inte hittar något i objektets prototyp.
const FURNITURE_NAMES = new Map(
  Object.entries({
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
  }),
);

const ROOM_NAMES = new Map(
  Object.entries({
    bathroom: "Badrum",
    bedroom: "Sovrum",
    diningRoom: "Matsal",
    kitchen: "Kök",
    livingRoom: "Vardagsrum",
  }),
);

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
  const value = Object.hasOwn(data, key) ? data[key] : undefined;
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
  const lastRowIsZero = [3, 7, 11].every((i) => Math.abs(flat[i]) < EPSILON);
  const rowMajor = !lastRowIsZero && [12, 13, 14].every((i) => Math.abs(flat[i]) < EPSILON);
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
    return "saknar uppgift om var den står";
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
    ? data.polygonCorners.slice(0, MAX_ITEMS).map((corner) => numbers(corner, 2))
    : [];
  if (corners.length >= 3 && corners.every((corner) => corner !== null)) {
    const local = (corners as number[][]).map((c): [number, number, number] => [
      c[0],
      c[1],
      c[2] ?? 0,
    ]);
    let polygon = local.map((corner) => onPlan(matrix, corner));
    if (polygonArea(polygon) < EPSILON) {
      // Matrisen saknas eller lägger golvet på högkant, så hörnen står i golvets
      // eget plan. Där pekar y bort från betraktaren, alltså mot skanningens −z
      // och uppåt på kartan.
      polygon = local.map(([x, y]) => ({ x: x + matrix[12], y: -y + matrix[14] }));
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
  if (polygonArea(polygon) < EPSILON) {
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

function midpoint(a: Point, b: Point): Point {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

/** Var längs sidan a–b (i meter från a) en sträcka ligger, om den ligger längs sidan. */
function alongSide(a: Point, b: Point, surface: Surface): [number, number] | null {
  const side = sub(b, a);
  const sideLength = length(side);
  const own = sub(surface.end, surface.start);
  if (sideLength < EPSILON || length(own) < EPSILON) {
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
  const covers = (spans: [number, number][], at: number) =>
    spans.some(([start, end]) => start <= at && at <= end);
  const pieces: Piece[] = [];
  for (let i = 0; i + 1 < cuts.length; i++) {
    const [start, end] = [cuts[i], cuts[i + 1]];
    if (end - start < EPSILON) {
      continue;
    }
    const middle = (start + end) / 2;
    const kind: WallKind = covers(doorways, middle)
      ? "dörröppning"
      : covers(windows, middle)
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
  const remaining = [...walls];
  const polygons: Point[][] = [];
  let unused = 0;
  while (remaining.length > 0) {
    const first = remaining.shift() as Surface;
    const chain: Point[] = [first.start];
    let end = first.end;
    for (;;) {
      const next = remaining.findIndex(
        (wall) =>
          distance(wall.start, end) < CORNER_TOLERANCE ||
          distance(wall.end, end) < CORNER_TOLERANCE,
      );
      if (next === -1) {
        break;
      }
      const [wall] = remaining.splice(next, 1);
      const [near, far] =
        distance(wall.start, end) < distance(wall.end, end)
          ? [wall.start, wall.end]
          : [wall.end, wall.start];
      chain.push(midpoint(end, near));
      end = far;
    }
    if (chain.length >= 3 && distance(end, chain[0]) < CORNER_TOLERANCE) {
      chain[0] = midpoint(end, chain[0]);
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
    const weight = distance(start, end);
    sumX += weight * Math.cos(4 * angle);
    sumY += weight * Math.sin(4 * angle);
  }
  return sumX === 0 && sumY === 0 ? 0 : -Math.atan2(sumY, sumX) / 4;
}

function rotate(point: Point, angle: number): Point {
  const [cos, sin] = [Math.cos(angle), Math.sin(angle)];
  return { x: point.x * cos - point.y * sin, y: point.x * sin + point.y * cos };
}

/** Ett antal med rätt form av ordet, till exempel "1 vägg" eller "3 väggar". */
export function counted(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

// --- Att läsa skanningen ---

/** Ytorna i skanningen, rätade så att väggarna går rakt på kartan. */
interface Surfaces {
  walls: Surface[];
  doorways: Surface[];
  windows: Surface[];
}

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
  const problems: string[] = [];
  const gather = gatherer(data, problems);

  const surfaces = (key: string, label: string): Surface[] =>
    gather(key, label).flatMap((value, i) => {
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
  const floors = gather("floors", "Golv").flatMap((value, i) => {
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
  if (floors.some((floor) => floor.story !== story) || walls.some((w) => w.story !== story)) {
    problems.push("Skanningen har flera våningsplan. Bara det första lästes in.");
  }
  const onStory = <T extends { story: number }>(items: T[]) =>
    items.filter((item) => item.story === story);

  let outlines = onStory(floors).map((floor) => floor.polygon);
  if (outlines.length === 0) {
    const fromWalls = roomsFromWalls(onStory(walls));
    outlines = fromWalls.polygons;
    problems.push(
      "Skanningen har inga golv (de kom i iOS 17), så rummen ritades efter väggarna. " +
        "Kontrollera hörnen.",
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
      : outlines.flatMap((outline) =>
          outline.map((start, i) => ({ start, end: outline[(i + 1) % outline.length] })),
        ),
  );
  const straight = (surface: Surface): Surface => ({
    ...surface,
    start: rotate(surface.start, angle),
    end: rotate(surface.end, angle),
  });
  const straightened: Surfaces = {
    walls: onStory(walls).map(straight),
    doorways: onStory(doorways).map(straight),
    windows: onStory(windows).map(straight),
  };
  const used = new Set<Surface>();

  // Avdelningarnas mittpunkter står i skanningens koordinater, som golven innan de vreds.
  const names = roomNames(gather("sections", "Avdelning"), outlines);
  const rooms = outlines.flatMap((original, index) => {
    const outline = original.map((point) => rotate(point, angle));
    const problem = roomProblem(outline);
    if (problem !== null) {
      problems.push(`Rum ${index + 1} hoppades över: ${problem}`);
      return [];
    }
    return [buildRoom(names[index], outline, straightened, used)];
  });
  reportUnused(straightened, used, floors.length > 0, problems);

  return {
    rooms,
    furniture: readFurniture(gather("objects", "Möbel"), story, angle, problems),
    doorways: straightened.doorways.filter((surface) => used.has(surface)).length,
    windows: straightened.windows.filter((surface) => used.has(surface)).length,
    problems,
  };
}

/**
 * En funktion som hämtar en lista ur skanningen, till exempel väggarna.
 * En skanning av flera rum (CapturedStructure) kan ha listorna i varje rum
 * för sig. Listorna kortas till `MAX_ITEMS`, och det skrivs i `problems`.
 */
function gatherer(data: Json, problems: string[]): (key: string, label: string) => unknown[] {
  const rooms = list(data, "rooms")
    .slice(0, MAX_ITEMS)
    .map(asObject)
    .filter((room) => room !== null);
  return (key, label) => {
    const items = Object.hasOwn(data, key)
      ? list(data, key)
      : rooms.flatMap((room) => list(room, key));
    if (items.length > MAX_ITEMS) {
      problems.push(
        `Skanningen har ${items.length} av sorten ${label.toLowerCase()}. ` +
          `Bara de första ${MAX_ITEMS} lästes.`,
      );
    }
    return items.slice(0, MAX_ITEMS);
  };
}

/** Var längs sidan a–b dörrarna eller fönstren ligger. De som hör till sidan läggs i `used`. */
function spansAlong(
  a: Point,
  b: Point,
  candidates: Surface[],
  used: Set<Surface>,
): [number, number][] {
  return candidates.flatMap((surface) => {
    const span = alongSide(a, b, surface);
    if (span === null) {
      return [];
    }
    used.add(surface);
    return [span];
  });
}

/**
 * Ett rum av golvets hörn. Varje sida delas där dörrar och fönster sitter,
 * och takhöjden är mittvärdet av höjden på väggarna längs rummet.
 */
function buildRoom(
  name: string,
  outline: Point[],
  surfaces: Surfaces,
  used: Set<Surface>,
): ScannedRoom {
  const polygon: Point[] = [];
  const kinds: WallKind[] = [];
  const heights: number[] = [];
  outline.forEach((a, i) => {
    const b = outline[(i + 1) % outline.length];
    for (const wall of surfaces.walls) {
      if (alongSide(a, b, wall) !== null) {
        used.add(wall);
        if (wall.height > 0 && wall.height <= MAX_CEILING_HEIGHT) {
          heights.push(wall.height);
        }
      }
    }
    const side = sub(b, a);
    const sideLength = length(side);
    const pieces = splitSide(
      sideLength,
      spansAlong(a, b, surfaces.doorways, used),
      spansAlong(a, b, surfaces.windows, used),
    );
    for (const piece of pieces) {
      const at = piece.start / sideLength;
      polygon.push({ x: a.x + side.x * at, y: a.y + side.y * at });
      kinds.push(piece.kind);
    }
  });
  const height = median(heights);
  return {
    name,
    polygon,
    walls: kinds,
    ceilingHeight: height,
  };
}

/** Säger till om väggar, dörrar och fönster som inte hamnade i något rum. */
function reportUnused(
  surfaces: Surfaces,
  used: Set<Surface>,
  hadFloors: boolean,
  problems: string[],
): void {
  const missed = (items: Surface[]) => items.filter((item) => !used.has(item)).length;
  const walls = missed(surfaces.walls);
  if (hadFloors && walls > 0) {
    problems.push(
      `${counted(walls, "vägg", "väggar")} låg inte längs något golv och kom inte med.`,
    );
  }
  const doorways = missed(surfaces.doorways);
  if (doorways > 0) {
    problems.push(
      `${counted(doorways, "dörr eller öppning", "dörrar eller öppningar")} låg inte vid ` +
        "någon vägg och kom inte med.",
    );
  }
  const windows = missed(surfaces.windows);
  if (windows > 0) {
    problems.push(
      `${counted(windows, "fönster", "fönster")} låg inte vid någon vägg och kom inte med.`,
    );
  }
}

/** Rummens namn efter skanningens avdelningar (sections), till exempel "Sovrum", annars "Rum 1". */
function roomNames(sections: unknown[], outlines: Point[][]): string[] {
  const labelled = sections.flatMap((value) => {
    const data = asObject(value);
    const center = numbers(data?.center, 3);
    const label = categoryName(data?.label);
    const name = label === null ? undefined : ROOM_NAMES.get(label);
    return center === null || name === undefined
      ? []
      : [{ name, at: { x: center[0], y: center[2] } }];
  });
  const used = new Map<string, number>();
  return outlines.map((outline, i) => {
    const section = labelled.find(({ at }) => inPolygon(at, outline));
    const base = section?.name ?? `Rum ${i + 1}`;
    const count = (used.get(base) ?? 0) + 1;
    used.set(base, count);
    return count === 1 ? base : `${base} ${count}`;
  });
}

/** Möblerna som rektanglar ovanifrån. RoomPlan ger varje möbel som en låda. */
function readFurniture(
  objects: unknown[],
  story: number,
  angle: number,
  problems: string[],
): ScannedFurniture[] {
  const unknownCategories = new Set<string>();
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
    let name = category === null ? undefined : FURNITURE_NAMES.get(category);
    if (name === undefined) {
      unknownCategories.add(category ?? "utan sort");
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
    if (polygonArea(polygon) < MIN_FURNITURE_AREA) {
      problems.push(`Möbel ${i + 1} (${name}) har ingen yta ovanifrån och hoppades över.`);
      return [];
    }
    return [{ name, polygon }];
  });
  if (unknownCategories.size > 0) {
    problems.push(
      `Möbler av okänd sort visas som "Möbel" (sorten i skanningen: ` +
        `${[...unknownCategories].join(", ")}).`,
    );
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
  let [minX, minY] = [Infinity, Infinity];
  for (const item of [...scan.rooms, ...scan.furniture]) {
    for (const point of item.polygon.map(turn)) {
      minX = Math.min(minX, point.x);
      minY = Math.min(minY, point.y);
    }
  }
  if (minX === Infinity) {
    return scan;
  }
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
  const same = (a: XY, b: XY) => a[0] === b[0] && a[1] === b[1];
  const kept: { point: XY; wall: WallKind }[] = [];
  polygon.map(toXY).forEach((point, i) => {
    const last = kept.at(-1);
    if (last !== undefined && same(last.point, point)) {
      last.wall = walls[i]; // sidan före blev noll lång; väggen från det här hörnet gäller
      return;
    }
    kept.push({ point, wall: walls[i] });
  });
  const closing = kept.at(-1);
  if (kept.length > 1 && closing !== undefined && same(kept[0].point, closing.point)) {
    kept.pop();
  }
  return { polygon: kept.map((k) => k.point), walls: kept.map((k) => k.wall) };
}

/** Var skanningen ska in: som en ny våning, eller i stället för det som finns på en våning. */
export type ScanTarget = { kind: "new" } | { kind: "replace"; floorId: string };

/** Namnet på våningen som en skanning blir, när den läses in som en ny våning. */
export const SCANNED_FLOOR_NAME = "Skannad våning";

/**
 * Lägger in en placerad skanning i hemmet. Som ny våning hamnar den ett
 * plan ovanför den översta. I stället för en våning ersätts våningens rum
 * och möbler, med allt i rummen (zoner, rumskort och lampor). Våningens
 * namn, routrar och planritning finns kvar.
 *
 * Hörnen avrundas till hela centimeter. Ett rum eller en möbel som då inte
 * längre har någon yta tas inte med, eftersom kärnan inte skulle godta det.
 * Ger ett `ScanError` om våningen som ska ersättas inte finns.
 */
export function importScan(
  document: HomeDocument,
  scan: Scan,
  target: ScanTarget,
): { document: HomeDocument; floorId: string } {
  if (target.kind === "replace" && !document.home.floors.some((f) => f.id === target.floorId)) {
    throw new ScanError("Våningen som skanningen skulle ersätta finns inte längre.");
  }
  const added =
    target.kind === "new"
      ? addFloor(document, SCANNED_FLOOR_NAME)
      : { document, floorId: target.floorId };
  const { floorId } = added;
  const withRooms = (doc: HomeDocument, rooms: RoomModel[], furniture: FurnitureModel[]) =>
    updateFloors(doc, (floor) => (floor.id === floorId ? { ...floor, rooms, furniture } : floor));
  // Våningens gamla rum tas bort först, så att deras id kan användas igen.
  const empty = withRooms(added.document, [], []);
  const rooms: RoomModel[] = [];
  for (const scanned of scan.rooms) {
    const outline = roundedOutline(scanned.polygon, scanned.walls);
    if (outline.polygon.length < 3 || polygonArea(outline.polygon.map(toPoint)) < EPSILON) {
      continue;
    }
    // Id:t räknas fram mot hemmet med de rum som redan är tillagda.
    const id = uniqueId(withRooms(empty, rooms, []), scanned.name);
    const room: RoomModel = { id, name: scanned.name, ...outline };
    if (scanned.ceilingHeight !== null) {
      room.ceiling_height = roundCm(scanned.ceilingHeight);
    }
    rooms.push(room);
  }
  const furniture = scan.furniture
    .map((item) => ({ name: item.name, polygon: item.polygon.map(toXY) }))
    .filter((item) => polygonArea(item.polygon.map(toPoint)) >= MIN_FURNITURE_AREA);
  return { document: withRooms(empty, rooms, furniture), floorId };
}
