/**
 * Kartan i kartredigeraren, ritad med Konva. Den ritar det den får och säger
 * till när någon trycker, men vet inget om hemmet i övrigt eller om sparande.
 * Allt på kartan är i meter: scenens skala är bildpunkter per meter.
 */
import Konva from "konva";
import { toPoint } from "./editing";
import type { FloorModel, PlanImage, RoomModel } from "./homefile";
import type { Point } from "./protocol";
import type { Draft, ToolName } from "./tools";
import { ZONE_STYLES } from "./zoneStyles";

/** Det som är valt med verktyget Välj, eller null. */
export type Selection = { kind: "room" | "zone"; id: string } | null;

/** Det kartredigeraren vill visa på kartan. */
export interface EditorScene {
  floor: FloorModel;
  /** Planritningen och adressen till bilden, eller null om våningen saknar bild. */
  planImage: { url: string; plan: PlanImage } | null;
  selection: Selection;
  tool: ToolName;
  /** Färgen på det som håller på att ritas med fri form, eller null för den vanliga. */
  draftColor: string | null;
  draft: Draft;
}

export interface EditorViewCallbacks {
  /** Tryck, rörelse och släpp på kartan, i hemmets meter. */
  onPointerDown(point: Point): void;
  onPointerMove(point: Point): void;
  onPointerUp(point: Point): void;
  /** Ett rum, en zon eller inget (vid klick bredvid) har valts med verktyget Välj. */
  onSelect(selection: Selection): void;
  /** Det valda rummet har dragits så här många meter. */
  onMoveRoom(roomId: string, dx: number, dy: number): void;
}

/** Det kartredigeraren kan be kartan om. */
export interface EditorView {
  render(scene: EditorScene): void;
  /** Hur många meter en skärmpunkt motsvarar just nu, för att veta vad som är "nära". */
  metersPerPixel(): number;
  zoom(factor: number): void;
  /** Visar hela våningen och bilden. */
  fit(): void;
  destroy(): void;
}

export type EditorViewFactory = (
  container: HTMLDivElement,
  callbacks: EditorViewCallbacks,
) => EditorView;

/** Ytan som visas när våningen är tom, i meter. */
const EMPTY_FLOOR = { width: 12, height: 8 };
const MARGIN_METERS = 1;
const MIN_ZOOM = 2; // pixlar per meter
const MAX_ZOOM = 400;
const PLAN_IMAGE_OPACITY = 0.55;
const HANDLE_RADIUS_PX = 6;
/** Genomskinligheten på en zons yta (som tvåsiffrigt hextal efter färgen), och på en vald zons. */
const ZONE_ALPHA = "38";
const ZONE_SELECTED_ALPHA = "66";

const COLORS = {
  /** Utanför planet, där inget kan ritas. */
  background: "#e9e7e2",
  paper: "#fbfaf7",
  grid: "#ebe7de",
  room: "rgba(244, 241, 234, 0.8)",
  roomSelected: "rgba(196, 218, 250, 0.85)",
  wall: "#4a4a4a",
  selected: "#2f6fed",
  draft: "#2f6fed",
  scale: "#d64545",
  text: "#222222",
};

interface Bounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

export const createKonvaEditorView: EditorViewFactory = (container, callbacks) => {
  const stage = new Konva.Stage({ container, width: 1, height: 1 });
  const gridLayer = new Konva.Layer({ listening: false });
  const imageLayer = new Konva.Layer({ listening: false });
  const roomLayer = new Konva.Layer();
  const draftLayer = new Konva.Layer({ listening: false });
  stage.add(gridLayer, imageLayer, roomLayer, draftLayer);

  const images = new Map<string, HTMLImageElement>();
  let scene: EditorScene | null = null;
  let fittedFor: string | null = null;

  const pixelsPerMeter = () => stage.scaleX();
  const pointer = (): Point | null => stage.getRelativePointerPosition();

  const imageSize = (current: EditorScene): { width: number; height: number } | null => {
    const image = current.planImage === null ? undefined : images.get(current.planImage.url);
    if (
      current.planImage === null ||
      image === undefined ||
      !image.complete ||
      image.naturalWidth === 0
    ) {
      return null;
    }
    const metersPerPixel = current.planImage.plan.meters_per_pixel;
    return {
      width: image.naturalWidth * metersPerPixel,
      height: image.naturalHeight * metersPerPixel,
    };
  };

  const bounds = (current: EditorScene): Bounds => {
    const points = current.floor.rooms.flatMap((room) => room.polygon.map(toPoint));
    const size = imageSize(current);
    if (current.planImage !== null && size !== null) {
      const [x, y] = current.planImage.plan.position;
      points.push({ x, y }, { x: x + size.width, y: y + size.height });
    }
    if (points.length === 0) {
      return { minX: 0, minY: 0, maxX: EMPTY_FLOOR.width, maxY: EMPTY_FLOOR.height };
    }
    return {
      minX: Math.min(...points.map((p) => p.x)),
      minY: Math.min(...points.map((p) => p.y)),
      maxX: Math.max(...points.map((p) => p.x)),
      maxY: Math.max(...points.map((p) => p.y)),
    };
  };

  const resize = () => {
    stage.size({ width: container.clientWidth || 600, height: container.clientHeight || 400 });
  };

  const fit = () => {
    if (scene === null) {
      return;
    }
    resize();
    const b = bounds(scene);
    const width = b.maxX - b.minX + 2 * MARGIN_METERS;
    const height = b.maxY - b.minY + 2 * MARGIN_METERS;
    const perMeter = Math.min(stage.width() / width, stage.height() / height);
    stage.scale({ x: perMeter, y: perMeter });
    // Centrera våningen på kartan.
    stage.position({
      x: (stage.width() - (b.maxX + b.minX) * perMeter) / 2,
      y: (stage.height() - (b.maxY + b.minY) * perMeter) / 2,
    });
  };

  /** Ett rutnät med en meter mellan linjerna, där det går att rita (x och y från 0). */
  const drawGrid = () => {
    gridLayer.destroyChildren();
    const perMeter = pixelsPerMeter();
    const left = Math.max(0, Math.floor(-stage.x() / perMeter));
    const top = Math.max(0, Math.floor(-stage.y() / perMeter));
    const right = Math.ceil((stage.width() - stage.x()) / perMeter);
    const bottom = Math.ceil((stage.height() - stage.y()) / perMeter);
    if (right <= left || bottom <= top) {
      return;
    }
    gridLayer.add(
      new Konva.Rect({
        x: left,
        y: top,
        width: right - left,
        height: bottom - top,
        fill: COLORS.paper,
      }),
    );
    // Vid stark utzoomning blir varje meter för tät; visa då var tionde.
    const step = perMeter < 8 ? 10 : 1;
    const line = (points: number[]) =>
      gridLayer.add(
        new Konva.Line({ points, stroke: COLORS.grid, strokeWidth: 1, strokeScaleEnabled: false }),
      );
    for (let x = Math.ceil(left / step) * step; x <= right; x += step) {
      line([x, top, x, bottom]);
    }
    for (let y = Math.ceil(top / step) * step; y <= bottom; y += step) {
      line([left, y, right, y]);
    }
  };

  const drawImage = (current: EditorScene) => {
    imageLayer.destroyChildren();
    if (current.planImage === null) {
      return;
    }
    const { url, plan } = current.planImage;
    let image = images.get(url);
    if (image === undefined) {
      image = new Image();
      image.addEventListener("load", () => {
        fittedFor = null; // bilden har fått en storlek; visa hela den
        draw();
      });
      image.src = url;
      images.set(url, image);
    }
    const size = imageSize(current);
    if (size === null) {
      return;
    }
    imageLayer.add(
      new Konva.Image({
        image,
        x: plan.position[0],
        y: plan.position[1],
        width: size.width,
        height: size.height,
        opacity: PLAN_IMAGE_OPACITY,
      }),
    );
  };

  const drawRooms = (current: EditorScene) => {
    roomLayer.destroyChildren();
    const metersPerScreenPixel = 1 / pixelsPerMeter();
    for (const room of current.floor.rooms) {
      const selected = current.selection?.kind === "room" && current.selection.id === room.id;
      const group = new Konva.Group({ draggable: selected && current.tool === "select" });
      group.add(
        new Konva.Line({
          points: room.polygon.flat(),
          closed: true,
          fill: selected ? COLORS.roomSelected : COLORS.room,
          stroke: selected ? COLORS.selected : COLORS.wall,
          strokeWidth: selected ? 4 : 3,
          strokeScaleEnabled: false,
          lineJoin: "round",
          // Ett rum som ritas över ett annat ska gå att börja inne i det.
          listening: current.tool === "select",
        }),
        ...drawZones(room, current, metersPerScreenPixel),
        new Konva.Text({
          x: Math.min(...room.polygon.map(([x]) => x)) + 8 * metersPerScreenPixel,
          y: Math.min(...room.polygon.map(([, y]) => y)) + 8 * metersPerScreenPixel,
          text: room.name,
          fontSize: 15 * metersPerScreenPixel,
          fill: COLORS.text,
          listening: false,
        }),
      );
      group.on("pointerclick", () => {
        if (current.tool === "select") {
          callbacks.onSelect({ kind: "room", id: room.id });
        }
      });
      group.on("mouseenter", () => {
        if (current.tool === "select") {
          container.style.cursor = selected ? "move" : "pointer";
        }
      });
      group.on("mouseleave", () => (container.style.cursor = ""));
      group.on("dragend", () => {
        const [dx, dy] = [group.x(), group.y()];
        group.position({ x: 0, y: 0 });
        callbacks.onMoveRoom(room.id, dx, dy);
      });
      roomLayer.add(group);
    }
  };

  /** Rummets zoner i sina färger. De ritas i rummets grupp, så att de följer med när det dras. */
  const drawZones = (
    room: RoomModel,
    current: EditorScene,
    metersPerScreenPixel: number,
  ): Konva.Shape[] =>
    (room.zones ?? []).flatMap((zone) => {
      const selected = current.selection?.kind === "zone" && current.selection.id === zone.id;
      const color = ZONE_STYLES[zone.kind].color;
      const xs = zone.polygon.map(([x]) => x);
      const ys = zone.polygon.map(([, y]) => y);
      const fontSize = 12 * metersPerScreenPixel;
      const area = new Konva.Line({
        points: zone.polygon.flat(),
        closed: true,
        fill: color + (selected ? ZONE_SELECTED_ALPHA : ZONE_ALPHA),
        stroke: selected ? COLORS.selected : color,
        strokeWidth: selected ? 3 : 2,
        strokeScaleEnabled: false,
        dash: selected ? undefined : [6 * metersPerScreenPixel, 4 * metersPerScreenPixel],
        listening: current.tool === "select",
      });
      area.on("pointerclick", (event) => {
        if (current.tool === "select") {
          event.cancelBubble = true; // annars väljs rummet under zonen
          // Ett klick till på en vald zon väljer rummet, som annars kan vara helt täckt av zoner.
          callbacks.onSelect(
            selected ? { kind: "room", id: room.id } : { kind: "zone", id: zone.id },
          );
        }
      });
      area.on("mouseenter", () => {
        if (current.tool === "select") {
          container.style.cursor = "pointer";
        }
      });
      const label = new Konva.Text({
        x: Math.min(...xs),
        y: (Math.min(...ys) + Math.max(...ys)) / 2 - fontSize / 2,
        width: Math.max(...xs) - Math.min(...xs),
        align: "center",
        text: zone.name,
        wrap: "none",
        ellipsis: true,
        fontSize,
        fill: color,
        fontStyle: "bold",
        listening: false,
      });
      return [area, label];
    });

  const drawDraft = (current: EditorScene) => {
    draftLayer.destroyChildren();
    const metersPerScreenPixel = 1 / pixelsPerMeter();
    const draft = current.draft;
    const dot = (p: Point, radius: number, color: string) =>
      new Konva.Circle({
        ...p,
        radius: radius * metersPerScreenPixel,
        fill: "white",
        stroke: color,
        strokeWidth: 2,
        strokeScaleEnabled: false,
      });
    const line = (points: Point[], color: string, closed = false, dash?: number[]) =>
      new Konva.Line({
        points: points.flatMap((p) => [p.x, p.y]),
        stroke: color,
        strokeWidth: 2,
        strokeScaleEnabled: false,
        closed,
        dash: dash?.map((d) => d * metersPerScreenPixel),
      });
    switch (draft.kind) {
      case "polygon": {
        const color = current.draftColor ?? COLORS.draft;
        const path = draft.pointer === null ? draft.points : [...draft.points, draft.pointer];
        draftLayer.add(line(path, color));
        draft.points.forEach((p, i) =>
          // Det första hörnet är större: där stängs rummet eller zonen.
          draftLayer.add(
            dot(
              p,
              i === 0 && draft.points.length >= 3 ? 2 * HANDLE_RADIUS_PX : HANDLE_RADIUS_PX,
              color,
            ),
          ),
        );
        break;
      }
      case "rectangle":
        draftLayer.add(line(draft.corners, COLORS.draft, true, [8, 6]));
        break;
      case "scale": {
        const path = draft.pointer === null ? draft.points : [...draft.points, draft.pointer];
        draftLayer.add(line(path, COLORS.scale));
        draft.points.forEach((p) => draftLayer.add(dot(p, HANDLE_RADIUS_PX, COLORS.scale)));
        break;
      }
      case "none":
        break;
    }
  };

  const draw = () => {
    if (scene === null) {
      return;
    }
    const key = `${scene.floor.id} ${scene.planImage?.url ?? ""}`;
    if (fittedFor !== key) {
      fittedFor = key;
      fit();
    }
    stage.draggable(scene.tool === "select");
    container.style.background = COLORS.background;
    drawGrid();
    drawImage(scene);
    drawRooms(scene);
    drawDraft(scene);
  };

  const forward = (handler: (point: Point) => void) => () => {
    const point = pointer();
    if (point !== null) {
      handler(point);
    }
  };
  stage.on("pointerdown", forward(callbacks.onPointerDown));
  stage.on("pointermove", forward(callbacks.onPointerMove));
  stage.on("pointerup", forward(callbacks.onPointerUp));
  stage.on("pointerclick", (event) => {
    if (event.target === stage && scene?.tool === "select") {
      callbacks.onSelect(null);
    }
  });

  const zoomAround = (factor: number, center: Point) => {
    const old = pixelsPerMeter();
    const next = Math.min(Math.max(old * factor, MIN_ZOOM), MAX_ZOOM);
    const pointInMeters = { x: (center.x - stage.x()) / old, y: (center.y - stage.y()) / old };
    stage.scale({ x: next, y: next });
    stage.position({ x: center.x - pointInMeters.x * next, y: center.y - pointInMeters.y * next });
    draw();
  };
  stage.on("dragmove", (event) => {
    if (event.target === stage) {
      drawGrid();
    }
  });
  stage.on("wheel", (event) => {
    event.evt.preventDefault();
    const at = stage.getPointerPosition() ?? { x: stage.width() / 2, y: stage.height() / 2 };
    zoomAround(event.evt.deltaY < 0 ? 1.15 : 1 / 1.15, at);
  });

  const onResize = () => {
    resize();
    draw();
  };
  window.addEventListener("resize", onResize);

  return {
    render(next) {
      scene = next;
      draw();
    },
    metersPerPixel: () => 1 / pixelsPerMeter(),
    zoom: (factor) => zoomAround(factor, { x: stage.width() / 2, y: stage.height() / 2 }),
    fit() {
      fit();
      draw();
    },
    destroy() {
      window.removeEventListener("resize", onResize);
      stage.destroy();
    },
  };
};
