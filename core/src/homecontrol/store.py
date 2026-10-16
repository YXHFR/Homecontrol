"""Hemmodellen i Postgres: tabellerna, sparande och inläsning.

Tabellerna skapas av SQL-filerna i `migrations/`, som körs i nummerordning
och bara en gång var. Hemmet sparas som ett dokument i samma form som
exportfilen, så det har en enda version att hålla reda på (`homefile.VERSION`).

Bilderna av planritningarna sparas i en egen tabell, en per våning.

Funktionerna tar en öppen anslutning i autocommit-läge och sköter sina egna
transaktioner. Ingen av dem anropas när lampor tänds och släcks; hemmet
läses när kärnan startar och skrivs när någon importerar eller redigerar det.
`DatabaseStore` är samma sak för kärnans webbgränssnitt, med en egen
anslutning per anrop.
"""

import logging
import re
import time
from dataclasses import dataclass
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


@dataclass(frozen=True)
class StoredImage:
    media_type: str
    """"image/png", "image/jpeg" eller "image/webp"."""
    data: bytes


def save_home(conn: psycopg.Connection, home: Home) -> None:
    """Ersätter hemmet i databasen med `home`.

    Bilder av planritningar för våningar som inte längre finns tas bort.
    """
    with conn.transaction():
        conn.execute(
            "INSERT INTO home (document) VALUES (%s)"
            " ON CONFLICT (id) DO UPDATE SET document = excluded.document, updated_at = now()",
            (Jsonb(homefile.to_document(home)),),
        )
        conn.execute(
            "DELETE FROM plan_image WHERE NOT (floor_id = ANY(%s))",
            ([floor.id for floor in home.floors],),
        )


def load_home(conn: psycopg.Connection) -> Home | None:
    """Läser hemmet från databasen, eller None om inget hem har sparats än.

    Ett sparat hem som inte längre går att läsa ger `homefile.HomeFileError`.
    """
    row = conn.execute("SELECT document FROM home").fetchone()
    if row is None:
        return None
    return homefile.from_document(row[0])


def save_plan_image(conn: psycopg.Connection, floor_id: str, image: StoredImage) -> None:
    """Ersätter våningens bild av planritningen."""
    conn.execute(
        "INSERT INTO plan_image (floor_id, media_type, data) VALUES (%s, %s, %s)"
        " ON CONFLICT (floor_id) DO UPDATE"
        " SET media_type = excluded.media_type, data = excluded.data, updated_at = now()",
        (floor_id, image.media_type, image.data),
    )


def load_plan_image(conn: psycopg.Connection, floor_id: str) -> StoredImage | None:
    row = conn.execute(
        "SELECT media_type, data FROM plan_image WHERE floor_id = %s", (floor_id,)
    ).fetchone()
    return None if row is None else StoredImage(row[0], bytes(row[1]))


def delete_plan_image(conn: psycopg.Connection, floor_id: str) -> None:
    conn.execute("DELETE FROM plan_image WHERE floor_id = %s", (floor_id,))


class DatabaseStore:
    """Sparar det som ändras i webbgränssnittet. Varje anrop öppnar en egen anslutning.

    Anropen väntar på databasen och ska därför köras utanför händelseloopen.
    """

    def __init__(self, conninfo: str) -> None:
        self._conninfo = conninfo

    def save_home(self, home: Home) -> None:
        with connect(self._conninfo, attempts=1) as conn:
            save_home(conn, home)

    def save_plan_image(self, floor_id: str, image: StoredImage) -> None:
        with connect(self._conninfo, attempts=1) as conn:
            save_plan_image(conn, floor_id, image)

    def load_plan_image(self, floor_id: str) -> StoredImage | None:
        with connect(self._conninfo, attempts=1) as conn:
            return load_plan_image(conn, floor_id)

    def delete_plan_image(self, floor_id: str) -> None:
        with connect(self._conninfo, attempts=1) as conn:
            delete_plan_image(conn, floor_id)


def _migrations() -> list[tuple[int, str, str]]:
    found = []
    for entry in resources.files("homecontrol").joinpath("migrations").iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match:
            found.append((int(match.group(1)), entry.name, entry.read_text("utf-8")))
    return sorted(found)
