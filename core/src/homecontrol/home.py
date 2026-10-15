"""Hemmet: rum och lampor. Koordinater är meter i ett gemensamt plan för hela hemmet.

Än så länge finns bara ett hårdkodat demohem. En riktig hemmodell som går att
rita och spara kommer senare.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Room:
    """Ett rektangulärt rum. (x, y) är hörnet med minst koordinater."""

    id: str
    name: str
    x: float
    y: float
    width: float
    height: float

    def contains(self, point: Point) -> bool:
        return (
            self.x <= point.x <= self.x + self.width and self.y <= point.y <= self.y + self.height
        )

    @property
    def center(self) -> Point:
        return Point(self.x + self.width / 2, self.y + self.height / 2)


@dataclass(frozen=True)
class Lamp:
    id: str
    name: str
    room_id: str
    position: Point


@dataclass(frozen=True)
class Home:
    rooms: tuple[Room, ...]
    lamps: tuple[Lamp, ...]

    def __post_init__(self) -> None:
        room_ids = [room.id for room in self.rooms]
        if len(set(room_ids)) != len(room_ids):
            raise ValueError("Två rum har samma id")
        lamp_ids = [lamp.id for lamp in self.lamps]
        if len(set(lamp_ids)) != len(lamp_ids):
            raise ValueError("Två lampor har samma id")
        for lamp in self.lamps:
            if lamp.room_id not in room_ids:
                raise ValueError(f"Lampan {lamp.id} sitter i ett okänt rum: {lamp.room_id}")

    def room(self, room_id: str) -> Room:
        for room in self.rooms:
            if room.id == room_id:
                return room
        raise KeyError(room_id)


def demo_home() -> Home:
    """Ett vardagsrum på 5 × 4 meter med en taklampa i mitten."""
    living_room = Room(id="vardagsrum", name="Vardagsrum", x=0.0, y=0.0, width=5.0, height=4.0)
    return Home(
        rooms=(living_room,),
        lamps=(
            Lamp(
                id="taklampa",
                name="Taklampa",
                room_id=living_room.id,
                position=living_room.center,
            ),
        ),
    )
