import Konva from "konva";
import { corners, fitTransform, parkingSpot, wallSegments, type Transform } from "./geometry";
import type { Floor, Point, StateMessage, WallKind } from "./protocol";

export interface LiveViewCallbacks {
  /** Låtsaspersonen har dragits till en ny plats, i hemmets meter. */
  onMovePerson(position: Point): void;
}

/** Ritytan i livevyn. Den ritar en våning åt gången, och bara det kärnan har skickat. */
export interface LiveView {
  setFloor(floor: Floor): void;
  setState(state: StateMessage): void;
  destroy(): void;
}

export type ViewFactory = (container: HTMLDivElement, callbacks: LiveViewCallbacks) => LiveView;

const MAX_WIDTH = 800;
const ASPECT = 0.65;
const PERSON_RADIUS = 12;
const LAMP_RADIUS = 14;
/** Skenet runt en tänd lampa, i meter, och hur genomskinligt det är. */
const GLOW_RADIUS_METERS = 0.9;
const GLOW_OPACITY = 0.35;
/** Under dragning skickas positionen högst så här ofta. */
const SEND_INTERVAL_MS = 50;
/**
 * Så länge efter en dragning litar vyn på sin egen position. Svar på äldre
 * positioner kan annars hinna fram och få pricken att hoppa tillbaka.
 */
const SETTLE_MS = 300;

const COLORS = {
  floor: "#f4f1ea",
  occupied: "#e3eefb",
  wall: "#4a4a4a",
  lampOn: "#ffc933",
  lampOff: "#b8b8b8",
  person: "#d64545",
  text: "#222222",
};

/** Hur varje sorts vägg ser ut: färg och tjocklek i pixlar. Dörröppningar streckas. */
const WALLS: Record<WallKind, { color: string; width: number; dash?: number[] }> = {
  gips: { color: "#4a4a4a", width: 4 },
  betong: { color: "#111111", width: 6 },
  trä: { color: "#a0652c", width: 4 },
  glas: { color: "#4bb3d6", width: 3 },
  dörröppning: { color: "#e08a00", width: 2, dash: [6, 4] },
};

function drawRoomFloors(
  layer: Konva.Layer,
  t: Transform,
  floor: Floor,
  state: StateMessage | null,
) {
  for (const room of floor.rooms) {
    const occupied = state?.rooms.find((r) => r.id === room.id)?.occupied ?? false;
    layer.add(
      new Konva.Line({
        points: corners(room).flatMap((p) => {
          const screen = t.toScreen(p);
          return [screen.x, screen.y];
        }),
        closed: true,
        fill: occupied ? COLORS.occupied : COLORS.floor,
      }),
    );
  }
}

/**
 * Grannrum delar väggar. Dörröppningar ritas sist så att de syns även om
 * grannrummet har en hel vägg på samma ställe, och alla väggar ritas åt
 * samma håll så att två likadana dörröppningar får strecken på samma ställe.
 */
function drawWalls(layer: Konva.Layer, t: Transform, floor: Floor) {
  const walls = floor.rooms.flatMap(wallSegments);
  const ordered = [
    ...walls.filter((wall) => wall.kind !== "dörröppning"),
    ...walls.filter((wall) => wall.kind === "dörröppning"),
  ];
  for (const wall of ordered) {
    const [a, b] = [t.toScreen(wall.from), t.toScreen(wall.to)];
    const [from, to] = a.x < b.x || (a.x === b.x && a.y <= b.y) ? [a, b] : [b, a];
    const style = WALLS[wall.kind];
    layer.add(
      new Konva.Line({
        points: [from.x, from.y, to.x, to.y],
        stroke: style.color,
        strokeWidth: style.width,
        dash: style.dash,
        lineCap: "round",
      }),
    );
  }
}

function drawRoomNames(layer: Konva.Layer, t: Transform, floor: Floor) {
  for (const room of floor.rooms) {
    const points = corners(room).map((p) => t.toScreen(p));
    layer.add(
      new Konva.Text({
        x: Math.min(...points.map((p) => p.x)) + 8,
        y: Math.min(...points.map((p) => p.y)) + 8,
        text: room.name,
        fontSize: 16,
        fill: COLORS.text,
      }),
    );
  }
}

function drawLamps(layer: Konva.Layer, t: Transform, floor: Floor, state: StateMessage | null) {
  for (const lamp of floor.lamps) {
    const on = state?.lamps.find((l) => l.id === lamp.id)?.on ?? false;
    const center = t.toScreen(lamp);
    if (on) {
      layer.add(
        new Konva.Circle({
          ...center,
          radius: GLOW_RADIUS_METERS * t.scale,
          fill: COLORS.lampOn,
          opacity: GLOW_OPACITY,
        }),
      );
    }
    layer.add(
      new Konva.Circle({
        ...center,
        radius: LAMP_RADIUS,
        fill: on ? COLORS.lampOn : COLORS.lampOff,
        stroke: COLORS.wall,
        strokeWidth: 1,
      }),
      new Konva.Text({
        x: center.x - 50,
        y: center.y + LAMP_RADIUS + 6,
        width: 100,
        align: "center",
        text: `${lamp.name} (${on ? "tänd" : "släckt"})`,
        fontSize: 13,
        fill: COLORS.text,
      }),
    );
  }
}

export const createKonvaView: ViewFactory = (container, callbacks) => {
  const stage = new Konva.Stage({ container, width: 1, height: 1 });
  const roomLayer = new Konva.Layer();
  const personLayer = new Konva.Layer();
  stage.add(roomLayer, personLayer);

  const person = new Konva.Circle({
    radius: PERSON_RADIUS,
    fill: COLORS.person,
    stroke: "white",
    strokeWidth: 2,
    draggable: true,
  });
  personLayer.add(person);

  let shownFloor: Floor | null = null;
  let state: StateMessage | null = null;
  /** Senaste omräkningen mellan meter och pixlar; behövs när personen dras. */
  let transform: Transform | null = null;
  let dragging = false;
  let lastSent = 0;
  let settledAt = 0;

  const sendPosition = (force: boolean) => {
    const now = performance.now();
    if (transform === null || (!force && now - lastSent < SEND_INTERVAL_MS)) {
      return;
    }
    lastSent = now;
    callbacks.onMovePerson(transform.toHome(person.position()));
  };
  person.on("dragstart", () => {
    dragging = true;
  });
  person.on("dragmove", () => sendPosition(false));
  person.on("dragend", () => {
    dragging = false;
    settledAt = performance.now() + SETTLE_MS;
    sendPosition(true);
  });
  person.on("mouseenter", () => (stage.container().style.cursor = "grab"));
  person.on("mouseleave", () => (stage.container().style.cursor = ""));

  const placePerson = (t: Transform, shown: Floor) => {
    if (dragging || performance.now() < settledAt) {
      return;
    }
    // Kärnans bild av var personen är gäller. Är den inte på den här
    // våningen står den och väntar utanför rummen.
    const target = state?.targets.find((candidate) => candidate.floor_id === shown.id);
    person.position(t.toScreen(target ?? parkingSpot(shown.rooms)));
  };

  const render = () => {
    if (shownFloor === null) {
      return;
    }
    const width = Math.min(container.clientWidth || MAX_WIDTH, MAX_WIDTH);
    const height = Math.round(width * ASPECT);
    stage.size({ width, height });
    transform = fitTransform(shownFloor.rooms, width, height);

    roomLayer.destroyChildren();
    drawRoomFloors(roomLayer, transform, shownFloor, state);
    drawWalls(roomLayer, transform, shownFloor);
    drawRoomNames(roomLayer, transform, shownFloor);
    drawLamps(roomLayer, transform, shownFloor, state);
    placePerson(transform, shownFloor);
  };

  const onResize = () => render();
  window.addEventListener("resize", onResize);

  return {
    setFloor(next) {
      shownFloor = next;
      render();
    },
    setState(next) {
      state = next;
      render();
    },
    destroy() {
      window.removeEventListener("resize", onResize);
      stage.destroy();
    },
  };
};
