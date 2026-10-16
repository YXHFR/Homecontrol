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

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from homecontrol.home import Capability

logger = logging.getLogger(__name__)


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

    Med `node_id` gäller bortfallet bara den noden, och med `capability` bara
    den delen av noden, till exempel radarn på ett kort vars Wi-Fi-mätning
    fortfarande fungerar. Det gäller även en nod som källan aldrig har hörts
    från, som ett placerat kort som inte har svarat sedan kärnan startade.
    Utan `node_id` gäller det alla noder som senast hördes från källan.
    """

    source_id: str
    node_id: str | None = None
    capability: Capability | None = None


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

Defer = Callable[[Callable[[], None]], object]
"""Kör en funktion strax efter det som pågår, till exempel när kärnan har
lämnat alla observationer eller kommandon från ett och samma beslut."""


def defer_in_loop(callback: Callable[[], None]) -> None:
    """Kör `callback` när händelseloopen har gjort klart det den håller på med."""
    asyncio.get_running_loop().call_soon(callback)


class Source(Protocol):
    """En källkoppling. Den lämnar observationer till `sink` tills den stoppas.

    När källan tappar kontakten, eller stoppas, skickar den `SourceLost` så att
    ingen blir kvar i ett rum bara för att källan tystnade, och så att rummet
    i stället får läget okänt. Det gäller också när bara en del av källan
    tystnar, till exempel radarn på ett av korten: kärnan litar på att det
    radarn senast såg finns kvar tills radarn säger något annat.

    En källa får lämna observationer med flera `source_id`, till exempel ett
    per del av ett kort (se esphome.py), så att varje del kan tystna för sig.
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

    Varje utgång begränsar hur ofta en lampa får styras (se `LampLimit`).
    """

    def send(self, command: LampCommand) -> None: ...


LIMIT_BURST = 5
"""Så många kommandon i rad som en lampa tar emot innan gränsen slår till."""
LIMIT_PER_SECOND = 1.0
"""Så många kommandon per sekund som en lampa sedan tar emot i längden."""


class LampLimit:
    """Hur ofta varje lampa får styras, så att en trasig eller elak klient inte får den att blinka.

    Varje lampa har en hink med `burst` kommandon som fylls på med
    `per_second` kommandon i sekunden. Automatikens kommandon, kalibreringen
    och klick i livevyn kommer mycket glesare än så och märker aldrig gränsen.

    Kärnan skickar inte om ett beslut som inte har ändrats, så en utgång tappar
    aldrig ett stoppat kommando. Den skjuter upp det senaste kommandot till varje
    lampa och skickar det när `allow` säger ja (se `ZigbeeOutput` och
    `SimulatedLampOutput`). Att en lampa stoppas loggas en gång, inte för
    varje kommando.

    `now` är tiden i sekunder.
    """

    def __init__(
        self,
        now: Callable[[], float] = time.monotonic,
        *,
        burst: int = LIMIT_BURST,
        per_second: float = LIMIT_PER_SECOND,
    ) -> None:
        self._now = now
        self._burst = burst
        self._per_second = per_second
        self._buckets: dict[str, tuple[float, float]] = {}
        """Hur många kommandon som finns kvar i varje lampas hink, och när det räknades."""
        self._stopped: set[str] = set()
        """Lamporna som gränsen stoppar just nu, så att det bara loggas en gång."""

    def allow(self, lamp_ids: Iterable[str]) -> bool:
        """Om ett kommando till alla lamporna får gå ut nu. I så fall räknas det av för var och en.

        Ett gruppkommando räknas som ett kommando till varje lampa i gruppen.
        """
        now = self._now()
        lamp_ids = list(lamp_ids)
        left = {lamp_id: self._left(lamp_id, now) for lamp_id in lamp_ids}
        stopped = [lamp_id for lamp_id, tokens in left.items() if tokens < 1]
        if stopped:
            for lamp_id in stopped:
                if lamp_id not in self._stopped:
                    self._stopped.add(lamp_id)
                    logger.warning(
                        "%s styrs oftare än %g gånger i sekunden; kommandona skjuts upp",
                        lamp_id,
                        self._per_second,
                    )
            return False
        for lamp_id, tokens in left.items():
            self._buckets[lamp_id] = (tokens - 1, now)
            self._stopped.discard(lamp_id)
        return True

    def _left(self, lamp_id: str, now: float) -> float:
        tokens, counted_at = self._buckets.get(lamp_id, (self._burst, now))
        return min(self._burst, tokens + (now - counted_at) * self._per_second)
