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
            decision = _occupied_room(room.name, state.lux, is_on)
        else:
            decision = _vacant_room(room.name, vacant_since, is_on, now)

        explanations[lamp.id] = decision.explanation
        if decision.switch_on is not None:
            commands.append(LampCommand(lamp.id, decision.switch_on, decision.explanation))
        if decision.wake_at is not None:
            wake_times.append(decision.wake_at)

    return RuleResult(
        commands=tuple(commands),
        explanations=explanations,
        wake_at=min(wake_times, default=None),
    )


@dataclass(frozen=True)
class _Decision:
    explanation: str
    switch_on: bool | None = None
    """True tänder, False släcker, None låter lampan vara."""
    wake_at: float | None = None


def _occupied_room(name: str, lux: float | None, is_on: bool) -> _Decision:
    if is_on:
        return _Decision(f"Tänd: någon är i {name}.")
    if lux is None:
        return _Decision(f"Släckt: ljusnivån i {name} är okänd.")
    if lux < DARK_BELOW_LUX:
        return _Decision(
            f"Tänd: någon är i {name} och det är mörkt ({lux:g} lux, under {DARK_BELOW_LUX:g}).",
            switch_on=True,
        )
    return _Decision(f"Släckt: någon är i {name} men det är ljust nog ({lux:g} lux).")


def _vacant_room(name: str, vacant_since: float, is_on: bool, now: float) -> _Decision:
    delay = _minutes(OFF_AFTER_EMPTY_SECONDS)
    if not is_on:
        return _Decision(f"Släckt: {name} är tomt.")
    if now - vacant_since >= OFF_AFTER_EMPTY_SECONDS:
        return _Decision(f"Släckt: {name} har varit tomt i {delay}.", switch_on=False)
    return _Decision(
        f"Tänd: {name} är tomt och släcks när det varit tomt i {delay}.",
        wake_at=vacant_since + OFF_AFTER_EMPTY_SECONDS,
    )


def _minutes(seconds: float) -> str:
    minutes = seconds / 60
    return "1 minut" if minutes == 1 else f"{minutes:g} minuter"
