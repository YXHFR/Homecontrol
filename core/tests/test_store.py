"""Hemmodellen i Postgres. Kräver HOMECONTROL_TEST_DATABASE (se conftest.py)."""

from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from homecontrol import store
from homecontrol.__main__ import export_home, import_home
from homecontrol.homefile import FORMAT, VERSION, HomeFileError, dumps, example_home, loads
from homecontrol.images import StoredImage
from homes import one_room_home


def test_saved_home_is_loaded_unchanged(conn: psycopg.Connection) -> None:
    home = example_home()

    store.save_home(conn, home)

    assert store.load_home(conn) == home


def test_empty_database_has_no_home(conn: psycopg.Connection) -> None:
    assert store.load_home(conn) is None


def test_saving_replaces_the_whole_home(conn: psycopg.Connection) -> None:
    store.save_home(conn, example_home())
    store.save_home(conn, one_room_home())

    assert store.load_home(conn) == one_room_home()
    assert conn.execute("SELECT count(*) FROM home").fetchone() == (1,)


def test_home_is_stored_in_the_same_form_as_the_file(conn: psycopg.Connection) -> None:
    store.save_home(conn, example_home())

    row = conn.execute("SELECT document FROM home").fetchone()

    assert row is not None
    document = row[0]
    assert (document["format"], document["version"]) == (FORMAT, VERSION)
    rooms = document["home"]["floors"][0]["rooms"]
    assert rooms[0]["walls"] == ["betong", "dörröppning", "dörröppning", "betong"]


def test_broken_document_in_the_database_is_reported(conn: psycopg.Connection) -> None:
    conn.execute(
        "INSERT INTO home (document) VALUES (%s)",
        (Jsonb({"format": FORMAT, "version": VERSION, "home": {"name": "H", "floors": []}}),),
    )

    with pytest.raises(HomeFileError, match="minst en våning"):
        store.load_home(conn)


def test_migrating_again_changes_nothing(conn: psycopg.Connection) -> None:
    store.save_home(conn, example_home())

    store.migrate(conn)

    assert store.load_home(conn) == example_home()
    assert conn.execute("SELECT version FROM schema_migration").fetchall() == [(1,), (2,)]


def test_file_round_trip_through_the_database(database: str, tmp_path: Path) -> None:
    """Fil → import → databas → export → fil ger samma fil."""
    original = tmp_path / "hem.json"
    original.write_text(dumps(example_home()), encoding="utf-8")
    exported = tmp_path / "export.json"

    import_home(database, str(original))
    export_home(database, str(exported))

    assert exported.read_text("utf-8") == original.read_text("utf-8")
    assert loads(exported.read_text("utf-8")) == example_home()


def test_exported_file_can_only_be_read_by_its_owner(database: str, tmp_path: Path) -> None:
    import_home(database, None)
    exported = tmp_path / "export.json"

    export_home(database, str(exported))

    assert exported.stat().st_mode & 0o777 == 0o600


def test_import_of_a_broken_file_leaves_the_home_alone(database: str, tmp_path: Path) -> None:
    import_home(database, None)  # exempelhemmet
    broken = tmp_path / "trasig.json"
    broken.write_text('{"format": "homecontrol-hem", "version": 1, "home": {}}', "utf-8")

    with pytest.raises(SystemExit, match="Kunde inte läsa hemmet"):
        import_home(database, str(broken))

    with store.connect(database, attempts=1) as conn:
        assert store.load_home(conn) == example_home()


def test_plan_image_is_stored_per_floor(conn: psycopg.Connection) -> None:
    store.save_home(conn, example_home())
    floor_id = example_home().floors[0].id
    image = StoredImage("image/png", b"\x89PNG\r\n\x1a\nbild")

    store.save_plan_image(conn, floor_id, image)

    assert store.load_plan_image(conn, floor_id) == image
    assert store.load_plan_image(conn, example_home().floors[1].id) is None
    store.delete_plan_image(conn, floor_id)
    assert store.load_plan_image(conn, floor_id) is None


def test_saving_a_home_forgets_images_of_removed_floors(conn: psycopg.Connection) -> None:
    store.save_home(conn, example_home())
    image = StoredImage("image/jpeg", b"\xff\xd8\xffbild")
    for floor in example_home().floors:
        store.save_plan_image(conn, floor.id, image)

    store.save_home(conn, one_room_home())

    assert conn.execute("SELECT count(*) FROM plan_image").fetchone() == (0,)


def test_database_store_saves_what_the_editor_changes(database: str) -> None:
    with store.connect(database, attempts=1) as conn:
        store.migrate(conn)
    database_store = store.DatabaseStore(database)
    image = StoredImage("image/webp", b"RIFF\0\0\0\0WEBP")

    database_store.save_home(example_home())
    database_store.save_plan_image(one_room_home(), "plan", image)

    with store.connect(database, attempts=1) as conn:
        assert store.load_home(conn) == one_room_home()
    assert database_store.load_plan_image("plan") == image
    database_store.delete_plan_image(example_home(), "plan")
    assert database_store.load_plan_image("plan") is None
    with store.connect(database, attempts=1) as conn:
        assert store.load_home(conn) == example_home()
