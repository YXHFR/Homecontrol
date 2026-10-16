"""Realtidsström till livevyn över WebSocket, och timern som driver kärnan i realtid.

Protokollet är JSON. Kärnan skickar:

* ``{"type": "home", ...}`` vid anslutning och när hemmet har ändrats, till
  exempel i kartredigeraren: våningarna med sina rum, väggar, zoner, rumskort
  och lampor. Punkter är ``[x, y]`` i meter med origo uppe till vänster.
  En lampa som ingen riktig lamputgång styr är ``"virtual": true``.
* ``{"type": "state", ...}`` vid anslutning och efter varje ändring: spåren
  (``held`` är ett spår som radarn har tappat men som hålls kvar), läget i
  varje rum och zon (``"upptaget"``, ``"tomt"`` eller ``"okänt"``), lamporna
  och simuleringen. Fältet ``simulation`` visar simuleringsläget, eller är
  null om det inte finns. Fältet ``cards`` visar de riktiga rumskorten (se
  `cards_message`), eller är null om inga kort kan anslutas.

Livevyn kan be om en uppdatering av ett korts firmware, som användaren har
godkänt genom att trycka på knappen:

* ``{"type": "install_update", "card": "homecontrol-kok"}``

Livevyn skickar kommandon till simuleringen:

* ``{"type": "set_simulation", "enabled": true}``
* ``{"type": "add_person", "floor_id": "entreplan", "x": 1.0, "y": 2.0}``
* ``{"type": "move_person", "person_id": "1", "floor_id": "entreplan", "x": 1.0, "y": 2.0}``
* ``{"type": "remove_person", "person_id": "1"}``
* ``{"type": "set_daylight", "lux": 10}``
* ``{"type": "set_speed", "speed": 60}``

Personerna och dagsljuset går in i kärnan genom låtsaskällan, precis som en
riktig källa. Utan simulering, eller när simuleringsläget är avslaget,
ignoreras kommandona, men läget strömmas ändå.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable, Sequence, Set
from typing import Annotated, Any, Literal, Protocol

import anyio
import anyio.abc
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator

from homecontrol.clock import PacedClock
from homecontrol.controller import Controller, Snapshot
from homecontrol.esphome import CardView
from homecontrol.home import Home, Point
from homecontrol.simulation import Simulation, SimulationView, Speed
from homecontrol.trust import trusted

logger = logging.getLogger(__name__)

# Gränser som håller orimliga värden borta från kärnan. Ett hem är mindre än så här.
_MAX_COORDINATE = 1000.0
_MAX_LUX = 200_000.0  # starkt solljus

Coordinate = Annotated[float, Field(ge=-_MAX_COORDINATE, le=_MAX_COORDINATE, allow_inf_nan=False)]


PersonId = Annotated[str, Field(max_length=64)]
FloorId = Annotated[str, Field(max_length=64)]


class SetSimulation(BaseModel):
    type: Literal["set_simulation"]
    enabled: bool


class AddPerson(BaseModel):
    type: Literal["add_person"]
    floor_id: FloorId
    x: Coordinate
    y: Coordinate


class MovePerson(BaseModel):
    type: Literal["move_person"]
    person_id: PersonId
    floor_id: FloorId
    x: Coordinate
    y: Coordinate


class RemovePerson(BaseModel):
    type: Literal["remove_person"]
    person_id: PersonId


class SetDaylight(BaseModel):
    type: Literal["set_daylight"]
    lux: float = Field(ge=0, le=_MAX_LUX, allow_inf_nan=False)


class SetSpeed(BaseModel):
    type: Literal["set_speed"]
    speed: Speed

    @field_validator("speed", mode="before")
    @classmethod
    def _not_bool(cls, value: object) -> object:
        # Annars tolkas true som tidsfarten 1.
        if isinstance(value, bool):
            raise ValueError("Tidsfarten är ett tal")
        return value


class InstallUpdate(BaseModel):
    type: Literal["install_update"]
    card: Annotated[str, Field(max_length=64)]


ClientMessage = (
    SetSimulation | AddPerson | MovePerson | RemovePerson | SetDaylight | SetSpeed | InstallUpdate
)


class Cards(Protocol):
    """De riktiga rumskorten (se esphome.py)."""

    def cards(self) -> Sequence[CardView]: ...

    def install_update(self, name: str) -> bool: ...

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]: ...


_client_message: TypeAdapter[ClientMessage] = TypeAdapter(
    Annotated[ClientMessage, Field(discriminator="type")]
)


def home_message(home: Home, virtual_lamps: Set[str] = frozenset()) -> dict[str, Any]:
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
                        "zones": [
                            {
                                "id": zone.id,
                                "name": zone.name,
                                "kind": zone.kind.value,
                                "polygon": [list(point) for point in zone.polygon],
                            }
                            for zone in room.zones
                        ],
                    }
                    for room in floor.rooms
                ],
                "nodes": [
                    {
                        "id": node.id,
                        "name": node.name,
                        "room_id": room.id,
                        "x": node.position.x,
                        "y": node.position.y,
                    }
                    for room in floor.rooms
                    for node in room.nodes
                ],
                "lamps": [
                    {
                        "id": lamp.id,
                        "name": lamp.name,
                        "room_id": room.id,
                        "x": lamp.position.x,
                        "y": lamp.position.y,
                        "virtual": lamp.id in virtual_lamps,
                    }
                    for room in floor.rooms
                    for lamp in room.lamps
                ],
            }
            for floor in home.floors
        ],
    }


def state_message(
    snapshot: Snapshot,
    simulation: SimulationView | None,
    cards: Sequence[CardView] | None = None,
) -> dict[str, Any]:
    return {
        "type": "state",
        "tracks": [
            {
                "floor_id": track.floor_id,
                "x": track.position.x,
                "y": track.position.y,
                "held": track.held,
            }
            for track in snapshot.tracks
        ],
        "rooms": [
            {"id": room.room_id, "status": room.status.value, "lux": room.lux}
            for room in snapshot.rooms
        ],
        "zones": [{"id": zone.zone_id, "status": zone.status.value} for zone in snapshot.zones],
        "lamps": [
            {"id": lamp.lamp_id, "on": lamp.on, "explanation": lamp.explanation}
            for lamp in snapshot.lamps
        ],
        "simulation": None if simulation is None else simulation_message(simulation),
        "cards": None if cards is None else cards_message(cards),
    }


def cards_message(cards: Sequence[CardView]) -> list[dict[str, Any]]:
    """Korten: placerade kort med sina varningar, och nya kort som väntar på att placeras.

    Ett kort är ``"placed": false`` tills det har placerats i kartredigeraren.
    ``problems`` är varningar i klartext. ``update`` är null, eller den version
    som kortet kan uppdateras till.
    """
    return [
        {
            "id": card.name,
            "name": card.friendly_name,
            "placed": card.placed,
            "online": card.online,
            "problems": list(card.problems),
            "calibrating": card.calibrating,
            "wifi_signal": card.wifi_signal,
            "firmware_version": card.firmware_version,
            "update": (
                None
                if card.update is None or not card.update.available
                else {
                    "version": card.update.latest_version,
                    "in_progress": card.update.in_progress,
                    "progress": card.update.progress,
                }
            ),
        }
        for card in cards
    ]


def simulation_message(simulation: SimulationView) -> dict[str, Any]:
    return {
        "enabled": simulation.enabled,
        "speed": simulation.speed,
        "speed_locked": simulation.speed_locked,
        "daylight": simulation.daylight,
        "persons": [
            {
                "id": person_id,
                "floor_id": person.floor_id,
                "x": person.position.x,
                "y": person.position.y,
            }
            for person_id, person in simulation.persons.items()
        ],
    }


def create_live_router(
    controller: Controller,
    simulation: Simulation | None,
    allowed_hosts: Set[str] = frozenset(),
    virtual_lamps: Callable[[Home], Set[str]] = lambda _: frozenset(),
    cards: Cards | None = None,
) -> APIRouter:
    """`allowed_hosts` är värdnamn utöver localhost och IP-adresser som livevyn får nås på.

    `virtual_lamps` ger lamporna i ett hem som bara finns i simuleringen, utan
    någon riktig lampa. Den anropas varje gång hemmet har bytts.

    `cards` är de riktiga rumskorten. Kartredigeraren hämtar dem från
    ``GET /api/cards`` (samma form som ``cards`` i läget) för att kunna
    placera nya kort.
    """
    router = APIRouter()

    @router.get("/api/cards")
    def get_cards(request: Request) -> JSONResponse:
        # Samma kontroll som strömmen: korten säger vilka rum som finns.
        if not trusted(request.headers, allowed_hosts):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Okänd adress eller avsändare")
        return JSONResponse(
            [] if cards is None else cards_message(cards.cards()),
            headers={"Cache-Control": "no-store"},
        )

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
        unsubscribes = [controller.subscribe(changed.set)]
        for source in (simulation, cards):
            if source is not None:
                unsubscribes.append(source.subscribe(changed.set))

        async def send_states() -> None:
            sent_home: Home | None = None
            while True:
                await changed.wait()
                changed.clear()
                if controller.home is not sent_home:
                    sent_home = controller.home
                    await websocket.send_json(home_message(sent_home, virtual_lamps(sent_home)))
                view = None if simulation is None else simulation.view()
                card_views = None if cards is None else cards.cards()
                await websocket.send_json(state_message(controller.snapshot(), view, card_views))

        async def receive_commands(group: anyio.abc.TaskGroup) -> None:
            try:
                while True:
                    _handle(simulation, cards, await websocket.receive_text())
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
            for unsubscribe in unsubscribes:
                unsubscribe()

    return router


def _handle(simulation: Simulation | None, cards: Cards | None, text: str) -> None:
    try:
        message = _client_message.validate_json(text)
    except ValidationError:
        logger.warning("Ogiltigt meddelande från livevyn ignoreras")
        return
    if isinstance(message, InstallUpdate):
        if cards is None or not cards.install_update(message.card):
            logger.info("Kortet kan inte uppdateras just nu")
        return
    if simulation is None:
        return
    match message:
        case SetSimulation(enabled=enabled):
            simulation.set_enabled(enabled)
        case AddPerson(floor_id=floor_id, x=x, y=y):
            simulation.add_person(floor_id, Point(x, y))
        case MovePerson(person_id=person_id, floor_id=floor_id, x=x, y=y):
            simulation.move_person(person_id, floor_id, Point(x, y))
        case RemovePerson(person_id=person_id):
            simulation.remove_person(person_id)
        case SetDaylight(lux=lux):
            simulation.set_daylight(lux)
        case SetSpeed(speed=speed):
            simulation.set_speed(speed)


async def run_timer(controller: Controller, clock: PacedClock) -> None:
    """Anropar `controller.tick()` när klockan når `wake_at`. Körs tills den avbryts.

    Väntetiden räknas om när kärnan ändras och när klockans takt ändras, så
    att en släckning kommer i tid även när tiden går fortare.
    """
    changed = asyncio.Event()
    unsubscribes = [controller.subscribe(changed.set), clock.subscribe(changed.set)]
    try:
        while True:
            wake_at = controller.wake_at
            timeout = None if wake_at is None else clock.real_seconds_until(wake_at)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(changed.wait(), timeout)
            changed.clear()
            try:
                controller.tick()
            except Exception:
                # Timern måste leva vidare, annars släcks inga tomma rum.
                logger.exception("Fel när kärnan väcktes")
    finally:
        for unsubscribe in unsubscribes:
            unsubscribe()
