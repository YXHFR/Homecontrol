import Konva from "konva";
import { fitTransform, parkingSpot, type Transform } from "./geometry";
import type { HomeMessage, Point, StateMessage } from "./protocol";

export interface LiveViewCallbacks {
  /** Låtsaspersonen har dragits till en ny plats, i hemmets meter. */
  onMovePerson(position: Point): void;
}

/** Ritytan i livevyn. Den ritar bara det kärnan har skickat. */
export interface LiveView {
  setHome(home: HomeMessage): void;
  setState(state: StateMessage): void;
  destroy(): void;
}

export type ViewFactory = (container: HTMLDivElement, callbacks: LiveViewCallbacks) => LiveView;

const MAX_WIDTH = 800;
const ASPECT = 0.65;
const PERSON_RADIUS = 12;
/** Under dragning skickas positionen högst så här ofta. */
const SEND_INTERVAL_MS = 50;

const COLORS = {
  floor: "#f4f1ea",
  occupied: "#e3eefb",
  wall: "#4a4a4a",
  lampOn: "#ffc933",
  lampOff: "#b8b8b8",
  person: "#d64545",
  text: "#222222",
};

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

  let home: HomeMessage | null = null;
  let state: StateMessage | null = null;
  let transform: Transform | null = null;
  let dragging = false;
  let lastSent = 0;

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
    sendPosition(true);
  });
  person.on("mouseenter", () => (stage.container().style.cursor = "grab"));
  person.on("mouseleave", () => (stage.container().style.cursor = ""));

  const render = () => {
    if (home === null) {
      return;
    }
    const width = Math.min(container.clientWidth || MAX_WIDTH, MAX_WIDTH);
    const height = Math.round(width * ASPECT);
    stage.size({ width, height });
    const t = fitTransform(home.rooms, width, height);
    transform = t;

    roomLayer.destroyChildren();
    for (const room of home.rooms) {
      const occupied = state?.rooms.find((r) => r.id === room.id)?.occupied ?? false;
      const corner = t.toScreen({ x: room.x, y: room.y });
      roomLayer.add(
        new Konva.Rect({
          ...corner,
          width: room.width * t.scale,
          height: room.height * t.scale,
          fill: occupied ? COLORS.occupied : COLORS.floor,
          stroke: COLORS.wall,
          strokeWidth: 4,
        }),
        new Konva.Text({
          x: corner.x + 8,
          y: corner.y + 8,
          text: room.name,
          fontSize: 16,
          fill: COLORS.text,
        }),
      );
    }
    for (const lamp of home.lamps) {
      const on = state?.lamps.find((l) => l.id === lamp.id)?.on ?? false;
      const center = t.toScreen(lamp);
      roomLayer.add(
        new Konva.Circle({
          ...center,
          radius: on ? 0.9 * t.scale : 0.2 * t.scale,
          fill: COLORS.lampOn,
          opacity: on ? 0.35 : 0,
        }),
        new Konva.Circle({
          ...center,
          radius: 14,
          fill: on ? COLORS.lampOn : COLORS.lampOff,
          stroke: COLORS.wall,
          strokeWidth: 1,
        }),
        new Konva.Text({
          x: center.x - 50,
          y: center.y + 20,
          width: 100,
          align: "center",
          text: `${lamp.name} (${on ? "tänd" : "släckt"})`,
          fontSize: 13,
          fill: COLORS.text,
        }),
      );
    }

    if (!dragging) {
      // Kärnans bild av var personen är gäller; utan mål står den och väntar utanför.
      person.position(t.toScreen(state?.targets[0] ?? parkingSpot(home.rooms)));
    }
  };

  const onResize = () => render();
  window.addEventListener("resize", onResize);

  return {
    setHome(next) {
      home = next;
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
