import Konva from "konva";
import { corners, fitTransform, wallSegments, type Transform } from "./geometry";
import type { Floor, Person, Point, StateMessage, WallKind } from "./protocol";
import { ROOM_FILLS, trackStyle, tracksOnFloor } from "./trackStyles";
import { ZONE_STYLES } from "./zoneStyles";

export interface LiveViewCallbacks {
  /** En låtsasperson har dragits till en ny plats, i hemmets meter. */
  onMovePerson(personId: string, position: Point): void;
}

/**
 * Ritytan i livevyn. Den ritar en våning åt gången, och bara det kärnan har skickat.
 *
 * Kärnans spår ritas som små prickar, och ett spår som radarn har tappat men
 * som hålls kvar som en streckad ring. Låtsaspersonerna ritas ovanpå som
 * större ringar som går att dra med mus eller finger.
 */
export interface LiveView {
  setFloor(floor: Floor): void;
  setState(state: StateMessage): void;
  destroy(): void;
}

export type ViewFactory = (container: HTMLDivElement, callbacks: LiveViewCallbacks) => LiveView;

const MAX_WIDTH = 800;
const ASPECT = 0.65;
const PERSON_RADIUS = 12;
/** Extra yta runt personen som går att ta tag i, så att den är lätt att dra med fingret. */
const PERSON_HIT_WIDTH = 24;
const TRACK_RADIUS = 5;
/** Hur genomskinlig en zons yta är. Zonerna ska synas men inte ta över kartan. */
const ZONE_OPACITY = 0.18;
/** En upptagen zon syns tydligare. */
const ZONE_OCCUPIED_OPACITY = 0.4;
const ZONE_FONT_SIZE = 12;
const LAMP_RADIUS = 14;
const NODE_SIZE = 10;
/** Hur bred lampans namnetikett får vara, i pixlar. Den centreras under lampan. */
const LAMP_LABEL_WIDTH = 120;
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
  wall: "#4a4a4a",
  lampOn: "#ffc933",
  lampOff: "#b8b8b8",
  person: "#d64545",
  node: "#5a7da8",
  warning: "#c62828",
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
    const status = state?.rooms.find((r) => r.id === room.id)?.status ?? "tomt";
    layer.add(
      new Konva.Line({
        points: corners(room).flatMap((p) => {
          const screen = t.toScreen(p);
          return [screen.x, screen.y];
        }),
        closed: true,
        fill: ROOM_FILLS[status],
      }),
    );
  }
}

/** Zonerna i sina färger, med streckad kant och namnet i mitten. En upptagen zon är starkare. */
function drawZones(layer: Konva.Layer, t: Transform, floor: Floor, state: StateMessage | null) {
  const occupied = new Set(
    state?.zones.filter((zone) => zone.status === "upptaget").map((zone) => zone.id),
  );
  for (const zone of floor.rooms.flatMap((room) => room.zones)) {
    const points = zone.polygon.map(([x, y]) => t.toScreen({ x, y }));
    const color = ZONE_STYLES[zone.kind].color;
    const xs = points.map((p) => p.x);
    const ys = points.map((p) => p.y);
    layer.add(
      new Konva.Line({
        points: points.flatMap((p) => [p.x, p.y]),
        closed: true,
        fill: color,
        opacity: occupied.has(zone.id) ? ZONE_OCCUPIED_OPACITY : ZONE_OPACITY,
      }),
      new Konva.Line({
        points: points.flatMap((p) => [p.x, p.y]),
        closed: true,
        stroke: color,
        strokeWidth: 1.5,
        dash: [5, 4],
      }),
      new Konva.Text({
        x: Math.min(...xs),
        y: (Math.min(...ys) + Math.max(...ys)) / 2 - ZONE_FONT_SIZE / 2,
        width: Math.max(...xs) - Math.min(...xs),
        align: "center",
        text: zone.name,
        wrap: "none",
        ellipsis: true,
        fontSize: ZONE_FONT_SIZE,
        fill: color,
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
    // En virtuell lampa finns bara i simuleringen och ritas streckad.
    layer.add(
      new Konva.Circle({
        ...center,
        radius: LAMP_RADIUS,
        fill: on ? COLORS.lampOn : COLORS.lampOff,
        stroke: COLORS.wall,
        strokeWidth: lamp.virtual ? 1.5 : 1,
        dash: lamp.virtual ? [4, 3] : undefined,
      }),
      new Konva.Text({
        x: center.x - LAMP_LABEL_WIDTH / 2,
        y: center.y + LAMP_RADIUS + 6,
        width: LAMP_LABEL_WIDTH,
        align: "center",
        text: `${lamp.name} (${lamp.virtual ? "virtuell, " : ""}${on ? "tänd" : "släckt"})`,
        fontSize: 13,
        fill: COLORS.text,
      }),
    );
  }
}

/** Rumskorten som små fyrkanter. Ett riktigt kort med varningar blir rött med ⚠. */
function drawNodes(layer: Konva.Layer, t: Transform, floor: Floor, state: StateMessage | null) {
  for (const node of floor.nodes) {
    const card = state?.cards?.find((c) => c.id === node.id);
    const warning = card !== undefined && card.problems.length > 0;
    const center = t.toScreen(node);
    layer.add(
      new Konva.Rect({
        x: center.x - NODE_SIZE / 2,
        y: center.y - NODE_SIZE / 2,
        width: NODE_SIZE,
        height: NODE_SIZE,
        fill: warning ? COLORS.warning : COLORS.node,
        stroke: COLORS.wall,
        strokeWidth: 1,
        // Ett rumskort som inte är ett riktigt kort finns bara i simuleringen.
        dash: card === undefined ? [3, 2] : undefined,
      }),
    );
    if (warning) {
      layer.add(
        new Konva.Text({
          x: center.x + NODE_SIZE / 2 + 2,
          y: center.y - NODE_SIZE,
          text: "⚠",
          fontSize: 16,
          fill: COLORS.warning,
        }),
      );
    }
  }
}

function drawTracks(layer: Konva.Layer, t: Transform, floor: Floor, state: StateMessage | null) {
  for (const track of tracksOnFloor(state, floor.id)) {
    layer.add(new Konva.Circle({ ...t.toScreen(track), radius: TRACK_RADIUS, ...trackStyle(track) }));
  }
}

/** En låtsasperson på ritytan och vad vyn vet om dragningen av den. */
interface PersonMarker {
  group: Konva.Group;
  dragging: boolean;
  lastSent: number;
  settledAt: number;
}

export const createKonvaView: ViewFactory = (container, callbacks) => {
  const stage = new Konva.Stage({ container, width: 1, height: 1 });
  const roomLayer = new Konva.Layer();
  const personLayer = new Konva.Layer();
  stage.add(roomLayer, personLayer);

  let shownFloor: Floor | null = null;
  let state: StateMessage | null = null;
  /** Senaste omräkningen mellan meter och pixlar; behövs när personer dras. */
  let transform: Transform | null = null;
  const markers = new Map<string, PersonMarker>();

  const sendPosition = (personId: string, marker: PersonMarker, force: boolean) => {
    const now = performance.now();
    if (transform === null || (!force && now - marker.lastSent < SEND_INTERVAL_MS)) {
      return;
    }
    marker.lastSent = now;
    callbacks.onMovePerson(personId, transform.toHome(marker.group.position()));
  };

  const createMarker = (person: Person): PersonMarker => {
    const group = new Konva.Group({ draggable: true });
    group.add(
      new Konva.Circle({
        radius: PERSON_RADIUS,
        fill: COLORS.person,
        opacity: 0.85,
        stroke: "white",
        strokeWidth: 2,
        hitStrokeWidth: PERSON_HIT_WIDTH,
      }),
      new Konva.Text({
        x: -PERSON_RADIUS,
        y: -6,
        width: 2 * PERSON_RADIUS,
        align: "center",
        text: person.id,
        fontSize: 12,
        fill: "white",
        listening: false,
      }),
    );
    const marker: PersonMarker = { group, dragging: false, lastSent: 0, settledAt: 0 };
    group.on("dragstart", () => {
      marker.dragging = true;
    });
    group.on("dragmove", () => sendPosition(person.id, marker, false));
    group.on("dragend", () => {
      marker.dragging = false;
      marker.settledAt = performance.now() + SETTLE_MS;
      sendPosition(person.id, marker, true);
    });
    group.on("mouseenter", () => (stage.container().style.cursor = "grab"));
    group.on("mouseleave", () => (stage.container().style.cursor = ""));
    personLayer.add(group);
    return marker;
  };

  /** Ritar låtsaspersonerna på våningen som visas, där kärnan senast sa att de var. */
  const placePersons = (t: Transform, shown: Floor) => {
    const simulation = state?.simulation;
    const persons = simulation?.enabled
      ? simulation.persons.filter((person) => person.floor_id === shown.id)
      : [];
    const ids = new Set(persons.map((person) => person.id));
    for (const [id, marker] of markers) {
      if (!ids.has(id)) {
        // Personen kan försvinna mitt i en dragning, till exempel från en annan flik.
        marker.group.stopDrag();
        marker.group.destroy();
        markers.delete(id);
      }
    }
    for (const person of persons) {
      const marker = markers.get(person.id) ?? createMarker(person);
      markers.set(person.id, marker);
      if (!marker.dragging && performance.now() >= marker.settledAt) {
        marker.group.position(t.toScreen(person));
      }
    }
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
    drawZones(roomLayer, transform, shownFloor, state);
    drawWalls(roomLayer, transform, shownFloor);
    drawRoomNames(roomLayer, transform, shownFloor);
    drawNodes(roomLayer, transform, shownFloor, state);
    drawLamps(roomLayer, transform, shownFloor, state);
    drawTracks(roomLayer, transform, shownFloor, state);
    placePersons(transform, shownFloor);
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
