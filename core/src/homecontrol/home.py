"""Hemmodellen: hem, våningar, rum, väggar, dörröppningar, zoner, noder, signalkällor och lampor.

Koordinater är meter i ett plan per våning, med origo uppe till vänster:
x växer åt höger och y nedåt, precis som på en ritning eller skärm. Rotation
anges i grader medurs, där 0° pekar åt höger (längs x).

Modellen är kapslad som hemmet självt: en våning har rum och signalkällor,
ett rum har väggar, zoner, noder och lampor. Varje sak har ett id som är
unikt i hela hemmet, så att resten av kärnan kan peka ut den med bara id:t.

Modellen är oföränderlig och kontrolleras när den skapas, oavsett om den
kommer från databasen, en importerad fil eller koden.

Några delar är förberedda för kommande steg och påverkar ännu inget:
zonernas sorter, lampornas zon, rummens ljusgräns och nodernas placering.
Regeln använder i dag bara rummens mörkergräns och fördröjning.
"""

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, NamedTuple, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


class Point(NamedTuple):
    """En punkt i meter. I filer och meddelanden skrivs den som [x, y]."""

    x: float
    y: float


@dataclass(frozen=True)
class Target:
    """Ett mål (en person) någonstans i hemmet: en punkt på en våning."""

    floor_id: str
    position: Point


# Gränser som håller orimliga värden ute. Ett hem är mindre än så här.
MAX_COORDINATE = 1000.0
MAX_HEIGHT = 20.0
MAX_LUX = 200_000.0
MAX_DELAY_SECONDS = 24 * 60 * 60

DEFAULT_DARK_BELOW_LUX = 30.0
DEFAULT_BRIGHT_ABOVE_LUX = 100.0
DEFAULT_OFF_AFTER_EMPTY_SECONDS = 180.0


def _on_plan(point: Point) -> Point:
    for value in point:
        if not math.isfinite(value) or not 0 <= value <= MAX_COORDINATE:
            raise ValueError(
                f"Koordinaterna ska vara mellan 0 och {MAX_COORDINATE:g} meter: {list(point)}"
            )
    return point


Position = Annotated[Point, AfterValidator(_on_plan)]
"""En punkt på planritningen. Origo är uppe till vänster, så inget är negativt."""

Id = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=64)]
"""Små bokstäver a–z, siffror, - och _. Id:t står i filer och loggar, så det hålls enkelt."""

Name = Annotated[str, Field(min_length=1, max_length=100)]
Height = Annotated[float, Field(ge=0, le=MAX_HEIGHT, allow_inf_nan=False)]
Lux = Annotated[float, Field(ge=0, le=MAX_LUX, allow_inf_nan=False)]


def _polygon(points: tuple[Point, ...]) -> tuple[Point, ...]:
    if len(points) < 3:
        raise ValueError("En yta behöver minst tre hörn")
    if abs(_signed_area(points)) < 1e-9:
        raise ValueError("Ytan har ingen area")
    return points


Polygon = Annotated[tuple[Position, ...], AfterValidator(_polygon)]
"""Hörnen i ordning. Sista hörnet förbinds med det första."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class WallKind(StrEnum):
    """Vad en vägg är gjord av, eller att den är en dörröppning utan vägg."""

    PLASTERBOARD = "gips"
    CONCRETE = "betong"
    WOOD = "trä"
    GLASS = "glas"
    DOORWAY = "dörröppning"

    @property
    def is_doorway(self) -> bool:
        return self is WallKind.DOORWAY


class ZoneKind(StrEnum):
    """Vad en zon betyder för närvaron. Används när sammanvägningen kommer (#37)."""

    NORMAL = "vanlig"
    STILL = "stillhet"
    """Där man sitter eller ligger still länge, till exempel soffa och säng."""
    IGNORE = "ignorera"
    """Störkällor som radarn inte ska bry sig om, till exempel en fläkt."""
    ENTRANCE = "entré"
    """Vid en dörr, där den som försvinner ur sikte har gått ut."""


class Capability(StrEnum):
    """Vad en nod kan mäta."""

    RADAR = "radar"
    CSI = "csi"
    """Rörelse i Wi-Fi-signalen från en signalkälla."""
    LUX = "lux"


class LampProfile(StrEnum):
    """Hur lampan ska styras. Lamputgången anpassar sig efter lampans egenheter."""

    GENERIC = "generisk"
    IKEA_TRADFRI = "ikea-tradfri"


class RoomSettings(_Model):
    dark_below_lux: Lux = DEFAULT_DARK_BELOW_LUX
    """Under den här ljusnivån räknas rummet som mörkt."""
    bright_above_lux: Lux = DEFAULT_BRIGHT_ABOVE_LUX
    """Över den här ljusnivån räknas rummet som ljust av dagsljus. Används inte än."""
    off_after_empty_seconds: Annotated[float, Field(gt=0, le=MAX_DELAY_SECONDS)] = (
        DEFAULT_OFF_AFTER_EMPTY_SECONDS
    )
    """Så länge rummet ska ha varit tomt innan lamporna släcks."""

    @model_validator(mode="after")
    def _limits_in_order(self) -> Self:
        if self.dark_below_lux >= self.bright_above_lux:
            raise ValueError("Mörkergränsen måste vara lägre än ljusgränsen")
        return self


class Zone(_Model):
    id: Id
    name: Name
    kind: ZoneKind
    polygon: Polygon


class Node(_Model):
    """Ett rumskort (ESP32) med radar, Wi-Fi-mätning och ljussensor."""

    id: Id
    name: Name
    position: Position
    height: Height
    """Monteringshöjd över golvet i meter."""
    rotation: Annotated[float, Field(ge=0, lt=360, allow_inf_nan=False)] = 0.0
    """Åt vilket håll radarn tittar, i grader medurs från höger (x-axeln)."""
    mirrored: bool = False
    """Kortet sitter upp och ned, så radarns sidled är spegelvänd."""
    capabilities: tuple[Capability, ...] = ()

    @model_validator(mode="after")
    def _unique_capabilities(self) -> Self:
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError(f"Noden {self.id} har samma förmåga flera gånger")
        return self


class Lamp(_Model):
    id: Id
    name: Name
    position: Position
    zone_id: Id | None = None
    """Zonen i rummet som lampan hör till, om den bara lyser upp en del av rummet."""
    profile: LampProfile = LampProfile.GENERIC


class Room(_Model):
    id: Id
    name: Name
    polygon: Polygon
    walls: tuple[WallKind, ...]
    """En vägg per sida i polygonen: väggen från hörn i till hörn i + 1."""
    settings: RoomSettings = RoomSettings()
    zones: tuple[Zone, ...] = ()
    nodes: tuple[Node, ...] = ()
    lamps: tuple[Lamp, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if len(self.walls) != len(self.polygon):
            raise ValueError(
                f"Rummet {self.id} har {len(self.polygon)} sidor men {len(self.walls)} väggar"
            )
        zone_ids = {zone.id for zone in self.zones}
        for lamp in self.lamps:
            if lamp.zone_id is not None and lamp.zone_id not in zone_ids:
                raise ValueError(f"Lampan {lamp.id} hör till en zon som inte finns i rummet")
        return self

    def contains(self, point: Point) -> bool:
        """Om punkten är i rummet. Punkter på väggen räknas som inne."""
        return _in_polygon(point, self.polygon)

    @property
    def center(self) -> Point:
        """Ytans tyngdpunkt."""
        return _centroid(self.polygon)

    def wall_segments(self) -> Iterator[tuple[Point, Point, WallKind]]:
        """Varje vägg med sina två ändpunkter."""
        for i, kind in enumerate(self.walls):
            yield self.polygon[i], self.polygon[(i + 1) % len(self.polygon)], kind


class SignalSource(_Model):
    """En Wi-Fi-sändare, till exempel routern, som noderna mäter rörelse i signalen från."""

    id: Id
    name: Name
    position: Position
    height: Height


class Floor(_Model):
    id: Id
    name: Name
    level: int = Field(ge=-10, le=100)
    """Våningsplanet: 0 är entréplanet, 1 en trappa upp och -1 källaren."""
    rooms: tuple[Room, ...] = ()
    signal_sources: tuple[SignalSource, ...] = ()

    @model_validator(mode="after")
    def _shared_walls_agree(self) -> Self:
        """Två rum som delar en väggsträcka måste säga samma sak om den."""
        walls = [
            (room.id, start, end, kind)
            for room in self.rooms
            for start, end, kind in room.wall_segments()
        ]
        for i, (room_a, start_a, end_a, kind_a) in enumerate(walls):
            for room_b, start_b, end_b, kind_b in walls[i + 1 :]:
                if (
                    room_a != room_b
                    and kind_a != kind_b
                    and _overlapping(start_a, end_a, start_b, end_b)
                ):
                    raise ValueError(
                        f"Rummen {room_a} och {room_b} delar en vägg men anger olika "
                        f"sorter ({kind_a.value} och {kind_b.value}). Dela sidan med ett "
                        "extra hörn där väggen byter sort."
                    )
        return self


class Home(_Model):
    name: Name
    floors: tuple[Floor, ...]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not self.floors:
            raise ValueError("Hemmet behöver minst en våning")
        levels = [floor.level for floor in self.floors]
        if len(set(levels)) != len(levels):
            raise ValueError("Två våningar har samma plan")
        seen: set[str] = set()
        for item_id in self._all_ids():
            if item_id in seen:
                raise ValueError(f"Id:t {item_id} används två gånger")
            seen.add(item_id)
        return self

    @property
    def rooms(self) -> tuple[Room, ...]:
        """Alla rum på alla våningar."""
        return tuple(room for floor in self.floors for room in floor.rooms)

    @property
    def lamps(self) -> tuple[Lamp, ...]:
        """Alla lampor i alla rum."""
        return tuple(lamp for room in self.rooms for lamp in room.lamps)

    def room(self, room_id: str) -> Room:
        for room in self.rooms:
            if room.id == room_id:
                return room
        raise KeyError(room_id)

    def floor_of(self, room_id: str) -> Floor:
        for floor in self.floors:
            if any(room.id == room_id for room in floor.rooms):
                return floor
        raise KeyError(room_id)

    def room_of(self, lamp_id: str) -> Room:
        for room in self.rooms:
            if any(lamp.id == lamp_id for lamp in room.lamps):
                return room
        raise KeyError(lamp_id)

    def _all_ids(self) -> Iterator[str]:
        for floor in self.floors:
            yield floor.id
            yield from (source.id for source in floor.signal_sources)
            for room in floor.rooms:
                yield room.id
                for items in (room.zones, room.nodes, room.lamps):
                    yield from (item.id for item in items)


def rectangle(x: float, y: float, width: float, height: float) -> tuple[Point, ...]:
    """Hörnen i ett rektangulärt rum, medurs från övre vänstra hörnet."""
    return (Point(x, y), Point(x + width, y), Point(x + width, y + height), Point(x, y + height))


def _signed_area(points: Sequence[Point]) -> float:
    return (
        sum(a.x * b.y - b.x * a.y for a, b in zip(points, (*points[1:], points[0]), strict=True))
        / 2
    )


def _centroid(points: Sequence[Point]) -> Point:
    area = _signed_area(points)
    cx = cy = 0.0
    for a, b in zip(points, (*points[1:], points[0]), strict=True):
        cross = a.x * b.y - b.x * a.y
        cx += (a.x + b.x) * cross
        cy += (a.y + b.y) * cross
    return Point(cx / (6 * area), cy / (6 * area))


def _in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    edges = list(zip(polygon, (*polygon[1:], polygon[0]), strict=True))
    if any(_on_segment(point, a, b) for a, b in edges):
        return True
    # Strålmetoden: räkna hur många sidor en stråle åt höger från punkten korsar.
    inside = False
    for a, b in edges:
        if (a.y > point.y) != (b.y > point.y):
            crossing_x = a.x + (point.y - a.y) * (b.x - a.x) / (b.y - a.y)
            if point.x < crossing_x:
                inside = not inside
    return inside


def _overlapping(a: Point, b: Point, c: Point, d: Point, tolerance: float = 1e-6) -> bool:
    """Om sträckorna a–b och c–d ligger på samma linje och delar en bit."""
    dx, dy = b.x - a.x, b.y - a.y
    length = math.hypot(dx, dy)
    if length < tolerance:
        return False
    for p in (c, d):
        if abs(dx * (p.y - a.y) - dy * (p.x - a.x)) / length > tolerance:
            return False
    # Hur långt längs a–b som c och d ligger, i meter.
    along_c = ((c.x - a.x) * dx + (c.y - a.y) * dy) / length
    along_d = ((d.x - a.x) * dx + (d.y - a.y) * dy) / length
    shared = min(length, max(along_c, along_d)) - max(0.0, min(along_c, along_d))
    return shared > tolerance


def _on_segment(point: Point, a: Point, b: Point, tolerance: float = 1e-9) -> bool:
    cross = (b.x - a.x) * (point.y - a.y) - (b.y - a.y) * (point.x - a.x)
    if abs(cross) > tolerance:
        return False
    return (
        min(a.x, b.x) - tolerance <= point.x <= max(a.x, b.x) + tolerance
        and min(a.y, b.y) - tolerance <= point.y <= max(a.y, b.y) + tolerance
    )
