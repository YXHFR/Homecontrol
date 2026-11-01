"""Rumskort för testerna: inspelade exempelmeddelanden och ett låtsat nätverk.

Exemplen i esphome_exempel/ har samma form som aioesphomeapi lämnar dem och
samma entiteter som firmware-paketet (se homecontrol/esphome.py).
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aioesphomeapi import model
from aioesphomeapi.model import EntityInfo, EntityState

from homecontrol.esphome import CardEvents, Discovered
from homecontrol.home import Floor, Home, RoomSettings
from homes import FLOOR, radar_node, room

EXAMPLES = Path(__file__).parent / "esphome_exempel"
CARD = "homecontrol-vardagsrum"
SECRET = "test-hemlighet-som-bara-finns-i-testerna"  # noqa: S105

_TYPES = {
    name: getattr(model, name)
    for name in (
        "SensorInfo",
        "BinarySensorInfo",
        "TextSensorInfo",
        "ButtonInfo",
        "LightInfo",
        "LightState",
        "UpdateInfo",
        "SensorState",
        "BinarySensorState",
        "TextSensorState",
        "UpdateState",
    )
}


@dataclass(frozen=True)
class Recording:
    entities: list[EntityInfo]
    states: list[tuple[float, EntityState]]
    """Varje värde och när det kom, i sekunder från anslutningen."""


def recording(name: str) -> Recording:
    data = json.loads((EXAMPLES / f"{name}.json").read_text("utf-8"))

    def parse(message: dict[str, Any]) -> Any:
        fields = {key: value for key, value in message.items() if key not in ("type", "t")}
        return _TYPES[message["type"]].from_dict(fields)

    return Recording(
        [parse(entity) for entity in data["entities"]],
        [(message["t"], parse(message)) for message in data["states"]],
    )


def card_home(*, wifi_weight: float = 0.5, placed: bool = True) -> Home:
    """Ett vardagsrum på 5 × 4 meter där rumskortet sitter mitt på övre väggen.

    Med `placed=False` sitter ett rumskort där som inte är det riktiga kortet.
    """
    living_room = room(
        "vardagsrum",
        "Vardagsrum",
        0,
        0,
        5,
        4,
        lamp="taklampa",
        node=radar_node(CARD if placed else "rumskort-1", 2.5, 0, rotation=90),
    ).model_copy(update={"settings": RoomSettings(wifi_weight=wifi_weight)})
    floor = Floor(id=FLOOR, name="Plan 1", level=0, rooms=(living_room,))
    return Home(name="Korthemmet", floors=(floor,))


@dataclass
class FakeLink:
    name: str
    key: bytes
    events: CardEvents
    running: bool = False
    lights: list[tuple[int, bool, float]] = field(default_factory=list)
    """Ljuskommandona som kortet har fått: nyckel, tänd och ljusstyrka (0–1)."""

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def light(self, key: int, on: bool, brightness: float) -> None:
        self.lights.append((key, on, brightness))


class FakeNetwork:
    def __init__(self) -> None:
        self.links: dict[str, FakeLink] = {}

    def link(self, name: str, key: bytes, events: CardEvents) -> FakeLink:
        self.links[name] = FakeLink(name, key, events)
        return self.links[name]

    async def discover(
        self, found: Callable[[Discovered], None], gone: Callable[[str], None]
    ) -> None:
        await asyncio.Event().wait()  # hittar inga nya kort

    async def close(self) -> None:
        pass
