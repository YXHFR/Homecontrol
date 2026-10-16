"""Kärnans kontakt med databasen, och återanslutningen när kontakten har tappats.

Kärnan startar även när databasen inte svarar, med den lokala kopian av
hemmet (se homecopy.py). Då, och när ett sparande i databasen misslyckas,
har kärnan tappat kontakten (`mark_lost`). Livevyn visar en varning, kartredigerarens
ändringar nekas (se editor.py), och kärnan försöker ansluta igen i bakgrunden.

När databasen svarar igen läses hemmet därifrån och lämnas till kärnan om det
skiljer sig från det kärnan använder. Först därefter tas ändringar emot igen,
så att ingen ändring görs på ett hem som är äldre än databasens.

Historiken har en egen anslutning och klarar sig själv (se history.py).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homecontrol.home import Home
from homecontrol.store import SchemaOutdatedError

logger = logging.getLogger(__name__)

RETRY_SECONDS = 10.0
"""Så länge kärnan väntar mellan försöken att ansluta igen."""

Fetch = Callable[[Home], Home]
"""Läser hemmet i databasen (blockerar). Har databasen inget hem sparas det som ges."""


@dataclass(frozen=True)
class DatabaseView:
    """Det som livevyn visar om databasen."""

    connected: bool


class DatabaseLink:
    """Om kärnan har kontakt med databasen. Används i händelseloopen."""

    def __init__(self, fetch: Fetch, *, connected: bool = True) -> None:
        self._fetch = fetch
        self._connected = connected
        self._listeners: list[Callable[[], None]] = []
        self._lost = asyncio.Event()
        if not connected:
            self._lost.set()

    @property
    def connected(self) -> bool:
        return self._connected

    def view(self) -> DatabaseView:
        return DatabaseView(self._connected)

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def mark_lost(self) -> None:
        """Kontakten har tappats: ändringar nekas tills hemmet har lästs från databasen igen."""
        if self._connected:
            logger.warning("Tappade kontakten med databasen; försöker ansluta igen")
            self._set(connected=False)

    async def run(self, resync: Callable[[Fetch], Awaitable[None]]) -> None:
        """Ansluter igen när kontakten har tappats. Körs tills den avbryts.

        `resync` läser hemmet med den givna funktionen och lämnar det till
        kärnan (se `HomeChanges.resync`).
        """
        reported = False
        while True:
            if self._connected:
                await self._lost.wait()
            await asyncio.sleep(RETRY_SECONDS)
            try:
                await resync(self._fetch)
            except SchemaOutdatedError as error:
                # Svarar, men är inte uppdaterad: det går inte över av sig självt.
                if not reported:
                    logger.warning("Kan inte läsa hemmet från databasen: %s", error)
                reported = True
                continue
            except Exception as error:
                if not reported:
                    # En gång per avbrott, så att loggen inte fylls. Bara feltypen:
                    # ett fel i det sparade hemmet kan citera rummens id.
                    logger.warning("Databasen svarar inte än (%s)", type(error).__name__)
                reported = True
                continue
            logger.info("Databasen svarar igen; hemmet är läst därifrån")
            reported = False
            self._set(connected=True)

    def _set(self, *, connected: bool) -> None:
        self._connected = connected
        if connected:
            self._lost.clear()
        else:
            self._lost.set()
        for listener in list(self._listeners):
            listener()
