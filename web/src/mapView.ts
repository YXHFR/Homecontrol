/**
 * Kartan, ritad med Konva. Den ritar det den får och säger till när någon
 * trycker eller drar, men vet inget om hemmet i övrigt eller om sparande.
 * Allt på kartan är i meter: scenens skala är bildpunkter per meter.
 *
 * Kartan har lager för rummen, möblerna, ljuset från tända lampor, väggarna,
 * sakerna (rumskort, lampor, routrar), spåren och låtsaspersonerna, och det
 * som håller på att ritas. Läget från kärnan kommer upp till tio gånger i
 * sekunden. Då ritas bara spåren och personerna om, och resten bara när det
 * har ändrats, så att ett tryck på en sak inte tappas för att saken byttes ut
 * mitt i trycket. Medan något dras ritas inget om under det.
 */
import Konva from "konva";
import { toPoint } from "./editing";
import type { FloorModel, PlanImage, RoomModel } from "./homefile";
import {
  drawItems,
  drawLight,
  drawWalls,
  type ItemCallbacks,
  type LiveScene,
  type MapMode,
} from "./mapItems";
import type { Person, Point } from "./protocol";
import { mapColors, onColorSchemeChange, type MapColors } from "./theme";
import type { Draft, ToolName } from "./tools";
import { trackStyle, tracksOnFloor } from "./trackStyles";
import { ZONE_STYLES } from "./zoneStyles";

/**
 * Det som är valt, eller null. "item" är ett rumskort, en router eller en
 * lampa (se mapItems.ts).
 */
export type Selection = { kind: "room" | "zone" | "item"; id: string } | null;

export type { LiveScene, MapMode } from "./mapItems";

/** Det sidan vill visa på kartan. */
export interface MapScene {
  floor: FloorModel;
  /** Planritningen och adressen till bilden, eller null om våningen saknar bild. */
  planImage: { url: string; plan: PlanImage } | null;
  selection: Selection;
  mode: MapMode;
  /** Saken som flyttas i läget Flytta. */
  movingId: string | null;
  /** Ritverktyget i Ändra rummet. Utanför Ändra rummet är det alltid "select". */
  tool: ToolName;
  /** Färgen på det som håller på att ritas med fri form, eller null för den vanliga. */
  draftColor: string | null;
  draft: Draft;
  live: LiveScene;
}

export interface MapCallbacks extends ItemCallbacks {
  /** Tryck, rörelse och släpp på kartan, i hemmets meter. */
  onPointerDown(point: Point): void;
  onPointerMove(point: Point): void;
  onPointerUp(point: Point): void;
  /**
   * Ett rum, en zon eller inget (vid tryck bredvid) har valts. Rumskort,
   * routrar och lampor väljs med onSelectItem.
   */
  onSelect(selection: Selection): void;
  /** Det valda rummet har dragits så här många meter. */
  onMoveRoom(roomId: string, dx: number, dy: number): void;
  /** En låtsasperson dras hit. `done` när den har släppts. */
  onMovePerson(personId: string, point: Point, done: boolean): void;
}

/** Det sidan kan be kartan om. */
export interface MapView {
  render(scene: MapScene): void;
  /** Hur många meter en skärmpunkt motsvarar just nu, för att veta vad som är "nära". */
  metersPerPixel(): number;
  zoom(factor: number): void;
  /** Visar hela våningen och bilden. */
  fit(): void;
  /** Flyttar kartan så att punkten hamnar i mitten. */
  centerOn(point: Point): void;
  destroy(): void;
}

export type MapViewFactory = (container: HTMLDivElement, callbacks: MapCallbacks) => MapView;

/** Ytan som visas när våningen är tom, i meter. */
const EMPTY_FLOOR = { width: 12, height: 8 };
/** Luft runt våningen, så att låtsaspersoner som väntar bredvid rummen syns (se parkingSpot). */
const MARGIN_METERS = 1.5;
const MIN_ZOOM = 2; // pixlar per meter
const MAX_ZOOM = 400;
const PLAN_IMAGE_OPACITY = 0.5;
const HANDLE_RADIUS_PX = 6;
/** Genomskinligheten på en zons yta (som tvåsiffrigt hextal efter färgen), och på en vald zons. */
const ZONE_ALPHA = "24";
const ZONE_SELECTED_ALPHA = "55";
const TRACK_RADIUS_PX = 5;
const PERSON_RADIUS_PX = 13;
/** Extra yta runt personen som går att ta tag i, så att den är lätt att dra med fingret. */
const PERSON_HIT_PX = 24;
/** Under dragning skickas personens position högst så här ofta. */
const SEND_INTERVAL_MS = 50;
/**
 * Så länge efter en dragning litar kartan på sin egen position. Svar på
 * äldre positioner kan annars hinna fram och få personen att hoppa tillbaka.
 */
const SETTLE_MS = 300;

interface Bounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

/** En låtsasperson på kartan och vad kartan vet om dragningen av den. */
interface PersonMarker {
  group: Konva.Group;
  dragging: boolean;
  lastSent: number;
  settledAt: number;
}

export const createKonvaMapView: MapViewFactory = (container, callbacks) => {
  // Två fingrar som zoomar ska inte samtidigt dra i det som är under dem.
  Konva.hitOnDragEnabled = true;
  const stage = new Konva.Stage({ container, width: 1, height: 1 });
  const gridLayer = new Konva.Layer({ listening: false });
  const imageLayer = new Konva.Layer({ listening: false });
  const roomLayer = new Konva.Layer();
  const furnitureLayer = new Konva.Layer({ listening: false });
  const lightLayer = new Konva.Layer({ listening: false });
  const wallLayer = new Konva.Layer();
  const itemLayer = new Konva.Layer();
  const trackLayer = new Konva.Layer({ listening: false });
  const personLayer = new Konva.Layer();
  const draftLayer = new Konva.Layer({ listening: false });
  stage.add(
    gridLayer,
    imageLayer,
    roomLayer,
    furnitureLayer,
    lightLayer,
    wallLayer,
    itemLayer,
    trackLayer,
    personLayer,
    draftLayer,
  );

  const images = new Map<string, HTMLImageElement>();
  let scene: MapScene | null = null;
  let fittedFor: string | null = null;
  let colors: MapColors = mapColors();
  /** Det som rummen och sakerna senast ritades för; samma igen ritas inte om. */
  let drawnRooms = "";
  let drawnItems = "";
  /** Något på kartan dras just nu (inte kartan själv). */
  let dragging = false;
  const markers = new Map<string, PersonMarker>();
  let markerScale = 0;
  /** Någon har panorerat eller zoomat sedan kartan senast visade hela våningen. */
  let moved = false;

  const pixelsPerMeter = () => stage.scaleX();
  const pointer = (): Point | null => stage.getRelativePointerPosition();

  const imageSize = (current: MapScene): { width: number; height: number } | null => {
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

  const bounds = (current: MapScene): Bounds => {
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
    moved = false;
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
        fill: colors.planBg,
      }),
    );
    // Vid stark utzoomning blir varje meter för tät; visa då var tionde.
    const step = perMeter < 8 ? 10 : 1;
    const line = (points: number[]) =>
      gridLayer.add(
        new Konva.Line({ points, stroke: colors.planGrid, strokeWidth: 1, strokeScaleEnabled: false }),
      );
    for (let x = Math.ceil(left / step) * step; x <= right; x += step) {
      line([x, top, x, bottom]);
    }
    for (let y = Math.ceil(top / step) * step; y <= bottom; y += step) {
      line([left, y, right, y]);
    }
  };

  const drawImage = (current: MapScene) => {
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
        drawnRooms = "";
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

  /** Om rummen och zonerna går att trycka på (och det valda rummet att dra). */
  const roomsListen = (current: MapScene) =>
    current.mode === "view" || (current.mode === "edit" && current.tool === "select");

  const drawRooms = (current: MapScene) => {
    roomLayer.destroyChildren();
    const metersPerScreenPixel = 1 / pixelsPerMeter();
    const occupancy = new Map(current.live.state?.rooms.map((room) => [room.id, room.status]));
    const editing = current.mode === "edit" && current.tool === "select";
    roomLayer.listening(roomsListen(current));
    for (const room of current.floor.rooms) {
      const selected = current.selection?.kind === "room" && current.selection.id === room.id;
      const group = new Konva.Group({ draggable: selected && editing });
      group.setAttr("roomId", room.id);
      const status = occupancy.get(room.id);
      group.add(
        new Konva.Line({
          points: room.polygon.flat(),
          closed: true,
          fill: selected
            ? colors.roomSelected
            : status === "upptaget"
              ? colors.roomOccupied
              : colors.roomFill,
          stroke: selected ? colors.text : colors.lineStrong,
          strokeWidth: selected ? 2 : 1,
          strokeScaleEnabled: false,
          lineJoin: "round",
        }),
        ...drawZones(room, current, metersPerScreenPixel),
        new Konva.Text({
          x: Math.min(...room.polygon.map(([x]) => x)) + 10 * metersPerScreenPixel,
          y: Math.min(...room.polygon.map(([, y]) => y)) + 10 * metersPerScreenPixel,
          text: status === "okänt" ? `${room.name.toUpperCase()} · OKÄNT LÄGE` : room.name.toUpperCase(),
          fontSize: 11 * metersPerScreenPixel,
          fontFamily: "ui-monospace, Menlo, Consolas, monospace",
          letterSpacing: 0.8 * metersPerScreenPixel,
          fill: colors.text2,
          listening: false,
        }),
      );
      group.on("pointerclick", () => callbacks.onSelect({ kind: "room", id: room.id }));
      group.on("mouseenter", () => {
        container.style.cursor = selected && editing ? "move" : "pointer";
      });
      group.on("mouseleave", () => (container.style.cursor = ""));
      group.on("dragstart", () => {
        dragging = true;
      });
      group.on("dragend", () => {
        const [dx, dy] = [group.x(), group.y()];
        group.position({ x: 0, y: 0 });
        callbacks.onMoveRoom(room.id, dx, dy);
      });
      roomLayer.add(group);
    }
  };

  /** Möblerna, gråa och utan namn som tar plats: de är bara till hjälp och går inte att välja. */
  const drawFurniture = (current: MapScene) => {
    furnitureLayer.destroyChildren();
    const metersPerScreenPixel = 1 / pixelsPerMeter();
    for (const item of current.floor.furniture ?? []) {
      const xs = item.polygon.map(([x]) => x);
      const ys = item.polygon.map(([, y]) => y);
      const fontSize = 10 * metersPerScreenPixel;
      furnitureLayer.add(
        new Konva.Line({
          points: item.polygon.flat(),
          closed: true,
          fill: colors.furn,
          stroke: colors.furnEdge,
          strokeWidth: 1,
          strokeScaleEnabled: false,
        }),
        new Konva.Text({
          x: Math.min(...xs),
          y: (Math.min(...ys) + Math.max(...ys)) / 2 - fontSize / 2,
          width: Math.max(...xs) - Math.min(...xs),
          align: "center",
          text: item.name.toUpperCase(),
          wrap: "none",
          ellipsis: true,
          fontSize,
          fontFamily: "ui-monospace, Menlo, Consolas, monospace",
          fill: colors.text3,
        }),
      );
    }
  };

  /** Rummets zoner i sina färger. De ritas i rummets grupp, så att de följer med när det dras. */
  const drawZones = (
    room: RoomModel,
    current: MapScene,
    metersPerScreenPixel: number,
  ): Konva.Shape[] => {
    const occupied = new Set(
      current.live.state?.zones.filter((zone) => zone.status === "upptaget").map((zone) => zone.id),
    );
    return (room.zones ?? []).flatMap((zone) => {
      const selected = current.selection?.kind === "zone" && current.selection.id === zone.id;
      const color = ZONE_STYLES[zone.kind].color;
      const xs = zone.polygon.map(([x]) => x);
      const ys = zone.polygon.map(([, y]) => y);
      const fontSize = 11 * metersPerScreenPixel;
      const area = new Konva.Line({
        points: zone.polygon.flat(),
        closed: true,
        fill: color + (selected || occupied.has(zone.id) ? ZONE_SELECTED_ALPHA : ZONE_ALPHA),
        stroke: selected ? colors.text : color,
        strokeWidth: selected ? 2.5 : 1.5,
        strokeScaleEnabled: false,
        dash: selected ? undefined : [6, 4],
      });
      area.on("pointerclick", (event) => {
        if (current.mode === "edit") {
          event.cancelBubble = true; // annars väljs rummet under zonen
          // Ett klick till på en vald zon väljer rummet, som annars kan vara helt täckt av zoner.
          callbacks.onSelect(
            selected ? { kind: "room", id: room.id } : { kind: "zone", id: zone.id },
          );
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
        fontFamily: "ui-monospace, Menlo, Consolas, monospace",
        fill: color,
        fontStyle: "bold",
        listening: false,
      });
      return [area, label];
    });
  };

  const drawDraft = (current: MapScene) => {
    draftLayer.destroyChildren();
    const metersPerScreenPixel = 1 / pixelsPerMeter();
    const draft = current.draft;
    const dot = (p: Point, radius: number, color: string) =>
      new Konva.Circle({
        ...p,
        radius: radius * metersPerScreenPixel,
        fill: colors.surface,
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
        dash,
      });
    switch (draft.kind) {
      case "polygon": {
        const color = current.draftColor ?? colors.draft;
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
        draftLayer.add(line(draft.corners, colors.draft, true, [8, 6]));
        break;
      case "scale": {
        const path = draft.pointer === null ? draft.points : [...draft.points, draft.pointer];
        draftLayer.add(line(path, colors.accentStrong));
        draft.points.forEach((p) => draftLayer.add(dot(p, HANDLE_RADIUS_PX, colors.accentStrong)));
        break;
      }
      case "none":
        break;
    }
  };

  /** Kärnans spår: en prick för den radarn ser, en streckad ring för den som hålls kvar. */
  const drawTracks = (current: MapScene) => {
    trackLayer.destroyChildren();
    const perPixel = 1 / pixelsPerMeter();
    for (const track of tracksOnFloor(current.live.state, current.floor.id)) {
      trackLayer.add(
        new Konva.Circle({
          x: track.x,
          y: track.y,
          radius: TRACK_RADIUS_PX * perPixel,
          ...trackStyle(track, colors.person),
          strokeScaleEnabled: false,
        }),
      );
    }
  };

  const createMarker = (person: Person): PersonMarker => {
    const perPixel = 1 / pixelsPerMeter();
    const group = new Konva.Group({ draggable: true });
    group.add(
      new Konva.Circle({
        radius: PERSON_RADIUS_PX * perPixel,
        fill: colors.planBg,
        stroke: colors.person,
        strokeWidth: 2,
        dash: [3, 2.5],
        strokeScaleEnabled: false,
        hitStrokeWidth: PERSON_HIT_PX,
      }),
      new Konva.Text({
        x: -PERSON_RADIUS_PX * perPixel,
        y: -5 * perPixel,
        width: 2 * PERSON_RADIUS_PX * perPixel,
        align: "center",
        text: person.id,
        fontSize: 10 * perPixel,
        fontFamily: "ui-monospace, Menlo, Consolas, monospace",
        fill: colors.text,
        listening: false,
      }),
    );
    group.setAttr("personId", person.id);
    const marker: PersonMarker = { group, dragging: false, lastSent: 0, settledAt: 0 };
    const send = (done: boolean) => {
      const now = performance.now();
      if (!done && now - marker.lastSent < SEND_INTERVAL_MS) {
        return;
      }
      marker.lastSent = now;
      callbacks.onMovePerson(person.id, group.position(), done);
    };
    group.on("dragstart", () => {
      marker.dragging = true;
      dragging = true;
    });
    group.on("dragmove", () => send(false));
    group.on("dragend", () => {
      marker.dragging = false;
      marker.settledAt = performance.now() + SETTLE_MS;
      send(true);
    });
    group.on("mouseenter", () => (container.style.cursor = "grab"));
    group.on("mouseleave", () => (container.style.cursor = ""));
    personLayer.add(group);
    return marker;
  };

  /** Ritar låtsaspersonerna där kärnan senast sa att de var. Bara de går att dra utan Flytta. */
  const placePersons = (current: MapScene) => {
    if (markerScale !== pixelsPerMeter()) {
      // Personerna är lika stora på skärmen hur långt in man än zoomar.
      for (const [id, marker] of markers) {
        if (!marker.dragging) {
          marker.group.destroy();
          markers.delete(id);
        }
      }
      markerScale = pixelsPerMeter();
    }
    personLayer.listening(current.mode === "view");
    const ids = new Set(current.live.persons.map((person) => person.id));
    for (const [id, marker] of markers) {
      if (!ids.has(id)) {
        // Personen kan försvinna mitt i en dragning, till exempel från en annan flik.
        marker.group.stopDrag();
        marker.group.destroy();
        markers.delete(id);
      }
    }
    for (const person of current.live.persons) {
      const marker = markers.get(person.id) ?? createMarker(person);
      markers.set(person.id, marker);
      if (!marker.dragging && performance.now() >= marker.settledAt) {
        marker.group.position({ x: person.x, y: person.y });
      }
    }
  };

  /** Det som rummen ritas efter; samma igen ritas inte om. */
  const roomKey = (current: MapScene) =>
    JSON.stringify([
      current.floor,
      current.planImage?.url,
      current.selection,
      current.mode,
      current.tool,
      pixelsPerMeter(),
      current.live.state?.rooms.map((room) => room.status),
      current.live.state?.zones,
      colors,
    ]);

  const itemKey = (current: MapScene) =>
    JSON.stringify([
      current.floor,
      current.selection,
      current.mode,
      current.movingId,
      current.tool,
      pixelsPerMeter(),
      current.live.state?.lamps.map((lamp) => [lamp.id, lamp.on, lamp.brightness, lamp.responding]),
      current.live.state?.cards?.map((card) => [card.id, card.online, card.problems, card.calibrating]),
      current.live.state?.cards === null,
      [...current.live.virtualLamps],
      colors,
    ]);

  const draw = () => {
    if (scene === null) {
      return;
    }
    const key = `${scene.floor.id} ${scene.planImage?.url ?? ""}`;
    if (fittedFor !== key) {
      fittedFor = key;
      fit();
    }
    drawTracks(scene);
    placePersons(scene);
    if (dragging) {
      return; // resten ritas om när dragningen är klar
    }
    // I läget Flytta och med ritverktygen panoreras kartan inte, utom med Välj.
    stage.draggable(scene.mode === "view" || (scene.mode === "edit" && scene.tool === "select"));
    container.style.background = colors.planOutside;
    const rooms = roomKey(scene);
    if (rooms !== drawnRooms) {
      drawnRooms = rooms;
      drawGrid();
      drawImage(scene);
      drawRooms(scene);
      drawFurniture(scene);
    }
    const items = itemKey(scene);
    if (items !== drawnItems) {
      drawnItems = items;
      const itemScene = {
        ...scene,
        selectedItemId: scene.selection?.kind === "item" ? scene.selection.id : null,
      };
      drawLight(lightLayer, itemScene, colors);
      drawWalls(wallLayer, itemScene, callbacks, container, colors);
      drawItems(itemLayer, itemScene, callbacks, container, stage, colors);
    }
    drawDraft(scene);
  };

  /** Ritar om allt, till exempel efter zoomning eller när färgerna har bytts. */
  const redraw = () => {
    drawnRooms = "";
    drawnItems = "";
    draw();
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
    if (event.target === stage && scene !== null && roomsListen(scene)) {
      callbacks.onSelect(null);
    }
  });

  const zoomAround = (factor: number, center: Point) => {
    moved = true;
    const old = pixelsPerMeter();
    const next = Math.min(Math.max(old * factor, MIN_ZOOM), MAX_ZOOM);
    const pointInMeters = { x: (center.x - stage.x()) / old, y: (center.y - stage.y()) / old };
    stage.scale({ x: next, y: next });
    stage.position({ x: center.x - pointInMeters.x * next, y: center.y - pointInMeters.y * next });
    draw();
  };
  stage.on("dragstart", (event) => {
    if (event.target !== stage) {
      dragging = true;
    }
  });
  stage.on("dragmove", (event) => {
    if (event.target === stage) {
      moved = true;
      drawGrid();
    }
  });
  stage.on("dragend", (event) => {
    if (event.target === stage) {
      drawGrid();
      return;
    }
    // Det dragna har redan lämnats till sidan; nu får kartan ritas om.
    dragging = markers.size > 0 && [...markers.values()].some((marker) => marker.dragging);
    redraw();
  });
  stage.on("wheel", (event) => {
    event.evt.preventDefault();
    const at = stage.getPointerPosition() ?? { x: stage.width() / 2, y: stage.height() / 2 };
    zoomAround(event.evt.deltaY < 0 ? 1.15 : 1 / 1.15, at);
  });

  // Två fingrar zoomar och flyttar kartan.
  let pinch: { distance: number; center: Point } | null = null;
  stage.on("touchmove", (event) => {
    const touches = event.evt.touches;
    if (touches.length !== 2) {
      return;
    }
    event.evt.preventDefault();
    if (stage.isDragging()) {
      stage.stopDrag();
    }
    const box = container.getBoundingClientRect();
    const [a, b] = [touches[0], touches[1]];
    const center = {
      x: (a.clientX + b.clientX) / 2 - box.left,
      y: (a.clientY + b.clientY) / 2 - box.top,
    };
    const distance = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
    if (pinch !== null && pinch.distance > 0) {
      stage.position({
        x: stage.x() + center.x - pinch.center.x,
        y: stage.y() + center.y - pinch.center.y,
      });
      zoomAround(distance / pinch.distance, center);
    }
    pinch = { distance, center };
  });
  stage.on("touchend touchcancel", () => {
    pinch = null;
  });

  const onResize = () => {
    if (container.clientWidth === stage.width() && container.clientHeight === stage.height()) {
      return; // samma storlek; att rita om skulle bara byta ut det som trycks på
    }
    // Har ingen flyttat kartan visas hela våningen även när ytan byter storlek.
    if (moved) {
      resize();
    } else {
      fit();
    }
    redraw();
  };
  window.addEventListener("resize", onResize);
  const observer =
    typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => onResize());
  observer?.observe(container);
  onColorSchemeChange(() => {
    colors = mapColors();
    redraw();
  });

  return {
    render(next) {
      scene = next;
      draw();
    },
    metersPerPixel: () => 1 / pixelsPerMeter(),
    zoom: (factor) => zoomAround(factor, { x: stage.width() / 2, y: stage.height() / 2 }),
    fit() {
      fit();
      redraw();
    },
    centerOn(point) {
      moved = true;
      const perMeter = pixelsPerMeter();
      stage.position({
        x: stage.width() / 2 - point.x * perMeter,
        y: stage.height() / 2 - point.y * perMeter,
      });
      redraw();
    },
    destroy() {
      window.removeEventListener("resize", onResize);
      observer?.disconnect();
      stage.destroy();
    },
  };
};
