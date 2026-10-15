import asyncio
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from homecontrol.app import create_app
from homecontrol.connectors import LightLevelObserved, TargetsObserved
from homecontrol.controller import Controller
from homecontrol.home import Point, demo_home
from homecontrol.live import run_timer
from homecontrol.rules import OFF_AFTER_EMPTY_SECONDS
from homecontrol.simulation import SimulatedLampOutput


def lamp_on(state: dict[str, Any]) -> bool:
    on: bool = state["lamps"][0]["on"]
    return on


def test_streams_home_and_state_and_lights_the_lamp() -> None:
    with TestClient(create_app()) as client, client.websocket_connect("/api/live") as ws:
        home = ws.receive_json()
        assert home["type"] == "home"
        assert home["rooms"][0]["id"] == "vardagsrum"
        assert home["lamps"][0]["id"] == "taklampa"

        state = ws.receive_json()
        assert state["type"] == "state"
        assert not lamp_on(state)

        ws.send_json({"type": "set_light_level", "room_id": "vardagsrum", "lux": 10})
        assert ws.receive_json()["rooms"][0]["lux"] == 10

        ws.send_json({"type": "move_person", "x": 2.5, "y": 2.0})
        state = ws.receive_json()
        assert state["targets"] == [{"x": 2.5, "y": 2.0}]
        assert lamp_on(state)
        assert "mörkt" in state["lamps"][0]["explanation"]

        ws.send_json({"type": "remove_person"})
        state = ws.receive_json()
        assert state["targets"] == []
        assert not state["rooms"][0]["occupied"]


def test_invalid_messages_are_ignored() -> None:
    app = create_app()
    with TestClient(app) as client, client.websocket_connect("/api/live") as ws:
        ws.receive_json()
        ws.receive_json()
        for bad in ['{"type": "move_person", "x": "NaN", "y": 0}', "inte json", '{"type": "?"}']:
            ws.send_text(bad)
        ws.send_json({"type": "move_person", "x": 1e9, "y": 0})
        ws.send_json({"type": "remove_person"})

        # Bara det sista, giltiga meddelandet har nått kärnan.
        assert ws.receive_json()["targets"] == []
        assert app.state.source.person is None


def test_refuses_other_origins() -> None:
    with (
        TestClient(create_app()) as client,
        pytest.raises((WebSocketDisconnect, WebSocketDenialResponse)),
        client.websocket_connect("/api/live", headers={"origin": "http://elak.example"}) as ws,
    ):
        ws.receive_json()


def test_accepts_same_origin() -> None:
    with (
        TestClient(create_app()) as client,
        client.websocket_connect("/api/live", headers={"origin": "http://testserver"}) as ws,
    ):
        assert ws.receive_json()["type"] == "home"


class OffsetClock:
    """Riktig tid plus en förskjutning, så att testet kan hoppa nästan tre minuter."""

    def __init__(self) -> None:
        self.offset = 0.0

    def now(self) -> float:
        return time.monotonic() + self.offset


def test_timer_switches_off_the_lamp_when_the_delay_is_over() -> None:
    async def scenario() -> None:
        clock = OffsetClock()
        lamps = SimulatedLampOutput()
        controller = Controller(demo_home(), clock, [lamps])
        timer = asyncio.create_task(run_timer(controller, clock))
        controller.observe(LightLevelObserved("sim", "vardagsrum", 5))
        controller.observe(TargetsObserved("sim", (Point(1, 1),)))
        controller.observe(TargetsObserved("sim", ()))
        assert lamps.states == {"taklampa": True}

        # Hoppa fram till strax före släckning; en ny observation låter timern räkna om.
        clock.offset += OFF_AFTER_EMPTY_SECONDS - 0.05
        controller.observe(LightLevelObserved("sim", "vardagsrum", 5))
        await asyncio.sleep(0)
        assert lamps.states == {"taklampa": True}

        await asyncio.wait_for(_until(lambda: not lamps.states["taklampa"]), timeout=2)
        timer.cancel()

    asyncio.run(scenario())


async def _until(condition: Any) -> None:
    while not condition():
        await asyncio.sleep(0.01)
