"""Historiken i TimescaleDB. Testerna med databas kräver HOMECONTROL_TEST_DATABASE (se conftest.py).

TimescaleDB kör nedsamplingen, lagringstiden och komprimeringen som jobb i
bakgrunden. Testerna kör jobben direkt i stället för att vänta på dem.
"""

import time
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

from homecontrol import history
from homecontrol.app import create_app
from homecontrol.controller import RoomView, Snapshot
from homecontrol.fusion import Occupancy, Track
from homecontrol.history import History
from homecontrol.home import Point
from homecontrol.homefile import example_home

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
ANNA = str(uuid.uuid4())
BO = str(uuid.uuid4())


def insert_positions(
    conn: psycopg.Connection, track_id: str, start: datetime, xs: list[float]
) -> None:
    """Spårets positioner tio gånger i sekunden från `start`, som radarn ger dem."""
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO track_position (time, track_id, floor_id, x, y, held)"
            " VALUES (%s, %s, 'plan', %s, 1, false)",
            [(start + timedelta(seconds=i / 10), track_id, x) for i, x in enumerate(xs)],
        )


def pause_background_jobs(conn: psycopg.Connection) -> None:
    """Stoppar TimescaleDB:s egna körningar av jobben, så att bara testet kör dem.

    Uppdateringen av aggregatet körs annars direkt när den skapas, samtidigt som testet.
    """
    jobs = [
        job_id
        for (job_id,) in conn.execute(
            "SELECT job_id FROM timescaledb_information.jobs"
            " WHERE hypertable_schema = current_schema()"
        )
    ]
    for job_id in jobs:
        conn.execute("SELECT alter_job(%s, scheduled => false)", (job_id,))
    deadline = time.monotonic() + 10
    while conn.execute(
        "SELECT count(*) FROM timescaledb_information.job_stats"
        " WHERE job_id = ANY(%s) AND job_status = 'Running'",
        (jobs,),
    ).fetchone() != (0,):
        assert time.monotonic() < deadline, "TimescaleDB:s jobb blev aldrig klara"
        time.sleep(0.05)


def run_jobs(conn: psycopg.Connection, kind: str, table: str) -> None:
    jobs = conn.execute(
        "SELECT job_id FROM timescaledb_information.jobs"
        " WHERE proc_name = %s AND hypertable_name = %s AND hypertable_schema = current_schema()",
        (kind, table),
    ).fetchall()
    assert jobs, f"{kind} saknas för {table}"
    for (job_id,) in jobs:
        conn.execute("CALL run_job(%s)", (job_id,))


def per_second(conn: psycopg.Connection) -> list[tuple[datetime, str, float]]:
    return [
        (when, str(track_id), x)
        for when, track_id, x in conn.execute(
            "SELECT time, track_id, x FROM track_position_per_second"
        )
    ]


def test_old_positions_are_downsampled_to_one_per_second_and_compressed(
    conn: psycopg.Connection,
) -> None:
    history.configure(conn)
    pause_background_jobs(conn)
    old = datetime.now(UTC).replace(microsecond=0) - timedelta(days=3)
    insert_positions(conn, ANNA, old, [float(i) for i in range(25)])  # 2,5 sekunder
    insert_positions(conn, BO, old, [7.0] * 10)
    recent = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)
    insert_positions(conn, ANNA, recent, [1.0, 2.0])
    # Det som policyn gör löpande, fast för en tid långt bakåt.
    conn.execute(
        "CALL refresh_continuous_aggregate('track_position_per_second', %s, %s)",
        (old - timedelta(hours=1), old + timedelta(hours=1)),
    )

    run_jobs(conn, "policy_retention", "track_position")
    run_jobs(conn, "policy_compression", "track_position_per_second")
    run_jobs(conn, "policy_refresh_continuous_aggregate", "track_position_per_second")

    # Full upplösning finns bara kvar för det senaste dygnet.
    remaining = conn.execute("SELECT count(*), min(time) FROM track_position").fetchone()
    assert remaining is not None
    assert remaining[0] == 2
    assert remaining[1] >= recent - timedelta(seconds=1)
    # Det gamla finns kvar som medelvärdet av varje sekund, och det senaste
    # har policyn nedsamplat.
    assert sorted(per_second(conn)) == sorted(
        [
            (old, ANNA, 4.5),
            (old + timedelta(seconds=1), ANNA, 14.5),
            (old + timedelta(seconds=2), ANNA, 22.0),
            (old, BO, 7.0),
            (recent, ANNA, 1.5),
        ]
    )
    compressed = conn.execute(
        "SELECT is_compressed FROM timescaledb_information.chunks c"
        " JOIN timescaledb_information.continuous_aggregates a"
        "   ON a.materialization_hypertable_name = c.hypertable_name"
        " WHERE a.view_name = 'track_position_per_second' AND a.view_schema = current_schema()"
        "   AND c.range_end <= %s",
        (old + timedelta(days=1),),
    ).fetchall()
    assert compressed == [(True,)]


def test_downsampled_positions_are_kept_without_time_limit(conn: psycopg.Connection) -> None:
    history.configure(conn)

    retention = conn.execute(
        "SELECT hypertable_name FROM timescaledb_information.jobs"
        " WHERE proc_name = 'policy_retention' AND hypertable_schema = current_schema()"
    ).fetchall()

    assert retention == [("track_position",)]


def policy(conn: psycopg.Connection, kind: str, table: str, key: str) -> list[timedelta]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT (config->>%s)::interval FROM timescaledb_information.jobs"
            " WHERE proc_name = %s AND hypertable_name = %s"
            "   AND hypertable_schema = current_schema()",
            (key, kind, table),
        )
    ]


def test_full_resolution_lasts_one_day_by_default(conn: psycopg.Connection) -> None:
    history.configure(conn)

    assert policy(conn, "policy_retention", "track_position", "drop_after") == [timedelta(days=1)]
    assert policy(conn, "policy_compression", "track_position_per_second", "compress_after") == [
        timedelta(days=1)
    ]


def test_full_resolution_time_can_be_changed(conn: psycopg.Connection) -> None:
    history.configure(conn)

    history.configure(conn, timedelta(hours=6))

    assert policy(conn, "policy_retention", "track_position", "drop_after") == [timedelta(hours=6)]
    assert policy(conn, "policy_compression", "track_position_per_second", "compress_after") == [
        timedelta(hours=6)
    ]


def test_full_resolution_shorter_than_the_downsampling_is_refused(
    conn: psycopg.Connection,
) -> None:
    with pytest.raises(ValueError, match="minst"):
        history.configure(conn, timedelta(minutes=30))


def snapshot(*tracks: Track, kitchen: Occupancy = Occupancy.VACANT) -> Snapshot:
    return Snapshot(tracks=tracks, rooms=(RoomView("kok", kitchen, None),), zones=(), lamps=())


def anna_at(x: float, *, held: bool = False) -> Track:
    return Track(ANNA, "plan", Point(x, 2), held)


def test_tracks_room_changes_and_explanations_are_written(
    conn: psycopg.Connection, database: str
) -> None:
    times = iter(NOW + timedelta(seconds=i) for i in range(100))
    log = History(database, flush_seconds=0.01, wall_clock=lambda: next(times))
    log.start()

    log.record(snapshot())
    log.record(snapshot(anna_at(1), kitchen=Occupancy.OCCUPIED))
    log.record(snapshot(anna_at(1), kitchen=Occupancy.OCCUPIED))  # inget nytt
    log.explain("Tändes: radarn ser 1 person.", room_id="kok", lamp_id="taklampa")
    log.record(snapshot(anna_at(1, held=True), kitchen=Occupancy.OCCUPIED))
    log.close()

    positions = conn.execute(
        "SELECT time, track_id::text, floor_id, x, y, held FROM track_position ORDER BY time"
    ).fetchall()
    events = conn.execute(
        "SELECT time, kind, room_id, lamp_id, detail FROM event_log ORDER BY time"
    ).fetchall()
    assert positions == [
        (NOW + timedelta(seconds=1), ANNA, "plan", 1, 2, False),
        (NOW + timedelta(seconds=4), ANNA, "plan", 1, 2, True),
    ]
    assert events == [
        (NOW, "rum", "kok", None, "tomt"),
        (NOW + timedelta(seconds=1), "rum", "kok", None, "upptaget"),
        (
            NOW + timedelta(seconds=3),
            "förklaring",
            "kok",
            "taklampa",
            "Tändes: radarn ser 1 person.",
        ),
    ]


def test_full_queue_drops_points_instead_of_waiting() -> None:
    log = History("host=127.0.0.1 port=1 connect_timeout=1", max_queued=3)

    started = time.monotonic()
    for x in range(10):
        log.record(snapshot(anna_at(x)))

    assert time.monotonic() - started < 0.1
    # Rummets läge och de två första positionerna får plats.
    assert log.dropped == 8


def test_unreachable_database_does_not_stop_the_writer(caplog: pytest.LogCaptureFixture) -> None:
    log = History("host=127.0.0.1 port=1 connect_timeout=1", flush_seconds=0.01)
    log.start()

    log.record(snapshot(anna_at(1)))
    deadline = time.monotonic() + 5
    while "Kunde inte skriva historiken" not in caplog.text and time.monotonic() < deadline:
        time.sleep(0.01)
    log.record(snapshot(anna_at(2)))
    log.close(timeout=1)

    assert "Kunde inte skriva historiken" in caplog.text
    assert "1, 2" not in caplog.text  # positionerna loggas inte


def test_core_writes_its_history_while_it_runs(conn: psycopg.Connection, database: str) -> None:
    home = example_home()
    room = next(room for room in home.rooms if room.nodes and room.lamps)
    floor = next(floor for floor in home.floors if room in floor.rooms)
    middle = Point(
        sum(x for x, _ in room.polygon) / len(room.polygon),
        sum(y for _, y in room.polygon) / len(room.polygon),
    )
    app = create_app(home=home, history=History(database, flush_seconds=0.01))

    with TestClient(app):
        app.state.simulation.set_daylight(5)
        app.state.simulation.add_person(floor.id, middle)

    tracks = conn.execute("SELECT count(DISTINCT track_id) FROM track_position").fetchone()
    events = conn.execute("SELECT kind, room_id, detail FROM event_log").fetchall()
    assert tracks == (1,)
    assert ("rum", room.id, "upptaget") in events
    assert any(kind == "förklaring" and room_id == room.id for kind, room_id, _ in events)
