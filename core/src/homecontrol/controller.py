"""Kärnans flöde: observationer in, närvarobedömning, regel, lampkommandon ut.

`Controller` håller det senaste läget och är den enda platsen där det ändras.
Den gör inget av sig själv: någon lämnar observationer via `observe` och
anropar `tick` när klockan har nått `wake_at`. Det gör att samma kod kan
köras i realtid av servern och i snabbspolad tid av scenariotester.
"""

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from homecontrol import presence, rules
from homecontrol.clock import Clock
from homecontrol.connectors import (
    LampOutput,
    LightLevelObserved,
    Observation,
    SourceLost,
    TargetsObserved,
)
from homecontrol.home import Home, Target

logger = logging.getLogger(__name__)

Listener = Callable[[], None]


@dataclass(frozen=True)
class RoomView:
    room_id: str
    occupied: bool
    lux: float | None


@dataclass(frozen=True)
class LampView:
    lamp_id: str
    on: bool
    explanation: str


@dataclass(frozen=True)
class Snapshot:
    """Det som just nu går att visa om hemmet."""

    targets: tuple[Target, ...]
    rooms: tuple[RoomView, ...]
    lamps: tuple[LampView, ...]


class Controller:
    def __init__(self, home: Home, clock: Clock, outputs: Sequence[LampOutput]) -> None:
        self._home = home
        self._clock = clock
        self._outputs = tuple(outputs)
        self._targets: dict[str, tuple[Target, ...]] = {}
        self._lux: dict[str, float] = {}
        self._presence = presence.initial_presence(home, clock.now())
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
        return self._wake_at

    def observe(self, observation: Observation) -> None:
        """Ingången för alla källkopplingar."""
        match observation:
            case TargetsObserved(source_id=source_id, targets=targets):
                self._targets[source_id] = targets
                self._assess_presence()
            case SourceLost(source_id=source_id):
                self._targets.pop(source_id, None)
                self._assess_presence()
            case LightLevelObserved(room_id=room_id, lux=lux):
                if room_id not in self._room_ids:
                    logger.warning("Ljusnivå för okänt rum %r ignoreras", room_id)
                    return
                if not math.isfinite(lux) or lux < 0:
                    logger.warning("Ogiltig ljusnivå %r ignoreras", lux)
                    return
                self._lux[room_id] = lux
        self._evaluate()
        self._notify()

    def tick(self) -> None:
        """Låter regeln titta igen om klockan har nått `wake_at`."""
        if self._wake_at is not None and self._clock.now() >= self._wake_at:
            self._evaluate()
            self._notify()

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Anropar `listener` efter varje ändring. Returnerar en funktion som avslutar."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def snapshot(self) -> Snapshot:
        return Snapshot(
            targets=self._all_targets(),
            rooms=tuple(
                RoomView(room.id, self._presence[room.id].occupied, self._lux.get(room.id))
                for room in self._home.rooms
            ),
            lamps=tuple(
                LampView(lamp.id, self._lamps_on[lamp.id], self._explanations[lamp.id])
                for lamp in self._home.lamps
            ),
        )

    def _assess_presence(self) -> None:
        self._presence = presence.assess(
            self._home, self._targets, self._presence, self._clock.now()
        )

    def _all_targets(self) -> tuple[Target, ...]:
        return tuple(target for targets in self._targets.values() for target in targets)

    def _evaluate(self) -> None:
        room_states = {
            room.id: rules.RoomState(self._presence[room.id], self._lux.get(room.id))
            for room in self._home.rooms
        }
        result = rules.evaluate(self._home, room_states, self._lamps_on, self._clock.now())
        for command in result.commands:
            # Förklaringen säger var någon är, så den loggas bara när man felsöker.
            logger.info("%s %s", "Tänder" if command.on else "Släcker", command.lamp_id)
            logger.debug("%s: %s", command.lamp_id, command.reason)
            self._lamps_on[command.lamp_id] = command.on
            for output in self._outputs:
                # En trasig utgång får inte hindra de andra eller stoppa kärnan.
                try:
                    output.send(command)
                except Exception:
                    logger.exception("Lamputgången kunde inte ta emot %s", command)
        self._explanations = dict(result.explanations)
        self._wake_at = result.wake_at

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()
