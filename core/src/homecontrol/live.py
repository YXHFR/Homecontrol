"""Realtidsström till livevyn över WebSocket, och timern som driver kärnan i realtid.

Protokollet är JSON. Kärnan skickar:

* ``{"type": "home", ...}`` vid anslutning och när hemmet har ändrats, till
  exempel i kartredigeraren: våningarna med sina rum, väggar och lampor.
  Punkter är ``[x, y]`` i meter med origo uppe till vänster.
* ``{"type": "state", ...}`` vid anslutning och efter varje ändring.

Livevyn skickar kommandon till låtsaskällan:

* ``{"type": "move_person", "floor_id": "entreplan", "x": 1.0, "y": 2.0}``
* ``{"type": "remove_person"}``
* ``{"type": "set_light_level", "room_id": "vardagsrum", "lux": 10}``

Kommandona går in i kärnan genom Simulering, precis som en riktig källa.
Utan simulering ignoreras de, men läget strömmas ändå.
"""

import asyncio
import contextlib
import logging
from collections.abc import Set
from typing import Annotated, Any, Literal

import anyio
import anyio.abc
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from homecontrol.clock import Clock
from homecontrol.controller import Controller, Snapshot
from homecontrol.home import Home, Point
from homecontrol.simulation import SimulatedSource
from homecontrol.trust import trusted

logger = logging.getLogger(__name__)

# Gränser som håller orimliga värden borta från kärnan. Ett hem är mindre än så här.
_MAX_COORDINATE = 1000.0
_MAX_LUX = 200_000.0  # starkt solljus

Coordinate = Annotated[float, Field(ge=-_MAX_COORDINATE, le=_MAX_COORDINATE, allow_inf_nan=False)]


class MovePerson(BaseModel):
    type: Literal["move_person"]
    floor_id: str = Field(max_length=64)
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
        "name": home.name,
        "floors": [
            {
                "id": floor.id,
                "name": floor.name,
                "level": floor.level,
                "rooms": [
                    {
                        "id": room.id,
                        "name": room.name,
                        "polygon": [list(point) for point in room.polygon],
                        "walls": [kind.value for kind in room.walls],
                    }
                    for room in floor.rooms
                ],
                "lamps": [
                    {
                        "id": lamp.id,
                        "name": lamp.name,
                        "room_id": room.id,
                        "x": lamp.position.x,
                        "y": lamp.position.y,
                    }
                    for room in floor.rooms
                    for lamp in room.lamps
                ],
            }
            for floor in home.floors
        ],
    }


def state_message(snapshot: Snapshot) -> dict[str, Any]:
    return {
        "type": "state",
        "targets": [
            {"floor_id": t.floor_id, "x": t.position.x, "y": t.position.y} for t in snapshot.targets
        ],
        "rooms": [
            {"id": room.room_id, "occupied": room.occupied, "lux": room.lux}
            for room in snapshot.rooms
        ],
        "lamps": [
            {"id": lamp.lamp_id, "on": lamp.on, "explanation": lamp.explanation}
            for lamp in snapshot.lamps
        ],
    }


def create_live_router(
    controller: Controller,
    source: SimulatedSource | None,
    allowed_hosts: Set[str] = frozenset(),
) -> APIRouter:
    """`allowed_hosts` är värdnamn utöver localhost och IP-adresser som livevyn får nås på."""
    router = APIRouter()

    @router.websocket("/api/live")
    async def live(websocket: WebSocket) -> None:
        # Utan kontrollerna kunde vilken webbsida som helst som någon i hemmet
        # besöker läsa strömmen och styra simuleringen.
        if not trusted(websocket.headers, allowed_hosts):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await websocket.accept()

        changed = asyncio.Event()
        changed.set()
        unsubscribe = controller.subscribe(changed.set)

        async def send_states() -> None:
            sent_home: Home | None = None
            while True:
                await changed.wait()
                changed.clear()
                if controller.home is not sent_home:
                    sent_home = controller.home
                    await websocket.send_json(home_message(sent_home))
                await websocket.send_json(state_message(controller.snapshot()))

        async def receive_commands(group: anyio.abc.TaskGroup) -> None:
            try:
                while True:
                    _handle(source, await websocket.receive_text())
            except WebSocketDisconnect:
                # Att klienten går är det normala slutet; då behövs inga fler lägen.
                group.cancel_scope.cancel()

        # En anyio-grupp, inte lösa asyncio-uppgifter, så att Starlettes egen
        # avbrytning (när servern eller testklienten stänger) når båda uppgifterna.
        try:
            async with anyio.create_task_group() as group:
                group.start_soon(send_states)
                group.start_soon(receive_commands, group)
        except* WebSocketDisconnect:
            pass  # klienten hann gå medan ett läge skickades
        finally:
            unsubscribe()

    return router


def _handle(source: SimulatedSource | None, text: str) -> None:
    if source is None:
        return
    try:
        message = _client_message.validate_json(text)
    except ValidationError:
        logger.warning("Ogiltigt meddelande från livevyn ignoreras")
        return
    match message:
        case MovePerson(floor_id=floor_id, x=x, y=y):
            source.move_person(floor_id, Point(x, y))
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
            try:
                controller.tick()
            except Exception:
                # Timern måste leva vidare, annars släcks inga tomma rum.
                logger.exception("Fel när kärnan väcktes")
    finally:
        unsubscribe()
