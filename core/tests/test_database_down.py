"""Kärnan utan databas: start med kopian av hemmet, varning, nekade ändringar och återanslutning."""

import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from homecontrol import __main__ as cli
from homecontrol import store
from homecontrol.app import create_app
from homecontrol.config import MqttSettings, Settings
from homecontrol.databaselink import DatabaseLink
from homecontrol.editor import MemoryStore
from homecontrol.home import Home
from homecontrol.homecopy import CopyingStore, HomeCopy
from homecontrol.homefile import dumps, to_document
from homes import one_room_home, room

# En databas som inte finns: socketen saknas, så anslutningen misslyckas direkt.
DATABASE_OFF = "host=/finns-inte dbname=homecontrol user=homecontrol_core"


def with_kitchen() -> Home:
    home = one_room_home()
    floor = home.floors[0]
    kitchen = room("kok", "Kök", 5, 0, 3, 4)
    return home.model_copy(
        update={"floors": (floor.model_copy(update={"rooms": (*floor.rooms, kitchen)}),)}
    )


def settings(database: str, copy: Path, **more: Any) -> Settings:
    return Settings(
        host="127.0.0.1", port=8000, web_dir=Path("web"), database=database, home_copy=copy, **more
    )


def receive_until(ws: WebSocketTestSession, done: Callable[[dict[str, Any]], bool]) -> Any:
    """Tar emot lägen tills ett uppfyller `done`."""
    for _ in range(100):
        message = ws.receive_json()
        if message["type"] == "state" and done(message):
            return message
    raise AssertionError("Läget kom aldrig")


def lamp_on(state: dict[str, Any], lamp_id: str) -> Any:
    return next(lamp["on"] for lamp in state["lamps"] if lamp["id"] == lamp_id)


@pytest.fixture
def quick_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kärnan ger upp om databasen direkt vid start, i stället för efter tio sekunder."""
    monkeypatch.setattr(cli, "STARTUP_ATTEMPTS_WITH_COPY", 1)


@pytest.mark.usefixtures("quick_start")
def test_core_starts_from_the_copy_and_controls_the_lamps_with_the_database_off(
    tmp_path: Path,
) -> None:
    copy = HomeCopy(tmp_path / "hem.json")
    copy.save(one_room_home())

    home, home_store, database = cli.open_home(settings(DATABASE_OFF, copy.path))

    assert home == one_room_home()
    assert database is not None and not database.connected
    app = create_app(
        allowed_hosts={"testserver"}, home=home, home_store=home_store, database=database
    )
    with TestClient(app) as client, client.websocket_connect("/api/live") as ws:
        assert ws.receive_json()["type"] == "home"
        state = ws.receive_json()
        # Varningen i livevyn.
        assert state["database"] == {"connected": False}

        ws.send_json({"type": "set_lamp", "lamp_id": "taklampa", "on": True})

        receive_until(ws, lambda state: lamp_on(state, "taklampa") is True)


@pytest.mark.usefixtures("quick_start")
def test_changes_are_refused_clearly_while_the_database_is_down(tmp_path: Path) -> None:
    copy = HomeCopy(tmp_path / "hem.json")
    copy.save(one_room_home())
    home, home_store, database = cli.open_home(settings(DATABASE_OFF, copy.path))
    app = create_app(
        allowed_hosts={"testserver"}, home=home, home_store=home_store, database=database
    )
    with TestClient(app) as client:
        response = client.put("/api/home", json=to_document(with_kitchen()))

        assert response.status_code == 503
        assert "Databasen svarar inte" in response.json()["detail"]
        assert client.get("/api/home").json() == to_document(one_room_home())
    assert copy.load() == one_room_home()


def test_reconnects_and_takes_the_newer_home_from_the_database(
    database: str, tmp_path: Path
) -> None:
    """En kopia som är äldre än databasen skriver aldrig över databasens hem."""
    with store.connect(database, attempts=1) as conn:
        store.migrate(conn)
        store.save_home(conn, with_kitchen())
    copy = HomeCopy(tmp_path / "hem.json")
    copy.save(one_room_home())
    database_store = store.DatabaseStore(database)
    database_off = True

    def fetch(seed: Home) -> Home:
        if database_off:
            raise psycopg.OperationalError("databasen är avstängd")
        home = database_store.fetch_home(seed)
        copy.save(home)
        return home

    link = DatabaseLink(fetch, connected=False, retry_seconds=0.01)
    app = create_app(
        allowed_hosts={"testserver"},
        home=copy.load(),
        home_store=CopyingStore(database_store, copy),
        database=link,
    )
    with TestClient(app) as client, client.websocket_connect("/api/live") as ws:
        assert client.put("/api/home", json=to_document(one_room_home())).status_code == 503

        database_off = False

        receive_until(ws, lambda state: state["database"] == {"connected": True})
        assert app.state.controller.home == with_kitchen()
        assert client.get("/api/home").json() == to_document(with_kitchen())
        # Nu går det att ändra igen.
        assert client.put("/api/home", json=to_document(one_room_home())).status_code == 204
    with store.connect(database, attempts=1) as conn:
        assert store.load_home(conn) == one_room_home()
    assert copy.load() == one_room_home()


def test_reconnecting_keeps_the_home_when_the_database_has_the_same(tmp_path: Path) -> None:
    link = DatabaseLink(lambda seed: seed, connected=False, retry_seconds=0.01)
    app = create_app(allowed_hosts={"testserver"}, home=one_room_home(), database=link)
    with TestClient(app) as client, client.websocket_connect("/api/live") as ws:
        etag = client.get("/api/home").headers["etag"]

        receive_until(ws, lambda state: state["database"] == {"connected": True})

        # Samma hem, samma revision: en redigerare som var öppen kan spara direkt.
        assert client.get("/api/home").headers["etag"] == etag


def test_a_failed_save_shows_the_warning_and_refuses_changes_until_reconnected() -> None:
    class Broken(MemoryStore):
        def save_home(self, home: Home) -> None:
            raise psycopg.OperationalError("databasen försvann")

    reconnect = False

    def fetch(seed: Home) -> Home:
        if not reconnect:
            raise psycopg.OperationalError("fortfarande borta")
        return seed

    link = DatabaseLink(fetch, retry_seconds=0.01)
    app = create_app(
        allowed_hosts={"testserver"}, home=one_room_home(), home_store=Broken(), database=link
    )
    with TestClient(app) as client, client.websocket_connect("/api/live") as ws:
        assert receive_until(ws, lambda _: True)["database"] == {"connected": True}

        first = client.put("/api/home", json=to_document(with_kitchen()))
        receive_until(ws, lambda state: state["database"] == {"connected": False})
        second = client.put("/api/home", json=to_document(with_kitchen()))

        assert first.status_code == 503
        assert second.status_code == 503
        assert "Databasen svarar inte" in second.json()["detail"]
        reconnect = True
        receive_until(ws, lambda state: state["database"] == {"connected": True})


def test_without_a_database_the_live_view_has_no_database_warning() -> None:
    app = create_app(allowed_hosts={"testserver"})
    with TestClient(app) as client, client.websocket_connect("/api/live") as ws:
        assert receive_until(ws, lambda _: True)["database"] is None


def test_copy_is_written_at_start_and_when_the_home_is_saved(database: str, tmp_path: Path) -> None:
    with store.connect(database, attempts=1) as conn:
        store.migrate(conn)
        store.save_home(conn, one_room_home())
    path = tmp_path / "data" / "hem.json"

    home, home_store, link = cli.open_home(settings(database, path))

    assert home == one_room_home()
    assert link is not None and link.connected
    assert HomeCopy(path).load() == one_room_home()
    assert home_store is not None
    home_store.save_home(with_kitchen())
    assert HomeCopy(path).load() == with_kitchen()


def test_copy_is_the_export_file_without_secrets_and_only_readable_by_the_core(
    database: str, tmp_path: Path
) -> None:
    path = tmp_path / "data" / "hem.json"
    mqtt_password = "mqtt-456"  # noqa: S105
    secrets = {
        "esphome_secret": "e" * 64,
        "core_database_password": "databaslosenord-123",
        "mqtt": MqttSettings(host="127.0.0.1", username="homecontrol_core", password=mqtt_password),
    }

    home, _, _ = cli.open_home(settings(database, path, **secrets))

    text = path.read_text("utf-8")
    assert text == dumps(home)
    for secret in ("e" * 64, "databaslosenord-123", "mqtt-456"):
        assert secret not in text
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


@pytest.mark.usefixtures("quick_start")
def test_an_empty_database_gets_the_home_from_the_copy(database: str, tmp_path: Path) -> None:
    copy = HomeCopy(tmp_path / "hem.json")
    copy.save(with_kitchen())

    home, _, link = cli.open_home(settings(database, copy.path))

    assert home == with_kitchen()
    assert link is not None and link.connected
    with store.connect(database, attempts=1) as conn:
        assert store.load_home(conn) == with_kitchen()


def test_a_broken_copy_is_ignored(database: str, tmp_path: Path) -> None:
    path = tmp_path / "hem.json"
    path.write_text("inte json", "utf-8")

    home, _, link = cli.open_home(settings(database, path))

    # Ingen användbar kopia: kärnan väntar på databasen och börjar med exempelhemmet.
    assert link is not None and link.connected
    assert HomeCopy(path).load() == home


def test_without_a_copy_the_core_waits_for_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []
    monkeypatch.setattr("homecontrol.store.time.sleep", waits.append)

    with pytest.raises(psycopg.OperationalError):
        cli.open_home(
            Settings(host="127.0.0.1", port=8000, web_dir=Path("web"), database=DATABASE_OFF)
        )
    assert sum(waits) >= 30  # som förut, inte bara några sekunder
