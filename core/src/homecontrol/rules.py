"""Regelmotorn: en ren funktion från läget i hemmet till lampkommandon och förklaringar.

Funktionen läser ingen klocka och gör inga anrop, så samma indata ger alltid
samma utdata. Den säger också när den vill bli tillfrågad igen (`wake_at`),
till exempel när ett tomt rum ska släckas.

Regeln just nu:

* Tänd när någon är i rummet och det är mörkt (under 30 lux).
* Släck när rummet har varit tomt i 3 minuter.

En tänd lampa släcks inte för att det blir ljust, eftersom lampan själv gör
rummet ljusare.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from homecontrol.connectors import LampCommand
from homecontrol.home import Home
from homecontrol.presence import RoomPresence

DARK_BELOW_LUX = 30.0
OFF_AFTER_EMPTY_SECONDS = 180.0


@dataclass(frozen=True)
class RoomState:
    presence: RoomPresence
    lux: float | None
    """Senast uppmätta ljusnivå, eller None om ingen källa har mätt den än."""


@dataclass(frozen=True)
class RuleResult:
    commands: tuple[LampCommand, ...]
    explanations: Mapping[str, str]
    """Varför varje lampa är tänd eller släckt, med lampans id som nyckel."""
    wake_at: float | None
    """Nästa tidpunkt då regeln kan komma fram till något nytt, om inget annat händer."""


def evaluate(
    home: Home,
    rooms: Mapping[str, RoomState],
    lamps_on: Mapping[str, bool],
    now: float,
) -> RuleResult:
    commands: list[LampCommand] = []
    explanations: dict[str, str] = {}
    wake_times: list[float] = []

    for lamp in home.lamps:
        room = home.room(lamp.room_id)
        state = rooms[room.id]
        is_on = lamps_on.get(lamp.id, False)
        vacant_since = state.presence.vacant_since

        if vacant_since is None:
            if is_on:
                explanations[lamp.id] = f"Tänd: någon är i {room.name}."
            elif state.lux is None:
                explanations[lamp.id] = f"Släckt: ljusnivån i {room.name} är okänd."
            elif state.lux < DARK_BELOW_LUX:
                reason = (
                    f"Tänd: någon är i {room.name} och det är mörkt "
                    f"({state.lux:g} lux, under {DARK_BELOW_LUX:g})."
                )
                commands.append(LampCommand(lamp.id, on=True, reason=reason))
                explanations[lamp.id] = reason
            else:
                explanations[lamp.id] = (
                    f"Släckt: någon är i {room.name} men det är ljust nog ({state.lux:g} lux)."
                )
        elif not is_on:
            explanations[lamp.id] = f"Släckt: {room.name} är tomt."
        elif now - vacant_since >= OFF_AFTER_EMPTY_SECONDS:
            reason = f"Släckt: {room.name} har varit tomt i {_minutes(OFF_AFTER_EMPTY_SECONDS)}."
            commands.append(LampCommand(lamp.id, on=False, reason=reason))
            explanations[lamp.id] = reason
        else:
            wake_times.append(vacant_since + OFF_AFTER_EMPTY_SECONDS)
            explanations[lamp.id] = (
                f"Tänd: {room.name} är tomt och släcks när det varit tomt i "
                f"{_minutes(OFF_AFTER_EMPTY_SECONDS)}."
            )

    return RuleResult(
        commands=tuple(commands),
        explanations=explanations,
        wake_at=min(wake_times, default=None),
    )


def _minutes(seconds: float) -> str:
    minutes = seconds / 60
    return "1 minut" if minutes == 1 else f"{minutes:g} minuter"
