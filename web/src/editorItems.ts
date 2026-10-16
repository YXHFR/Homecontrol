/**
 * Det som ritas ovanpå rummen i kartredigeraren: väggarnas material,
 * rumskorten med radarns synfält och vridring, routrarna och lamporna.
 * Allt är i meter, som resten av kartan (se editorView.ts).
 */
import Konva from "konva";
import { toPoint } from "./editing";
import type { FloorModel } from "./homefile";
import { bearing, RADAR_HALF_ANGLE, RADAR_RANGE } from "./placing";
import type { Point, WallKind } from "./protocol";
import type { ToolName } from "./tools";

/** Det av kartans läge som behövs för att rita sakerna. */
export interface ItemScene {
  floor: FloorModel;
  tool: ToolName;
  selectedItemId: string | null;
}

export interface ItemCallbacks {
  /** En sak har valts med verktyget Välj. */
  onSelectItem(itemId: string): void;
  /** En sak har dragits hit. */
  onMoveItem(itemId: string, point: Point): void;
  /** Ett rumskort har vridits med ringen, till så här många grader. */
  onRotateNode(nodeId: string, rotation: number): void;
  /** En vägg har klickats med väggverktyget: rummet och väggens nummer. */
  onClickWall(roomId: string, index: number): void;
}

/** Hur väggarna ser ut. En dörröppning är streckad, eftersom den inte är någon vägg. */
export const WALL_STYLES: Record<WallKind, { color: string; width: number }> = {
  gips: { color: "#8a8a8a", width: 3 },
  betong: { color: "#3a3a3a", width: 5 },
  trä: { color: "#9b6a35", width: 4 },
  glas: { color: "#4aa8d8", width: 3 },
  dörröppning: { color: "#e0a030", width: 3 },
};

const COLORS = {
  selected: "#2f6fed",
  node: "#6a3fc8",
  fieldOfView: "rgba(106, 63, 200, 0.07)",
  fieldOfViewSelected: "rgba(47, 111, 237, 0.18)",
  router: "#1f8a5b",
  lamp: "#f2b705",
  text: "#222222",
};

/** Storlekar i skärmpunkter, så att sakerna syns lika bra hur långt in man än har zoomat. */
const ITEM_RADIUS_PX = 9;
const RING_RADIUS_PX = 46;
const HANDLE_RADIUS_PX = 9;
/** Hur brett (i skärmpunkter) ett klick på en vägg får vara från linjen. */
const WALL_HIT_PX = 14;

export function drawWalls(
  layer: Konva.Layer,
  scene: ItemScene,
  callbacks: ItemCallbacks,
  container: HTMLElement,
): void {
  layer.destroyChildren();
  const active = scene.tool === "walls";
  layer.listening(active);
  for (const room of scene.floor.rooms) {
    room.walls.forEach((kind, index) => {
      const start = room.polygon[index];
      const end = room.polygon[(index + 1) % room.polygon.length];
      const style = WALL_STYLES[kind];
      const wall = new Konva.Line({
        points: [...start, ...end],
        stroke: style.color,
        strokeWidth: style.width,
        hitStrokeWidth: WALL_HIT_PX,
        strokeScaleEnabled: false,
        lineCap: "round",
        dash: kind === "dörröppning" ? [6, 5] : undefined,
        dashEnabled: kind === "dörröppning",
      });
      wall.on("pointerclick", () => callbacks.onClickWall(room.id, index));
      wall.on("mouseenter", () => (container.style.cursor = "pointer"));
      wall.on("mouseleave", () => (container.style.cursor = ""));
      layer.add(wall);
    });
  }
}

export function drawItems(
  layer: Konva.Layer,
  scene: ItemScene,
  callbacks: ItemCallbacks,
  container: HTMLElement,
  stage: Konva.Stage,
): void {
  layer.destroyChildren();
  const perPixel = 1 / stage.scaleX();
  const selecting = scene.tool === "select";
  // Med de andra verktygen ska ett klick gå igenom till kartan.
  layer.listening(selecting);

  const label = (at: Point, text: string) =>
    new Konva.Text({
      x: at.x + (ITEM_RADIUS_PX + 3) * perPixel,
      y: at.y - 7 * perPixel,
      text,
      fontSize: 12 * perPixel,
      fill: COLORS.text,
      listening: false,
    });

  /** En grupp som kan väljas och dras med verktyget Välj. */
  const movable = (itemId: string, at: Point): Konva.Group => {
    const group = new Konva.Group({ draggable: selecting, name: "item", id: itemId });
    group.on("pointerclick", () => callbacks.onSelectItem(itemId));
    group.on("mouseenter", () => (container.style.cursor = "move"));
    group.on("mouseleave", () => (container.style.cursor = ""));
    group.on("dragend", () => {
      const moved = { x: at.x + group.x(), y: at.y + group.y() };
      group.position({ x: 0, y: 0 });
      callbacks.onMoveItem(itemId, moved);
    });
    return group;
  };

  const marker = (at: Point, color: string, selected: boolean, shape: "circle" | "square") => {
    const common = {
      fill: color,
      stroke: selected ? COLORS.selected : "white",
      strokeWidth: selected ? 3 : 2,
      strokeScaleEnabled: false,
    };
    const radius = ITEM_RADIUS_PX * perPixel;
    return shape === "circle"
      ? new Konva.Circle({ ...at, radius, ...common })
      : new Konva.Rect({
          x: at.x - radius,
          y: at.y - radius,
          width: 2 * radius,
          height: 2 * radius,
          cornerRadius: radius / 3,
          ...common,
        });
  };

  for (const room of scene.floor.rooms) {
    for (const node of room.nodes ?? []) {
      const at = toPoint(node.position);
      const selected = node.id === scene.selectedItemId;
      const rotation = node.rotation ?? 0;
      const group = movable(node.id, at);
      const fieldOfView = new Konva.Wedge({
        ...at,
        radius: RADAR_RANGE,
        angle: 2 * RADAR_HALF_ANGLE,
        rotation: rotation - RADAR_HALF_ANGLE,
        fill: selected ? COLORS.fieldOfViewSelected : COLORS.fieldOfView,
        stroke: selected ? COLORS.selected : COLORS.node,
        strokeWidth: 1,
        strokeScaleEnabled: false,
        dash: [6, 4],
        listening: false,
      });
      const pointer = new Konva.Line({
        points: pointerLine(at, rotation, 2.2 * ITEM_RADIUS_PX * perPixel),
        stroke: COLORS.node,
        strokeWidth: 3,
        strokeScaleEnabled: false,
        lineCap: "round",
      });
      group.add(
        fieldOfView,
        pointer,
        marker(at, COLORS.node, selected, "circle"),
        label(at, node.mirrored ? `${node.name} (spegelvänd)` : node.name),
      );
      layer.add(group);
      if (selected && selecting) {
        layer.add(
          rotationRing(at, rotation, (angle) => {
            fieldOfView.rotation(angle - RADAR_HALF_ANGLE);
            pointer.points(pointerLine(at, angle, 2.2 * ITEM_RADIUS_PX * perPixel));
          }),
        );
      }
    }
    for (const lamp of room.lamps ?? []) {
      const at = toPoint(lamp.position);
      const group = movable(lamp.id, at);
      group.add(
        marker(at, COLORS.lamp, lamp.id === scene.selectedItemId, "circle"),
        label(at, lamp.name),
      );
      layer.add(group);
    }
  }
  for (const router of scene.floor.signal_sources ?? []) {
    const at = toPoint(router.position);
    const group = movable(router.id, at);
    group.add(
      marker(at, COLORS.router, router.id === scene.selectedItemId, "square"),
      label(at, router.name),
    );
    layer.add(group);
  }

  /**
   * Ringen runt ett valt rumskort. Handtaget dras runt ringen och visar
   * riktningen; `preview` anropas medan det dras och kärnan får vridningen
   * när det släpps.
   */
  function rotationRing(at: Point, rotation: number, preview: (angle: number) => void) {
    const radius = RING_RADIUS_PX * perPixel;
    const nodeId = scene.selectedItemId ?? "";
    const ring = new Konva.Group({ name: "rotation-ring" });
    ring.add(
      new Konva.Circle({
        ...at,
        radius,
        stroke: COLORS.selected,
        strokeWidth: 2,
        strokeScaleEnabled: false,
        dash: [4, 4],
        listening: false,
      }),
    );
    let angle = rotation;
    const handle = new Konva.Circle({
      ...ahead(at, rotation, radius),
      radius: HANDLE_RADIUS_PX * perPixel,
      fill: "white",
      stroke: COLORS.selected,
      strokeWidth: 3,
      strokeScaleEnabled: false,
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
