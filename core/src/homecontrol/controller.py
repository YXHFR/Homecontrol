"""Kärnans flöde: observationer in, sammanvägning, regel, lampkommandon ut.

`Controller` håller det senaste läget och är den enda platsen där det ändras.
Den gör inget av sig själv: någon lämnar observationer via `observe` och
anropar `tick` när klockan har nått `wake_at`. Det gör att samma kod kan
köras i realtid av servern och i snabbspolad tid av scenariotester.
"""

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from homecontrol import rules
from homecontrol.clock import Clock
from homecontrol.connectors import (
    LampCommand,
    LampOutput,
    LightLevelObserved,
    MotionObserved,
    Observation,
    RadarTargetsObserved,
    SourceLost,
)
from homecontrol.fusion import Fusion, Occupancy, Track
from homecontrol.home import Home

logger = logging.getLogger(__name__)

Listener = Callable[[], None]


@dataclass(frozen=True)
class RoomView:
    room_id: str
    status: Occupancy
    lux: float | None

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
    on: bool
    explanation: str


@dataclass(frozen=True)
class Snapshot:
    """Det som just nu går att visa om hemmet."""

    tracks: tuple[Track, ...]
    """Spåren av personerna, utan radarns numrering."""
    rooms: tuple[RoomView, ...]
    zones: tuple[ZoneView, ...]
    lamps: tuple[LampView, ...]


class Controller:
    def __init__(self, home: Home, clock: Clock, outputs: Sequence[LampOutput]) -> None:
        self._home = home
        self._clock = clock
        self._outputs = tuple(outputs)
        # Ljusnivån per rum och källa. Det senast mätta värdet gäller, och en
        # källa som tappas tar sina värden med sig.
        self._lux: dict[str, dict[str, float]] = {}
        self._fusion = Fusion(home, clock.now())
        # Lamporna antas vara släckta vid start.
        self._lamps_on = {lamp.id: False for lamp in home.lamps}
        self._explanations: dict[str, str] = {}
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
        self._evaluate()
        self._notify()

    def replace_home(self, home: Home) -> None:
        """Byter till ett ändrat hem, till exempel när kartredigeraren har sparat.

        Det som fortfarande gäller behålls: spåren från noder som finns kvar,
        ljusnivån i rum som finns kvar, hur länge rummen har varit tomma och
        vilka lampor som är tända.
        En tänd lampa som inte längre finns i hemmet släcks, så att den inte
        lyser kvar utan att något styr den.

        `home` ska vara ett nytt objekt; livevyn ser att hemmet har bytts genom
        att jämföra med det förra.
        """
        kept = {lamp.id for lamp in home.lamps}
        for lamp_id, on in self._lamps_on.items():
            if on and lamp_id not in kept:
                self._send(LampCommand(lamp_id, on=False, reason="Lampan togs bort ur hemmet."))
        self._home = home
        self._room_ids = {room.id for room in home.rooms}
        self._lux = {
            room_id: lux for room_id, lux in self._lux.items() if room_id in self._room_ids
        }
        self._lamps_on = {lamp.id: self._lamps_on.get(lamp.id, False) for lamp in home.lamps}
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
            rooms=tuple(
                RoomView(room.id, rooms[room.id].status, self._room_lux(room.id))
                for room in self._home.rooms
            ),
            zones=tuple(
                ZoneView(zone.id, zones[zone.id].status)
                for room in self._home.rooms
                for zone in room.zones
            ),
            lamps=tuple(
                LampView(lamp.id, self._lamps_on[lamp.id], self._explanations[lamp.id])
                for lamp in self._home.lamps
            ),
        )

    def _room_lux(self, room_id: str) -> float | None:
        by_source = self._lux.get(room_id)
        # Källan som mätte senast ligger sist, eftersom den läggs in på nytt varje gång.
        return next(reversed(by_source.values()), None) if by_source else None

    def _evaluate(self) -> None:
        presence = self._fusion.rooms()
        room_states = {
            room.id: rules.RoomState(presence[room.id], self._room_lux(room.id))
            for room in self._home.rooms
        }
        result = rules.evaluate(self._home, room_states, self._lamps_on, self._clock.now())
        for command in result.commands:
            # Förklaringen säger var någon är, så den loggas bara när man felsöker.
            logger.info("%s %s", "Tänder" if command.on else "Släcker", command.lamp_id)
            logger.debug("%s: %s", command.lamp_id, command.reason)
            self._lamps_on[command.lamp_id] = command.on
            self._send(command)
        self._explanations = dict(result.explanations)
        self._wake_at = result.wake_at

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
