"""Hemmodellen i Postgres. Kräver HOMECONTROL_TEST_DATABASE (se conftest.py)."""

from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from homecontrol import store
from homecontrol.__main__ import export_home, import_home
from homecontrol.homefile import dumps, example_home, loads
from homes import demo_home


def test_saved_home_is_loaded_unchanged(conn: psycopg.Connection) -> None:
    home = example_home()

    store.save_home(conn, home)

    assert store.load_home(conn) == home


def test_empty_database_has_no_home(conn: psycopg.Connection) -> None:
    assert store.load_home(conn) is None


def test_saving_replaces_the_whole_home(conn: psycopg.Connection) -> None:
    store.save_home(conn, example_home())
    store.save_home(conn, demo_home())

    assert store.load_home(conn) == demo_home()
    assert conn.execute("SELECT count(*) FROM lamp").fetchone() == (1,)


def test_every_concept_has_its_own_table(conn: psycopg.Connection) -> None:
    store.save_home(conn, example_home())

    for table in ("home", "floor", "room", "wall", "zone", "node", "signal_source", "lamp"):
        row = conn.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        ).fetchone()
        assert row is not None
        assert row[0] > 0, table
    doorways = conn.execute("SELECT count(*) FROM wall WHERE kind = 'dörröppning'").fetchone()
    assert doorways is not None
    assert doorways[0] > 0


def test_migrating_again_changes_nothing(conn: psycopg.Connection) -> None:
    store.save_home(conn, example_home())

    store.migrate(conn)

    assert store.load_home(conn) == example_home()
    assert conn.execute("SELECT version FROM schema_migration").fetchall() == [(1,)]


def test_failed_save_keeps_the_old_home(conn: psycopg.Connection) -> None:
    store.save_home(conn, example_home())
    # model_copy kontrollerar inget, så databasens egen kontroll får säga nej
    # mitt i sparandet, efter att det gamla hemmet redan tagits bort.
    room = demo_home().floors[0].rooms[0]
    settings = room.settings.model_copy(update={"dark_below_lux": 500.0})
    floor = (
        demo_home()
        .floors[0]
        .model_copy(update={"rooms": (room.model_copy(update={"settings": settings}),)})
    )
    broken = demo_home().model_copy(update={"floors": (floor,)})

    with pytest.raises(psycopg.errors.CheckViolation):
        store.save_home(conn, broken)

    assert store.load_home(conn) == example_home()


def test_database_refuses_values_outside_the_model(conn: psycopg.Connection) -> None:
    store.save_home(conn, demo_home())

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE wall SET kind = 'lera'")


def test_file_round_trip_through_the_database(database: str, tmp_path: Path) -> None:
    """Fil → import → databas → export → fil ger samma fil."""
    original = tmp_path / "hem.json"
    original.write_text(dumps(example_home()), encoding="utf-8")
    exported = tmp_path / "export.json"

    import_home(database, str(original))
    export_home(database, str(exported))

    assert exported.read_text("utf-8") == original.read_text("utf-8")
    assert loads(exported.read_text("utf-8")) == example_home()


def test_import_of_a_broken_file_leaves_the_home_alone(database: str, tmp_path: Path) -> None:
    import_home(database, None)  # exempelhemmet
    broken = tmp_path / "trasig.json"
    broken.write_text('{"format": "homecontrol-hem", "version": 1, "home": {}}', "utf-8")

    with pytest.raises(SystemExit, match="Kunde inte läsa hemmet"):
        import_home(database, str(broken))

    with store.connect(database, attempts=1) as conn:
        assert store.load_home(conn) == example_home()
