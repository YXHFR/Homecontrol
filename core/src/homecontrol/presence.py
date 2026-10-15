"""Minimal närvarobedömning: ett rum är upptaget om någon källa ser ett mål i det.

Senare vägs flera källor samman med osäkerhet. Gränssnittet är redan det
som behövs då: in går alla mål som källorna ser, ut går ett läge per rum.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from homecontrol.home import Home, Point


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
    targets: Iterable[Point],
    previous: Mapping[str, RoomPresence],
    now: float,
) -> dict[str, RoomPresence]:
    targets = tuple(targets)
    result: dict[str, RoomPresence] = {}
    for room in home.rooms:
        if any(room.contains(target) for target in targets):
            result[room.id] = RoomPresence(vacant_since=None)
        else:
            # Ett rum som redan var tomt behåller sin tidpunkt, annars blev det tomt nu.
            before = previous.get(room.id)
            vacant_since = before.vacant_since if before is not None else None
            result[room.id] = RoomPresence(
                vacant_since=now if vacant_since is None else vacant_since
            )
    return result
