"""Startpunkt: `python -m homecontrol` eller kommandot `homecontrol`.

    homecontrol                      startar kärnan
    homecontrol exportera [FIL]      skriver hemmet till FIL (eller skärmen)
    homecontrol importera FIL        ersätter hemmet med det i FIL
                                     (- läser det som skickas in med <)
    homecontrol importera --exempelhem

Export och import kräver databasen (HOMECONTROL_DATABASE). Efter en import
startas kärnan om för att läsa in det nya hemmet.
"""

import argparse
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

import uvicorn

from homecontrol import history, store
from homecontrol.app import create_app
from homecontrol.config import Settings
from homecontrol.esphome import MIN_SECRET_LENGTH
from homecontrol.home import Home
from homecontrol.homefile import HomeFileError, dumps, example_home, loads

logger = logging.getLogger("homecontrol")


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
    if args.command == "exportera":
        export_home(settings.database, args.fil)
    else:
        import_home(settings.database, None if args.exempelhem else args.fil)


def serve(settings: Settings) -> None:
    secret = settings.esphome_secret
    if secret is not None and len(secret) < MIN_SECRET_LENGTH:
        sys.exit(
            f"ESPHOME_SECRET i .env är för kort (minst {MIN_SECRET_LENGTH} tecken). "
            "Skapa en med: openssl rand -hex 32"
        )
    if secret is None:
        logger.info("ESPHOME_SECRET är inte satt: inga rumskort ansluts")
    uvicorn.run(
        create_app(
            settings.web_dir,
            allowed_hosts=settings.allowed_hosts,
            home=load_or_seed_home(settings),
            home_store=(
                None if settings.database is None else store.DatabaseStore(settings.database)
            ),
            history=prepare_history(settings),
            esphome_secret=secret,
        ),
        host=settings.host,
        port=settings.port,
        # Livevyns meddelanden är små; större meddelanden avvisas.
        ws_max_size=64 * 1024,
    )


def export_home(database: str, path: str) -> None:
    with store.connect(database) as conn:
        store.migrate(conn)
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


def import_home(database: str, path: str | None) -> None:
    """Läser och kontrollerar hela filen innan något i databasen ändras."""
    try:
        if path is None:
            home = example_home()
        else:
            home = loads(sys.stdin.read() if path == "-" else Path(path).read_text("utf-8"))
    except (HomeFileError, OSError) as error:
        sys.exit(f"Kunde inte läsa hemmet: {error}")
    with store.connect(database) as conn:
        store.migrate(conn)
        store.save_home(conn, home)
    logger.info(
        "Importerade hemmet (%s). Starta om kärnan för att använda det: "
        "docker compose restart core",
        _summary(home),
    )


def load_or_seed_home(settings: Settings) -> Home:
    if settings.database is None:
        logger.warning("Ingen databas är inställd: visar exempelhemmet och sparar inget")
        return example_home()
    with store.connect(settings.database) as conn:
        store.migrate(conn)
        try:
            home = store.load_home(conn)
        except HomeFileError as error:
            # Till exempel ett hem som sparades innan en kontroll skärptes.
            # Exempelhemmet läses inte in i stället, för då skrivs hemmet över.
            sys.exit(
                f"Det sparade hemmet går inte att läsa: {error}\n"
                "Rätta hemfilen och läs in den med 'homecontrol importera', "
                "eller börja om med 'homecontrol importera --exempelhem'."
            )
        if home is None:
            # Första starten: börja med exempelhemmet så att det finns något att se.
            home = example_home()
            store.save_home(conn, home)
            logger.info("Inget hem var sparat, så exempelhemmet lades in")
    logger.info("Läste in hemmet från databasen (%s)", _summary(home))
    return home


def prepare_history(settings: Settings) -> history.History | None:
    """Ställer in hur länge historiken sparas. Utan databas sparas ingen historik."""
    if settings.database is None:
        return None
    with store.connect(settings.database) as conn:
        store.migrate(conn)
        history.configure(conn, settings.history_full_resolution, settings.history_keep)
    logger.info(
        "Historiken sparas med full upplösning i %g timmar, sedan nedsamplad %s",
        settings.history_full_resolution / timedelta(hours=1),
        "utan tidsgräns"
        if settings.history_keep is None
        else f"i {settings.history_keep / timedelta(days=1):g} dagar",
    )
    return history.History(settings.database)


def _summary(home: Home) -> str:
    # Hemmets och rummens namn kan vara personliga och loggas därför inte.
    return f"{len(home.floors)} våningar, {len(home.rooms)} rum, {len(home.lamps)} lampor"


if __name__ == "__main__":
    main()
