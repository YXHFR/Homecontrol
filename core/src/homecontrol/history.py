"""Historiken: spårens positioner och händelseloggen sparas lokalt i TimescaleDB.

Tabellerna skapas av `migrations/0003_historik.sql`, som också beskriver hur
positionerna nedsamplas och komprimeras. Hur länge full upplösning sparas
ställs in med `configure` när kärnan startar.

`History` tar emot kärnans läge (`record`) och förklaringar (`explain`) i
händelseloopen och lägger dem i en kö. En egen tråd skriver kön till
databasen ungefär en gång i sekunden. Om databasen är seg eller borta fylls
kön, och då tappas nya punkter i stället för att lamporna får vänta.
Det som inte gick att skriva tappas också; historiken är inte viktigare än
att kärnan fortsätter.

Inget av det som sparas lämnar databasen: inget skickas någonstans och inget
exporteras automatiskt. Spåren sparas med sitt slumpade id, aldrig med
radarns numrering, och positionerna loggas aldrig.
"""

import contextlib
import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg

from homecontrol.controller import Snapshot
from homecontrol.fusion import Occupancy
from homecontrol.home import Point

logger = logging.getLogger(__name__)

FULL_RESOLUTION = timedelta(days=1)
"""Hur länge varje punkt sparas innan bara en punkt per sekund och spår finns kvar."""

MIN_FULL_RESOLUTION = timedelta(hours=1)
"""Kortare tid än så hinner inte nedsamplingen med (den tittar 10 minuter bakåt)."""

_MAX_QUEUED = 50_000
"""Ungefär en och en halv minut med tre personer i varje rum av fem, tio gånger i sekunden."""
_FLUSH_SECONDS = 1.0
_RETRY_SECONDS = 10.0
"""Så länge skrivaren väntar innan den försöker ansluta igen efter ett fel."""


@dataclass(frozen=True)
class _Position:
    time: datetime
    track_id: str
    floor_id: str
    position: Point
    held: bool


@dataclass(frozen=True)
class _Event:
    time: datetime
    kind: str
    room_id: str | None
    lamp_id: str | None
    detail: str


_STOP = object()


def configure(conn: psycopg.Connection, full_resolution: timedelta = FULL_RESOLUTION) -> None:
    """Ställer in hur länge positionerna sparas med full upplösning.

    När tiden har gått tas de bort av TimescaleDB, och samtidigt komprimeras
    den nedsamplade historiken. Den nedsamplade historiken sparas utan tidsgräns.
    """
    if full_resolution < MIN_FULL_RESOLUTION:
        raise ValueError(
            f"Full upplösning måste sparas i minst {_hours(MIN_FULL_RESOLUTION)} timmar"
            f" (inte {_hours(full_resolution)})"
        )
    with conn.transaction():
        conn.execute("SELECT remove_retention_policy('track_position', if_exists => true)")
        conn.execute(
            "SELECT add_retention_policy('track_position', drop_after => %s)", (full_resolution,)
        )
        conn.execute(
            "CALL remove_columnstore_policy('track_position_per_second', if_exists => true)"
        )
        conn.execute(
            "CALL add_columnstore_policy('track_position_per_second', after => %s)",
            (full_resolution,),
        )


class History:
    """Skriver historiken i bakgrunden. Anropas från händelseloopen och väntar aldrig.

    Det som sparas är spårens positioner när de ändras, rummens läge när det
    ändras och förklaringarna. Starta skrivaren med `start` och stoppa den med
    `close`, som skriver det som ligger kvar i kön.
    """

    def __init__(
        self,
        conninfo: str,
        *,
        max_queued: int = _MAX_QUEUED,
        flush_seconds: float = _FLUSH_SECONDS,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._conninfo = conninfo
        self._queue: queue.Queue[_Position | _Event | object] = queue.Queue(max_queued)
        self._flush_seconds = flush_seconds
        self._wall_clock = wall_clock
        self._tracks: dict[str, tuple[str, Point, bool]] = {}
        self._rooms: dict[str, Occupancy] = {}
        self._dropped = 0
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    @property
    def dropped(self) -> int:
        """Hur många punkter och händelser som har tappats sedan skrivningen sist fungerade."""
        return self._dropped

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="historik", daemon=True)
        self._thread.start()

    def close(self, timeout: float = 5.0) -> None:
        """Skriver det som ligger i kön, om databasen svarar inom `timeout` sekunder."""
        if self._thread is None:
            return
        self._stopping.set()
        with contextlib.suppress(queue.Full):
            self._queue.put(_STOP, timeout=timeout)
        self._thread.join(timeout)
        self._thread = None

    def record(self, snapshot: Snapshot) -> None:
        """Lägger det som har ändrats sedan förra gången i kön."""
        now = self._wall_clock()
        tracks = {}
        for track in snapshot.tracks:
            state = (track.floor_id, track.position, track.held)
            tracks[track.id] = state
            if self._tracks.get(track.id) != state:
                self._put(_Position(now, track.id, *state))
        self._tracks = tracks
        rooms = {}
        for room in snapshot.rooms:
            rooms[room.room_id] = room.status
            if self._rooms.get(room.room_id) is not room.status:
                self._put(_Event(now, "rum", room.room_id, None, room.status.value))
        self._rooms = rooms

    def explain(self, text: str, *, room_id: str | None = None, lamp_id: str | None = None) -> None:
        self._put(_Event(self._wall_clock(), "förklaring", room_id, lamp_id, text))

    def _put(self, item: _Position | _Event) -> None:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            if self._dropped == 0:
                logger.warning("Historiken hinner inte skrivas, så nya punkter tappas")
            self._dropped += 1
            return
        if self._dropped:
            logger.info("Historiken skrivs igen (%d punkter och händelser tappades)", self._dropped)
            self._dropped = 0

    def _run(self) -> None:
        conn: psycopg.Connection | None = None
        try:
            while True:
                batch, stop = self._take()
                if batch:
                    conn = self._write(conn, batch)
                if stop or (self._stopping.is_set() and not batch):
                    return
                # Väntar en stund så att flera punkter skrivs på en gång.
                self._stopping.wait(self._flush_seconds)
        finally:
            if conn is not None:
                conn.close()

    def _take(self) -> tuple[list[_Position | _Event], bool]:
        """Tar allt som ligger i kön. Väntar på det första om kön är tom."""
        batch: list[_Position | _Event] = []
        try:
            item = self._queue.get(timeout=self._flush_seconds)
            while True:
                if isinstance(item, _Position | _Event):
                    batch.append(item)
                else:
                    return batch, True
                item = self._queue.get_nowait()
        except queue.Empty:
            return batch, False

    def _write(
        self, conn: psycopg.Connection | None, batch: list[_Position | _Event]
    ) -> psycopg.Connection | None:
        """Skriver `batch` och returnerar anslutningen, eller None om något gick fel."""
        try:
            if conn is None:
                conn = psycopg.connect(self._conninfo, autocommit=True, connect_timeout=5)
            _insert(conn, batch)
            return conn
        except psycopg.Error as error:
            logger.warning(
                "Kunde inte skriva historiken, %d punkter och händelser tappas: %s",
                len(batch),
                error,
            )
            if conn is not None:
                conn.close()
            self._stopping.wait(_RETRY_SECONDS)
            return None


def _hours(duration: timedelta) -> str:
    return f"{duration / timedelta(hours=1):g}"


def _insert(conn: psycopg.Connection, batch: list[_Position | _Event]) -> None:
    with conn.transaction(), conn.cursor() as cursor:
        with cursor.copy(
            "COPY track_position (time, track_id, floor_id, x, y, held) FROM STDIN"
        ) as copy:
            for item in batch:
                if isinstance(item, _Position):
                    copy.write_row(
                        (item.time, item.track_id, item.floor_id, *item.position, item.held)
                    )
        with cursor.copy(
            "COPY event_log (time, kind, room_id, lamp_id, detail) FROM STDIN"
        ) as copy:
            for item in batch:
                if isinstance(item, _Event):
                    copy.write_row((item.time, item.kind, item.room_id, item.lamp_id, item.detail))
