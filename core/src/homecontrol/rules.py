"""Regelmotorn: en ren funktion från läget i hemmet till lampkommandon och förklaringar.

Funktionen läser ingen klocka och gör inga anrop, så samma indata ger alltid
samma utdata. Den säger också när den vill bli tillfrågad igen (`wake_at`),
till exempel när ett tomt rum ska släckas.

Reglerna gäller ett rum i taget, och alla lampor i rummet styrs som en grupp:
ett beslut gäller i dag alltid alla lampor i rummet. En framtida regel för
zonlampor behöver därför kunna begränsa ett beslut till några av lamporna.
Standardreglerna frågas i den här ordningen. Värdena inom parentes är
standardvärden; varje rum har egna i sina inställningar.

1. **Lampkalibrering.** Medan lampornas eget ljus mäts (se calibration.py)
   pausar automatiken i rummet, och kalibreringen styr lamporna.
2. **Okänt läge.** När en sensor eller lampa i rummet har slutat svara lämnas
   lamporna som de är, så att ingen blir lämnad i mörker på grund av ett fel.
3. **Manuell styrning.** Har någon styrt en lampa för hand pausar automatiken
   i rummet tills det har varit tomt i hela släckfördröjningen.
4. **Dagsljus.** Släck när någon är i rummet och den justerade ljusnivån har
   varit över ljusgränsen (100 lux) i sträck i 10 minuter.
5. **Upptaget.** Tänd när någon är i rummet och den justerade ljusnivån är
   under mörkergränsen (30 lux). Ett rum utan fönster är alltid mörkt. Är
   lamporna dimrade går de upp igen.
6. **Tomt.** Dimra till hälften 30 sekunder innan lamporna släcks, och släck
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
in på rätt plats i `DEFAULT_RULES` (eller i listan som skickas till
`evaluate`); de befintliga reglerna behöver inte ändras.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

from homecontrol.connectors import LampCommand
from homecontrol.fusion import Occupancy, Presence
from homecontrol.home import Home, Lamp, Room, RoomSettings

FULL_BRIGHTNESS = 100
"""Full ljusstyrka, i procent."""

CALIBRATING = "kalibreras_sedan"
"""I rummets minne: sedan när lampkalibreringen pågår i rummet."""
MANUAL = "manuell"
"""I rummets minne: när någon senast styrde en lampa i rummet för hand."""
BRIGHT_SINCE = "ljust_sedan"
"""I rummets minne: sedan när dagsljuset har varit över ljusgränsen i sträck."""
WARNING_DIMMED = "varningsdimrat_sedan"
"""I rummets minne: sedan när lamporna är dimrade som varning för att rummet är tomt.

Lamporna kan lysa svagare av andra skäl (en annan regel, en fjärrkontroll), så
det är bara den här tidpunkten som säger att närvaro ska ge fullt ljus igen.
"""

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
    silent_lamps: tuple[str, ...] = ()
    """Namnen på rummets lampor som inte svarar. Då är läget okänt, för förklaringen."""


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
    silent_lamps: tuple[str, ...] = ()
    """Namnen på rummets lampor som inte svarar."""

    @property
    def settings(self) -> RoomSettings:
        return self.room.settings

    @property
    def lit(self) -> bool:
        """Om någon av rummets lampor lyser."""
        return any(lamp.on is True for lamp in self.lamps.values())

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
    dim_only: bool = False
    """Gäller bara lampor som lyser starkare, så att en dimring aldrig gör en lampa ljusare."""


@dataclass(frozen=True)
class Decision:
    """Ett beslut om rummets lampor. Det första beslutet i regelordningen gäller."""

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


class Calibrating:
    """Under lampkalibreringen pausar automatiken i rummet, och kalibreringen styr lamporna."""

    def decide(self, situation: Situation) -> Verdict:
        if CALIBRATING not in situation.memory:
            return NO_OPINION
        return Verdict(
            Decision(
                f"Lampkalibrering pågår i {situation.room.name}: automatiken pausar tills "
                "den är klar, och lamporna tänds och släcks en i taget."
            )
        )


class UnknownRoom:
    """Okänt läge i rummet: ingen automatik, lamporna lämnas som de är."""

    def decide(self, situation: Situation) -> Verdict:
        if situation.presence.status is not Occupancy.UNKNOWN:
            return NO_OPINION
        silent = situation.silent_lamps
        cause = f"{_join(silent)} inte svarar" if silent else "en sensor inte svarar"
        return Verdict(
            Decision(
                f"Läget i {situation.room.name} är okänt eftersom {cause}, "
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
        status = situation.presence.status
        if status is Occupancy.OCCUPIED and WARNING_DIMMED in situation.memory and situation.lit:
            return Verdict(_back_to_full(name), remember={WARNING_DIMMED: None})
        if status is not Occupancy.VACANT:
            return Verdict(paused)
        resumes_at = max(situation.presence.since, manual_since) + delay
        if situation.now >= resumes_at:
            return Verdict(
                remember={MANUAL: None},
                note=f"Automatiken tar över igen: {name} har varit tomt i {duration(delay)}.",
            )
        # När automatiken tar över i det tomma rummet släcks lamporna, så de
        # dimras som varning innan dess, precis som annars.
        settings = situation.settings
        dim_at = resumes_at - min(settings.dim_before_off_seconds, delay)
        if situation.now < dim_at or not situation.lit or dim_at == resumes_at:
            return Verdict(paused, wake_at=dim_at if situation.now < dim_at else resumes_at)
        return Verdict(
            _warning_dim(
                name, settings, f"automatiken tar över om {duration(resumes_at - dim_at)}"
            ),
            remember={WARNING_DIMMED: situation.memory.get(WARNING_DIMMED, situation.now)},
            wake_at=resumes_at,
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
                f"{_format_lux(settings.bright_above_lux)} lux i {duration(hold)} "
                f"({_format_lux(lux)} lux nu, lampornas eget ljus avräknat).",
                Switch(on=False),
            ),
            remember={BRIGHT_SINCE: None},
        )


class OccupiedRoom:
    """Tänd när någon är i rummet och det är mörkt, och ta upp varningsdimrade lampor igen."""

    def decide(self, situation: Situation) -> Verdict:
        if situation.presence.status is not Occupancy.OCCUPIED:
            return NO_OPINION
        verdict = self._decide(situation)
        return replace(verdict, remember={WARNING_DIMMED: None, **verdict.remember})

    def _decide(self, situation: Situation) -> Verdict:
        name = situation.room.name
        settings = situation.settings
        if WARNING_DIMMED in situation.memory and situation.lit:
            return Verdict(_back_to_full(name), remember={WARNING_DIMMED: None})
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
                    f"({_format_lux(lux)} lux, under {_format_lux(dark)}).",
                    Switch(on=True),
                )
            )
        return Verdict(
            Decision(
                f"Någon är i {name}, men det är ljust nog ({_format_lux(lux)} lux, "
                f"mörkt är under {_format_lux(dark)})."
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
            return Verdict(Decision(f"{name} är tomt."), remember={WARNING_DIMMED: None})
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
        return Verdict(
            _warning_dim(
                name, settings, f"lamporna släcks när det varit tomt i {duration(off_after)}"
            ),
            remember={WARNING_DIMMED: situation.memory.get(WARNING_DIMMED, situation.now)},
            wake_at=off_at,
        )


def _warning_dim(name: str, settings: RoomSettings, when: str) -> Decision:
    """Dimra rummets lampor som varning för att de snart släcks. Dimringen gör ingen ljusare."""
    percent = settings.dim_percent
    return Decision(
        f"Dimrades till {percent} %: {name} är tomt, och {when}. "
        "Rör dig i rummet för att behålla ljuset.",
        Switch(on=True, brightness=percent, lit_only=True, dim_only=True),
    )


def _back_to_full(name: str) -> Decision:
    return Decision(
        f"Full styrka igen: någon rörde sig i {name} under varningsdimringen.",
        Switch(on=True, lit_only=True),
    )


DEFAULT_RULES: tuple[Rule, ...] = (
    Calibrating(),
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
            silent_lamps=state.silent_lamps,
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
        if switch.dim_only and state.brightness <= switch.brightness:
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
    action = "tändes" if on else "släcktes"
    return LampCommand(
        lamp.id,
        on,
        f"{lamp.name} {action} för hand. Automatiken i {room.name} pausar tills rummet har "
        f"varit tomt i {duration(room.settings.off_after_empty_seconds)}.",
    )


def external_change_text(room: Room, lamp: Lamp, state: LampState) -> str:
    """Förklaringen när någon har ändrat en lampa med fjärrkontroll eller app."""
    if not state.on:
        action = "släcktes"
    elif state.brightness < FULL_BRIGHTNESS:
        action = f"ställdes på {state.brightness} %"
    else:
        action = "tändes"
    return (
        f"{lamp.name} {action} med fjärrkontroll eller app. Automatiken i {room.name} pausar "
        f"tills rummet har varit tomt i {duration(room.settings.off_after_empty_seconds)}."
    )


def start_manual(memory: Memory, now: float) -> Memory:
    """Rummets minne efter att någon har styrt en lampa där för hand."""
    return {**memory, MANUAL: now}


def stop_manual(memory: Memory) -> Memory:
    """Rummets minne när automatiken ska ta över igen direkt."""
    return {key: value for key, value in memory.items() if key != MANUAL}


def start_calibrating(memory: Memory, now: float) -> Memory:
    """Rummets minne när lampkalibreringen börjar: automatiken pausar."""
    return {**memory, CALIBRATING: now}


def stop_calibrating(memory: Memory) -> Memory:
    """Rummets minne när lampkalibreringen är slut.

    Hur länge det har varit ljust räknas om från början, eftersom lamporna
    lyste för mätningen och deras ljus kan ha räknats som dagsljus.
    """
    return {key: value for key, value in memory.items() if key not in (CALIBRATING, BRIGHT_SINCE)}


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


def _join(names: Sequence[str]) -> str:
    """Namnen uppräknade: "Taklampa", "Taklampa och Golvlampa", "A, B och C"."""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} och {names[-1]}"


def _format_lux(value: float) -> str:
    """En ljusnivå med högst en decimal."""
    return f"{round(value, 1):g}"
