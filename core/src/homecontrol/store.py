"""Hemmodellen i Postgres: tabellerna, sparande och inläsning.

Tabellerna skapas av SQL-filerna i `migrations/`, som körs i nummerordning
och bara en gång var. Hemmet sparas som ett dokument i samma form som
exportfilen, så det har en enda version att hålla reda på (`homefile.VERSION`).

Funktionerna tar en öppen anslutning i autocommit-läge och sköter sina egna
transaktioner. Ingen av dem anropas när lampor tänds och släcks; hemmet
läses en gång när kärnan startar.
"""

import logging
import re
import time
from importlib import resources

import psycopg
from psycopg.types.json import Jsonb

from homecontrol import homefile
from homecontrol.home import Home

logger = logging.getLogger(__name__)

_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
# Godtyckligt men fast tal som låser migreringarna om två kärnor startar samtidigt.
_MIGRATION_LOCK = 0x486F6D65


def connect(conninfo: str, *, attempts: int = 30, wait_seconds: float = 2.0) -> psycopg.Connection:
    """Ansluter till databasen och väntar en stund om den inte har startat än."""
    for attempt in range(1, attempts + 1):
        try:
            return psycopg.connect(conninfo, autocommit=True, connect_timeout=10)
        except psycopg.OperationalError:
            if attempt == attempts:
                raise
            logger.info("Databasen svarar inte än, försöker igen (%d/%d)", attempt, attempts)
            time.sleep(wait_seconds)
    raise AssertionError("unreachable")


def migrate(conn: psycopg.Connection) -> None:
    """Skapar eller uppdaterar tabellerna. Gör inget om de redan är aktuella."""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migration ("
            " version integer PRIMARY KEY,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migration")}
        for version, name, sql in _migrations():
            if version in applied:
                continue
            logger.info("Uppdaterar databasen: %s", name)
            conn.execute(sql.encode())
            conn.execute("INSERT INTO schema_migration (version) VALUES (%s)", (version,))


def save_home(conn: psycopg.Connection, home: Home) -> None:
    """Ersätter hemmet i databasen med `home`."""
    conn.execute(
        "INSERT INTO home (document) VALUES (%s)"
        " ON CONFLICT (id) DO UPDATE SET document = excluded.document, updated_at = now()",
        (Jsonb(homefile.to_document(home)),),
    )


def load_home(conn: psycopg.Connection) -> Home | None:
    """Läser hemmet från databasen, eller None om inget hem har sparats än.

    Ett sparat hem som inte längre går att läsa ger `homefile.HomeFileError`.
    """
    row = conn.execute("SELECT document FROM home").fetchone()
    if row is None:
        return None
    return homefile.from_document(row[0])


def _migrations() -> list[tuple[int, str, str]]:
    found = []
    for entry in resources.files("homecontrol").joinpath("migrations").iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match:
            found.append((int(match.group(1)), entry.name, entry.read_text("utf-8")))
    return sorted(found)
