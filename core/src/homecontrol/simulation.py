"""Simulering: en låtsaskälla och en virtuell lamputgång.

Låtsaskällan styrs från livevyn eller från scenariotester och lämnar sina
observationer genom samma ingång som riktiga källor.
"""

from homecontrol.connectors import (
    LampCommand,
    LightLevelObserved,
    ObservationSink,
    SourceLost,
    Target,
    TargetsObserved,
)
from homecontrol.home import Point


class SimulatedSource:
    """En källkoppling med en låtsasperson och låtsade ljusnivåer."""

    def __init__(self, source_id: str = "simulering") -> None:
        self._source_id = source_id
        self._sink: ObservationSink | None = None
        self._person: Target | None = None

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def person(self) -> Target | None:
        return self._person

    def start(self, sink: ObservationSink) -> None:
        self._sink = sink
        self._emit_targets()

    def stop(self) -> None:
        if self._sink is not None:
            self._sink(SourceLost(self._source_id))
        self._sink = None

    def move_person(self, floor_id: str, position: Point) -> None:
        self._person = Target(floor_id, position)
        self._emit_targets()

    def remove_person(self) -> None:
        self._person = None
        self._emit_targets()

    def set_light_level(self, room_id: str, lux: float) -> None:
        if self._sink is not None:
            self._sink(LightLevelObserved(self._source_id, room_id, lux))

    def _emit_targets(self) -> None:
        if self._sink is not None:
            targets = () if self._person is None else (self._person,)
            self._sink(TargetsObserved(self._source_id, targets))


class SimulatedLampOutput:
    """En lamputgång som bara minns vad lamporna senast fick för kommando."""

    def __init__(self) -> None:
        self.states: dict[str, bool] = {}
        self.history: list[LampCommand] = []

    def send(self, command: LampCommand) -> None:
        self.states[command.lamp_id] = command.on
        self.history.append(command)
