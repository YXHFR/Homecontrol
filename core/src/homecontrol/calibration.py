"""Lampkalibreringen: mäter hur mycket varje lampa själv lyser upp rummets ljussensor.

Kalibreringen görs ett rum i taget. Först släcks alla rummets lampor och
ljusnivån mäts. Sedan tänds lamporna en i taget på full ljusstyrka, och
efter varje lampa släcks den igen och ljusnivån mäts på nytt. Lampans bidrag
är skillnaden mellan ljuset med lampan tänd och medelvärdet av ljuset före
och efter. Regelmotorn drar sedan av bidraget (skalat efter ljusstyrkan) från
den uppmätta ljusnivån (se `rules.adjusted_lux`).

Varje mätning väntar först ut lampans övergång, och sedan tills sensorn har
visat samma ljusnivå en stund. Mätningen avbryts om ljusnivån aldrig blir
jämnt, om dagsljuset ändras kraftigt mellan två mätningar med lamporna
släckta (till exempel av ett moln), om sensorn tappas eller om en lampa
slutar svara.

Har rummet flera rumskort med ljussensor mäts lamporna vid det kort som mätte
senast när kalibreringen startade. Reglerna drar av samma bidrag oavsett
vilket kort som mätte, så med flera kort blir avräkningen bara ungefärlig.

`RoomCalibration` är bara själva mätningen och gör inget av sig själv. Kärnan
(`Controller`) lämnar ljusnivåerna till den, frågar vilka lampkommandon som
behövs och väcker den vid `wake_at`. Det gör att mätningen går att köra i
snabbspolad tid i scenariotesterna, med samma klocka som reglerna.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from homecontrol.connectors import LampCommand
from homecontrol.home import Home, Lamp, Room

SETTLE_SECONDS = 3.0
"""Så länge efter ett lampkommando som ljuset inte mäts, medan lampan tänds eller släcks.

Zigbee-lamporna tonar över på en sekund (se zigbee.py), och kommandot ska hinna fram.
"""
STABLE_SECONDS = 12.0
"""Så länge ljusnivån ska ha legat still innan den räknas som mätt.

Rumskortets ljussensor mäter var femte sekund, så det blir minst två mätningar.
"""
MIN_SAMPLES = 2
"""Så många gånger sensorn ska ha mätt samma ljusnivå innan den räknas som mätt."""
STEP_TIMEOUT_SECONDS = 90.0
"""Har ljusnivån inte legat still efter så här lång tid avbryts kalibreringen."""
STEADY_LUX = 2.0
"""Så mycket ljusnivån får skifta under en mätning, i lux, och ändå räknas som jämn."""
STEADY_SHARE = 0.05
"""Som `STEADY_LUX`, men som andel av ljusnivån. Den större gränsen gäller."""
DRIFT_LUX = 10.0
"""Så mycket ljuset med lamporna släckta får ändras mellan två mätningar, i lux."""
DRIFT_SHARE = 0.2
"""Som `DRIFT_LUX`, men som andel av ljusnivån. Den större gränsen gäller."""


class Phase(StrEnum):
    RUNNING = "pågår"
    DONE = "klar"
    FAILED = "avbruten"


@dataclass(frozen=True)
class CalibrationView:
    """Det som just nu går att visa om den pågående eller senaste kalibreringen."""

    room_id: str
    phase: Phase
    text: str
    """Vad som händer just nu, varför kalibreringen avbröts eller vad den kom fram till."""
    step: int
    """Mätningen som pågår, från 1, eller antalet mätningar när kalibreringen är klar."""
    steps: int
    """Hur många mätningar kalibreringen har."""
    results: Mapping[str, float] = field(default_factory=dict)
    """Varje mätt lampas bidrag i lux vid full ljusstyrka, när kalibreringen är klar."""


@dataclass(frozen=True)
class LampSetting:
    """Hur en lampa var innan kalibreringen, så att den kan ställas tillbaka."""

    on: bool
    brightness: int


class _Measurement:
    """En mätning av ljusnivån: väntar ut övergången och sedan tills ljuset är jämnt.

    Bara det sensorn mäter efter övergången räknas, så att ett värde från
    innan lamporna styrdes aldrig kommer med.
    """

    def __init__(self, started_at: float) -> None:
        self.started_at = started_at
        self._window_start: float | None = None
        """När ljuset senast ändrades efter övergången, eller None innan sensorn har mätt."""
        self._reference = 0.0
        self._samples: list[float] = []

    @property
    def settled_at(self) -> float:
        return self.started_at + SETTLE_SECONDS

    @property
    def gives_up_at(self) -> float:
        return self.started_at + STEP_TIMEOUT_SECONDS

    @property
    def heard(self) -> bool:
        """Sensorn har mätt efter övergången."""
        return self._window_start is not None

    def reading(self, lux: float, now: float) -> None:
        if now < self.settled_at:
            return
        if self._window_start is None or not _steady(lux, self._reference):
            self._window_start = now
            self._reference = lux
            self._samples = [lux]
        else:
            self._samples.append(lux)

    def result(self, now: float) -> float | None:
        """Den mätta ljusnivån, eller None om ljuset inte har varit jämnt länge nog än.

        Ljuset ska ha varit jämnt i `STABLE_SECONDS`, och sensorn ska ha mätt
        minst två gånger under tiden.
        """
        if self._window_start is None or now - self._window_start < STABLE_SECONDS:
            return None
        if len(self._samples) < MIN_SAMPLES:
            return None
        return sum(self._samples) / len(self._samples)

    def wake_at(self, now: float) -> float:
        """När mätningen kan vara klar, om inget nytt mäts innan dess.

        Väntar mätningen på fler avläsningar är det när den ger upp; nästa
        avläsning väcker kärnan ändå.
        """
        if now < self.settled_at:
            return self.settled_at
        if self._window_start is not None and len(self._samples) >= MIN_SAMPLES:
            return min(self._window_start + STABLE_SECONDS, self.gives_up_at)
        return self.gives_up_at


@dataclass(frozen=True)
class _Step:
    lamp_id: str | None
    """Lampan som är tänd under mätningen, eller None när alla är släckta."""


class RoomCalibration:
    """Kalibreringen av ett rum, från första till sista mätningen.

    Rummet måste ha lampor, och `before` ska ha läget för var och en av dem.
    `source_id` är källan vars ljussensor mäter; ett rum med flera kort mäts
    med ett av dem, så att deras värden inte blandas.
    """

    def __init__(
        self, room: Room, before: Mapping[str, LampSetting], source_id: str, now: float
    ) -> None:
        self.room = room
        self.source_id = source_id
        self.before = dict(before)
        self._lamps = room.lamps
        # Släckt, sedan varje lampa tänd och släckt igen.
        self._steps = (
            _Step(None),
            *(step for lamp in self._lamps for step in (_Step(lamp.id), _Step(None))),
        )
        self._index = 0
        self._measured: list[float] = []
        self._measurement = _Measurement(now)
        self._failure: str | None = None

    @property
    def room_id(self) -> str:
        return self.room.id

    @property
    def finished(self) -> bool:
        return self._failure is not None or self._index >= len(self._steps)

    @property
    def failed(self) -> bool:
        return self._failure is not None

    def commands(self) -> list[LampCommand]:
        """Lampkommandona för mätningen som pågår: bara den mätta lampan lyser."""
        if self.finished:
            return []
        step = self._steps[self._index]
        lamp = None if step.lamp_id is None else self._lamp(step.lamp_id)
        reason = (
            f"Lampkalibrering i {self.room.name}: alla lampor släcks för att mäta ljuset utan dem."
            if lamp is None
            else f"Lampkalibrering i {self.room.name}: {lamp.name} tänds för att mäta dess ljus."
        )
        return [
            LampCommand(other.id, on=other.id == step.lamp_id, reason=reason)
            for other in self._lamps
        ]

    def reading(self, source_id: str, lux: float, now: float) -> None:
        """En sensor i rummet har mätt ljusnivån. Bara kalibreringens egen källa räknas."""
        if source_id == self.source_id and not self.finished:
            self._measurement.reading(lux, now)

    def update(self, lux: float | None, now: float) -> bool:
        """Låter mätningen gå vidare. Returnerar True när lamporna ska styras om.

        `lux` är den senast mätta ljusnivån från kalibreringens källa, eller
        None om sensorn har tappats.
        """
        if self.finished:
            return False
        if lux is None:
            self.fail("ljussensorn i rummet slutade svara")
            return False
        value = self._measurement.result(now)
        if value is None:
            if now >= self._measurement.gives_up_at:
                self.fail(
                    "ljuset blev aldrig jämnt. Försök igen när ljuset är stadigare, "
                    "gärna när det är mörkt ute"
                    if self._measurement.heard
                    else "ljussensorn i rummet har inte mätt något på en stund"
                )
            return False
        self._measured.append(value)
        if self._steps[self._index].lamp_id is None and self._index > 0:
            # Jämför med förra mätningen med släckta lampor, två mätningar bakåt.
            before, after = self._measured[-3], value
            if not _steady(after, before, DRIFT_LUX, DRIFT_SHARE):
                self.fail(
                    f"dagsljuset ändrades under mätningen (från {_format(before)} till "
                    f"{_format(after)} lux), till exempel av ett moln. Försök igen när ljuset "
                    "är jämnare, gärna när det är mörkt ute"
                )
                return False
        self._index += 1
        self._measurement = _Measurement(now)
        return not self.finished

    def fail(self, reason: str) -> None:
        """Avbryter kalibreringen. `reason` säger varför, utan stor bokstav och punkt."""
        if not self.finished:
            self._failure = reason

    def wake_at(self, now: float) -> float | None:
        return None if self.finished else self._measurement.wake_at(now)

    def results(self) -> dict[str, float]:
        """Varje lampas bidrag i lux vid full ljusstyrka. Bara när kalibreringen är klar."""
        if not self.finished or self.failed:
            return {}
        results = {}
        for number, lamp in enumerate(self._lamps):
            off_before, lit, off_after = self._measured[2 * number : 2 * number + 3]
            results[lamp.id] = round(max(0.0, lit - (off_before + off_after) / 2), 1)
        return results

    def restore_commands(self) -> list[LampCommand]:
        """Kommandona som ställer tillbaka lamporna som de var innan kalibreringen."""
        reason = f"Lampkalibreringen i {self.room.name} är slut, så lamporna ställs tillbaka."
        return [
            LampCommand(lamp_id, setting.on, reason, setting.brightness)
            for lamp_id, setting in self.before.items()
        ]

    def view(self) -> CalibrationView:
        steps = len(self._steps)
        if self._failure is not None:
            return CalibrationView(
                self.room.id,
                Phase.FAILED,
                f"Lampkalibreringen i {self.room.name} avbröts: {self._failure}.",
                min(self._index + 1, steps),
                steps,
            )
        if self.finished:
            return CalibrationView(
                self.room.id,
                Phase.DONE,
                f"Lampkalibreringen i {self.room.name} är klar. Reglerna räknar nu bort "
                "lampornas eget ljus.",
                steps,
                steps,
                self.results(),
            )
        step = self._steps[self._index]
        text = (
            "Mäter ljuset med alla lampor släckta …"
            if step.lamp_id is None
            else f"Mäter ljuset från {self._lamp(step.lamp_id).name} …"
        )
        return CalibrationView(self.room.id, Phase.RUNNING, text, self._index + 1, steps)

    def _lamp(self, lamp_id: str) -> Lamp:
        return next(lamp for lamp in self._lamps if lamp.id == lamp_id)


def with_contributions(home: Home, results: Mapping[str, float]) -> Home:
    """Hemmet med lampornas uppmätta bidrag. Lampor som inte finns längre hoppas över."""
    floors = tuple(
        floor.model_copy(
            update={
                "rooms": tuple(
                    room.model_copy(
                        update={
                            "lamps": tuple(
                                lamp.model_copy(update={"lux_contribution": results[lamp.id]})
                                if lamp.id in results
                                else lamp
                                for lamp in room.lamps
                            )
                        }
                    )
                    for room in floor.rooms
                )
            }
        )
        for floor in home.floors
    )
    return home.model_copy(update={"floors": floors})


def _steady(
    lux: float, reference: float, absolute: float = STEADY_LUX, share: float = STEADY_SHARE
) -> bool:
    return math.fabs(lux - reference) <= max(absolute, share * reference)


def _format(value: float) -> str:
    return f"{round(value, 1):g}"
