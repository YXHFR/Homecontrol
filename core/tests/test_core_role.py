"""Kärnans egen databasroll. Kräver HOMECONTROL_TEST_DATABASE (se conftest.py).

Testdatabasens användare är superanvändare, som databasens ägare i Docker
Compose. Varje test skapar en egen roll med ett slumpat namn och tar bort den.
"""

import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest
from history_helpers import anna_at, events, lit, positions, snapshot, ticking
from psycopg import sql
from psycopg.conninfo import make_conninfo

from homecontrol import history, store
from homecontrol.__main__ import export_home, import_home, prepare_database_for_core
from homecontrol.config import Settings
from homecontrol.fusion import Occupancy
from homecontrol.history import History
from homecontrol.homefile import example_home, loads
from homecontrol.images import StoredImage

PASSWORD = uuid.uuid4().hex


@pytest.fixture
def role(database: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Namnet på kärnans roll i testet. Rollen tas bort efteråt om den skapades."""
    name = f"test_core_{uuid.uuid4().hex}"
    monkeypatch.setattr(store, "CORE_ROLE", name)
    yield name
    with psycopg.connect(database, autocommit=True) as admin:
        exists = admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone()
        if exists:
            admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(name)))
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(name)))


def prepare(database: str, role: str) -> str:
    """Kör `homecontrol förbered-databasen` och ger anslutningen som kärnans roll."""
    settings = Settings(
        host="127.0.0.1",
        port=8000,
        web_dir=Path("web"),
        database=database,
        core_database_password=PASSWORD,
    )
    prepare_database_for_core(database, settings)
    return make_conninfo(database, user=role, password=PASSWORD)


def test_core_role_is_not_a_superuser(database: str, role: str) -> None:
    prepare(database, role)

    with store.connect(database, attempts=1) as admin:
        row = admin.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin FROM pg_roles"
            " WHERE rolname = %s",
            (role,),
        ).fetchone()

    assert row == (False, False, False, True)


def test_preparing_without_the_core_password_says_what_is_missing(database: str) -> None:
    settings = Settings(host="127.0.0.1", port=8000, web_dir=Path("web"), database=database)

    with pytest.raises(SystemExit, match="HOMECONTROL_CORE_DATABASE_PASSWORD"):
        prepare_database_for_core(database, settings)


def test_core_role_reads_and_writes_the_home(database: str, role: str) -> None:
    core = prepare(database, role)
    database_store = store.DatabaseStore(core)
    image = StoredImage("image/png", b"\x89PNG\r\n\x1a\nbild")
    floor_id = example_home().floors[0].id

    database_store.save_plan_image(example_home(), floor_id, image)

    with store.connect(core, attempts=1) as conn:
        assert store.load_home(conn) == example_home()
    assert database_store.load_plan_image(floor_id) == image
    database_store.delete_plan_image(example_home(), floor_id)
    assert database_store.load_plan_image(floor_id) is None


def test_core_role_exports_and_imports_the_home(database: str, role: str, tmp_path: Path) -> None:
    core = prepare(database, role)
    exported = tmp_path / "hem.json"

    import_home(core, None, prepare=False)
    export_home(core, str(exported), prepare=False)

    assert loads(exported.read_text("utf-8")) == example_home()


def test_core_role_writes_the_history(database: str, role: str) -> None:
    core = prepare(database, role)
    writer = History(core, flush_seconds=0.01, wall_clock=ticking())
    writer.start()

    writer.record(snapshot(anna_at(1), kitchen=Occupancy.OCCUPIED, events=(lit(),)))
    writer.close()

    with store.connect(core, attempts=1) as conn:
        assert len(positions(conn)) == 1
        assert len(events(conn)) == 2
        conn.execute("SELECT * FROM track_position_per_second").fetchall()


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE annat (id integer)",
        "DROP TABLE home",
        "ALTER TABLE home ADD COLUMN annat text",
        "TRUNCATE event_log",
        "SELECT remove_retention_policy('event_log', if_exists => true)",
        "CREATE ROLE annan",
        "COPY home TO PROGRAM 'true'",
    ],
)
def test_core_role_cannot_change_tables_or_reach_outside_them(
    database: str, role: str, statement: str
) -> None:
    core = prepare(database, role)

    with (
        store.connect(core, attempts=1) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute(statement)


def test_database_from_before_the_core_role_keeps_its_home_and_history(
    database: str, role: str
) -> None:
    """Som en installation från en tidigare version, där ägaren skrev allt."""
    with store.connect(database, attempts=1) as owner:
        store.migrate(owner)
        history.configure(owner, timedelta(days=1), None)
        store.save_home(owner, example_home())
    writer = History(database, flush_seconds=0.01, wall_clock=ticking())
    writer.start()
    writer.record(snapshot(anna_at(1), kitchen=Occupancy.OCCUPIED, events=(lit(),)))
    writer.close()

    core = prepare(database, role)

    with store.connect(core, attempts=1) as conn:
        assert store.load_home(conn) == example_home()
        assert len(positions(conn)) == 1
        assert len(events(conn)) == 2
        store.save_home(conn, example_home())
        conn.execute("DELETE FROM track_position")


def test_preparing_again_keeps_the_home_and_covers_new_tables(database: str, role: str) -> None:
    core = prepare(database, role)
    import_home(core, None, prepare=False)
    with store.connect(database, attempts=1) as admin:
        # Som en ny migrering i en senare version.
        admin.execute("CREATE TABLE ny_tabell (id integer)")

    prepare(database, role)

    with store.connect(core, attempts=1) as conn:
        assert store.load_home(conn) == example_home()
        conn.execute("INSERT INTO ny_tabell VALUES (1)")


def test_core_without_rights_to_migrate_says_what_to_run(database: str, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="förbered-databasen"):
        export_home(database, str(tmp_path / "hem.json"), prepare=False)

    with store.connect(database, attempts=1) as conn:
        store.migrate(conn)
        store.check_schema(conn)
