"""Hjälp för testerna av historiken: ögonblicksbilder från kärnan och det som sparats."""

import itertools
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from homecontrol.controller import Event, RoomView, Snapshot
from homecontrol.fusion import Occupancy, Track
from homecontrol.home import Point

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
ANNA = str(uuid.uuid4())


def snapshot(
    *tracks: Track, kitchen: Occupancy = Occupancy.VACANT, events: tuple[Event, ...] = ()
) -> Snapshot:
    room = RoomView("kok", kitchen, None, None, manual=False, explanation="")
    return Snapshot(tracks=tracks, rooms=(room,), zones=(), lamps=(), events=events)


def lit(number: int = 1) -> Event:
    """Kärnans händelse när lampan i köket tändes."""
    return Event(number, 0.0, "kok", "Tändes: någon är i Kök och det var mörkt.")


def anna_at(x: float, *, held: bool = False) -> Track:
    return Track(ANNA, "plan", Point(x, 2), held)


def ticking(start: datetime = NOW) -> Callable[[], datetime]:
    """En klocka som går en sekund framåt varje gång den läses."""
    times = (start + timedelta(seconds=i) for i in itertools.count())
    return lambda: next(times)


def positions(conn: psycopg.Connection) -> list[tuple[Any, ...]]:
    return conn.execute(
        "SELECT time, track_id::text, floor_id, x, y, held FROM track_position ORDER BY time"
    ).fetchall()


def events(conn: psycopg.Connection) -> list[tuple[Any, ...]]:
    return conn.execute(
        "SELECT time, kind, room_id, detail FROM event_log ORDER BY time, seq"
    ).fetchall()
