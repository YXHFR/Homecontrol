/**
 * Det som ritas ovanpå rummen på kartan: väggarnas material, rumskorten med
 * radarns synfält och vridring, routrarna och lamporna, med läget från kärnan
 * (tänd lampa, kort som inte svarar). Allt är i meter, som resten av kartan
 * (se mapView.ts).
 */
import Konva from "konva";
import { toPoint } from "./editing";
import type { FloorModel } from "./homefile";
import { bearing, RADAR_HALF_ANGLE, RADAR_RANGE } from "./placing";
import type { Card, LampState, Person, Point, StateMessage, WallKind } from "./protocol";
import type { MapColors } from "./theme";
import type { ToolName } from "./tools";

/**
 * Hur kartan används just nu. I "view" väljs saker med ett tryck och inget
 * går att dra, i "move" går bara saken som flyttas att dra, och i "edit"
 * (Ändra rummet) gäller ritverktygen.
 */
export type MapMode = "view" | "move" | "edit";

/** Det kärnan säger just nu, som kartan visar ovanpå hemmet. */
export interface LiveScene {
  /** Läget från kärnan, eller null innan det har kommit. */
  state: StateMessage | null;
  /** Lamporna som bara finns i simuleringen, utan någon riktig lampa. */
  virtualLamps: ReadonlySet<string>;
  /** Låtsaspersonerna på våningen. Tom när simuleringen är av. */
  persons: Person[];
}

/** Det av kartans läge som behövs för att rita sakerna. */
export interface ItemScene {
  floor: FloorModel;
  mode: MapMode;
  tool: ToolName;
  selectedItemId: string | null;
  /** Saken som flyttas i läget Flytta. */
  movingId: string | null;
  live: LiveScene;
}

export interface ItemCallbacks {
  /** En sak har valts med ett tryck. */
  onSelectItem(itemId: string): void;
  /** En sak har börjat dras, eller vridas med ringen. */
  onDragStart(itemId: string): void;
  /** En sak har dragits hit. */
  onMoveItem(itemId: string, point: Point): void;
  /** Ett rumskort har vridits med ringen, till så här många grader. */
  onRotateNode(nodeId: string, rotation: number): void;
  /** En vägg har klickats med väggverktyget: rummet och väggens nummer. */
  onClickWall(roomId: string, index: number): void;
}

/** Hur väggarna ser ut. En dörröppning är streckad, eftersom den inte är någon vägg. */
export const WALL_STYLES: Record<WallKind, { width: number; dash?: number[]; opacity?: number }> = {
  gips: { width: 4 },
  betong: { width: 6 },
  trä: { width: 4, dash: [10, 3] },
  glas: { width: 2, opacity: 0.6 },
  dörröppning: { width: 2, dash: [6, 5], opacity: 0.7 },
};

/** Storlekar i skärmpunkter, så att sakerna syns lika bra hur långt in man än har zoomat. */
const ITEM_RADIUS_PX = 10;
const RING_RADIUS_PX = 46;
const HANDLE_RADIUS_PX = 11;
const HEALTH_DOT_PX = 4;
/** Hur brett (i skärmpunkter) ett klick på en vägg får vara från linjen. */
const WALL_HIT_PX = 14;
/** Extra yta runt en sak som går att trycka på, så att den är lätt att träffa med fingret. */
const ITEM_HIT_PX = 14;
/** Skenet runt en tänd lampa, i meter, och hur starkt det är. */
const GLOW_RADIUS_METERS = 0.9;
const GLOW_OPACITY = 0.5;
/** Det som inte flyttas syns svagare medan något flyttas. */
const DIMMED = 0.3;
/** En router är vilande (Wi-Fi-avkänningen används inte) och visas dämpad. */
const ROUTER_OPACITY = 0.45;

/** Hur en sak mår: grön, gul, röd, grå (okänt) eller ingen prick. */
export type Health = "ok" | "warn" | "bad" | "unk" | null;

/** Det riktiga kortet som ett placerat rumskort är, om det är ett. */
export function cardFor(state: StateMessage | null, nodeId: string): Card | undefined {
  return state?.cards?.find((card) => card.id === nodeId);
}

/** Hur ett rumskort mår. Ett simulerat kort har ingen prick. */
export function nodeHealth(card: Card | undefined): Health {
  if (card === undefined) {
    return null;
  }
  if (!card.online) {
    return "bad";
  }
  return card.problems.length > 0 || card.calibrating ? "warn" : "ok";
}

/** Hur en lampa mår: röd när den inte svarar, grå när läget är okänt. */
export function lampHealth(state: LampState | undefined): Health {
  if (state?.responding === false) {
    return "bad";
  }
  return state === undefined || state.on === null ? "unk" : null;
}

function healthColor(health: Health, colors: MapColors): string {
  switch (health) {
    case "ok":
      return colors.ok;
    case "warn":
      return colors.warn;
    case "bad":
      return colors.bad;
    default:
      return colors.unk;
  }
}

export function drawWalls(
  layer: Konva.Layer,
  scene: ItemScene,
  callbacks: ItemCallbacks,
  container: HTMLElement,
  colors: MapColors,
): void {
  layer.destroyChildren();
  const active = scene.mode === "edit" && scene.tool === "walls";
  layer.listening(active);
  // Grannrum delar väggar. Dörröppningar ritas sist så att de syns även om
  // grannrummet har en hel vägg på samma ställe.
  const walls = scene.floor.rooms.flatMap((room) =>
    room.walls.map((kind, index) => ({ room, kind, index })),
  );
  const ordered = [
    ...walls.filter((wall) => wall.kind !== "dörröppning"),
    ...walls.filter((wall) => wall.kind === "dörröppning"),
  ];
  for (const { room, kind, index } of ordered) {
    const start = room.polygon[index];
    const end = room.polygon[(index + 1) % room.polygon.length];
    const style = WALL_STYLES[kind];
    // En dörröppning får golvets färg under strecken, så att den syns som en öppning.
    if (kind === "dörröppning") {
      layer.add(
        new Konva.Line({
          points: [...start, ...end],
          stroke: colors.roomFill,
          strokeWidth: WALL_STYLES.betong.width + 2,
          strokeScaleEnabled: false,
          listening: false,
        }),
      );
    }
    const wall = new Konva.Line({
      points: [...start, ...end],
      stroke: colors.wall,
      strokeWidth: style.width,
      opacity: style.opacity ?? 1,
      hitStrokeWidth: WALL_HIT_PX,
      strokeScaleEnabled: false,
      lineCap: kind === "dörröppning" ? "butt" : "square",
      dash: style.dash,
      dashEnabled: style.dash !== undefined,
    });
    wall.on("pointerclick", () => callbacks.onClickWall(room.id, index));
    wall.on("mouseenter", () => (container.style.cursor = "pointer"));
    wall.on("mouseleave", () => (container.style.cursor = ""));
    layer.add(wall);
  }
}

/** Skenet runt de tända lamporna. Det ligger under rummens väggar och saker. */
export function drawLight(layer: Konva.Layer, scene: ItemScene, colors: MapColors): void {
  layer.destroyChildren();
  const lamps = new Map(scene.live.state?.lamps.map((lamp) => [lamp.id, lamp]));
  for (const room of scene.floor.rooms) {
    for (const lamp of room.lamps ?? []) {
      const state = lamps.get(lamp.id);
      if (state?.on !== true || state.responding === false) {
        continue;
      }
      const at = toPoint(lamp.position);
      // En dimrad lampa har svagare sken.
      layer.add(
        new Konva.Circle({
          ...at,
          radius: GLOW_RADIUS_METERS,
          fillRadialGradientStartPoint: { x: 0, y: 0 },
          fillRadialGradientStartRadius: 0,
          fillRadialGradientEndPoint: { x: 0, y: 0 },
          fillRadialGradientEndRadius: GLOW_RADIUS_METERS,
          fillRadialGradientColorStops: [0, colors.accent, 1, "rgba(0,0,0,0)"],
          opacity: (GLOW_OPACITY * state.brightness) / 100,
          listening: false,
        }),
      );
    }
  }
}

export function drawItems(
  layer: Konva.Layer,
  scene: ItemScene,
  callbacks: ItemCallbacks,
  container: HTMLElement,
  stage: Konva.Stage,
  colors: MapColors,
): void {
  layer.destroyChildren();
  const perPixel = 1 / stage.scaleX();
  const editing = scene.mode === "edit" && scene.tool === "select";
  // Med de andra ritverktygen ska ett klick gå igenom till kartan.
  layer.listening(scene.mode !== "edit" || editing);
  const state = scene.live.state;
  const lampStates = new Map(state?.lamps.map((lamp) => [lamp.id, lamp]));

  const label = (at: Point, text: string, faint = false) =>
    new Konva.Text({
      x: at.x + (ITEM_RADIUS_PX + 4) * perPixel,
      y: at.y - 6 * perPixel,
      text,
      fontSize: 11 * perPixel,
      fontFamily: "ui-monospace, Menlo, Consolas, monospace",
      fill: faint ? colors.text3 : colors.text2,
      listening: false,
    });

  const healthDot = (at: Point, health: Health) =>
    new Konva.Circle({
      x: at.x + ITEM_RADIUS_PX * 0.75 * perPixel,
      y: at.y - ITEM_RADIUS_PX * 0.75 * perPixel,
      radius: HEALTH_DOT_PX * perPixel,
      fill: healthColor(health, colors),
      stroke: colors.planBg,
      strokeWidth: 2,
      strokeScaleEnabled: false,
      listening: false,
    });

  /** Om saken går att dra just nu. */
  const draggable = (itemId: string) =>
    editing || (scene.mode === "move" && scene.movingId === itemId);

  /** En grupp som kan väljas med ett tryck och dras när det är tillåtet. */
  const movable = (itemId: string, at: Point): Konva.Group => {
    const other = scene.mode === "move" && scene.movingId !== itemId;
    const group = new Konva.Group({
      draggable: draggable(itemId),
      name: "item",
      id: itemId,
      opacity: other ? DIMMED : 1,
      // Medan något flyttas går inget annat att trycka på.
      listening: !other,
    });
    group.setAttr("itemId", itemId);
    group.on("pointerclick", () => callbacks.onSelectItem(itemId));
    group.on("mouseenter", () => (container.style.cursor = draggable(itemId) ? "move" : "pointer"));
    group.on("mouseleave", () => (container.style.cursor = ""));
    group.on("dragstart", () => callbacks.onDragStart(itemId));
    group.on("dragend", () => {
      const moved = { x: at.x + group.x(), y: at.y + group.y() };
      group.position({ x: 0, y: 0 });
      callbacks.onMoveItem(itemId, moved);
    });
    return group;
  };

  const selectionRing = (at: Point, itemId: string) =>
    itemId === scene.selectedItemId || itemId === scene.movingId
      ? [
          new Konva.Circle({
            ...at,
            radius: (ITEM_RADIUS_PX + 5) * perPixel,
            stroke: scene.mode === "move" ? colors.accentStrong : colors.text,
            strokeWidth: 2,
            strokeScaleEnabled: false,
            listening: false,
          }),
        ]
      : [];

  /** En osynlig yta runt saken, som gör den lätt att träffa. */
  const hitArea = (at: Point) =>
    new Konva.Circle({ ...at, radius: (ITEM_RADIUS_PX + ITEM_HIT_PX) * perPixel, fill: "transparent" });

  for (const room of scene.floor.rooms) {
    for (const node of room.nodes ?? []) {
      const at = toPoint(node.position);
      const selected = node.id === scene.selectedItemId || node.id === scene.movingId;
      const rotation = node.rotation ?? 0;
      const card = cardFor(state, node.id);
      const health = nodeHealth(card);
      // Ett kort som inte svarar ser inget; konen töms och kortet blir grått och streckat.
      const down = health === "bad";
      const group = movable(node.id, at);
      const fieldOfView = new Konva.Wedge({
        ...at,
        radius: RADAR_RANGE,
        angle: 2 * RADAR_HALF_ANGLE,
        rotation: rotation - RADAR_HALF_ANGLE,
        fill: down ? undefined : selected ? colors.coneSelected : colors.cone,
        stroke: down ? colors.unk : colors.lineStrong,
        strokeWidth: 1,
        strokeScaleEnabled: false,
        dash: [6, 4],
        dashEnabled: down,
        listening: false,
      });
      const pointer = new Konva.Line({
        points: pointerLine(at, rotation, 2 * ITEM_RADIUS_PX * perPixel),
        stroke: down ? colors.unk : colors.text,
        strokeWidth: 3,
        strokeScaleEnabled: false,
        lineCap: "round",
      });
      const body = new Konva.Circle({
        ...at,
        radius: ITEM_RADIUS_PX * perPixel,
        fill: colors.surface,
        stroke: down ? colors.unk : colors.text,
        strokeWidth: 2,
        strokeScaleEnabled: false,
        dash: [3, 3],
        // Ett simulerat rumskort, utan riktigt kort, ritas också streckat.
        dashEnabled: down || (state?.cards != null && card === undefined),
      });
      group.add(
        fieldOfView,
        hitArea(at),
        ...selectionRing(at, node.id),
        pointer,
        body,
        label(at, node.mirrored ? `${node.name} (spegelvänd)` : node.name),
      );
      if (health !== null) {
        group.add(healthDot(at, health));
      }
      layer.add(group);
      const ring =
        (editing && node.id === scene.selectedItemId) ||
        (scene.mode === "move" && node.id === scene.movingId);
      if (ring) {
        layer.add(
          rotationRing(node.id, at, rotation, (angle) => {
            fieldOfView.rotation(angle - RADAR_HALF_ANGLE);
            pointer.points(pointerLine(at, angle, 2 * ITEM_RADIUS_PX * perPixel));
          }),
        );
      }
    }
    for (const lamp of room.lamps ?? []) {
      const at = toPoint(lamp.position);
      const lampState = lampStates.get(lamp.id);
      const health = lampHealth(lampState);
      const on = lampState?.on === true && health !== "bad";
      const group = movable(lamp.id, at);
      group.add(
        hitArea(at),
        ...selectionRing(at, lamp.id),
        new Konva.Circle({
          ...at,
          radius: ITEM_RADIUS_PX * perPixel,
          fill: on ? colors.accent : colors.surface,
          stroke: on ? colors.accentStrong : health === null ? colors.lampOff : colors.unk,
          strokeWidth: 2.5,
          strokeScaleEnabled: false,
          dash: [3, 3],
          // En lampa i simuleringen, eller en vars läge är okänt, ritas streckad.
          dashEnabled: health !== null || scene.live.virtualLamps.has(lamp.id),
        }),
        label(at, lamp.name, !on),
      );
      if (health === "bad") {
        group.add(healthDot(at, health));
      }
      layer.add(group);
    }
  }
  for (const router of scene.floor.signal_sources ?? []) {
    const at = toPoint(router.position);
    const group = movable(router.id, at);
    const radius = ITEM_RADIUS_PX * perPixel;
    const resting = new Konva.Group({ opacity: ROUTER_OPACITY });
    resting.add(
      new Konva.Rect({
        x: at.x - radius,
        y: at.y - radius * 0.7,
        width: 2 * radius,
        height: 1.4 * radius,
        cornerRadius: radius / 4,
        fill: colors.surface,
        stroke: colors.text3,
        strokeWidth: 1.5,
        strokeScaleEnabled: false,
      }),
      label(at, router.name, true),
    );
    group.add(hitArea(at), ...selectionRing(at, router.id), resting);
    layer.add(group);
  }

  /**
   * Ringen runt ett rumskort som vrids. Handtaget dras runt ringen och visar
   * riktningen; `preview` anropas medan det dras och kärnan får vridningen
   * när det släpps.
   */
  function rotationRing(nodeId: string, at: Point, rotation: number, preview: (angle: number) => void) {
    const radius = RING_RADIUS_PX * perPixel;
    const ring = new Konva.Group({ name: "rotation-ring" });
    ring.add(
      new Konva.Circle({
        ...at,
        radius,
        stroke: colors.text,
        strokeWidth: 1,
        strokeScaleEnabled: false,
        dash: [4, 4],
        listening: false,
      }),
    );
    let angle = rotation;
    const handle = new Konva.Circle({
      ...ahead(at, rotation, radius),
      radius: HANDLE_RADIUS_PX * perPixel,
      fill: colors.surface,
      stroke: colors.text,
      strokeWidth: 2,
      strokeScaleEnabled: false,
      // Lätt att ta tag i med fingret.
      hitStrokeWidth: 24,
      draggable: true,
      name: "rotation-handle",
      // Handtaget följer ringen vart pekaren än dras.
      dragBoundFunc(position) {
        const center = stage.getAbsoluteTransform().point(at);
        const toward = Math.atan2(position.y - center.y, position.x - center.x);
        const onScreen = RING_RADIUS_PX;
        return {
          x: center.x + onScreen * Math.cos(toward),
          y: center.y + onScreen * Math.sin(toward),
        };
      },
    });
    handle.on("mouseenter", () => (container.style.cursor = "grab"));
    handle.on("mouseleave", () => (container.style.cursor = ""));
    handle.on("dragstart", () => callbacks.onDragStart(nodeId));
    handle.on("dragmove", () => {
      angle = bearing(at, handle.position());
      preview(angle);
    });
    handle.on("dragend", () => callbacks.onRotateNode(nodeId, angle));
    ring.add(handle);
    return ring;
  }
}

/** Punkten `length` meter från `from` i riktningen `degrees` (medurs från höger). */
function ahead(from: Point, degrees: number, length: number): Point {
  const radians = (degrees * Math.PI) / 180;
  return { x: from.x + length * Math.cos(radians), y: from.y + length * Math.sin(radians) };
}

/** Linjen från rumskortet en bit i riktningen det tittar åt, som visar vart det pekar. */
function pointerLine(at: Point, degrees: number, length: number): number[] {
  const tip = ahead(at, degrees, length);
  return [at.x, at.y, tip.x, tip.y];
}
