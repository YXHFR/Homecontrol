"""Realtidsström till livevyn över WebSocket, och timern som driver kärnan i realtid.

Protokollet är JSON. Kärnan skickar:

* ``{"type": "home", ...}`` en gång vid anslutning: rummen och lamporna.
* ``{"type": "state", ...}`` vid anslutning och efter varje ändring.

Livevyn skickar kommandon till låtsaskällan:

* ``{"type": "move_person", "x": 1.0, "y": 2.0}``
* ``{"type": "remove_person"}``
* ``{"type": "set_light_level", "room_id": "vardagsrum", "lux": 10}``

Kommandona går in i kärnan genom Simulering, precis som en riktig källa.
"""

import asyncio
import contextlib
import logging
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from homecontrol.clock import Clock
from homecontrol.controller import Controller, Snapshot
from homecontrol.home import Home, Point
from homecontrol.simulation import SimulatedSource

logger = logging.getLogger(__name__)

# Gränser som håller orimliga värden borta från kärnan. Ett hem är mindre än så här.
_MAX_COORDINATE = 1000.0
_MAX_LUX = 200_000.0  # starkt solljus

Coordinate = Annotated[float, Field(ge=-_MAX_COORDINATE, le=_MAX_COORDINATE, allow_inf_nan=False)]


class MovePerson(BaseModel):
    type: Literal["move_person"]
    x: Coordinate
    y: Coordinate


class RemovePerson(BaseModel):
    type: Literal["remove_person"]


class SetLightLevel(BaseModel):
    type: Literal["set_light_level"]
    room_id: str = Field(max_length=64)
    lux: float = Field(ge=0, le=_MAX_LUX, allow_inf_nan=False)


_client_message: TypeAdapter[MovePerson | RemovePerson | SetLightLevel] = TypeAdapter(
    Annotated[MovePerson | RemovePerson | SetLightLevel, Field(discriminator="type")]
)


def home_message(home: Home) -> dict[str, Any]:
    return {
        "type": "home",
        "rooms": [
            {
                "id": room.id,
                "name": room.name,
                "x": room.x,
                "y": room.y,
                "width": room.width,
                "height": room.height,
            }
            for room in home.rooms
        ],
        "lamps": [
            {
                "id": lamp.id,
                "name": lamp.name,
                "room_id": lamp.room_id,
                "x": lamp.position.x,
                "y": lamp.position.y,
            }
            for lamp in home.lamps
        ],
    }


def state_message(snapshot: Snapshot) -> dict[str, Any]:
    return {
        "type": "state",
        "targets": [{"x": target.x, "y": target.y} for target in snapshot.targets],
        "rooms": [
            {"id": room.room_id, "occupied": room.occupied, "lux": room.lux}
            for room in snapshot.rooms
        ],
        "lamps": [
            {"id": lamp.lamp_id, "on": lamp.on, "explanation": lamp.explanation}
            for lamp in snapshot.lamps
        ],
    }


def create_live_router(controller: Controller, source: SimulatedSource) -> APIRouter:
    router = APIRouter()

    @router.websocket("/api/live")
    async def live(websocket: WebSocket) -> None:
        # Webbläsaren skickar alltid Origin. Utan kontrollen kunde vilken
        # webbsida som helst som någon i hemmet besöker styra simuleringen.
        if not _same_origin(websocket):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await websocket.accept()

        changed = asyncio.Event()
        changed.set()
        unsubscribe = controller.subscribe(changed.set)

        async def send_states() -> None:
            await websocket.send_json(home_message(controller.home))
            while True:
                await changed.wait()
                changed.clear()
                await websocket.send_json(state_message(controller.snapshot()))

        async def receive_commands() -> None:
            while True:
                _handle(source, await websocket.receive_text())

        tasks = [asyncio.create_task(send_states()), asyncio.create_task(receive_commands())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                # Att klienten går är det normala slutet; allt annat ska synas.
                with contextlib.suppress(WebSocketDisconnect):
                    task.result()
        finally:
            unsubscribe()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    return router


def _same_origin(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if origin is None:
        # Inte en webbläsare, till exempel ett skript på samma dator.
        return True
    return urlsplit(origin).netloc == websocket.headers.get("host")


def _handle(source: SimulatedSource, text: str) -> None:
    try:
        message = _client_message.validate_json(text)
    except ValidationError:
        logger.warning("Ogiltigt meddelande från livevyn ignoreras")
        return
    match message:
        case MovePerson(x=x, y=y):
            source.move_person(Point(x, y))
        case RemovePerson():
            source.remove_person()
        case SetLightLevel(room_id=room_id, lux=lux):
            source.set_light_level(room_id, lux)


async def run_timer(controller: Controller, clock: Clock) -> None:
    """Anropar `controller.tick()` när klockan når `wake_at`. Körs tills den avbryts.

    `clock` måste gå i takt med asyncios egen klocka, som `SystemClock` gör.
    """
    changed = asyncio.Event()
    unsubscribe = controller.subscribe(changed.set)
    try:
        while True:
            wake_at = controller.wake_at
            timeout = None if wake_at is None else max(0.0, wake_at - clock.now())
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(changed.wait(), timeout)
            changed.clear()
            controller.tick()
    finally:
        unsubscribe()
