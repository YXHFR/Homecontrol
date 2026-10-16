"""Simulering: en låtsaskälla, virtuella lampor och simuleringsläget i livevyn.

Låtsaskällan styrs från livevyn eller från scenariotester och lämnar sina
observationer genom samma ingång som riktiga källor. Kärnan kan alltså inte
se skillnad på en låtsasperson och en person som radarn ser.
"""

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from itertools import count
from typing import Literal, get_args

from homecontrol.clock import SpeedControl
from homecontrol.connectors import (
    LampCommand,
    LightLevelObserved,
    ObservationSink,
    SourceLost,
    TargetsObserved,
)
from homecontrol.home import Point, Target

logger = logging.getLogger(__name__)

Listener = Callable[[], None]

MAX_PERSONS = 10
"""Fler låtsaspersoner än så här behövs inte för att prova ett hem."""

Speed = Literal[1, 10, 60]
"""Tidsfarterna som går att välja. Vid 60× går en minut på en sekund."""
SPEEDS: tuple[Speed, ...] = get_args(Speed)

DEFAULT_DAYLIGHT_LUX = 10.0
"""Dagsljuset när simuleringen startar: mörkt, så att lamporna tänds."""


class TooManyPersons(Exception):
    pass


class SimulatedSource:
    """En källkoppling med låtsaspersoner och låtsat dagsljus.

    Varje person får ett id när den läggs till, så att den kan flyttas och tas
    bort. Id:t stannar i simuleringen; kärnan får bara se var personerna är.
    """

    def __init__(self, source_id: str = "simulering", room_ids: Iterable[str] = ()) -> None:
        self._source_id = source_id
        self._room_ids = tuple(room_ids)
        self._sink: ObservationSink | None = None
        self._persons: dict[str, Target] = {}
        self._next_id = count(1)
        self._daylight: float | None = None

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def persons(self) -> Mapping[str, Target]:
        return dict(self._persons)

    @property
    def daylight(self) -> float | None:
        """Dagsljuset i alla rum, eller None om det inte är satt."""
        return self._daylight

    def start(self, sink: ObservationSink) -> None:
        self._sink = sink
        self._emit_targets()
        if self._daylight is not None:
            self.set_daylight(self._daylight)

    def stop(self) -> None:
        """Källan tystnar och glömmer sina personer, som en radar som stängs av."""
        self._persons.clear()
        if self._sink is not None:
            self._sink(SourceLost(self._source_id))
        self._sink = None

    def add_person(self, floor_id: str, position: Point) -> str:
        """Lägger till en person och returnerar dess id."""
        if len(self._persons) >= MAX_PERSONS:
            raise TooManyPersons(f"Simuleringen har redan {MAX_PERSONS} personer")
        person_id = str(next(self._next_id))
        self._persons[person_id] = Target(floor_id, position)
        self._emit_targets()
        return person_id

    def move_person(self, person_id: str, floor_id: str, position: Point) -> None:
        """Flyttar en person. En person som inte finns (längre) lämnas i fred."""
        if person_id in self._persons:
            self._persons[person_id] = Target(floor_id, position)
            self._emit_targets()

    def remove_person(self, person_id: str) -> None:
        if self._persons.pop(person_id, None) is not None:
            self._emit_targets()

    def set_daylight(self, lux: float) -> None:
        """Samma ljusnivå i alla rum, som när solen går upp eller ner."""
        self._daylight = lux
        for room_id in self._room_ids:
            self.set_light_level(room_id, lux)

    def set_light_level(self, room_id: str, lux: float) -> None:
        """Ljusnivån i ett enda rum."""
        if self._sink is not None:
            self._sink(LightLevelObserved(self._source_id, room_id, lux))

    def _emit_targets(self) -> None:
        if self._sink is not None:
            self._sink(TargetsObserved(self._source_id, tuple(self._persons.values())))


class SimulatedLampOutput:
    """En lamputgång som bara minns vad lamporna senast fick för kommando."""

    def __init__(self) -> None:
        self.states: dict[str, bool] = {}
        self.history: list[LampCommand] = []

    def send(self, command: LampCommand) -> None:
        self.states[command.lamp_id] = command.on
        self.history.append(command)


@dataclass(frozen=True)
class SimulationView:
    """Det som just nu går att visa om simuleringen."""

    enabled: bool
    speed: float
    daylight: float | None
    persons: Mapping[str, Target]


class Simulation:
    """Simuleringsläget: slår på och av låtsaskällan och styr dagsljus och tidsfart.

    När läget är avslaget tystnar låtsaskällan, precis som en radar som stängs
    av, och tiden går i vanlig takt. Kommandon till personerna och dagsljuset
    ignoreras då.

    Tidsfarten gäller hela kärnan, eftersom kärnan har en enda klocka. Det går
    bra så länge alla lampor är virtuella; med riktiga lampor skulle de släckas
    60 gånger för tidigt.

    `floor_ids` är hemmets våningar. Personer på andra våningar avvisas. Utan
    `daylight` lämnas ljusnivån i rummen okänd tills någon ställer den.
    """

    def __init__(
        self,
        source: SimulatedSource,
        sink: ObservationSink,
        clock: SpeedControl,
        *,
        floor_ids: Iterable[str],
        enabled: bool = True,
        daylight: float | None = DEFAULT_DAYLIGHT_LUX,
    ) -> None:
        self._source = source
        self._sink = sink
        self._clock = clock
        self._floor_ids = frozenset(floor_ids)
        self._enabled = False
        self._listeners: list[Listener] = []
        if daylight is not None:
            source.set_daylight(daylight)  # minns värdet tills källan startar
        self.set_enabled(enabled)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def view(self) -> SimulationView:
        return SimulationView(
            enabled=self._enabled,
            speed=self._clock.speed,
            daylight=self._source.daylight,
            persons=self._source.persons,
        )

    def set_enabled(self, enabled: bool) -> None:
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if enabled:
            self._source.start(self._sink)
        else:
            self._source.stop()
            self._clock.set_speed(1)
        self._notify()

    def add_person(self, floor_id: str, position: Point) -> str | None:
        """Lägger till en person och returnerar dess id, eller None om det inte gick."""
        if not self._enabled or not self._known_floor(floor_id):
            return None
        try:
            person_id = self._source.add_person(floor_id, position)
        except TooManyPersons:
            logger.info("Simuleringen har redan %d personer", MAX_PERSONS)
            return None
        self._notify()
        return person_id

    def move_person(self, person_id: str, floor_id: str, position: Point) -> None:
        if self._enabled and self._known_floor(floor_id):
            self._source.move_person(person_id, floor_id, position)
            self._notify()

    def remove_person(self, person_id: str) -> None:
        if self._enabled:
            self._source.remove_person(person_id)
            self._notify()

    def set_daylight(self, lux: float) -> None:
        if self._enabled:
            self._source.set_daylight(lux)
            self._notify()

    def set_speed(self, speed: Speed) -> None:
        if speed not in SPEEDS:
            raise ValueError(f"Tidsfarten ska vara en av {SPEEDS}, inte {speed}")
        if self._enabled:
            self._clock.set_speed(speed)
            self._notify()

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Anropar `listener` efter varje ändring. Returnerar en funktion som avslutar."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _known_floor(self, floor_id: str) -> bool:
        if floor_id in self._floor_ids:
            return True
        logger.warning("Person på okänd våning ignoreras")
        return False

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()
