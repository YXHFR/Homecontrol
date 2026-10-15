"""Små hem för testerna."""

from homecontrol.connectors import Target
from homecontrol.home import Floor, Home, Lamp, Point, Room, RoomSettings, WallKind, rectangle

FLOOR = "plan"


def demo_home() -> Home:
    """Ett vardagsrum på 5 × 4 meter med en taklampa i mitten, på en enda våning."""
    return Home(
        name="Demohemmet",
        floors=(
            Floor(
                id=FLOOR,
                name="Plan 1",
                level=0,
                rooms=(room("vardagsrum", "Vardagsrum", 0, 0, 5, 4, lamp="taklampa"),),
            ),
        ),
    )


def room(
    room_id: str,
    name: str,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    lamp: str | None = None,
) -> Room:
    """Ett rektangulärt rum med gipsväggar och, om `lamp` anges, en taklampa i mitten."""
    polygon = rectangle(x, y, width, height)
    center = Point(x + width / 2, y + height / 2)
    return Room(
        id=room_id,
        name=name,
        polygon=polygon,
        walls=(WallKind.GIPS,) * 4,
        lamps=() if lamp is None else (Lamp(id=lamp, name="Taklampa", position=center),),
    )


def at(x: float, y: float, floor: str = FLOOR) -> Target:
    """Ett mål (en person) i punkten (x, y) på demohemmets våning."""
    return Target(floor, Point(x, y))


def two_floor_home() -> Home:
    """Två rum precis ovanför varandra, på var sin våning, med var sin taklampa."""
    return Home(
        name="Tvåvåningshemmet",
        floors=(
            Floor(
                id="nere",
                name="Nere",
                level=0,
                rooms=(room("vardagsrum", "Vardagsrum", 0, 0, 5, 4, lamp="lampa-nere"),),
            ),
            Floor(
                id="uppe",
                name="Uppe",
                level=1,
                rooms=(room("sovrum", "Sovrum", 0, 0, 5, 4, lamp="lampa-uppe"),),
            ),
        ),
    )


def home_with_settings(settings: RoomSettings) -> Home:
    """Demohemmet, men vardagsrummet har egna inställningar."""
    home = demo_home()
    floor = home.floors[0]
    living_room = floor.rooms[0].model_copy(update={"settings": settings})
    return home.model_copy(update={"floors": (floor.model_copy(update={"rooms": (living_room,)}),)})
