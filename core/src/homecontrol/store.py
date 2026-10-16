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

Med Docker Compose ansluter kärnan med en egen roll (`CORE_ROLE`) som bara
får läsa och skriva i tabellerna. Tabellerna skapas och ändras i stället
av ett eget steg vid start (`homecontrol förbered-databasen`), som ansluter
som databasens ägare och skapar kärnans roll om den saknas (`grant_core_role`).
"""

import logging
import re
import time
from importlib import resources

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from homecontrol import homefile
from homecontrol.home import Home
from homecontrol.images import StoredImage

logger = logging.getLogger(__name__)

_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
# Godtyckligt men fast tal som låser migreringarna om två kärnor startar samtidigt.
_MIGRATION_LOCK = 0x486F6D65

CORE_ROLE = "homecontrol_core"
"""Rollen som kärnan ansluter med i Docker Compose. Den är ingen superanvändare.

Samma namn står i kärnans HOMECONTROL_DATABASE i compose.yaml.
"""


class SchemaOutdatedError(Exception):
    """Tabellerna är inte skapade eller uppdaterade, och kärnan får inte göra det själv."""


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


def check_schema(conn: psycopg.Connection) -> None:
    """Kontrollerar att tabellerna är aktuella, utan att ändra något.

    Används i stället för `migrate` när kärnan saknar rätt att ändra tabellerna.
    Ger `SchemaOutdatedError` om någon migrering inte har körts.
    """
    if conn.execute("SELECT to_regclass('schema_migration') IS NOT NULL").fetchone() == (True,):
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migration")}
    else:
        applied = set()
    missing = [name for version, name, _ in _migrations() if version not in applied]
    if missing:
        raise SchemaOutdatedError(
            f"Databasen är inte uppdaterad (saknar {', '.join(missing)}). "
            "Den uppdateras av 'homecontrol förbered-databasen', "
            "som Docker Compose kör före kärnan."
        )


def grant_core_role(conn: psycopg.Connection, password: str) -> None:
    """Skapar kärnans roll om den saknas och ger den rätt att läsa och skriva tabellerna.

    Rollen får inte skapa, ändra eller ta bort tabeller och är ingen
    superanvändare. Lösenordet sätts om varje gång, så att det följer .env.
    Det skickas bara hashat till databasen och syns därför inte i dess logg.
    Anropas av databasens ägare, efter `migrate`, så att rättigheterna
    också gäller nya tabeller.
    """
    role = CORE_ROLE
    name = sql.Identifier(role)
    hashed = conn.pgconn.encrypt_password(password.encode(), role.encode(), b"scram-sha-256")
    with conn.transaction():
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is None:
            logger.info("Skapar databasrollen %s åt kärnan", role)
            conn.execute(sql.SQL("CREATE ROLE {}").format(name))
        conn.execute(
            sql.SQL(
                "ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE"
                " NOREPLICATION NOBYPASSRLS NOINHERIT PASSWORD {}"
            ).format(name, sql.Literal(hashed.decode()))
        )
        schema = sql.Identifier(_current_schema(conn))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema, name))
        conn.execute(sql.SQL("REVOKE CREATE ON SCHEMA {} FROM {}").format(schema, name))
        conn.execute(
            sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}").format(
                schema, name
            )
        )
        conn.execute(
            sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA {} TO {}").format(schema, name)
        )


def _current_schema(conn: psycopg.Connection) -> str:
    row = conn.execute("SELECT current_schema()").fetchone()
    if row is None or row[0] is None:
        raise RuntimeError("Databasen har inget schema att skapa tabellerna i (search_path)")
    return str(row[0])


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

    def save_plan_image(self, home: Home, floor_id: str, image: StoredImage) -> None:
        """Sparar bilden och hemmet (med bildens nya placering) på en gång."""
        with connect(self._conninfo, attempts=1) as conn, conn.transaction():
            save_home(conn, home)
            save_plan_image(conn, floor_id, image)

    def delete_plan_image(self, home: Home, floor_id: str) -> None:
        """Tar bort bilden och sparar hemmet (utan bilden) på en gång."""
        with connect(self._conninfo, attempts=1) as conn, conn.transaction():
            save_home(conn, home)
            delete_plan_image(conn, floor_id)

    def load_plan_image(self, floor_id: str) -> StoredImage | None:
        with connect(self._conninfo, attempts=1) as conn:
            return load_plan_image(conn, floor_id)


def _migrations() -> list[tuple[int, str, str]]:
    found = []
    for entry in resources.files("homecontrol").joinpath("migrations").iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match:
            found.append((int(match.group(1)), entry.name, entry.read_text("utf-8")))
    return sorted(found)
