"""Kärnans lokala kopia av hemmet, så att lamporna styrs även när databasen är nere.

Kopian är en vanlig hemfil, i samma form som exportfilen (se homefile.py).
Den skrivs varje gång hemmet har lästs från eller sparats i databasen, och
läses bara när kärnan startar och databasen inte svarar.

Databasen bestämmer alltid. Kopian skrivs aldrig tillbaka över ett hem i
databasen: när databasen svarar igen läser kärnan hemmet därifrån (se
databaselink.py), och medan den är nere nekas alla ändringar. Därför kan en
kopia som är äldre än databasen aldrig skriva över nyare data. Bara när
databasen inte har något hem alls, till exempel efter att dess volym har
tömts, läggs kopian in där.

Hemmet innehåller inga hemligheter: inga lösenord, och inga nycklar till
rumskorten (de räknas fram ur ESPHOME_SECRET). Planritningarnas bilder finns
bara i databasen. Filen kan bara läsas av kärnans egen användare.
"""

import contextlib
import logging
import os
import secrets
from pathlib import Path

from homecontrol import homefile
from homecontrol.home import Home
from homecontrol.images import StoredImage
from homecontrol.store import DatabaseStore

logger = logging.getLogger(__name__)


class HomeCopy:
    """Kopian av hemmet i en fil. Fel loggas men stoppar aldrig kärnan."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, home: Home) -> None:
        """Ersätter kopian. Den gamla finns kvar tills den nya är helt skriven."""
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(4)}")
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                file.write(homefile.dumps(home))
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            _sync_directory(self.path.parent)
        except OSError as error:
            # Hemmet är sparat i databasen ändå; bara kopian blir inaktuell.
            logger.warning("Kunde inte skriva kopian av hemmet i %s: %s", self.path, error)
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)

    def load(self) -> Home | None:
        """Hemmet i kopian, eller None om det inte finns någon som går att läsa."""
        try:
            text = self.path.read_text("utf-8")
        except FileNotFoundError:
            return None
        except OSError as error:
            logger.warning("Kunde inte läsa kopian av hemmet i %s: %s", self.path, error)
            return None
        try:
            return homefile.loads(text)
        except homefile.HomeFileError:
            # Inte felet i sig: det kan citera rummens id, som kan vara personliga.
            logger.warning("Kopian av hemmet i %s går inte att läsa och används inte", self.path)
            return None


class CopyingStore:
    """Läser och sparar i databasen och skriver sedan kopian, så att den följer databasen."""

    def __init__(self, store: DatabaseStore, copy: HomeCopy) -> None:
        self._store = store
        self._copy = copy

    def fetch_home(self, seed: Home) -> Home:
        """Som `DatabaseStore.fetch_home`."""
        home = self._store.fetch_home(seed)
        self._copy.save(home)
        return home

    def save_home(self, home: Home) -> None:
        self._store.save_home(home)
        self._copy.save(home)

    def save_plan_image(self, home: Home, floor_id: str, image: StoredImage) -> None:
        self._store.save_plan_image(home, floor_id, image)
        self._copy.save(home)

    def delete_plan_image(self, home: Home, floor_id: str) -> None:
        self._store.delete_plan_image(home, floor_id)
        self._copy.save(home)

    def load_plan_image(self, floor_id: str) -> StoredImage | None:
        return self._store.load_plan_image(floor_id)


def _sync_directory(directory: Path) -> None:
    """Ser till att bytet av fil överlever ett strömavbrott."""
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
