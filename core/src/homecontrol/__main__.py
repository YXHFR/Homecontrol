"""Startpunkt: `python -m homecontrol` eller kommandot `homecontrol`.

    homecontrol                      startar kärnan
    homecontrol exportera [FIL]      skriver hemmet till FIL (eller skärmen)
    homecontrol importera FIL        ersätter hemmet med det i FIL
                                     (- läser det som skickas in med <)
    homecontrol importera --exempelhem
    homecontrol förbered-databasen   skapar och uppdaterar tabellerna och
                                     kärnans databasroll, och avslutas

Export och import kräver databasen (HOMECONTROL_DATABASE). Efter en import
startas kärnan om för att läsa in det nya hemmet.

`förbered-databasen` körs av Docker Compose före kärnan och ansluter som
databasens ägare. Kärnans roll får lösenordet i HOMECONTROL_CORE_DATABASE_PASSWORD.
Kärnan själv (med HOMECONTROL_PREPARE_DATABASE=false) kontrollerar då bara
att tabellerna är aktuella.

Med HOMECONTROL_HOME_COPY har kärnan en lokal kopia av hemmet och startar
även när databasen inte svarar (se homecopy.py och databaselink.py).
"""

import argparse
import logging
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

import psycopg
import uvicorn

from homecontrol import history, store
from homecontrol.app import create_app
from homecontrol.config import Settings
from homecontrol.databaselink import DatabaseLink
from homecontrol.editor import HomeStore
from homecontrol.esphome import MIN_SECRET_LENGTH
from homecontrol.home import Home
from homecontrol.homecopy import CopyingStore, HomeCopy
from homecontrol.homefile import HomeFileError, dumps, example_home, loads
from homecontrol.zigbee import mqtt_connect

logger = logging.getLogger("homecontrol")

STARTUP_ATTEMPTS_WITH_COPY = 5
"""Så många gånger kärnan försöker nå databasen vid start, med två sekunder emellan,
innan den startar med kopian av hemmet."""


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="homecontrol", description="Närvarostyrd belysning.")
    commands = parser.add_subparsers(dest="command")
    export = commands.add_parser("exportera", help="skriv hemmet till en läsbar fil")
    export.add_argument(
        "fil", nargs="?", default="-", help="filen att skriva (- skriver ut den, som med >)"
    )
    import_ = commands.add_parser("importera", help="ersätt hemmet med det i en fil")
    source = import_.add_mutually_exclusive_group(required=True)
    source.add_argument("fil", nargs="?", help="filen att läsa (- läser det som skickas in med <)")
    source.add_argument("--exempelhem", action="store_true", help="läs in exempelhemmet")
    commands.add_parser(
        "förbered-databasen",
        help="skapa och uppdatera tabellerna och kärnans databasroll (som databasens ägare)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        settings = Settings.from_env()
    except ValueError as error:
        sys.exit(str(error))
    if args.command is None:
        serve(settings)
        return
    if settings.database is None:
        sys.exit("HOMECONTROL_DATABASE är inte satt, så det finns inget sparat hem.")
    if args.command == "förbered-databasen":
        prepare_database_for_core(settings.database, settings)
    elif args.command == "exportera":
        export_home(settings.database, args.fil, prepare=settings.prepare_database)
    else:
        import_home(
            settings.database,
            None if args.exempelhem else args.fil,
            prepare=settings.prepare_database,
        )


def serve(settings: Settings) -> None:
    secret = settings.esphome_secret
    if secret is not None and len(secret) < MIN_SECRET_LENGTH:
        sys.exit(
            f"ESPHOME_SECRET i .env är för kort (minst {MIN_SECRET_LENGTH} tecken). "
            "Skapa en med: openssl rand -hex 32"
        )
    if secret is None:
        logger.info("ESPHOME_SECRET är inte satt: inga rumskort ansluts")
    home, home_store, database = open_home(settings)
    uvicorn.run(
        create_app(
            settings.web_dir,
            allowed_hosts=settings.allowed_hosts,
            home=home,
            home_store=home_store,
            database=database,
            history=prepare_history(settings, database),
            esphome_secret=secret,
            mqtt=None if settings.mqtt is None else mqtt_connect(settings.mqtt),
        ),
        host=settings.host,
        port=settings.port,
        # Livevyns meddelanden är små; större meddelanden avvisas.
        ws_max_size=64 * 1024,
    )


def prepare_database_for_core(database: str, settings: Settings) -> None:
    """Skapar och uppdaterar tabellerna, ställer in historiken och ger kärnans roll åtkomst.

    Körs som databasens ägare. Rollen får rättigheter sist, så att de också
    gäller tabeller som just skapades.
    """
    if settings.core_database_password is None:
        sys.exit("HOMECONTROL_CORE_DATABASE_PASSWORD är inte satt, se .env.example.")
    with store.connect(database) as conn:
        store.migrate(conn)
        _configure_history(conn, settings)
        store.grant_core_role(conn, settings.core_database_password)
    logger.info("Databasen är klar för kärnan (rollen %s)", store.CORE_ROLE)


def export_home(database: str, path: str, *, prepare: bool = True) -> None:
    with store.connect(database) as conn:
        _migrate_or_check(conn, prepare)
        home = store.load_home(conn)
    if home is None:
        sys.exit("Det finns inget sparat hem än. Starta kärnan en gång eller importera ett.")
    text = dumps(home)
    if path == "-":
        sys.stdout.write(text)
        return
    # Filen visar hur hemmet ser ut, så bara ägaren får läsa den.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(text)
    logger.info("Skrev hemmet till %s (%s)", path, _summary(home))


def import_home(database: str, path: str | None, *, prepare: bool = True) -> None:
    """Läser och kontrollerar hela filen innan något i databasen ändras."""
    try:
        if path is None:
            home = example_home()
        else:
            home = loads(sys.stdin.read() if path == "-" else Path(path).read_text("utf-8"))
    except (HomeFileError, OSError) as error:
        sys.exit(f"Kunde inte läsa hemmet: {error}")
    with store.connect(database) as conn:
        _migrate_or_check(conn, prepare)
        store.save_home(conn, home)
    logger.info(
        "Importerade hemmet (%s). Starta om kärnan för att använda det: "
        "docker compose restart core",
        _summary(home),
    )


def open_home(settings: Settings) -> tuple[Home, HomeStore | None, DatabaseLink | None]:
    """Hemmet att starta med, var ändringar sparas och kärnans kontakt med databasen.

    Med en kopia av hemmet (`Settings.home_copy`) startar kärnan med kopian
    om databasen inte svarar inom några sekunder, och ansluter sedan igen i
    bakgrunden. Utan kopia väntar kärnan på databasen.
    """
    if settings.database is None:
        return load_or_seed_home(settings), None, None
    database = store.DatabaseStore(settings.database, prepare=settings.prepare_database)
    if settings.home_copy is None:
        return load_or_seed_home(settings), database, DatabaseLink(database.fetch_home)
    copy = HomeCopy(settings.home_copy)

    def fetch(seed: Home) -> Home:
        home = database.fetch_home(seed)
        copy.save(home)
        return home

    home_store = CopyingStore(database, copy)
    saved = copy.load()
    if saved is None:
        # Ingen kopia än, till exempel första gången: vänta på databasen som vanligt.
        home = load_or_seed_home(settings)
        copy.save(home)
        return home, home_store, DatabaseLink(fetch)
    for attempt in range(1, STARTUP_ATTEMPTS_WITH_COPY + 1):
        try:
            home = fetch(saved)
        except HomeFileError as error:
            sys.exit(_unreadable(error))
        except (psycopg.OperationalError, store.SchemaOutdatedError) as error:
            if attempt < STARTUP_ATTEMPTS_WITH_COPY:
                logger.info("Databasen svarar inte än, försöker igen (%d)", attempt)
                time.sleep(2.0)
                continue
            logger.warning(
                "Databasen svarar inte (%s). Startar med kopian av hemmet i %s (%s); "
                "ändringar kan inte sparas förrän databasen svarar igen",
                type(error).__name__,
                settings.home_copy,
                _summary(saved),
            )
            return saved, home_store, DatabaseLink(fetch, connected=False)
        logger.info("Läste in hemmet från databasen (%s)", _summary(home))
        return home, home_store, DatabaseLink(fetch)
    raise AssertionError("unreachable")


def load_or_seed_home(settings: Settings) -> Home:
    if settings.database is None:
        logger.warning("Ingen databas är inställd: visar exempelhemmet och sparar inget")
        return example_home()
    with store.connect(settings.database) as conn:
        _migrate_or_check(conn, settings.prepare_database)
        try:
            home = store.load_home(conn)
        except HomeFileError as error:
            # Till exempel ett hem som sparades innan en kontroll skärptes.
            # Exempelhemmet läses inte in i stället, för då skrivs hemmet över.
            sys.exit(_unreadable(error))
        if home is None:
            # Första starten: börja med exempelhemmet så att det finns något att se.
            home = example_home()
            store.save_home(conn, home)
            logger.info("Inget hem var sparat, så exempelhemmet lades in")
    logger.info("Läste in hemmet från databasen (%s)", _summary(home))
    return home


def prepare_history(
    settings: Settings, database: DatabaseLink | None = None
) -> history.History | None:
    """Skrivaren av historiken. Utan databas sparas ingen historik.

    Får kärnan förbereda databasen ställs också in hur länge historiken sparas;
    annars har `förbered-databasen` redan gjort det. Det hoppas över om kärnan
    startade utan kontakt med databasen; skrivaren försöker ändå i bakgrunden.
    """
    if settings.database is None:
        return None
    if settings.prepare_database and (database is None or database.connected):
        with store.connect(settings.database) as conn:
            store.migrate(conn)
            _configure_history(conn, settings)
    return history.History(settings.database)


def _configure_history(conn: psycopg.Connection, settings: Settings) -> None:
    history.configure(conn, settings.history_full_resolution, settings.history_keep)
    logger.info(
        "Historiken sparas med full upplösning i %g timmar, sedan nedsamplad %s",
        settings.history_full_resolution / timedelta(hours=1),
        "utan tidsgräns"
        if settings.history_keep is None
        else f"i {settings.history_keep / timedelta(days=1):g} dagar",
    )


def _migrate_or_check(conn: psycopg.Connection, prepare: bool) -> None:
    """Uppdaterar tabellerna, eller kontrollerar bara att de är aktuella om kärnan inte får."""
    if prepare:
        store.migrate(conn)
        return
    try:
        store.check_schema(conn)
    except store.SchemaOutdatedError as error:
        sys.exit(str(error))


def _unreadable(error: HomeFileError) -> str:
    return (
        f"Det sparade hemmet går inte att läsa: {error}\n"
        "Rätta hemfilen och läs in den med 'homecontrol importera', "
        "eller börja om med 'homecontrol importera --exempelhem'."
    )


def _summary(home: Home) -> str:
    # Hemmets och rummens namn kan vara personliga och loggas därför inte.
    return f"{len(home.floors)} våningar, {len(home.rooms)} rum, {len(home.lamps)} lampor"


if __name__ == "__main__":
    main()
