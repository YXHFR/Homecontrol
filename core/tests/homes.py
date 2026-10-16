"""Små hem för testerna."""

from collections.abc import Sequence

from homecontrol.connectors import RadarTarget, RadarTargetsObserved
from homecontrol.home import (
    Capability,
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

FLOOR = "plan"
NODE_HEIGHT = 2.0


def one_room_home() -> Home:
    """Ett vardagsrum på 5 × 4 meter med en taklampa i mitten, på en enda våning.

    Rumskortet sitter mitt på övre väggen och tittar in i rummet.
    """
    return Home(
        name="Enrumshemmet",
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
    node: Node | None = None,
    walls: Sequence[WallKind] = (WallKind.PLASTERBOARD,) * 4,
    zones: Sequence[Zone] = (),
) -> Room:
    """Ett rektangulärt rum med, om `lamp` anges, en taklampa i mitten.

    Utan `node` sitter ett rumskort med radar och Wi-Fi mitt på övre väggen
    och tittar rakt in i rummet. Väggarna är som standard av gips.
    """
    polygon = rectangle(x, y, width, height)
    center = Point(x + width / 2, y + height / 2)
    if node is None:
        node = radar_node(f"nod-{room_id}", x + width / 2, y, rotation=90)
    return Room(
        id=room_id,
        name=name,
        polygon=polygon,
        walls=tuple(walls),
        zones=tuple(zones),
        nodes=(node,),
        lamps=() if lamp is None else (Lamp(id=lamp, name="Taklampa", position=center),),
    )


def radar_node(node_id: str, x: float, y: float, *, rotation: float) -> Node:
    return Node(
        id=node_id,
        name="Rumskort",
        position=Point(x, y),
        height=NODE_HEIGHT,
        rotation=rotation,
        capabilities=(Capability.RADAR, Capability.CSI, Capability.LUX),
    )


def zone(zone_id: str, kind: ZoneKind, x: float, y: float, width: float, height: float) -> Zone:
    return Zone(
        id=zone_id, name=zone_id.capitalize(), kind=kind, polygon=rectangle(x, y, width, height)
    )


def radar_sees(
    *points: tuple[float, float], home: Home | None = None, source_id: str = "sim"
) -> RadarTargetsObserved:
    """Det rumskortet i hemmets första rum ser när personer står i `points` (hemmets meter)."""
    node = (home if home is not None else one_room_home()).rooms[0].nodes[0]
    targets = tuple(RadarTarget(*node.home_to_radar(Point(x, y))) for x, y in points)
    return RadarTargetsObserved(source_id, node.id, targets)


def side_by_side_home(
    *, a_zones: Sequence[Zone] = (), b_zones: Sequence[Zone] = (), doorway: bool = True
) -> Home:
    """Två rum på 5 × 4 meter bredvid varandra, med en dörröppning emellan eller en gipsvägg.

    Rummet A (x 0–5) har sitt rumskort på vänstra väggen och rummet B (x 5–10)
    på högra, och båda tittar mot dörröppningen. Radarerna ser alltså en bit
    in i grannrummet, genom väggen.
    """
    between = WallKind.DOORWAY if doorway else WallKind.PLASTERBOARD
    doorway_right = (WallKind.PLASTERBOARD, between, *(WallKind.PLASTERBOARD,) * 2)
    doorway_left = (*(WallKind.PLASTERBOARD,) * 3, between)
    return Home(
        name="Tvårumshemmet",
        floors=(
            Floor(
                id=FLOOR,
                name="Plan 1",
                level=0,
                rooms=(
                    room(
                        "a",
                        "Rum A",
                        0,
                        0,
                        5,
                        4,
                        lamp="lampa-a",
                        node=radar_node("nod-a", 0, 2, rotation=0),
                        walls=doorway_right,
                        zones=a_zones,
                    ),
                    room(
                        "b",
                        "Rum B",
                        5,
                        0,
                        5,
                        4,
                        lamp="lampa-b",
                        node=radar_node("nod-b", 10, 2, rotation=180),
                        walls=doorway_left,
                        zones=b_zones,
                    ),
                ),
            ),
        ),
    )


def home_with_zones(*zones: Zone, settings: RoomSettings | None = None) -> Home:
    """Enrumshemmet med zoner i vardagsrummet, och eventuellt egna inställningar."""
    home = one_room_home()
    floor = home.floors[0]
    update: dict[str, object] = {"zones": zones}
    if settings is not None:
        update["settings"] = settings
    living_room = floor.rooms[0].model_copy(update=update)
    return Home.model_validate(
        home.model_copy(
            update={"floors": (floor.model_copy(update={"rooms": (living_room,)}),)}
        ).model_dump()
    )


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


def home_with_settings(settings: RoomSettings, *, lamp_lux: float = 0) -> Home:
    """Enrumshemmet, men vardagsrummet har egna inställningar.

    `lamp_lux` är hur mycket taklampan lyser upp ljussensorn vid full styrka.
    """
    home = one_room_home()
    floor = home.floors[0]
    room = floor.rooms[0]
    lamps = tuple(lamp.model_copy(update={"lux_contribution": lamp_lux}) for lamp in room.lamps)
    living_room = room.model_copy(update={"settings": settings, "lamps": lamps})
    return home.model_copy(update={"floors": (floor.model_copy(update={"rooms": (living_room,)}),)})


def two_lamp_home() -> Home:
    """Enrumshemmet med en golvlampa i vardagsrummets högra hörn, utöver taklampan.

    Taklampan sitter 2 meter från rumskortet och golvlampan √5 meter.
    """
    home = one_room_home()
    floor = home.floors[0]
    room = floor.rooms[0]
    floor_lamp = Lamp(id="golvlampa", name="Golvlampa", position=Point(4.5, 1))
    living_room = room.model_copy(update={"lamps": (*room.lamps, floor_lamp)})
    return home.model_copy(update={"floors": (floor.model_copy(update={"rooms": (living_room,)}),)})
