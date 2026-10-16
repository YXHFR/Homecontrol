/**
 * Panelerna under kartan i livevyn: "Just nu" (läget i varje rum, med knappar för
 * manuell styrning) och "Varför hände det?" (regelmotorns beslut med klockslag).
 * Modulen beror inte på Konva, så att den går att pröva utan en rityta.
 */
import { button, element } from "./dom";
import type { Floor, LampEvent, LampState, StateMessage } from "./protocol";
import { OCCUPANCY_TEXT } from "./trackStyles";

export interface RoomActions {
  /** Någon vill tända eller släcka lampan för hand. */
  setLamp(lampId: string, on: boolean): void;
  /** Någon vill att automatiken ska ta över i rummet igen direkt. */
  resumeAutomation(roomId: string): void;
}

/** Lampans läge i klartext: "tänd", "dimrad 50 %", "släckt", "svarar inte" eller "okänt läge". */
export function lampStateText(state: LampState | undefined): string {
  if (state?.responding === false) {
    return "svarar inte";
  }
  if (state === undefined || state.on === null) {
    return "okänt läge";
  }
  if (!state.on) {
    return "släckt";
  }
  return state.brightness < 100 ? `dimrad ${state.brightness} %` : "tänd";
}

/** Vad ett klick på lampan gör: släcker en lampa som lyser, annars tänder den. */
export function shouldTurnOn(state: LampState | undefined): boolean {
  return state?.on !== true;
}

function formatLux(lux: number): string {
  return lux.toLocaleString("sv-SE", { maximumFractionDigits: 1 });
}

/** Ljusnivån, och den justerade nivån när rummets lampor lyser upp sensorn. */
export function luxText(lux: number | null, adjusted: number | null): string {
  if (lux === null) {
    return "ljusnivån okänd";
  }
  // Skillnader som försvinner när värdet visas med en decimal syns inte.
  if (adjusted === null || Math.abs(adjusted - lux) < 0.05) {
    return `${formatLux(lux)} lux`;
  }
  return `${formatLux(lux)} lux (${formatLux(adjusted)} utan lampornas eget ljus)`;
}

/** Klockslaget för en händelse, som "19:02", i webbläsarens tidszon. */
export function clockTime(at: number): string {
  return new Date(at * 1000).toLocaleTimeString("sv-SE", { hour: "2-digit", minute: "2-digit" });
}

/** Ett listelement per rum på våningen, för panelen "Just nu". */
export function roomsNowItems(
  floor: Floor,
  state: StateMessage | null,
  actions: RoomActions,
): HTMLLIElement[] {
  const rooms = new Map(state?.rooms.map((room) => [room.id, room]));
  const lamps = new Map(state?.lamps.map((lamp) => [lamp.id, lamp]));
  return floor.rooms.flatMap((room) => {
    const roomState = rooms.get(room.id);
    if (roomState === undefined) {
      return [];
    }
    const summary = element(
      "p",
      { className: "room-summary" },
      element("strong", {}, room.name),
      `: ${OCCUPANCY_TEXT[roomState.status]}, ${luxText(roomState.lux, roomState.adjusted_lux)}`,
    );
    if (roomState.manual) {
      summary.append(
        " ",
        element("span", { className: "badge manual" }, "Manuellt läge"),
        " ",
        button("Automatik igen", () => actions.resumeAutomation(room.id), "resume"),
      );
    }
    const lampButtons = floor.lamps
      .filter((lamp) => lamp.room_id === room.id)
      .map((lamp) => {
        const lampState = lamps.get(lamp.id);
        const toggle = button(`${lamp.name}: ${lampStateText(lampState)}`, () =>
          actions.setLamp(lamp.id, shouldTurnOn(lampState)),
        );
        toggle.classList.add("lamp-toggle");
        toggle.setAttribute("aria-pressed", String(lampState?.on === true));
        toggle.title = lampState?.on === true ? "Släck för hand" : "Tänd för hand";
        return toggle;
      });
    const item = element(
      "li",
      {},
      summary,
      element("p", { className: "explanation" }, roomState.explanation),
    );
    if (lampButtons.length > 0) {
      item.append(element("div", { className: "lamp-toggles" }, ...lampButtons));
    }
    item.dataset.status = roomState.status;
    item.dataset.manual = String(roomState.manual);
    return [item];
  });
}

/**
 * Händelserna i rummen på våningen, senaste först, för panelen "Varför hände det?".
 * Texten nämner själv rummet, så bara klockslaget står före.
 */
export function eventItems(floor: Floor, events: readonly LampEvent[]): HTMLLIElement[] {
  const names = new Map(floor.rooms.map((room) => [room.id, room.name]));
  return events
    .filter((event) => names.has(event.room_id))
    .reverse()
    .map((event) =>
      element(
        "li",
        {},
        element("time", { dateTime: new Date(event.at * 1000).toISOString() }, clockTime(event.at)),
        ` ${event.text}`,
      ),
    );
}
