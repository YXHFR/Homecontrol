"""Minimal närvarobedömning: ett rum är upptaget om någon källa ser ett mål i det.

Målen kommer per källa, så att en senare sammanvägning kan väga källorna
olika. Här räcker det att någon av dem ser någon.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from homecontrol.connectors import Target
from homecontrol.home import Home, Point, Room


@dataclass(frozen=True)
class RoomPresence:
    """`vacant_since` är tidpunkten då rummet blev tomt, eller None om det är upptaget."""

    vacant_since: float | None

    @property
    def occupied(self) -> bool:
        return self.vacant_since is None


def initial_presence(home: Home, now: float) -> dict[str, RoomPresence]:
    """Vid start räknas alla rum som tomma sedan just nu."""
    return {room.id: RoomPresence(vacant_since=now) for room in home.rooms}


def assess(
    home: Home,
    targets_by_source: Mapping[str, tuple[Target, ...]],
    previous: Mapping[str, RoomPresence],
    now: float,
) -> dict[str, RoomPresence]:
    targets = [target for targets in targets_by_source.values() for target in targets]
    result: dict[str, RoomPresence] = {}
    for floor in home.floors:
        on_floor = [target.position for target in targets if target.floor_id == floor.id]
        for room in floor.rooms:
            result[room.id] = _assess_room(room, on_floor, previous, now)
    return result


def _assess_room(
    room: Room, targets: list[Point], previous: Mapping[str, RoomPresence], now: float
) -> RoomPresence:
    if any(room.contains(target) for target in targets):
        return RoomPresence(vacant_since=None)
    # Ett rum som redan var tomt behåller sin tidpunkt, annars blev det tomt nu.
    before = previous.get(room.id)
    if before is not None and before.vacant_since is not None:
        return before
    return RoomPresence(vacant_since=now)
