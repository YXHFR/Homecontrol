"""Historiken: spårens positioner och händelseloggen sparas lokalt i TimescaleDB.

Tabellerna skapas av `migrations/0003_historik.sql`, som också beskriver hur
positionerna nedsamplas och komprimeras. Hur länge historiken sparas ställs
in med `configure` när kärnan startar.

`History` tar emot kärnans läge (`record`) och förklaringar (`explain`) i
händelseloopen och lägger dem i en kö. En egen tråd skriver kön till
databasen ungefär en gång i sekunden. Om databasen är seg eller borta fylls
kön, och då tappas nya punkter i stället för att lamporna får vänta.
Det som inte gick att skriva tappas också; historiken är inte viktigare än
att kärnan fortsätter.

Rummets läge sparas före förklaringen som det gav upphov till, utom när
kön har varit full: då kan rumsändringen komma efter förklaringen.

En position sparas när spåret har flyttat sig eller bytt mellan sett och
kvarhållet. Ett spår som står helt still ger alltså inga nya punkter.

Inget av det som sparas lämnar databasen: inget skickas någonstans och inget
exporteras automatiskt. Spåren sparas med sitt slumpade id, aldrig med
radarns numrering, och positioner skrivs aldrig i kärnans logg.
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

_MAX_QUEUED = 50_000
"""Med 15 spår samtidigt, tio punkter i sekunden, räcker kön i drygt fem minuter."""
_FLUSH_SECONDS = 1.0
"""Så ofta skrivaren tömmer kön, så att många punkter skrivs på en gång."""
_RETRY_SECONDS = 10.0
"""Så länge skrivaren väntar innan den försöker ansluta igen efter ett fel."""

APPLICATION_NAME = "homecontrol-historik"
"""Skrivarens namn i databasen, så att dess anslutning syns i pg_stat_activity."""

# Händelsetyperna i event_log. De måste stämma med CHECK-villkoret i migreringen.
_ROOM_CHANGED = "rum"
_EXPLANATION = "förklaring"


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
"""Läggs sist i kön av `close`, så att skrivaren vet att allt före den är skrivet."""


def configure(conn: psycopg.Connection, full_resolution: timedelta, keep: timedelta | None) -> None:
    """Ställer in hur länge historiken sparas.

    Positionerna sparas med full upplösning i `full_resolution`. Sedan tas
    de bort av TimescaleDB, och samtidigt komprimeras den nedsamplade
    historiken. Den nedsamplade historiken och händelseloggen sparas i
    `keep`, eller utan tidsgräns om den är None.

    Tiderna kontrolleras av `config.Settings`. TimescaleDB tar bort hela
    delar av tabellerna åt gången, så det gamla kan ligga kvar ett tag till:
    händelseloggen upp till en vecka.
    """
    with conn.transaction():
        _replace_retention(conn, "track_position", full_resolution)
        _replace_retention(conn, "track_position_per_second", keep)
        _replace_retention(conn, "event_log", keep)
        conn.execute(
            "CALL remove_columnstore_policy('track_position_per_second', if_exists => true)"
        )
        conn.execute(
            "CALL add_columnstore_policy('track_position_per_second', after => %s)",
            (full_resolution,),
        )


def _replace_retention(conn: psycopg.Connection, table: str, drop_after: timedelta | None) -> None:
    conn.execute("SELECT remove_retention_policy(%s, if_exists => true)", (table,))
    if drop_after is not None:
        conn.execute("SELECT add_retention_policy(%s, drop_after => %s)", (table, drop_after))


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
        retry_seconds: float = _RETRY_SECONDS,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._conninfo = conninfo
        self._queue: queue.Queue[_Position | _Event | object] = queue.Queue(max_queued)
        self._flush_seconds = flush_seconds
        self._retry_seconds = retry_seconds
        self._wall_clock = wall_clock
        self._paused: Callable[[], bool] = lambda: False
        self._tracks: dict[str, tuple[str, Point, bool]] = {}
        """Det senast sparade om varje spår."""
        self._rooms: dict[str, Occupancy] = {}
        """Det senast sparade läget i varje rum."""
        self._dropped = 0
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    @property
    def dropped(self) -> int:
        """Hur många punkter och händelser som inte fick plats sedan kön sist tog emot något."""
        return self._dropped

    def pause_while(self, paused: Callable[[], bool]) -> None:
        """Sparar inget medan `paused()` är sant, till exempel i simuleringsläget."""
        self._paused = paused

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
        """Lägger det som har ändrats sedan det senast sparade i kön."""
        if self._paused():
            # Börja om när pausen är slut, så att rummens läge sparas på nytt.
            self._tracks.clear()
            self._rooms.clear()
            return
        now = self._wall_clock()
        # Det sparade uppdateras bara om ändringen fick plats i kön. Annars
        # försöker nästa anrop igen.
        tracks = {}
        for track in snapshot.tracks:
            state = (track.floor_id, track.position, track.held)
            saved = self._tracks.get(track.id)
            if saved != state and self._put(_Position(now, track.id, *state)):
                saved = state
            if saved is not None:
                tracks[track.id] = saved
        self._tracks = tracks
        rooms = {}
        for room in snapshot.rooms:
            saved_status = self._rooms.get(room.room_id)
            if saved_status is not room.status and self._put(
                _Event(now, _ROOM_CHANGED, room.room_id, None, room.status.value)
            ):
                saved_status = room.status
            if saved_status is not None:
                rooms[room.room_id] = saved_status
        self._rooms = rooms

    def explain(self, text: str, *, room_id: str | None = None, lamp_id: str | None = None) -> None:
        if not self._paused():
            self._put(_Event(self._wall_clock(), _EXPLANATION, room_id, lamp_id, text))

    def _put(self, item: _Position | _Event) -> bool:
        """Lägger `item` i kön om den får plats. Returnerar om den fick det."""
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            if self._dropped == 0:
                logger.warning("Historiken hinner inte skrivas, så nya punkter tappas")
            self._dropped += 1
            return False
        if self._dropped:
            logger.info("Historiken skrivs igen (%d punkter och händelser tappades)", self._dropped)
            self._dropped = 0
        return True

    def _run(self) -> None:
        conn: psycopg.Connection | None = None
        try:
            while True:
                batch, stop = self._take()
                if batch:
                    conn = self._write(conn, batch)
                # _STOP kommer inte fram om kön var full när close anropades,
                # så då slutar skrivaren när kön är tom.
                if stop or (self._stopping.is_set() and not batch):
                    return
                self._stopping.wait(self._flush_seconds)
        finally:
            if conn is not None:
                conn.close()

    def _take(self) -> tuple[list[_Position | _Event], bool]:
        """Tar allt som ligger i kön. Väntar på det första om kön är tom.

        Returnerar också om `_STOP` kom, alltså om skrivaren ska sluta.
        """
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
                conn = psycopg.connect(
                    self._conninfo,
                    autocommit=True,
                    application_name=APPLICATION_NAME,
                    connect_timeout=5,
                    # Ett avbrutet nätverk mot databasen märks inom en minut.
                    keepalives_idle=30,
                    keepalives_interval=10,
                    keepalives_count=3,
                    tcp_user_timeout=60_000,
                )
            _insert(conn, batch)
            return conn
        except Exception as error:
            # Bara feltypen: databasens felmeddelande kan citera raden, alltså en position.
            logger.warning(
                "Kunde inte skriva historiken, %d punkter och händelser tappas (%s %s)",
                len(batch),
                type(error).__name__,
                getattr(error, "sqlstate", None) or "",
            )
            if conn is not None:
                conn.close()
            self._stopping.wait(self._retry_seconds)
            return None


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
