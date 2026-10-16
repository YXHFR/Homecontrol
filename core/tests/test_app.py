import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from zigbee_examples import FONSTERLAMPA, HUE, TAKLAMPA, recorded, zigbee_home

from homecontrol import __version__
from homecontrol.app import create_app
from homecontrol.zigbee import MqttConnection


def test_health_reports_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_serves_web_ui_from_web_dir(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>Homecontrol</title>")
    client = TestClient(create_app(tmp_path))

    response = client.get("/")

    assert response.status_code == 200
    assert "<title>Homecontrol</title>" in response.text


def test_health_wins_over_web_ui(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html>")
    (tmp_path / "health").write_text("fel")
    client = TestClient(create_app(tmp_path))

    assert client.get("/health").json()["status"] == "ok"


def test_missing_web_dir_warns_and_root_is_not_found(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    client = TestClient(create_app(tmp_path / "saknas"))

    assert client.get("/").status_code == 404
    assert "Hittar inte webbgränssnittet" in caplog.text


TEST_HOST = frozenset({"testserver"})
"""TestClient anropar kärnan som http://testserver."""


@contextlib.asynccontextmanager
async def no_broker() -> AsyncIterator[MqttConnection]:
    raise ConnectionError("ingen broker i testet")
    yield  # pragma: no cover


def test_with_zigbee_the_simulation_starts_off_and_zigbee_lamps_are_real() -> None:
    application = create_app(
        home=zigbee_home(hue_placed=True), mqtt=no_broker, allowed_hosts=TEST_HOST
    )

    with TestClient(application) as client, client.websocket_connect("/api/live") as ws:
        home = ws.receive_json()
        state = ws.receive_json()

    virtual = {lamp["id"]: lamp["virtual"] for lamp in home["floors"][0]["lamps"]}
    assert virtual == {TAKLAMPA: False, FONSTERLAMPA: False, "kokslampa": True, HUE: False}
    assert state["simulation"]["enabled"] is False
    assert state["simulation"]["speed_locked"] is True
    # Utan broker svarar ingen riktig lampa, och ingen antas vara släckt.
    lamps = {lamp["id"]: lamp for lamp in state["lamps"]}
    assert lamps[TAKLAMPA]["on"] is None
    assert lamps[TAKLAMPA]["responding"] is False
    assert lamps["kokslampa"]["on"] is False


def test_found_zigbee_lamps_are_listed_for_the_editor() -> None:
    application = create_app(home=zigbee_home(), mqtt=no_broker, allowed_hosts=TEST_HOST)
    for topic, payload in recorded("start"):
        application.state.zigbee.handle(topic, payload)

    with TestClient(application) as client:
        response = client.get("/api/lamps")

    assert response.status_code == 200
    assert {lamp["id"]: (lamp["placed"], lamp["profile"]) for lamp in response.json()} == {
        TAKLAMPA: (True, "ikea-tradfri"),
        FONSTERLAMPA: (True, "ikea-tradfri"),
        HUE: (False, "generisk"),
    }


def test_found_lamps_are_not_shown_to_other_web_pages() -> None:
    with TestClient(create_app(mqtt=no_broker, allowed_hosts=TEST_HOST)) as client:
        response = client.get("/api/lamps", headers={"Origin": "http://elak.example"})

    assert response.status_code == 403


def test_without_zigbee_no_lamps_are_found() -> None:
    with TestClient(create_app(allowed_hosts=TEST_HOST)) as client:
        assert client.get("/api/lamps").json() == []
