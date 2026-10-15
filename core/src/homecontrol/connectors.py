"""Gemensamma gränssnitt för källkopplingar och lamputgångar.

En *källkoppling* (radar, Wi-Fi-CSI, ljussensor, simulering …) lämnar
observationer till kärnan genom en `ObservationSink`. En *lamputgång*
(Zigbee2MQTT, simulering …) tar emot `LampCommand` från kärnan.

Kärnan vet inget om vilken koppling som sitter i andra änden. Simuleringen
använder exakt samma ingång och utgång som de riktiga kopplingarna.

Kärnan körs i asyncios händelseloop och har inga lås. `ObservationSink` och
`LampOutput.send` anropas därför alltid från loopens tråd. En koppling som
har en egen tråd (till exempel ett MQTT-bibliotek) lämnar över med
`loop.call_soon_threadsafe`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from homecontrol.home import Target


@dataclass(frozen=True)
class TargetsObserved:
    """Alla mål (personer) som källan ser just nu, i hemmets koordinater.

    En tom tupel betyder att källan inte ser någon. Varje observation ersätter
    källans förra, så källan måste alltid skicka hela bilden.
    """

    source_id: str
    targets: tuple[Target, ...]


@dataclass(frozen=True)
class LightLevelObserved:
    """Uppmätt ljusnivå i ett rum, i lux."""

    source_id: str
    room_id: str
    lux: float


@dataclass(frozen=True)
class SourceLost:
    """Källan har tappat kontakten eller stoppats. Det den såg senast gäller inte längre."""

    source_id: str


Observation = TargetsObserved | LightLevelObserved | SourceLost
ObservationSink = Callable[[Observation], None]


class Source(Protocol):
    """En källkoppling. Den lämnar observationer till `sink` tills den stoppas.

    När källan tappar kontakten, eller stoppas, skickar den `SourceLost` så att
    ingen blir kvar i ett rum bara för att källan tystnade.
    """

    @property
    def source_id(self) -> str: ...

    def start(self, sink: ObservationSink) -> None: ...

    def stop(self) -> None: ...


@dataclass(frozen=True)
class LampCommand:
    """Tänd eller släck en lampa. `reason` förklarar beslutet i klartext."""

    lamp_id: str
    on: bool
    reason: str


class LampOutput(Protocol):
    """En lamputgång.

    `send` får inte blockera. En utgång som pratar med nätverket lägger
    kommandot i en egen kö och skickar det i bakgrunden.
    """

    def send(self, command: LampCommand) -> None: ...
