"""Gemensamma gränssnitt för källkopplingar och lamputgångar.

En *källkoppling* (radar, Wi-Fi-CSI, ljussensor, simulering …) lämnar
observationer till kärnan genom en `ObservationSink`. En *lamputgång*
(Zigbee2MQTT, simulering …) tar emot `LampCommand` från kärnan och berättar
hur lamporna är med `LampStateReported` genom samma ingång.

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


@dataclass(frozen=True)
class RadarTarget:
    """Ett mål som radarn ser, i meter i radarns egna koordinater.

    y är rakt fram från radarn och x åt sidan (se `Node.radar_to_home`).
    Kopplingen räknar om radarns egna enheter, till exempel millimeter, till meter.
    """

    x: float
    y: float


@dataclass(frozen=True)
class RadarTargetsObserved:
    """Alla mål som en nods radar ser just nu.

    En tom tupel betyder att radarn inte ser någon. Varje observation ersätter
    nodens förra, så källan måste alltid skicka hela bilden. Ordningen är
    radarns egen numrering av målen. Den byter ofta när två personer möts och
    används därför inte till något: bara sammanvägningen ser målen, och den
    bygger egna, anonyma spår av dem.
    """

    source_id: str
    node_id: str
    targets: tuple[RadarTarget, ...]


@dataclass(frozen=True)
class MotionObserved:
    """Om nodens Wi-Fi-mätning (CSI) känner rörelse i rummet just nu.

    `score` är hur säker mätningen är, från 0 till 1.
    """

    source_id: str
    node_id: str
    moving: bool
    score: float = 1.0


@dataclass(frozen=True)
class LightLevelObserved:
    """Uppmätt ljusnivå i ett rum, i lux."""

    source_id: str
    room_id: str
    lux: float


@dataclass(frozen=True)
class SourceLost:
    """Källan har tappat kontakten eller stoppats. Det den såg senast gäller inte längre.

    Rummen som källans noder sitter i får läget okänt tills källan hörs av igen.
    """

    source_id: str


@dataclass(frozen=True)
class LampStateReported:
    """Lampans eget besked om sitt läge, från lamputgången.

    Utgången skickar det när den startar, när lampan svarar igen och när
    lampan har ändrats. `on` är None när lampan inte svarar och läget är
    okänt; kärnan visar då att lampan inte svarar. Kärnan antar aldrig att en
    lampa är släckt; tills lampan har rapporterat eller kärnan själv har styrt
    den är läget okänt.

    Bara utgången vet om beskedet är lampans svar på kärnans eget kommando
    eller en ändring utifrån, så den talar om det med `external`.
    """

    lamp_id: str
    on: bool | None
    brightness: int = 100
    """Ljusstyrkan i procent (1–100) när lampan är tänd."""
    external: bool = False
    """Lampan ändrades utanför kärnan, till exempel med fjärrkontroll eller app.

    Kärnan räknar det som manuell styrning (se `Controller.set_lamp`). Lampans
    svar på kärnans kommandon och läget när lampan läses är inte ändringar utifrån.
    """


Observation = (
    RadarTargetsObserved | MotionObserved | LightLevelObserved | SourceLost | LampStateReported
)
ObservationSink = Callable[[Observation], None]


class Source(Protocol):
    """En källkoppling. Den lämnar observationer till `sink` tills den stoppas.

    När källan tappar kontakten, eller stoppas, skickar den `SourceLost` så att
    ingen blir kvar i ett rum bara för att källan tystnade, och så att rummet
    i stället får läget okänt. Det gäller också när bara en del av källan
    tystnar, till exempel radarn på ett av korten: kärnan litar på att det
    radarn senast såg finns kvar tills radarn säger något annat.
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
    brightness: int = 100
    """Ljusstyrkan i procent (1–100) när lampan tänds, till exempel lägre under en dimring."""
    color_temp: int | None = None
    """Färgtemperaturen i kelvin, eller None för att lämna den. Ingen regel sätter den än."""


class LampOutput(Protocol):
    """En lamputgång.

    `send` får inte blockera. En utgång som pratar med nätverket lägger
    kommandot i en egen kö och skickar det i bakgrunden.

    Kärnan lämnar varje kommando till alla utgångar. En utgång styr bara sina
    egna lampor och hoppar över kommandon till andra.
    """

    def send(self, command: LampCommand) -> None: ...
