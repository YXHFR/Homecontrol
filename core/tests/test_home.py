"""Hemmodellen: begrepp, koordinater och kontroller."""

import pytest
from pydantic import ValidationError

from homecontrol.home import (
    Floor,
    Home,
    Lamp,
    Node,
    Point,
    Room,
    RoomSettings,
    WallKind,
    Zone,
    ZoneKind,
    rectangle,
)
from homes import FLOOR, one_room_home, room

# Ett L-format rum: 4 × 4 meter där den nedre högra kvadranten saknas.
L_SHAPE = (Point(0, 0), Point(4, 0), Point(4, 2), Point(2, 2), Point(2, 4), Point(0, 4))


def l_room(**changes: object) -> Room:
    fields: dict[str, object] = {
        "id": "rum",
        "name": "Rum",
        "polygon": L_SHAPE,
        "walls": (WallKind.PLASTERBOARD,) * 6,
    }
    return Room.model_validate(fields | changes)


def home_with(*floors: Floor) -> Home:
    return Home(name="Hem", floors=floors)


def test_room_contains_its_inside_and_edges() -> None:
    rum = l_room()

    assert rum.contains(Point(1, 1))
    assert rum.contains(Point(0, 0))  # hörn
    assert rum.contains(Point(3, 2))  # på väggen in mot hacket
    assert not rum.contains(Point(3, 3))  # i hacket, utanför rummet
    assert not rum.contains(Point(4.01, 1))


def test_room_center_is_the_centroid_of_the_area() -> None:
    assert room("r", "R", 1, 2, 4, 2).center == Point(3, 3)
    # Tre kvadrater på 2 × 2 med mitt i (1, 1), (3, 1) och (1, 3).
    center = l_room().center
    assert center.x == pytest.approx(5 / 3)
    assert center.y == pytest.approx(5 / 3)


def test_walls_follow_the_sides_clockwise_from_top_left() -> None:
    rum = room("r", "R", 0, 0, 3, 2).model_copy(
        update={
            "walls": (WallKind.CONCRETE, WallKind.GLASS, WallKind.PLASTERBOARD, WallKind.DOORWAY)
        }
    )

    sides = [(a, b, kind) for a, b, kind in rum.wall_segments()]

    # Origo uppe till vänster och y nedåt: första sidan är överkanten, sedan höger.
    assert sides[0] == (Point(0, 0), Point(3, 0), WallKind.CONCRETE)
    assert sides[1] == (Point(3, 0), Point(3, 2), WallKind.GLASS)
    assert sides[3] == (Point(0, 2), Point(0, 0), WallKind.DOORWAY)
    assert sides[3][2].is_doorway


def test_room_needs_one_wall_per_side() -> None:
    with pytest.raises(ValidationError, match="4 sidor men 3 väggar"):
        Room(id="r", name="R", polygon=rectangle(0, 0, 1, 1), walls=(WallKind.PLASTERBOARD,) * 3)


@pytest.mark.parametrize(
    "polygon",
    [
        (Point(0, 0), Point(1, 1)),  # för få hörn
        (Point(0, 0), Point(1, 1), Point(2, 2)),  # på en linje
    ],
)
def test_polygon_needs_an_area(polygon: tuple[Point, ...]) -> None:
    with pytest.raises(ValidationError):
        Zone(id="z", name="Z", kind=ZoneKind.NORMAL, polygon=polygon)


def test_coordinates_start_at_the_top_left_corner() -> None:
    with pytest.raises(ValidationError, match="mellan 0 och 1000"):
        Lamp(id="l", name="L", position=Point(-0.5, 1))


def test_node_rotation_is_degrees_clockwise_below_a_full_turn() -> None:
    node = Node(id="n", name="N", position=Point(1, 1), height=2.2, rotation=90)
    assert node.rotation == 90
    with pytest.raises(ValidationError):
        node.model_validate(node.model_dump() | {"rotation": 360})


def test_lamp_zone_must_be_in_the_same_room() -> None:
    lamp = Lamp(id="l", name="L", position=Point(1, 1), zone_id="soffa")
    with pytest.raises(ValidationError, match="zon som inte finns"):
        l_room(lamps=(lamp,))

    sofa = Zone(id="soffa", name="Soffa", kind=ZoneKind.STILL, polygon=rectangle(0, 0, 1, 1))
    assert l_room(zones=(sofa,), lamps=(lamp,)).lamps[0].zone_id == "soffa"


def test_zone_must_be_inside_its_room() -> None:
    def zone(polygon: tuple[Point, ...]) -> Zone:
        return Zone(id="z", name="Z", kind=ZoneKind.IGNORE, polygon=polygon)

    # Längs väggen är fortfarande i rummet, till exempel en entrézon vid dörren.
    assert l_room(zones=(zone(rectangle(0, 3, 2, 1)),)).zones
    with pytest.raises(ValidationError, match="går utanför rummet"):
        l_room(zones=(zone(rectangle(3, 3, 0.5, 0.5)),))  # i hacket
    with pytest.raises(ValidationError, match="går utanför rummet"):
        l_room(zones=(zone(rectangle(3, 1, 2, 0.5)),))  # sticker ut genom väggen


def test_room_settings_have_defaults_and_keep_the_limits_in_order() -> None:
    assert RoomSettings() == RoomSettings(
        dark_below_lux=30, bright_above_lux=100, off_after_empty_seconds=180
    )
    with pytest.raises(ValidationError, match="Mörkergränsen"):
        RoomSettings(dark_below_lux=100, bright_above_lux=50)


def test_ids_are_unique_in_the_whole_home() -> None:
    first = Floor(id="nere", name="Nere", level=0, rooms=(room("hall", "Hall", 0, 0, 2, 2),))
    second = Floor(id="uppe", name="Uppe", level=1, rooms=(room("hall", "Hall", 0, 0, 2, 2),))

    with pytest.raises(ValidationError, match="hall används två gånger"):
        home_with(first, second)


def test_floors_have_different_levels() -> None:
    with pytest.raises(ValidationError, match="samma plan"):
        home_with(Floor(id="a", name="A", level=0), Floor(id="b", name="B", level=0))


def test_home_has_at_least_one_floor() -> None:
    with pytest.raises(ValidationError, match="minst en våning"):
        home_with()


def test_ids_are_simple() -> None:
    with pytest.raises(ValidationError):
        Floor(id="Våning 1", name="Våning 1", level=0)


def test_home_finds_rooms_floors_and_lamps() -> None:
    home = one_room_home()

    assert home.room("vardagsrum").name == "Vardagsrum"
    assert home.floor_of("vardagsrum").id == FLOOR
    assert home.room_of("taklampa").id == "vardagsrum"
    assert [lamp.id for lamp in home.lamps] == ["taklampa"]
    with pytest.raises(KeyError):
        home.room("saknas")


def test_model_cannot_be_changed_by_mistake() -> None:
    home = one_room_home()
    with pytest.raises(ValidationError):
        home.name = "Annat"


def test_rooms_sharing_a_wall_must_agree_on_it() -> None:
    left = room("vanster", "Vänster", 0, 0, 2, 2)
    right = room("hoger", "Höger", 2, 0, 2, 2)
    # Den vänstra kanten på det högra rummet är sida 4 (från (2, 2) upp till (2, 0)).
    door = right.model_copy(update={"walls": (*right.walls[:3], WallKind.DOORWAY)})

    with pytest.raises(ValidationError, match="vanster och hoger delar en vägg"):
        Floor(id="plan", name="Plan", level=0, rooms=(left, door))

    # Den högra kanten på det vänstra rummet är sida 2.
    walls = (left.walls[0], WallKind.DOORWAY, *left.walls[2:])
    both_doors = left.model_copy(update={"walls": walls})
    floor = Floor(id="plan", name="Plan", level=0, rooms=(both_doors, door))
    assert floor.rooms[1].walls[3].is_doorway


def test_walls_that_only_touch_at_a_corner_do_not_need_to_agree() -> None:
    first = room("a", "A", 0, 0, 2, 2)
    diagonal = room("b", "B", 2, 2, 2, 2)
    concrete = diagonal.model_copy(update={"walls": (WallKind.CONCRETE,) * 4})

    assert Floor(id="plan", name="Plan", level=0, rooms=(first, concrete))
