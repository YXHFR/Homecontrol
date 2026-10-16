"""Exempelmeddelandena från Zigbee2MQTT (zigbee_exempel/) och ett hem med Zigbee-lampor."""

import json
from pathlib import Path

from homecontrol.home import Floor, Home, Lamp, LampProfile, Point
from homes import FLOOR, room

EXAMPLES = Path(__file__).parent / "zigbee_exempel"

TAKLAMPA = "zigbee-000b57fffe0000a1"
FONSTERLAMPA = "zigbee-000b57fffe0000a2"
HUE = "zigbee-0017880100000c01"
GROUP_SET = "zigbee2mqtt/homecontrol-vardagsrum/set"


def recorded(name: str) -> list[tuple[str, bytes]]:
    document = json.loads((EXAMPLES / f"{name}.json").read_text("utf-8"))
    return [
        (message["topic"], json.dumps(message["payload"]).encode())
        for message in document["meddelanden"]
    ]


def zigbee_home(*, hue_placed: bool = False) -> Home:
    """Ett vardagsrum med två TRÅDFRI-lampor och ett kök med en virtuell lampa.

    Med `hue_placed` är Hue-lampan också placerad i köket.
    """
    living_room = room("vardagsrum", "Vardagsrum", 0, 0, 5, 4).model_copy(
        update={
            "lamps": (
                zigbee_lamp(TAKLAMPA, "Taklampa", 2.5, 2, LampProfile.IKEA_TRADFRI),
                zigbee_lamp(FONSTERLAMPA, "Fönsterlampa", 4, 1, LampProfile.IKEA_TRADFRI),
            )
        }
    )
    kitchen_lamps = [Lamp(id="kokslampa", name="Kökslampa", position=Point(7, 2))]
    if hue_placed:
        kitchen_lamps.append(zigbee_lamp(HUE, "Hue", 8, 2, LampProfile.GENERIC))
    kitchen = room("kok", "Kök", 5, 0, 5, 4).model_copy(update={"lamps": tuple(kitchen_lamps)})
    return Home(
        name="Zigbeehemmet",
        floors=(Floor(id=FLOOR, name="Plan 1", level=0, rooms=(living_room, kitchen)),),
    )


def zigbee_lamp(lamp: str, name: str, x: float, y: float, profile: LampProfile) -> Lamp:
    return Lamp(id=lamp, name=name, position=Point(x, y), profile=profile)
