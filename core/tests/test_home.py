"""Hemmodellen: begrepp, koordinater och kontroller."""

import math

import pytest
from pydantic import ValidationError

from homecontrol.home import (
    BODY_HEIGHT,
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
    # Hörnen ligger i båda benen av L:et, men kanten mellan dem går genom hacket.
    across = (Point(1, 1), Point(3.5, 1), Point(1, 3.5))
    with pytest.raises(ValidationError, match="går utanför rummet"):
        l_room(zones=(zone(across),))

    # I ett U-format rum kan en kant gå genom två av rummets hörn, (2, 2) och
    # (4, 4), och ta en genväg över hacket utan att korsa någon vägg.
    u_shape = (
        *(Point(0, 0), Point(6, 0), Point(6, 4), Point(4, 4)),
        *(Point(4, 2), Point(2, 2), Point(2, 4), Point(0, 4)),
    )
    shortcut = (Point(1, 1), Point(4, 4), Point(5, 1))
    with pytest.raises(ValidationError, match="går utanför rummet"):
        l_room(polygon=u_shape, walls=(WallKind.PLASTERBOARD,) * 8, zones=(zone(shortcut),))
    # Ett spetsigt hack (4, 5)–(5, 3)–(6, 5) som zonens överkant bara nuddar.
    spike = (
        *(Point(0, 0), Point(20, 0), Point(20, 10), Point(6, 10), Point(6, 5)),
        *(Point(5, 3), Point(4, 5), Point(4, 10), Point(0, 10)),
    )
    with pytest.raises(ValidationError, match="går utanför rummet"):
        l_room(
            polygon=spike, walls=(WallKind.PLASTERBOARD,) * 9, zones=(zone(rectangle(1, 5, 18, 4)),)
        )


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


# --- Radarns koordinater ---


def node(*, rotation: float = 0, mirrored: bool = False, height: float = BODY_HEIGHT) -> Node:
    """Ett rumskort i (5, 5). På kroppshöjd mäter radarn samma avstånd som längs golvet."""
    return Node(
        id="n", name="N", position=Point(5, 5), height=height, rotation=rotation, mirrored=mirrored
    )


def approx(point: Point) -> tuple[object, object]:
    return (pytest.approx(point.x), pytest.approx(point.y))


@pytest.mark.parametrize(
    ("rotation", "ahead", "right"),
    [
        (0, Point(7, 5), Point(7, 6)),  # tittar åt höger; dess högra sida är nedåt på kartan
        (90, Point(5, 7), Point(4, 7)),  # tittar nedåt; dess högra sida är åt vänster
        (180, Point(3, 5), Point(3, 4)),
        (270, Point(5, 3), Point(6, 3)),
        (
            45,
            Point(5 + math.sqrt(2), 5 + math.sqrt(2)),
            Point(5 + math.sqrt(2) - math.sqrt(0.5), 5 + math.sqrt(2) + math.sqrt(0.5)),
        ),
    ],
)
def test_radar_coordinates_follow_the_rotation(rotation: float, ahead: Point, right: Point) -> None:
    """2 meter rakt fram, och 1 meter åt höger (sett bakifrån radarn) 2 meter fram."""
    radar = node(rotation=rotation)

    assert approx(radar.radar_to_home(0, 2)) == approx(ahead)
    assert approx(radar.radar_to_home(1, 2)) == approx(right)


def test_mirrored_radar_swaps_left_and_right() -> None:
    radar = node(mirrored=True)

    assert approx(radar.radar_to_home(0, 2)) == approx(Point(7, 5))
    assert approx(radar.radar_to_home(1, 2)) == approx(Point(7, 4))


def test_mounting_height_shortens_the_distance_along_the_floor() -> None:
    """Radarn sitter en meter över kroppens mitt och mäter avståndet snett ned."""
    radar = node(height=BODY_HEIGHT + 1)

    assert approx(radar.radar_to_home(0, math.sqrt(5))) == approx(Point(7, 5))
    # Rakt under radarn är avståndet längs golvet noll, inte negativt.
    assert approx(radar.radar_to_home(0, 0.5)) == approx(Point(5, 5))


@pytest.mark.parametrize("rotation", [0, 37, 90, 200, 359])
@pytest.mark.parametrize("mirrored", [False, True])
def test_home_to_radar_is_the_reverse(rotation: float, mirrored: bool) -> None:
    radar = node(rotation=rotation, mirrored=mirrored, height=2.4)

    for point in (Point(6, 9), Point(1.5, 5.2), Point(5, 4)):
        seen = radar.home_to_radar(point)
        assert approx(radar.radar_to_home(*seen)) == approx(point)


def test_radar_sees_6_meters_and_60_degrees_to_each_side() -> None:
    radar = node()

    assert radar.radar_sees(0, 5.9)
    assert not radar.radar_sees(0, 6.1)
    assert radar.radar_sees(1.7, 1)  # 59,5° åt sidan
    assert not radar.radar_sees(1.8, 1)  # 61°
    assert not radar.radar_sees(0, -1)  # bakom radarn
