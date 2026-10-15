"""Hemmodellen i Postgres: tabeller, sparande och inläsning.

Tabellerna skapas av SQL-filerna i `migrations/`, som körs i nummerordning
och bara en gång var. Hela hemmet sparas i en transaktion, så databasen har
alltid antingen det gamla eller det nya hemmet, aldrig en blandning.

Funktionerna tar en öppen anslutning i autocommit-läge och sköter sina egna
transaktioner. Ingen av dem anropas när lampor tänds och släcks; hemmet
läses en gång när kärnan startar.
"""

import logging
import re
import time
from collections import defaultdict
from importlib import resources
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from homecontrol.home import (
    Floor,
    Home,
    Lamp,
    Node,
    Point,
    Room,
    RoomSettings,
    SignalSource,
    Zone,
)

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
    with conn.transaction(), conn.cursor() as cur:
        # Våningarna tar med sig allt som hör till dem.
        cur.execute("DELETE FROM floor")
        cur.execute("DELETE FROM home")
        cur.execute("INSERT INTO home (name) VALUES (%s)", (home.name,))
        for floor_order, floor in enumerate(home.floors):
            cur.execute(
                "INSERT INTO floor (id, sort_order, name, level) VALUES (%s, %s, %s, %s)",
                (floor.id, floor_order, floor.name, floor.level),
            )
            cur.executemany(
                "INSERT INTO signal_source (id, floor_id, sort_order, name, x, y, height)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (s.id, floor.id, i, s.name, s.position.x, s.position.y, s.height)
                    for i, s in enumerate(floor.signal_sources)
                ],
            )
            for room_order, room in enumerate(floor.rooms):
                _insert_room(cur, floor.id, room_order, room)


def load_home(conn: psycopg.Connection) -> Home | None:
    """Läser hemmet från databasen, eller None om inget hem har sparats än."""
    with conn.transaction(), conn.cursor() as cur:
        home_row = cur.execute("SELECT name FROM home").fetchone()
        if home_row is None:
            return None

        walls = _grouped(cur, "SELECT room_id, kind FROM wall ORDER BY room_id, side")
        zones = _grouped(
            cur, "SELECT room_id, id, name, kind, polygon FROM zone ORDER BY sort_order"
        )
        nodes = _grouped(
            cur,
            "SELECT room_id, id, name, x, y, height, rotation, mirrored, capabilities"
            " FROM node ORDER BY sort_order",
        )
        lamps = _grouped(
            cur,
            "SELECT room_id, id, name, x, y, zone_id, profile FROM lamp ORDER BY sort_order",
        )
        sources = _grouped(
            cur,
            "SELECT floor_id, id, name, x, y, height FROM signal_source ORDER BY sort_order",
        )
        rooms: defaultdict[str, list[Room]] = defaultdict(list)
        for floor_id, room_id, name, polygon, dark, bright, delay in cur.execute(
            "SELECT floor_id, id, name, polygon, dark_below_lux, bright_above_lux,"
            " off_after_empty_seconds FROM room ORDER BY sort_order"
        ):
            rooms[floor_id].append(
                Room(
                    id=room_id,
                    name=name,
                    polygon=_points(polygon),
                    walls=tuple(kind for (kind,) in walls[room_id]),
                    settings=RoomSettings(
                        dark_below_lux=dark, bright_above_lux=bright, off_after_empty_seconds=delay
                    ),
                    zones=tuple(
                        Zone(id=i, name=n, kind=k, polygon=_points(p))
                        for i, n, k, p in zones[room_id]
                    ),
                    nodes=tuple(
                        Node(
                            id=i,
                            name=n,
                            position=Point(x, y),
                            height=h,
                            rotation=r,
                            mirrored=m,
                            capabilities=tuple(c),
                        )
                        for i, n, x, y, h, r, m, c in nodes[room_id]
                    ),
                    lamps=tuple(
                        Lamp(id=i, name=n, position=Point(x, y), zone_id=z, profile=p)
                        for i, n, x, y, z, p in lamps[room_id]
                    ),
                )
            )
        floors = tuple(
            Floor(
                id=floor_id,
                name=name,
                level=level,
                rooms=tuple(rooms[floor_id]),
                signal_sources=tuple(
                    SignalSource(id=i, name=n, position=Point(x, y), height=h)
                    for i, n, x, y, h in sources[floor_id]
                ),
            )
            for floor_id, name, level in cur.execute(
                "SELECT id, name, level FROM floor ORDER BY sort_order"
            )
        )
        return Home(name=home_row[0], floors=floors)


def _insert_room(cur: psycopg.Cursor[Any], floor_id: str, order: int, room: Room) -> None:
    cur.execute(
        "INSERT INTO room (id, floor_id, sort_order, name, polygon, dark_below_lux,"
        " bright_above_lux, off_after_empty_seconds) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            room.id,
            floor_id,
            order,
            room.name,
            _polygon_json(room.polygon),
            room.settings.dark_below_lux,
            room.settings.bright_above_lux,
            room.settings.off_after_empty_seconds,
        ),
    )
    cur.executemany(
        "INSERT INTO wall (room_id, side, kind) VALUES (%s, %s, %s)",
        [(room.id, side, kind.value) for side, kind in enumerate(room.walls)],
    )
    cur.executemany(
        "INSERT INTO zone (id, room_id, sort_order, name, kind, polygon)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        [
            (z.id, room.id, i, z.name, z.kind.value, _polygon_json(z.polygon))
            for i, z in enumerate(room.zones)
        ],
    )
    cur.executemany(
        "INSERT INTO node (id, room_id, sort_order, name, x, y, height, rotation, mirrored,"
        " capabilities) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        [
            (
                n.id,
                room.id,
                i,
                n.name,
                n.position.x,
                n.position.y,
                n.height,
                n.rotation,
                n.mirrored,
                [c.value for c in n.capabilities],
            )
            for i, n in enumerate(room.nodes)
        ],
    )
    cur.executemany(
        "INSERT INTO lamp (id, room_id, sort_order, name, x, y, zone_id, profile)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [
            (
                lamp.id,
                room.id,
                i,
                lamp.name,
                lamp.position.x,
                lamp.position.y,
                lamp.zone_id,
                lamp.profile.value,
            )
            for i, lamp in enumerate(room.lamps)
        ],
    )


def _grouped(cur: psycopg.Cursor[Any], query: str) -> defaultdict[str, list[tuple[Any, ...]]]:
    """Raderna grupperade på första kolumnen, i den ordning frågan ger dem."""
    groups: defaultdict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for owner, *rest in cur.execute(query.encode()):
        groups[owner].append(tuple(rest))
    return groups


def _polygon_json(points: tuple[Point, ...]) -> Jsonb:
    return Jsonb([[p.x, p.y] for p in points])


def _points(value: Any) -> tuple[Point, ...]:
    return tuple(Point(x, y) for x, y in value)


def _migrations() -> list[tuple[int, str, str]]:
    found = []
    for entry in resources.files("homecontrol").joinpath("migrations").iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match:
            found.append((int(match.group(1)), entry.name, entry.read_text("utf-8")))
    return sorted(found)
