"""Regelmotorn: en ren funktion från läget i hemmet till lampkommandon och förklaringar.

Funktionen läser ingen klocka och gör inga anrop, så samma indata ger alltid
samma utdata. Den säger också när den vill bli tillfrågad igen (`wake_at`),
till exempel när ett tomt rum ska släckas.

Reglerna gäller ett rum i taget, och alla lampor i rummet styrs som en grupp.
Standardreglerna, med värdena från rummets inställningar, frågas i den här
ordningen:

1. **Okänt läge.** När en sensor i rummet har slutat svara lämnas lamporna som
   de är, så att ingen blir lämnad i mörker på grund av ett fel.
2. **Manuell styrning.** Har någon styrt en lampa för hand pausar automatiken
   i rummet tills det har varit tomt i hela släckfördröjningen.
3. **Dagsljus.** Släck när någon är i rummet och den justerade ljusnivån har
   varit över ljusgränsen (100 lux) i sträck i 10 minuter.
4. **Upptaget.** Tänd när någon är i rummet och den justerade ljusnivån är
   under mörkergränsen (30 lux). Ett rum utan fönster är alltid mörkt. Är
   lamporna dimrade går de upp igen.
5. **Tomt.** Dimra till hälften 30 sekunder innan lamporna släcks, och släck
   när rummet har varit tomt i 3 minuter.

Den *justerade* ljusnivån är den uppmätta minus det som rummets tända lampor
själva lyser upp sensorn med (lampkalibreringen, skalat efter ljusstyrkan).
Därför kan en lampas eget ljus aldrig släcka den.

Kärnan antar inget om en lampa vars läge är okänt. Regeln tänder den när
rummet ska tändas, men släcker eller dimrar den inte på chans.

Varje regel är ett objekt med metoden `decide` (se `Rule`). Alla regler
tillfrågas varje gång, så att de kan minnas tidpunkter och be om att bli
väckta, men det är den första regeln som fattar ett beslut som avgör vad som
händer med lamporna. En ny sorts regel, till exempel för tid på dygnet, läggs
in på rätt plats i listan som skickas till `evaluate`; de befintliga reglerna
behöver inte ändras.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from homecontrol.connectors import LampCommand
from homecontrol.fusion import Occupancy, Presence
from homecontrol.home import Home, Lamp, Room, RoomSettings

FULL_BRIGHTNESS = 100
"""Full ljusstyrka, i procent."""

MANUAL = "manuell"
"""I rummets minne: när någon senast styrde en lampa i rummet för hand."""
BRIGHT_SINCE = "ljust_sedan"
"""I rummets minne: sedan när dagsljuset har varit över ljusgränsen i sträck."""

Memory = Mapping[str, float]
"""Det reglerna minns om ett rum mellan två beslut: namngivna tidpunkter."""


@dataclass(frozen=True)
class LampState:
    """Vad kärnan vet om en lampa just nu."""

    on: bool | None
    """True tänd, False släckt, None okänt (lampan har inte rapporterat än)."""
    brightness: int = FULL_BRIGHTNESS
    """Ljusstyrkan i procent när lampan är tänd."""


UNKNOWN_LAMP = LampState(on=None)


@dataclass(frozen=True)
class RoomState:
    presence: Presence
    lux: float | None
    """Senast uppmätta ljusnivå, eller None om ingen källa har mätt den än."""
    memory: Memory = field(default_factory=dict)
    """Det regelmotorn minns om rummet sedan förra beslutet (se `RuleResult.memory`)."""


@dataclass(frozen=True)
class Situation:
    """Allt en regel vet om ett rum när den fattar sitt beslut."""

    room: Room
    presence: Presence
    measured_lux: float | None
    lamps: Mapping[str, LampState]
    """Läget för var och en av rummets lampor."""
    memory: Memory
    now: float

    @property
    def settings(self) -> RoomSettings:
        return self.room.settings

    @property
    def lit(self) -> bool:
        """Om någon av rummets lampor lyser."""
        return any(lamp.on is True for lamp in self.lamps.values())

    @property
    def dimmed(self) -> bool:
        """Om någon av rummets lampor lyser med lägre ljusstyrka än full."""
        return any(
            lamp.on is True and lamp.brightness < FULL_BRIGHTNESS for lamp in self.lamps.values()
        )

    @property
    def adjusted_lux(self) -> float | None:
        return adjusted_lux(self.room, self.measured_lux, self.lamps)


def adjusted_lux(room: Room, lux: float | None, lamps: Mapping[str, LampState]) -> float | None:
    """Den uppmätta ljusnivån minus det rummets tända lampor själva bidrar med.

    En lampa vars läge är okänt räknas som tänd, så att dess ljus aldrig tas
    för dagsljus. Resultatet är aldrig under 0.
    """
    if lux is None:
        return None
    lamp_light = sum(
        lamp.lux_contribution * _share_lit(lamps.get(lamp.id, UNKNOWN_LAMP)) for lamp in room.lamps
    )
    return max(0.0, lux - lamp_light)


def _share_lit(state: LampState) -> float:
    if state.on is None:
        return 1.0
    return state.brightness / FULL_BRIGHTNESS if state.on else 0.0


@dataclass(frozen=True)
class Switch:
    """Vad ett beslut gör med rummets lampor."""

    on: bool
    brightness: int = FULL_BRIGHTNESS
    lit_only: bool = False
    """Gäller bara lampor som redan lyser, så att en dimring inte tänder en släckt lampa."""


@dataclass(frozen=True)
class Decision:
    explanation: str
    """Varför, i klartext med värdena som beslutet bygger på."""
    switch: Switch | None = None
    """Vad som ska hända med lamporna, eller None för att lämna dem som de är."""


@dataclass(frozen=True)
class Verdict:
    """En regels svar. Utan beslut har regeln inget att säga, och nästa regel avgör."""

    decision: Decision | None = None
    remember: Mapping[str, float | None] = field(default_factory=dict)
    """Tidpunkter att minnas till nästa gång. None glömmer."""
    wake_at: float | None = None
    """När regeln kan komma fram till något nytt, om inget annat händer."""
    note: str | None = None
    """Något som har hänt utan att lamporna ändras, till exempel att automatiken tar över igen."""


NO_OPINION = Verdict()


class Rule(Protocol):
    def decide(self, situation: Situation) -> Verdict: ...


@dataclass(frozen=True)
class RoomEvent:
    """Ett beslut som ändrade något, för listan "Varför hände det?"."""

    room_id: str
    text: str


@dataclass(frozen=True)
class RuleResult:
    commands: tuple[LampCommand, ...]
    explanations: Mapping[str, str]
    """Varför rummets lampor är som de är, med rummets id som nyckel."""
    events: tuple[RoomEvent, ...]
    memory: Mapping[str, Memory]
    """Det reglerna minns om varje rum. Skickas tillbaka in nästa gång (`RoomState.memory`)."""
    wake_at: float | None
    """Nästa tidpunkt då regeln kan komma fram till något nytt, om inget annat händer."""


class UnknownRoom:
    """Okänt läge i rummet: ingen automatik, lamporna lämnas som de är."""

    def decide(self, situation: Situation) -> Verdict:
        if situation.presence.status is not Occupancy.UNKNOWN:
            return NO_OPINION
        return Verdict(
            Decision(
                f"Läget i {situation.room.name} är okänt eftersom en sensor inte svarar, "
                "så lamporna lämnas som de är."
            )
        )


class ManualControl:
    """Efter manuell styrning pausar automatiken tills rummet varit tomt i släckfördröjningen."""

    def decide(self, situation: Situation) -> Verdict:
        manual_since = situation.memory.get(MANUAL)
        if manual_since is None:
            return NO_OPINION
        name = situation.room.name
        delay = situation.settings.off_after_empty_seconds
        paused = Decision(
            f"Manuellt läge: automatiken i {name} pausar tills rummet har varit tomt "
            f"i {duration(delay)}."
        )
        if situation.presence.status is not Occupancy.VACANT:
            return Verdict(paused)
        resumes_at = max(situation.presence.since, manual_since) + delay
        if situation.now < resumes_at:
            return Verdict(paused, wake_at=resumes_at)
        return Verdict(
            remember={MANUAL: None},
            note=f"Automatiken tar över igen: {name} har varit tomt i {duration(delay)}.",
        )


class DaylightOff:
    """Släck när dagsljuset (lampornas eget ljus avräknat) har varit starkt en stund i sträck."""

    def decide(self, situation: Situation) -> Verdict:
        settings = situation.settings
        lux = situation.adjusted_lux
        if (
            situation.presence.status is not Occupancy.OCCUPIED
            or not situation.lit
            or settings.windowless
            or lux is None
            or lux <= settings.bright_above_lux
        ):
            return Verdict(remember={BRIGHT_SINCE: None})
        since = situation.memory.get(BRIGHT_SINCE, situation.now)
        hold = settings.daylight_off_after_seconds
        if situation.now - since < hold:
            return Verdict(remember={BRIGHT_SINCE: since}, wake_at=since + hold)
        return Verdict(
            Decision(
                f"Släcktes: dagsljuset i {situation.room.name} har varit över "
                f"{_lux(settings.bright_above_lux)} lux i {duration(hold)} "
                f"({_lux(lux)} lux nu, lampornas eget ljus avräknat).",
                Switch(on=False),
            ),
            remember={BRIGHT_SINCE: None},
        )


class OccupiedRoom:
    """Tänd när någon är i rummet och det är mörkt, och ta upp dimrade lampor igen."""

    def decide(self, situation: Situation) -> Verdict:
        if situation.presence.status is not Occupancy.OCCUPIED:
            return NO_OPINION
        name = situation.room.name
        settings = situation.settings
        if situation.dimmed:
            return Verdict(
                Decision(
                    f"Upp igen: någon är tillbaka i {name} under varningsdimringen.",
                    Switch(on=True, lit_only=True),
                )
            )
        if situation.lit:
            return Verdict(Decision(f"Någon är i {name}, och lamporna lyser."))
        if settings.windowless:
            return Verdict(
                Decision(
                    f"Tändes: någon är i {name}, som saknar fönster och alltid räknas som mörkt.",
                    Switch(on=True),
                )
            )
        lux = situation.adjusted_lux
        dark = settings.dark_below_lux
        if lux is None:
            return Verdict(Decision(f"Någon är i {name}, men ljusnivån där är okänd."))
        if lux < dark:
            return Verdict(
                Decision(
                    f"Tändes: någon är i {name} och det var mörkt "
                    f"({_lux(lux)} lux, under {_lux(dark)}).",
                    Switch(on=True),
                )
            )
        return Verdict(
            Decision(
                f"Någon är i {name}, men det är ljust nog ({_lux(lux)} lux, "
                f"mörkt är under {_lux(dark)})."
            )
        )


class VacantRoom:
    """Dimra som varning och släck sedan när rummet har varit tomt en stund."""

    def decide(self, situation: Situation) -> Verdict:
        if situation.presence.status is not Occupancy.VACANT:
            return NO_OPINION
        name = situation.room.name
        settings = situation.settings
        if not situation.lit:
            return Verdict(Decision(f"{name} är tomt."))
        off_after = settings.off_after_empty_seconds
        off_at = situation.presence.since + off_after
        if situation.now >= off_at:
            return Verdict(
                Decision(
                    f"Släcktes: {name} har varit tomt i {duration(off_after)}.",
                    Switch(on=False),
                )
            )
        dim = min(settings.dim_before_off_seconds, off_after)
        if dim == 0:
            return Verdict(
                Decision(
                    f"{name} är tomt. Lamporna släcks när det varit tomt i {duration(off_after)}."
                ),
                wake_at=off_at,
            )
        dim_at = off_at - dim
        if situation.now < dim_at:
            return Verdict(
                Decision(
                    f"{name} är tomt. Lamporna dimras när det varit tomt i "
                    f"{duration(off_after - dim)} och släcks när det varit tomt i "
                    f"{duration(off_after)}."
                ),
                wake_at=dim_at,
            )
        percent = settings.dim_percent
        return Verdict(
            Decision(
                f"Dimrades till {percent} %: {name} är tomt, och lamporna släcks när det varit "
                f"tomt i {duration(off_after)}. Rör dig i rummet för att behålla ljuset.",
                Switch(on=True, brightness=percent, lit_only=True),
            ),
            wake_at=off_at,
        )


DEFAULT_RULES: tuple[Rule, ...] = (
    UnknownRoom(),
    ManualControl(),
    DaylightOff(),
    OccupiedRoom(),
    VacantRoom(),
)


def evaluate(
    home: Home,
    rooms: Mapping[str, RoomState],
    lamps: Mapping[str, LampState],
    now: float,
    rules: Sequence[Rule] = DEFAULT_RULES,
) -> RuleResult:
    """Beslutar för varje rum. `lamps` är läget för hemmets lampor; en lampa som saknas är okänd."""
    commands: list[LampCommand] = []
    explanations: dict[str, str] = {}
    events: list[RoomEvent] = []
    memory: dict[str, Memory] = {}
    wake_times: list[float] = []

    for room in home.rooms:
        state = rooms[room.id]
        situation = Situation(
            room=room,
            presence=state.presence,
            measured_lux=state.lux,
            lamps={lamp.id: lamps.get(lamp.id, UNKNOWN_LAMP) for lamp in room.lamps},
            memory=state.memory,
            now=now,
        )
        decision: Decision | None = None
        remembered = dict(state.memory)
        for rule in rules:
            verdict = rule.decide(situation)
            for key, value in verdict.remember.items():
                if value is None:
                    remembered.pop(key, None)
                else:
                    remembered[key] = value
            if verdict.note is not None:
                events.append(RoomEvent(room.id, verdict.note))
            if verdict.wake_at is not None and verdict.wake_at > now:
                wake_times.append(verdict.wake_at)
            if decision is None:
                decision = verdict.decision
        if decision is None:
            decision = Decision(f"Ingen regel gäller i {room.name}, så lamporna lämnas som de är.")

        explanations[room.id] = decision.explanation
        memory[room.id] = remembered
        if decision.switch is not None:
            switched = _commands(situation, decision.switch, decision.explanation)
            commands.extend(switched)
            if switched:
                events.append(RoomEvent(room.id, decision.explanation))

    return RuleResult(
        commands=tuple(commands),
        explanations=explanations,
        events=tuple(events),
        memory=memory,
        wake_at=min(wake_times, default=None),
    )


def _commands(situation: Situation, switch: Switch, reason: str) -> list[LampCommand]:
    """Kommandon till de lampor i rummet som inte redan är som beslutet vill.

    En lampa vars läge är okänt tänds, men släcks eller dimras aldrig på chans.
    """
    commands = []
    for lamp_id, state in situation.lamps.items():
        if switch.lit_only and state.on is not True:
            continue
        if switch.on:
            needed = state.on is not True or state.brightness != switch.brightness
        else:
            needed = state.on is True
        if needed:
            commands.append(LampCommand(lamp_id, switch.on, reason, switch.brightness))
    return commands


# --- Manuell styrning ---


def manual_command(room: Room, lamp: Lamp, on: bool) -> LampCommand:
    """Kommandot när någon tänder eller släcker en lampa för hand, med förklaring."""
    action = "Tändes" if on else "Släcktes"
    return LampCommand(
        lamp.id,
        on,
        f"{action} för hand: {lamp.name}. Automatiken i {room.name} pausar tills rummet har "
        f"varit tomt i {duration(room.settings.off_after_empty_seconds)}.",
    )


def start_manual(memory: Memory, now: float) -> Memory:
    """Rummets minne efter att någon har styrt en lampa där för hand."""
    return {**memory, MANUAL: now}


def stop_manual(memory: Memory) -> Memory:
    """Rummets minne när automatiken ska ta över igen direkt."""
    return {key: value for key, value in memory.items() if key != MANUAL}


def is_manual(memory: Memory) -> bool:
    return MANUAL in memory


# --- Text ---


def duration(seconds: float) -> str:
    """Hur länge, i minuter och sekunder: "3 minuter", "2 minuter och 30 sekunder"."""
    minutes, rest = divmod(round(seconds), 60)
    parts = []
    if minutes:
        parts.append("1 minut" if minutes == 1 else f"{minutes} minuter")
    if rest or not minutes:
        parts.append("1 sekund" if rest == 1 else f"{rest} sekunder")
    return " och ".join(parts)


def _lux(value: float) -> str:
    """En ljusnivå med högst en decimal."""
    return f"{round(value, 1):g}"
