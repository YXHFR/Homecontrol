import Konva from "konva";
import { toPoint } from "./editing";
import type { FloorModel, PlanImage } from "./homefile";
import type { Point } from "./protocol";
import type { Draft, ToolName } from "./tools";

/** Det kartredigeraren vill visa på kartan. */
export interface EditorScene {
  floor: FloorModel;
  /** Planritningen och adressen till bilden, eller null om våningen saknar bild. */
  planImage: { url: string; plan: PlanImage } | null;
  selectedRoomId: string | null;
  tool: ToolName;
  draft: Draft;
}

export interface EditorViewCallbacks {
  /** Tryck, rörelse och släpp på kartan, i hemmets meter. */
  onPointerDown(point: Point): void;
  onPointerMove(point: Point): void;
  onPointerUp(point: Point): void;
  /** Ett rum (eller inget, vid klick bredvid) har valts med verktyget Välj. */
  onSelectRoom(roomId: string | null): void;
  /** Det valda rummet har dragits så här många meter. */
  onMoveRoom(roomId: string, dx: number, dy: number): void;
}

/** Kartan i kartredigeraren. Den ritar det den får och säger till när någon trycker. */
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

  const scale = () => stage.scaleX();
  const pointer = (): Point | null => stage.getRelativePointerPosition();

  const imageSize = (s: EditorScene): { width: number; height: number } | null => {
    const image = s.planImage === null ? undefined : images.get(s.planImage.url);
    if (
      s.planImage === null ||
      image === undefined ||
      !image.complete ||
      image.naturalWidth === 0
    ) {
      return null;
    }
    const mpp = s.planImage.plan.meters_per_pixel;
    return { width: image.naturalWidth * mpp, height: image.naturalHeight * mpp };
  };

  const bounds = (s: EditorScene): Bounds => {
    const points = s.floor.rooms.flatMap((room) => room.polygon.map(toPoint));
    const size = imageSize(s);
    if (s.planImage !== null && size !== null) {
      const [x, y] = s.planImage.plan.position;
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
    const s = Math.min(stage.width() / width, stage.height() / height);
    stage.scale({ x: s, y: s });
    // Centrera våningen på kartan.
    stage.position({
      x: (stage.width() - (b.maxX + b.minX) * s) / 2,
      y: (stage.height() - (b.maxY + b.minY) * s) / 2,
    });
  };

  /** Ett rutnät med en meter mellan linjerna, där det går att rita (x och y från 0). */
  const drawGrid = () => {
    gridLayer.destroyChildren();
    const s = scale();
    const left = Math.max(0, Math.floor(-stage.x() / s));
    const top = Math.max(0, Math.floor(-stage.y() / s));
    const right = Math.ceil((stage.width() - stage.x()) / s);
    const bottom = Math.ceil((stage.height() - stage.y()) / s);
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
    const step = s < 8 ? 10 : 1;
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

  const drawImage = (s: EditorScene) => {
    imageLayer.destroyChildren();
    if (s.planImage === null) {
      return;
    }
    const { url, plan } = s.planImage;
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
    const size = imageSize(s);
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

  const drawRooms = (s: EditorScene) => {
    roomLayer.destroyChildren();
    const px = 1 / scale();
    for (const room of s.floor.rooms) {
      const selected = room.id === s.selectedRoomId;
      const group = new Konva.Group({ draggable: selected && s.tool === "select" });
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
          listening: s.tool === "select",
        }),
        new Konva.Text({
          x: Math.min(...room.polygon.map(([x]) => x)) + 8 * px,
          y: Math.min(...room.polygon.map(([, y]) => y)) + 8 * px,
          text: room.name,
          fontSize: 15 * px,
          fill: COLORS.text,
          listening: false,
        }),
      );
      group.on("pointerclick", () => {
        if (s.tool === "select") {
          callbacks.onSelectRoom(room.id);
        }
      });
      group.on("mouseenter", () => {
        if (s.tool === "select") {
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

  const drawDraft = (s: EditorScene) => {
    draftLayer.destroyChildren();
    const px = 1 / scale();
    const draft = s.draft;
    const dot = (p: Point, radius: number, color: string) =>
      new Konva.Circle({
        ...p,
        radius: radius * px,
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
        dash: dash?.map((d) => d * px),
      });
    switch (draft.kind) {
      case "polygon": {
        const path = draft.pointer === null ? draft.points : [...draft.points, draft.pointer];
        draftLayer.add(line(path, COLORS.draft));
        draft.points.forEach((p, i) =>
          // Det första hörnet är större: där stängs rummet.
          draftLayer.add(
            dot(
              p,
              i === 0 && draft.points.length >= 3 ? 2 * HANDLE_RADIUS_PX : HANDLE_RADIUS_PX,
              COLORS.draft,
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
      callbacks.onSelectRoom(null);
    }
  });

  const zoomAround = (factor: number, center: Point) => {
    const old = scale();
    const next = Math.min(Math.max(old * factor, MIN_ZOOM), MAX_ZOOM);
    const home = { x: (center.x - stage.x()) / old, y: (center.y - stage.y()) / old };
    stage.scale({ x: next, y: next });
    stage.position({ x: center.x - home.x * next, y: center.y - home.y * next });
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
    metersPerPixel: () => 1 / scale(),
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
