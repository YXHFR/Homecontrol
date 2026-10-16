"""Kärnans flöde: observationer in, sammanvägning, regel, lampkommandon ut.

`Controller` håller det senaste läget och är den enda platsen där det ändras.
Den gör inget av sig själv: någon lämnar observationer via `observe` och
anropar `tick` när klockan har nått `wake_at`. Det gör att samma kod kan
köras i realtid av servern och i snabbspolad tid av scenariotester.

Manuell styrning (ett klick på en lampa i livevyn) kommer in via `set_lamp`.
Varje beslut som ändrar något sparas som en händelse med klockslag, för
listan "Varför hände det?" i livevyn. Händelserna finns bara i minnet.
"""

import logging
import math
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from homecontrol import rules
from homecontrol.clock import Clock
from homecontrol.connectors import (
    LampCommand,
    LampOutput,
    LampStateReported,
    LightLevelObserved,
    MotionObserved,
    Observation,
    RadarTargetsObserved,
    SourceLost,
)
from homecontrol.fusion import Fusion, Occupancy, Track
from homecontrol.home import Home, Room

logger = logging.getLogger(__name__)

Listener = Callable[[], None]

MAX_EVENTS = 30
"""Så många av de senaste händelserna sparas för "Varför hände det?"."""


@dataclass(frozen=True)
class RoomView:
    room_id: str
    status: Occupancy
    lux: float | None
    adjusted_lux: float | None
    """Ljusnivån med rummets egna lampors ljus avräknat; den som reglerna använder."""
    manual: bool
    """Någon har styrt en lampa i rummet för hand, och automatiken pausar."""
    explanation: str

    @property
    def occupied(self) -> bool:
        return self.status is Occupancy.OCCUPIED


@dataclass(frozen=True)
class ZoneView:
    zone_id: str
    status: Occupancy


@dataclass(frozen=True)
class LampView:
    lamp_id: str
    on: bool | None
    """None när lampans läge är okänt."""
    brightness: int
    explanation: str


@dataclass(frozen=True)
class Event:
    """Något som hände med lamporna, med förklaring: till exempel att de tändes eller dimrades."""

    number: int
    """Löpnummer från 1, så att den som sparar händelserna ser vilka som är nya."""
    at: float
    """Klockslaget som Unix-tid i sekunder."""
    room_id: str
    text: str


@dataclass(frozen=True)
class Snapshot:
    """Det som just nu går att visa om hemmet."""

    tracks: tuple[Track, ...]
    """Spåren av personerna, utan radarns numrering."""
    rooms: tuple[RoomView, ...]
    zones: tuple[ZoneView, ...]
    lamps: tuple[LampView, ...]
    events: tuple[Event, ...]
    """De senaste händelserna, äldst först."""


class Controller:
    def __init__(
        self,
        home: Home,
        clock: Clock,
        outputs: Sequence[LampOutput],
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        """`wall_clock` ger klockslaget som Unix-tid, bara för att visa när något hände."""
        self._home = home
        self._clock = clock
        self._wall_clock = wall_clock
        self._outputs = tuple(outputs)
        # Ljusnivån per rum och källa. Det senast mätta värdet gäller, och en
        # källa som tappas tar sina värden med sig.
        self._lux: dict[str, dict[str, float]] = {}
        self._fusion = Fusion(home, clock.now())
        # Kärnan vet inte hur lamporna är förrän de rapporterar eller den själv styr dem.
        self._lamps: dict[str, rules.LampState] = {}
        self._memory: dict[str, rules.Memory] = {}
        self._explanations: dict[str, str] = {}
        self._events: deque[Event] = deque(maxlen=MAX_EVENTS)
        self._event_count = 0
        self._wake_at: float | None = None
        self._listeners: list[Listener] = []
        self._room_ids = {room.id for room in home.rooms}
        self._evaluate()

    @property
    def home(self) -> Home:
        return self._home

    @property
    def wake_at(self) -> float | None:
        """När `tick` behöver anropas nästa gång, eller None om inget väntar."""
        return min(
            (when for when in (self._wake_at, self._fusion.wake_at) if when is not None),
            default=None,
        )

    def observe(self, observation: Observation) -> None:
        """Ingången för alla källkopplingar."""
        now = self._clock.now()
        match observation:
            case RadarTargetsObserved():
                self._fusion.radar(observation, now)
            case MotionObserved():
                self._fusion.motion(observation, now)
            case SourceLost(source_id=source_id):
                self._fusion.source_lost(observation, now)
                for by_source in self._lux.values():
                    by_source.pop(source_id, None)
            case LightLevelObserved(source_id=source_id, room_id=room_id, lux=lux):
                if room_id not in self._room_ids:
                    logger.warning("Ljusnivå för okänt rum %r ignoreras", room_id)
                    return
                if not math.isfinite(lux) or lux < 0:
                    logger.warning("Ogiltig ljusnivå %r ignoreras", lux)
                    return
                by_source = self._lux.setdefault(room_id, {})
                by_source.pop(source_id, None)  # sist i ordningen är senast mätt
                by_source[source_id] = lux
            case LampStateReported(lamp_id=lamp_id, on=on, brightness=brightness):
                if not any(lamp.id == lamp_id for lamp in self._home.lamps):
                    logger.warning("Läge för okänd lampa %r ignoreras", lamp_id)
                    return
                if on is True and not 1 <= brightness <= rules.FULL_BRIGHTNESS:
                    logger.warning("Ogiltig ljusstyrka %r ignoreras", brightness)
                    return
                self._lamps[lamp_id] = rules.LampState(on, brightness)
        self._evaluate()
        self._notify()

    def set_lamp(self, lamp_id: str, on: bool) -> None:
        """Manuell styrning: tänder eller släcker en lampa, till exempel efter ett klick i livevyn.

        Automatiken i lampans rum pausar sedan tills rummet har varit tomt i
        rummets släckfördröjning.
        """
        try:
            room = self._home.room_of(lamp_id)
        except KeyError:
            logger.warning("Manuell styrning av okänd lampa %r ignoreras", lamp_id)
            return
        lamp = next(lamp for lamp in room.lamps if lamp.id == lamp_id)
        command = rules.manual_command(room, lamp, on)
        self._memory[room.id] = rules.start_manual(self._memory.get(room.id, {}), self._clock.now())
        self._switch(command)
        self._log_event(room.id, command.reason)
        self._evaluate()
        self._notify()

    def resume_automation(self, room_id: str) -> None:
        """Låter automatiken ta över i ett rum direkt, utan att vänta tills rummet varit tomt."""
        room = next((room for room in self._home.rooms if room.id == room_id), None)
        if room is None:
            logger.warning("Automatik för okänt rum %r ignoreras", room_id)
            return
        memory = self._memory.get(room_id, {})
        if not rules.is_manual(memory):
            return
        self._memory[room_id] = rules.stop_manual(memory)
        self._log_event(room_id, f"Automatiken i {room.name} slogs på igen för hand.")
        self._evaluate()
        self._notify()

    def replace_home(self, home: Home) -> None:
        """Byter till ett ändrat hem, till exempel när kartredigeraren har sparat.

        Det som fortfarande gäller behålls: spåren från noder som finns kvar,
        ljusnivån och det reglerna minns (som manuellt läge) i rum som finns
        kvar, hur länge rummen har varit tomma och lampornas läge.
        En tänd lampa som inte längre finns i hemmet släcks, så att den inte
        lyser kvar utan att något styr den.

        `home` ska vara ett nytt objekt; livevyn ser att hemmet har bytts genom
        att jämföra med det förra.
        """
        kept = {lamp.id for lamp in home.lamps}
        for lamp_id, state in self._lamps.items():
            if state.on is True and lamp_id not in kept:
                self._send(LampCommand(lamp_id, on=False, reason="Lampan togs bort ur hemmet."))
        self._home = home
        self._room_ids = {room.id for room in home.rooms}
        self._lux = {
            room_id: lux for room_id, lux in self._lux.items() if room_id in self._room_ids
        }
        self._memory = {
            room_id: memory for room_id, memory in self._memory.items() if room_id in self._room_ids
        }
        self._lamps = {lamp_id: state for lamp_id, state in self._lamps.items() if lamp_id in kept}
        self._fusion.replace_home(home, self._clock.now())
        self._evaluate()
        self._notify()

    def tick(self) -> None:
        """Låter sammanvägningen och regeln titta igen om klockan har nått `wake_at`."""
        wake_at = self.wake_at
        if wake_at is not None and self._clock.now() >= wake_at:
            self._fusion.update(self._clock.now())
            self._evaluate()
            self._notify()

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Anropar `listener` efter varje ändring. Returnerar en funktion som avslutar."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def snapshot(self) -> Snapshot:
        rooms = self._fusion.rooms()
        zones = self._fusion.zones()
        return Snapshot(
            tracks=self._fusion.tracks(),
            rooms=tuple(self._room_view(room, rooms[room.id].status) for room in self._home.rooms),
            zones=tuple(
                ZoneView(zone.id, zones[zone.id].status)
                for room in self._home.rooms
                for zone in room.zones
            ),
            lamps=tuple(
                self._lamp_view(room, lamp.id) for room in self._home.rooms for lamp in room.lamps
            ),
            events=tuple(self._events),
        )

    def _room_view(self, room: Room, status: Occupancy) -> RoomView:
        lux = self._room_lux(room.id)
        return RoomView(
            room.id,
            status,
            lux=lux,
            adjusted_lux=rules.adjusted_lux(room, lux, self._lamps),
            manual=rules.is_manual(self._memory.get(room.id, {})),
            explanation=self._explanations[room.id],
        )

    def _lamp_view(self, room: Room, lamp_id: str) -> LampView:
        state = self._lamps.get(lamp_id, rules.UNKNOWN_LAMP)
        # Lamporna i ett rum styrs som en grupp och har rummets förklaring.
        return LampView(lamp_id, state.on, state.brightness, self._explanations[room.id])

    def _room_lux(self, room_id: str) -> float | None:
        by_source = self._lux.get(room_id)
        # Källan som mätte senast ligger sist, eftersom den läggs in på nytt varje gång.
        return next(reversed(by_source.values()), None) if by_source else None

    def _evaluate(self) -> None:
        presence = self._fusion.rooms()
        room_states = {
            room.id: rules.RoomState(
                presence[room.id], self._room_lux(room.id), self._memory.get(room.id, {})
            )
            for room in self._home.rooms
        }
        result = rules.evaluate(self._home, room_states, self._lamps, self._clock.now())
        for command in result.commands:
            self._switch(command)
        for event in result.events:
            self._log_event(event.room_id, event.text)
        self._memory = dict(result.memory)
        self._explanations = dict(result.explanations)
        self._wake_at = result.wake_at

    def _switch(self, command: LampCommand) -> None:
        # Förklaringen säger var någon är, så den loggas bara när man felsöker.
        if not command.on:
            logger.info("Släcker %s", command.lamp_id)
        elif command.brightness == rules.FULL_BRIGHTNESS:
            logger.info("Tänder %s", command.lamp_id)
        else:
            # En dimring säger när ett rum blev tomt, så den loggas bara när man felsöker.
            logger.debug("Dimrar %s till %d %%", command.lamp_id, command.brightness)
        logger.debug("%s: %s", command.lamp_id, command.reason)
        self._lamps[command.lamp_id] = rules.LampState(command.on, command.brightness)
        self._send(command)

    def _log_event(self, room_id: str, text: str) -> None:
        self._event_count += 1
        self._events.append(Event(self._event_count, self._wall_clock(), room_id, text))

    def _send(self, command: LampCommand) -> None:
        for output in self._outputs:
            # En trasig utgång får inte hindra de andra eller stoppa kärnan.
            try:
                output.send(command)
            except Exception:
                logger.exception("Lamputgången kunde inte ta emot %s", command)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()
