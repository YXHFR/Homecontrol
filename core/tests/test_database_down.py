"""Kärnan utan databas: start med kopian av hemmet, varning, nekade ändringar och återanslutning."""

import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from starlette.testclient import WebSocketTestSession

from homecontrol import __main__ as cli
from homecontrol import databaselink, store
from homecontrol.app import create_app
from homecontrol.config import MqttSettings, Settings
from homecontrol.databaselink import DatabaseLink
from homecontrol.home import Home
from homecontrol.homecopy import HomeCopy
from homecontrol.homefile import dumps, to_document
from homes import one_room_home, room

# En databas som inte finns: socketen saknas, så anslutningen misslyckas direkt.
DATABASE_OFF = "host=/finns-inte dbname=homecontrol user=homecontrol_core"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def with_kitchen() -> Home:
    home = one_room_home()
    floor = home.floors[0]
    kitchen = room("kok", "Kök", 5, 0, 3, 4)
    return home.model_copy(
        update={"floors": (floor.model_copy(update={"rooms": (*floor.rooms, kitchen)}),)}
    )


def settings(database: str, copy: Path | None = None, **more: Any) -> Settings:
    return Settings(
        host="127.0.0.1", port=8000, web_dir=Path("web"), database=database, home_copy=copy, **more
    )


def start(settings: Settings) -> FastAPI:
    """Kärnan som `homecontrol` startar den, men utan uvicorn."""
    home, home_store, database = cli.open_home(settings)
    return create_app(
        allowed_hosts={"testserver"}, home=home, home_store=home_store, database=database
    )


def receive_until(ws: WebSocketTestSession, done: Callable[[dict[str, Any]], bool]) -> Any:
    """Tar emot lägen tills ett uppfyller `done`."""
    for _ in range(200):
        message = ws.receive_json()
        if message["type"] == "state" and done(message):
            return message
    raise AssertionError("Läget kom aldrig")


def connected(state: dict[str, Any]) -> bool:
    return bool(state["database"] == {"connected": True})


def disconnected(state: dict[str, Any]) -> bool:
    return bool(state["database"] == {"connected": False})


def lamp_on(state: dict[str, Any], lamp_id: str) -> Any:
    return next(lamp["on"] for lamp in state["lamps"] if lamp["id"] == lamp_id)


def saved_home(database: str) -> Home | None:
    with store.connect(database, attempts=1) as conn:
        return store.load_home(conn)


def save(database: str, home: Home) -> None:
    with store.connect(database, attempts=1) as conn:
        store.migrate(conn)
        store.save_home(conn, home)


class Power:
    """Slår av och på databasen för kärnan, genom att anslutningarna misslyckas."""

    on = True


@pytest.fixture
def power(monkeypatch: pytest.MonkeyPatch) -> Power:
    power = Power()
    real_connect = store.connect

    def connect(conninfo: str, **options: Any) -> psycopg.Connection:
        if not power.on:
            raise psycopg.OperationalError("databasen är avstängd")
        return real_connect(conninfo, **options)

    monkeypatch.setattr(store, "connect", connect)
    return power


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Väntan vid start, som går direkt i testerna."""
    waited: list[float] = []
    monkeypatch.setattr("homecontrol.__main__.time.sleep", waited.append)
    return waited


@pytest.fixture(autouse=True)
def quick_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(databaselink, "RETRY_SECONDS", 0.01)


def test_core_starts_from_the_copy_and_controls_the_lamps_with_the_database_off(
    tmp_path: Path, waits: list[float]
) -> None:
    copy = HomeCopy(tmp_path / "hem.json")
    copy.save(one_room_home())

    app = start(settings(DATABASE_OFF, copy.path))

    # Några sekunders försök, sedan kopian.
    assert waits == [cli.STARTUP_RETRY_SECONDS] * (cli.STARTUP_ATTEMPTS_WITH_COPY - 1)
    assert app.state.controller.home == one_room_home()
    with TestClient(app) as client, client.websocket_connect("/api/live") as ws:
        assert ws.receive_json()["type"] == "home"
        # Varningen i livevyn.
        assert disconnected(ws.receive_json())

        ws.send_json({"type": "set_lamp", "lamp_id": "taklampa", "on": True})

        receive_until(ws, lambda state: lamp_on(state, "taklampa") is True)


@pytest.mark.usefixtures("waits")
def test_changes_are_refused_clearly_while_the_database_is_down(tmp_path: Path) -> None:
    copy = HomeCopy(tmp_path / "hem.json")
    copy.save(one_room_home())
    with TestClient(start(settings(DATABASE_OFF, copy.path))) as client:
        home = client.put("/api/home", json=to_document(with_kitchen()))
        image = client.put("/api/floors/plan/plan-image?width=10", content=PNG)

        for response in (home, image):
            assert response.status_code == 503
            assert "Databasen svarar inte" in response.json()["detail"]
        assert client.get("/api/home").json() == to_document(one_room_home())
    assert copy.load() == one_room_home()


@pytest.mark.usefixtures("waits")
def test_reconnects_and_takes_the_newer_home_from_the_database(
    database: str, tmp_path: Path, power: Power
) -> None:
    """En kopia som är äldre än databasen skriver aldrig över databasens hem."""
    save(database, with_kitchen())
    copy = HomeCopy(tmp_path / "hem.json")
    copy.save(one_room_home())
    power.on = False
    app = start(settings(database, copy.path))
    with TestClient(app) as client, client.websocket_connect("/api/live") as ws:
        assert client.put("/api/home", json=to_document(one_room_home())).status_code == 503

        power.on = True

        receive_until(ws, connected)
        assert app.state.controller.home == with_kitchen()
        assert client.get("/api/home").json() == to_document(with_kitchen())
        assert saved_home(database) == with_kitchen()
        assert copy.load() == with_kitchen()
        # Nu går det att ändra igen.
        assert client.put("/api/home", json=to_document(one_room_home())).status_code == 204
    assert saved_home(database) == one_room_home()
    assert copy.load() == one_room_home()


def test_a_save_that_failed_but_reached_the_database_is_picked_up_again(
    database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save(database, one_room_home())
    app = start(settings(database, tmp_path / "hem.json"))
    real_save = store.save_home
    failures = [psycopg.OperationalError("anslutningen bröts efter sparandet")]

    def save_then_fail(conn: psycopg.Connection, home: Home) -> None:
        real_save(conn, home)
        if failures:
            raise failures.pop()

    monkeypatch.setattr(store, "save_home", save_then_fail)
    with TestClient(app) as client, client.websocket_connect("/api/live") as ws:
        assert connected(receive_until(ws, lambda _: True))

        response = client.put("/api/home", json=to_document(with_kitchen()))

        assert response.status_code == 503
        receive_until(ws, lambda state: connected(state) and len(state["rooms"]) == 2)
        assert app.state.controller.home == with_kitchen()


def test_a_failed_save_shows_the_warning_and_refuses_changes_until_reconnected(
    database: str, tmp_path: Path, power: Power
) -> None:
    save(database, one_room_home())
    app = start(settings(database, tmp_path / "hem.json"))
    with TestClient(app) as client, client.websocket_connect("/api/live") as ws:
        power.on = False

        first = client.put("/api/home", json=to_document(with_kitchen()))
        receive_until(ws, disconnected)
        second = client.put("/api/home", json=to_document(with_kitchen()))

        assert first.status_code == 503
        assert second.status_code == 503
        assert "Databasen svarar inte" in second.json()["detail"]
        power.on = True
        receive_until(ws, connected)
        assert client.put("/api/home", json=to_document(with_kitchen())).status_code == 204


def test_reconnecting_keeps_the_revision_when_the_database_has_the_same_home() -> None:
    link = DatabaseLink(lambda seed: seed, connected=False)
    app = create_app(allowed_hosts={"testserver"}, home=one_room_home(), database=link)
    with TestClient(app) as client, client.websocket_connect("/api/live") as ws:
        etag = client.get("/api/home").headers["etag"]

        receive_until(ws, connected)

        # Samma hem, samma revision: en redigerare som var öppen kan spara direkt.
        assert client.get("/api/home").headers["etag"] == etag


def test_without_a_database_the_live_view_has_no_database_warning() -> None:
    app = create_app(allowed_hosts={"testserver"})
    with TestClient(app) as client, client.websocket_connect("/api/live") as ws:
        assert receive_until(ws, lambda _: True)["database"] is None


def test_the_core_waits_a_little_at_start_before_using_the_copy(
    database: str, tmp_path: Path, power: Power, monkeypatch: pytest.MonkeyPatch
) -> None:
    save(database, with_kitchen())
    copy = HomeCopy(tmp_path / "hem.json")
    copy.save(one_room_home())
    power.on = False
    # Databasen kommer igång medan kärnan väntar på den.
    monkeypatch.setattr("homecontrol.__main__.time.sleep", lambda _: setattr(power, "on", True))

    home, _, link = cli.open_home(settings(database, copy.path))

    assert home == with_kitchen()
    assert link is not None and link.connected
    assert copy.load() == with_kitchen()


def test_without_a_copy_the_core_waits_for_the_database(waits: list[float]) -> None:
    with pytest.raises(psycopg.OperationalError):
        cli.open_home(settings(DATABASE_OFF))

    assert len(waits) == cli.STARTUP_ATTEMPTS - 1


@pytest.mark.usefixtures("waits")
def test_an_unreadable_home_in_the_database_stops_the_core_even_with_a_copy(
    database: str, tmp_path: Path
) -> None:
    document = to_document(one_room_home())
    document["home"]["floors"][0]["rooms"][0]["name"] = ""
    with store.connect(database, attempts=1) as conn:
        store.migrate(conn)
        conn.execute("INSERT INTO home (document) VALUES (%s)", (Jsonb(document),))
    copy = HomeCopy(tmp_path / "hem.json")
    copy.save(with_kitchen())

    with pytest.raises(SystemExit, match="homecontrol importera"):
        cli.open_home(settings(database, copy.path))
    assert copy.load() == with_kitchen()


@pytest.mark.usefixtures("waits")
def test_an_outdated_database_stops_the_core_even_with_a_copy(
    database: str, tmp_path: Path
) -> None:
    copy = HomeCopy(tmp_path / "hem.json")
    copy.save(one_room_home())

    with pytest.raises(SystemExit, match="inte uppdaterad"):
        cli.open_home(settings(database, copy.path, prepare_database=False))


def test_copy_is_written_at_start_and_when_the_home_is_saved(database: str, tmp_path: Path) -> None:
    save(database, one_room_home())
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
    for secret in ("e" * 64, "databaslosenord-123", mqtt_password):
        assert secret not in text
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


def test_an_empty_database_gets_the_home_from_the_copy(database: str, tmp_path: Path) -> None:
    copy = HomeCopy(tmp_path / "hem.json")
    copy.save(with_kitchen())

    home, _, link = cli.open_home(settings(database, copy.path))

    assert home == with_kitchen()
    assert link is not None and link.connected
    assert saved_home(database) == with_kitchen()


def test_a_broken_copy_is_ignored(database: str, tmp_path: Path) -> None:
    path = tmp_path / "hem.json"
    path.write_text("inte json", "utf-8")

    home, _, link = cli.open_home(settings(database, path))

    # Ingen användbar kopia: kärnan väntar på databasen och börjar med exempelhemmet.
    assert link is not None and link.connected
    assert HomeCopy(path).load() == home


def test_database_setup_lets_the_core_start_when_the_database_does_not_respond(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("homecontrol.store.time.sleep", lambda _: None)

    # Avslutas utan fel, så att kärnan (som kräver att db-setup lyckas) startar.
    password = "bara-i-testet"  # noqa: S105
    cli.prepare_database_for_core(
        DATABASE_OFF, settings(DATABASE_OFF, core_database_password=password)
    )


def test_a_database_that_responds_is_told_apart_from_one_that_does_not(database: str) -> None:
    # Svarar databasen, till exempel med fel lösenord, ska db-setup misslyckas.
    assert store.responds(database)
    assert not store.responds(DATABASE_OFF)
