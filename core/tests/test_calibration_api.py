"""Lampkalibreringens API och sparandet av lampornas bidrag (se calibration_api.py)."""

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_live import receive_until

from homecontrol.app import create_app
from homecontrol.calibration_api import calibration_saver
from homecontrol.editor import HomeChanges, MemoryStore
from homecontrol.home import Home, RoomSettings
from homecontrol.images import StoredImage
from homes import home_with_settings, one_room_home
from scenario import Scenario

START = "/api/rooms/vardagsrum/calibration"


def client(home: Home | None = None) -> TestClient:
    # TestClient anropar kärnan som http://testserver.
    return TestClient(
        create_app(allowed_hosts={"testserver"}, home=home if home is not None else one_room_home())
    )


def calibration(state: dict[str, Any]) -> dict[str, Any] | None:
    view: dict[str, Any] | None = state["calibration"]
    return view


@pytest.mark.parametrize(("method", "path"), [("POST", START), ("DELETE", "/api/calibration")])
def test_other_sites_may_not_start_or_stop_it(method: str, path: str) -> None:
    with client() as c:
        other_origin = c.request(method, path, headers={"origin": "http://elak.example"})
        other_host = c.request(method, path, headers={"host": "elak.example"})
    assert other_origin.status_code == 403
    assert other_host.status_code == 403


def test_unknown_room() -> None:
    with client() as c:
        assert c.post("/api/rooms/finns-inte/calibration").status_code == 404


def test_room_without_windows_needs_no_calibration() -> None:
    with client(home_with_settings(RoomSettings(windowless=True))) as c:
        response = c.post(START, headers={"origin": "http://testserver"})
    assert response.status_code == 409
    assert "saknar fönster" in response.json()["detail"]


def test_start_shows_progress_and_cancel_puts_the_lamp_back() -> None:
    with client() as c, c.websocket_connect("/api/live") as ws:
        ws.receive_json()
        assert calibration(ws.receive_json()) is None

        assert c.post(START, headers={"origin": "http://testserver"}).status_code == 202
        state = receive_until(ws, calibration)
        assert state["calibration"]["room_id"] == "vardagsrum"
        assert state["calibration"]["phase"] == "pågår"
        assert state["calibration"]["steps"] == 3
        assert "Lampkalibrering pågår" in state["rooms"][0]["explanation"]

        again = c.post(START)
        assert again.status_code == 409
        assert "pågår redan" in again.json()["detail"]

        assert c.delete("/api/calibration").status_code == 204
        state = receive_until(ws, lambda s: s["calibration"]["phase"] == "avbruten")
        assert "tryckte på Avbryt" in state["calibration"]["text"]
        assert state["lamps"][0]["on"] is False


def test_the_home_message_has_each_lamps_measured_light() -> None:
    with client() as c, c.websocket_connect("/api/live") as ws:
        home = ws.receive_json()
    assert home["floors"][0]["lamps"][0]["lux_contribution"] == 0


class _Unreachable(MemoryStore):
    def save_home(self, home: Home) -> None:
        raise ConnectionError("databasen svarar inte")

    def save_plan_image(self, home: Home, floor_id: str, image: StoredImage) -> None:
        raise ConnectionError("databasen svarar inte")


def _calibrated() -> Scenario:
    return Scenario(lamp_light=True).daylight(10).start_calibration().wait_for_calibration()


def test_saves_the_measured_light_like_the_editor() -> None:
    scenario = _calibrated()
    store = MemoryStore()
    changes = HomeChanges(scenario.controller, store)
    before = changes.revision()

    async def save() -> None:
        calibration_saver(changes)({"taklampa": 12.5})
        await asyncio.sleep(0.05)

    asyncio.run(save())
    assert store.home is not None
    assert store.home.lamps[0].lux_contribution == 12.5
    assert scenario.controller.home.lamps[0].lux_contribution == 12.5
    # Revisionen ändras, så att redigeraren i ett annat fönster inte skriver över bidraget.
    assert changes.revision() != before


def test_tells_when_the_measured_light_could_not_be_saved() -> None:
    scenario = _calibrated().expect_calibration("klar")

    async def save() -> None:
        calibration_saver(HomeChanges(scenario.controller, _Unreachable()))({"taklampa": 12.5})
        await asyncio.sleep(0.05)

    asyncio.run(save())
    scenario.expect_calibration("avbruten", "gick inte att spara")
