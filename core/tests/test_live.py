import asyncio
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from homecontrol.app import create_app
from homecontrol.clock import SystemClock
from homecontrol.connectors import LightLevelObserved
from homecontrol.controller import Controller
from homecontrol.fusion import HOLD_SECONDS
from homecontrol.home import DEFAULT_OFF_AFTER_EMPTY_SECONDS as OFF_AFTER_EMPTY_SECONDS
from homecontrol.home import Floor, Home, Zone, ZoneKind, rectangle
from homecontrol.live import create_live_router, home_message, run_timer
from homecontrol.simulation import DEFAULT_DAYLIGHT_LUX, SimulatedLampOutput
from homes import FLOOR, one_room_home, radar_sees, room


def app() -> FastAPI:
    # TestClient anropar kärnan som http://testserver.
    return create_app(allowed_hosts={"testserver"}, home=one_room_home())


def lamp_on(state: dict[str, Any]) -> bool:
    on: bool = state["lamps"][0]["on"]
    return on


def test_streams_home_and_state_and_lights_the_lamp() -> None:
    with TestClient(app()) as client, client.websocket_connect("/api/live") as ws:
        home = ws.receive_json()
        assert home["type"] == "home"
        floor = home["floors"][0]
        assert floor["id"] == FLOOR
        assert floor["rooms"][0]["id"] == "vardagsrum"
        assert floor["rooms"][0]["polygon"] == [[0, 0], [5, 0], [5, 4], [0, 4]]
        assert floor["rooms"][0]["walls"] == ["gips"] * 4
        assert floor["rooms"][0]["zones"] == []
        assert floor["lamps"][0] == {
            "id": "taklampa",
            "name": "Taklampa",
            "room_id": "vardagsrum",
            "x": 2.5,
            "y": 2.0,
            "virtual": True,
        }

        # Simuleringen börjar påslagen och mörk.
        state = ws.receive_json()
        assert state["type"] == "state"
        assert not lamp_on(state)
        assert state["simulation"] == {
            "enabled": True,
            "speed": 1,
            "speed_locked": False,
            "daylight": DEFAULT_DAYLIGHT_LUX,
            "persons": [],
        }
        assert state["cards"] is None
        assert state["rooms"][0]["lux"] == DEFAULT_DAYLIGHT_LUX

        ws.send_json({"type": "add_person", "floor_id": FLOOR, "x": 6.0, "y": 2.0})
        state = receive_until(ws, lambda s: s["simulation"]["persons"])
        assert state["simulation"]["persons"] == [{"id": "1", "floor_id": FLOOR, "x": 6, "y": 2}]
        assert not lamp_on(state)

        ws.send_json({"type": "move_person", "person_id": "1", "floor_id": FLOOR, "x": 2.5, "y": 2})
        state = receive_until(ws, lamp_on)
        [track] = state["tracks"]
        assert track == {
            "floor_id": FLOOR,
            "x": pytest.approx(2.5),
            "y": pytest.approx(2),
            "held": False,
        }
        assert state["rooms"][0]["status"] == "upptaget"
        assert "mörkt" in state["lamps"][0]["explanation"]

        # Personen försvinner mitt i rummet: spåret hålls kvar och visas som tappat.
        ws.send_json({"type": "remove_person", "person_id": "1"})
        state = receive_until(ws, lambda s: not s["simulation"]["persons"])
        assert [track["held"] for track in state["tracks"]] == [True]
        assert state["rooms"][0]["status"] == "upptaget"


def test_daylight_speed_and_simulation_switch() -> None:
    with TestClient(app()) as client, client.websocket_connect("/api/live") as ws:
        ws.receive_json()
        ws.receive_json()

        ws.send_json({"type": "set_daylight", "lux": 250})
        state = receive_until(ws, lambda s: s["simulation"]["daylight"] == 250)
        assert state["rooms"][0]["lux"] == 250

        ws.send_json({"type": "set_speed", "speed": 60})
        receive_until(ws, lambda s: s["simulation"]["speed"] == 60)

        ws.send_json({"type": "add_person", "floor_id": FLOOR, "x": 1, "y": 1})
        receive_until(ws, lambda s: s["tracks"])

        # Avslaget läge: personerna och ljusnivåerna försvinner och tiden går normalt.
        # De virtuella korten har tystnat, så ingen vet längre om rummet är tomt.
        ws.send_json({"type": "set_simulation", "enabled": False})
        state = receive_until(ws, lambda s: not s["simulation"]["enabled"])
        assert state["simulation"]["speed"] == 1
        assert state["simulation"]["persons"] == []
        assert state["tracks"] == []
        assert state["rooms"][0]["status"] == "okänt"
        assert state["rooms"][0]["lux"] is None

        # Kommandon till ett avslaget läge ignoreras.
        ws.send_json({"type": "add_person", "floor_id": FLOOR, "x": 1, "y": 1})
        ws.send_json({"type": "set_simulation", "enabled": True})
        state = receive_until(ws, lambda s: s["simulation"]["enabled"])
        assert state["simulation"]["persons"] == []
        assert state["rooms"][0]["lux"] == 250


def test_invalid_messages_are_ignored() -> None:
    application = app()
    with TestClient(application) as client, client.websocket_connect("/api/live") as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "set_speed", "speed": 10})
        receive_until(ws, lambda s: s["simulation"]["speed"] == 10)
        for bad in [
            '{"type": "add_person", "floor_id": "plan", "x": "NaN", "y": 0}',
            '{"type": "add_person", "x": 1, "y": 1}',
            '{"type": "set_speed", "speed": 1000}',
            '{"type": "set_speed", "speed": true}',
            '{"type": "add_person", "floor_id": "vinden", "x": 1, "y": 1}',
            '{"type": "set_daylight", "lux": -1}',
            '{"type": "set_simulation", "enabled": "kanske"}',
            "inte json",
            '{"type": "?"}',
        ]:
            ws.send_text(bad)
        ws.send_json({"type": "add_person", "floor_id": FLOOR, "x": 1e9, "y": 0})
        ws.send_json({"type": "add_person", "floor_id": FLOOR, "x": 1, "y": 1})

        # Bara det sista, giltiga meddelandet har nått kärnan.
        state = receive_until(ws, lambda s: s["simulation"]["persons"])
        assert [(t["x"], t["y"]) for t in state["tracks"]] == [(pytest.approx(1), pytest.approx(1))]
        assert state["simulation"]["speed"] == 10
        assert state["simulation"]["daylight"] == DEFAULT_DAYLIGHT_LUX


def receive_until(ws: Any, condition: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    """Läser lägen tills ett uppfyller villkoret. Kärnan kan skicka flera på vägen."""
    for _ in range(20):
        state: dict[str, Any] = ws.receive_json()
        if condition(state):
            return state
    raise AssertionError("Läget blev aldrig det väntade")


def test_refuses_other_origins() -> None:
    with (
        TestClient(app()) as client,
        pytest.raises((WebSocketDisconnect, WebSocketDenialResponse)),
        client.websocket_connect("/api/live", headers={"origin": "http://elak.example"}) as ws,
    ):
        ws.receive_json()


def test_refuses_unknown_host_names_against_dns_rebinding() -> None:
    with (
        TestClient(create_app()) as client,
        pytest.raises((WebSocketDisconnect, WebSocketDenialResponse)),
        client.websocket_connect(
            "ws://elak.example/api/live", headers={"origin": "http://elak.example"}
        ) as ws,
    ):
        ws.receive_json()


@pytest.mark.parametrize(
    "host", ["localhost:8000", "127.0.0.1:8000", "192.168.1.20", "[::1]:8000", "HEMMA.local"]
)
def test_accepts_localhost_addresses_and_allowed_names(host: str) -> None:
    application = create_app(allowed_hosts={"hemma.local"})
    with (
        TestClient(application) as client,
        client.websocket_connect(
            f"ws://{host}/api/live", headers={"origin": f"http://{host}"}
        ) as ws,
    ):
        assert ws.receive_json()["type"] == "home"


def test_without_simulation_commands_are_ignored_but_state_streams() -> None:
    controller = Controller(one_room_home(), OffsetClock(), [])
    application = FastAPI()
    application.include_router(create_live_router(controller, None, {"testserver"}))
    with TestClient(application) as client, client.websocket_connect("/api/live") as ws:
        ws.receive_json()
        ws.send_json({"type": "add_person", "floor_id": FLOOR, "x": 1, "y": 1})
        state = ws.receive_json()
        assert state["tracks"] == []
        assert state["simulation"] is None
        controller.observe(radar_sees((1, 1), source_id="radar"))
        [track] = ws.receive_json()["tracks"]
        assert track["held"] is False
        assert (track["x"], track["y"]) == (pytest.approx(1), pytest.approx(1))


def test_client_that_leaves_stops_listening() -> None:
    controller = Controller(one_room_home(), OffsetClock(), [])
    listening = 0
    subscribe = controller.subscribe

    def counting_subscribe(listener: Callable[[], None]) -> Callable[[], None]:
        nonlocal listening
        listening += 1
        stop = subscribe(listener)

        def counting_stop() -> None:
            nonlocal listening
            listening -= 1
            stop()

        return counting_stop

    controller.subscribe = counting_subscribe  # type: ignore[method-assign]
    application = FastAPI()
    application.include_router(create_live_router(controller, None, {"testserver"}))
    with TestClient(application) as client:
        with client.websocket_connect("/api/live") as ws:
            ws.receive_json()
            ws.receive_json()
            assert listening == 1

        assert listening == 0


def test_accepts_same_origin() -> None:
    with (
        TestClient(app()) as client,
        client.websocket_connect("/api/live", headers={"origin": "http://testserver"}) as ws,
    ):
        assert ws.receive_json()["type"] == "home"


class OffsetClock(SystemClock):
    """Riktig tid plus en förskjutning, så att testet kan hoppa nästan tre minuter."""

    def __init__(self) -> None:
        self.offset = 0.0
        super().__init__(lambda: time.monotonic() + self.offset)


def test_timer_survives_an_error() -> None:
    async def scenario() -> None:
        clock = OffsetClock()
        controller = Controller(one_room_home(), clock, [])
        timer = asyncio.create_task(run_timer(controller, clock))
        await asyncio.sleep(0)  # låt timern börja lyssna
        original_tick = controller.tick
        calls = 0

        def tick() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("fel i kärnan")
            original_tick()

        controller.tick = tick  # type: ignore[method-assign]
        controller.observe(LightLevelObserved("sim", "vardagsrum", 5))
        await asyncio.wait_for(_until(lambda: calls == 1), timeout=2)
        controller.observe(LightLevelObserved("sim", "vardagsrum", 6))
        await asyncio.wait_for(_until(lambda: calls == 2), timeout=2)
        assert not timer.done()
        timer.cancel()

    asyncio.run(scenario())


def test_timer_switches_off_the_lamp_when_the_delay_is_over() -> None:
    async def scenario() -> None:
        clock = OffsetClock()
        lamps = SimulatedLampOutput()
        controller = Controller(one_room_home(), clock, [lamps])
        timer = asyncio.create_task(run_timer(controller, clock))
        controller.observe(LightLevelObserved("sim", "vardagsrum", 5))
        controller.observe(radar_sees((1, 1)))
        controller.observe(radar_sees())
        assert lamps.states == {"taklampa": True}

        # Hoppa fram till strax före släckning; en ny observation låter timern räkna om.
        # Spåret hålls kvar en stund innan rummet räknas som tomt.
        clock.offset += HOLD_SECONDS + OFF_AFTER_EMPTY_SECONDS - 0.05
        controller.observe(LightLevelObserved("sim", "vardagsrum", 5))
        await asyncio.sleep(0)
        assert lamps.states == {"taklampa": True}

        await asyncio.wait_for(_until(lambda: not lamps.states["taklampa"]), timeout=2)
        timer.cancel()

    asyncio.run(scenario())


async def _until(condition: Any) -> None:
    while not condition():
        await asyncio.sleep(0.01)


def test_timer_follows_a_faster_clock() -> None:
    """Vid 60× släcks ett tomt rum efter tre sekunder i stället för tre minuter."""

    async def scenario() -> None:
        clock = SystemClock()
        lamps = SimulatedLampOutput()
        controller = Controller(one_room_home(), clock, [lamps])
        timer = asyncio.create_task(run_timer(controller, clock))
        controller.observe(LightLevelObserved("sim", "vardagsrum", 5))
        controller.observe(radar_sees((1, 1)))
        controller.observe(radar_sees())
        await asyncio.sleep(0)  # timern väntar nu i tre minuter

        # Takten ändras medan timern väntar; den ska räkna om sin väntan.
        clock.set_speed(1000)
        await asyncio.wait_for(_until(lambda: not lamps.states["taklampa"]), timeout=2)
        timer.cancel()

    asyncio.run(scenario())


def test_home_message_shows_the_zones_of_each_room() -> None:
    sofa = Zone(id="soffa", name="Soffa", kind=ZoneKind.STILL, polygon=rectangle(1, 1, 2, 1))
    living = room("vardagsrum", "Vardagsrum", 0, 0, 5, 4).model_copy(update={"zones": (sofa,)})
    home = Home(name="Hem", floors=(Floor(id=FLOOR, name="Plan 1", level=0, rooms=(living,)),))

    rooms = home_message(home)["floors"][0]["rooms"]

    assert rooms[0]["zones"] == [
        {
            "id": "soffa",
            "name": "Soffa",
            "kind": "stillhet",
            "polygon": [[1, 1], [3, 1], [3, 2], [1, 2]],
        }
    ]
