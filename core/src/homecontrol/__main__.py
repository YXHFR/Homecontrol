"""Startpunkt: `python -m homecontrol` eller kommandot `homecontrol`.

    homecontrol                      startar kärnan
    homecontrol exportera [FIL]      skriver hemmet till FIL (eller skärmen)
    homecontrol importera FIL        ersätter hemmet med det i FIL
                                     (- läser det som skickas in med <)
    homecontrol importera --exempelhem
    homecontrol förbered-databasen   skapar och uppdaterar tabellerna och
                                     kärnans databasroll, och avslutas
    homecontrol ringa                låtsas att någon ringer på dörrklockan
                                     (som ett tryck på knappen, se doorbell.py)

Export och import kräver databasen (HOMECONTROL_DATABASE). Efter en import
startas kärnan om för att läsa in det nya hemmet.

`förbered-databasen` körs av Docker Compose före kärnan och ansluter som
databasens ägare. Svarar inte databasen alls avslutas den ändå utan fel, så
att kärnan kan starta med sin kopia av hemmet. Kärnans roll får lösenordet i
HOMECONTROL_CORE_DATABASE_PASSWORD.
Kärnan själv (med HOMECONTROL_PREPARE_DATABASE=false) kontrollerar då bara
att tabellerna är aktuella.

Med HOMECONTROL_HOME_COPY har kärnan en lokal kopia av hemmet och startar
även när databasen inte svarar (se homecopy.py och databaselink.py).
"""

import argparse
import asyncio
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
from homecontrol.config import DoorbellSettings, Settings
from homecontrol.databaselink import DatabaseLink, Fetch
from homecontrol.doorbell import DEFAULT_SOUND, Doorbell, simulate_ring
from homecontrol.editor import HomeStore
from homecontrol.esphome import MIN_SECRET_LENGTH
from homecontrol.home import Home
from homecontrol.homecopy import CopyingStore, HomeCopy
from homecontrol.homefile import HomeFileError, dumps, example_home, loads
from homecontrol.zigbee import mqtt_connect

logger = logging.getLogger("homecontrol")

STARTUP_ATTEMPTS = 30
"""Så många gånger kärnan försöker nå databasen vid start när den inte har någon kopia."""
STARTUP_ATTEMPTS_WITH_COPY = 5
"""Så många gånger kärnan försöker nå databasen vid start innan den startar med kopian."""
STARTUP_RETRY_SECONDS = 2.0
"""Väntan mellan försöken att nå databasen vid start."""


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
    commands.add_parser(
        "ringa", help="låtsas att någon ringer på dörrklockan, för att prova den utan hårdvara"
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
    if args.command == "ringa":
        ring(settings)
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
    doorbell, doorbell_mqtt = None, None
    if settings.doorbell is not None:
        if settings.mqtt is None:
            logger.warning(
                "Dörrklockan behöver MQTT-brokern (HOMECONTROL_MQTT_HOST); den är avstängd"
            )
        else:
            doorbell = build_doorbell(settings.doorbell, settings.port)
            doorbell_mqtt = mqtt_connect(settings.mqtt, "homecontrol-dorr")
    uvicorn.run(
        create_app(
            settings.web_dir,
            allowed_hosts=settings.allowed_hosts,
            home=home,
            home_store=home_store,
            database=database,
            history=prepare_history(settings),
            esphome_secret=secret,
            mqtt=None if settings.mqtt is None else mqtt_connect(settings.mqtt),
            doorbell=doorbell,
            doorbell_mqtt=doorbell_mqtt,
        ),
        host=settings.host,
        port=settings.port,
        # Livevyns meddelanden är små; större meddelanden avvisas.
        ws_max_size=64 * 1024,
    )


def build_doorbell(settings: DoorbellSettings, port: int) -> Doorbell:
    """Dörrklockan med de delar som är inställda. Notisen finns alltid; resten är valfritt."""
    # Kopplingarna (och pychromecast) laddas bara när dörrklockan är inställd.
    from homecontrol.doorbell_devices import (
        ChromecastSpeaker,
        EsphomeCamera,
        NtfyNotifier,
    )

    if settings.sound_file is not None and not settings.sound_file.is_file():
        sys.exit(f"DOORBELL_SOUND_FILE i .env: hittar inte filen {settings.sound_file}")
    camera = None
    if settings.camera_host is not None and settings.camera_key is not None:
        camera = EsphomeCamera(settings.camera_host, settings.camera_key)
    speaker = None
    if settings.speaker_host is not None:
        speaker = ChromecastSpeaker(
            settings.speaker_host, settings.speaker_volume, port, settings.media_host
        )
    logger.info(
        "Dörrklockan är på: notis%s%s%s",
        " med bild" if camera is not None else " utan bild (ingen kamera inställd)",
        ", ljud på högtalaren" if speaker is not None else "",
        ", dörrlampan" if settings.lamp is not None else "",
    )
    return Doorbell(
        NtfyNotifier(settings.ntfy_url, settings.ntfy_topic, settings.ntfy_token),
        camera=camera,
        speaker=speaker,
        lamp=settings.lamp,
        sound=DEFAULT_SOUND if settings.sound_file is None else settings.sound_file,
    )


def ring(settings: Settings) -> None:
    """Publicerar ett ring på MQTT som om någon tryckte på dörrklockan."""
    if settings.doorbell is None:
        sys.exit("Dörrklockan är inte inställd: fyll i DOORBELL_NTFY_TOPIC i .env.")
    if settings.mqtt is None:
        sys.exit("HOMECONTROL_MQTT_HOST är inte satt, så ringet kan inte skickas.")
    try:
        asyncio.run(simulate_ring(mqtt_connect(settings.mqtt, "homecontrol-ringa")))
    except ConnectionError as error:
        sys.exit(f"Når inte MQTT-brokern: {error}")
    logger.info("Ringde på dörrklockan. Kärnan skickar notisen om en stund.")


def prepare_database_for_core(database: str, settings: Settings) -> None:
    """Skapar och uppdaterar tabellerna, ställer in historiken och ger kärnans roll åtkomst.

    Körs som databasens ägare. Rollen får rättigheter sist, så att de också
    gäller tabeller som just skapades.
    """
    if settings.core_database_password is None:
        sys.exit("HOMECONTROL_CORE_DATABASE_PASSWORD är inte satt, se .env.example.")
    try:
        conn = store.connect(database)
    except psycopg.OperationalError:
        if store.responds(database):
            raise  # till exempel fel lösenord: då ska kärnan inte starta
        # Bara när databasen inte svarar alls får kärnan starta ändå, med sin
        # kopia av hemmet. Alla andra fel stoppar den (se compose.yaml).
        logger.warning(
            "Databasen svarar inte. Kärnan startar ändå och ansluter när den svarar. "
            "Tabellerna och hur länge historiken sparas ändrades inte; "
            "kör 'docker compose up -d' igen när databasen är igång"
        )
        return
    with conn:
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
        logger.warning("Ingen databas är inställd: visar exempelhemmet och sparar inget")
        return example_home(), None, None

    def prepare(conn: psycopg.Connection) -> None:
        store.migrate(conn)
        _configure_history(conn, settings)

    database_store = store.DatabaseStore(
        settings.database, prepare=prepare if settings.prepare_database else None
    )
    home_store: store.DatabaseStore | CopyingStore = database_store
    saved = None
    if settings.home_copy is not None:
        copy = HomeCopy(settings.home_copy)
        home_store = CopyingStore(database_store, copy)
        saved = copy.load()
    home, connected = _fetch_at_startup(home_store.fetch_home, saved)
    if connected:
        logger.info("Läste in hemmet från databasen (%s)", _summary(home))
    else:
        logger.warning(
            "Databasen svarar inte. Startar med kopian av hemmet i %s (%s); "
            "ändringar kan inte sparas förrän databasen svarar igen",
            settings.home_copy,
            _summary(home),
        )
    return home, home_store, DatabaseLink(home_store.fetch_home, connected=connected)


def _fetch_at_startup(fetch: Fetch, saved: Home | None) -> tuple[Home, bool]:
    """Hemmet i databasen och True, eller kopian (`saved`) och False om databasen inte svarar.

    Har databasen inget hem läggs kopian in, eller exempelhemmet första
    gången. Utan kopia väntar kärnan en minut på databasen och ger sedan upp
    med `psycopg.OperationalError`, så att Docker startar om den.
    """
    seed = example_home() if saved is None else saved
    attempts = STARTUP_ATTEMPTS if saved is None else STARTUP_ATTEMPTS_WITH_COPY
    for attempt in range(1, attempts):
        try:
            return _fetch_or_exit(fetch, seed), True
        except psycopg.OperationalError:
            logger.info("Databasen svarar inte än, försöker igen (%d/%d)", attempt, attempts)
            time.sleep(STARTUP_RETRY_SECONDS)
    try:
        return _fetch_or_exit(fetch, seed), True
    except psycopg.OperationalError:
        if saved is None:
            raise
        return saved, False


def _fetch_or_exit(fetch: Fetch, seed: Home) -> Home:
    """Som `fetch`, men avslutar kärnan med en förklaring om databasen inte går att använda."""
    try:
        return fetch(seed)
    except HomeFileError as error:
        # Till exempel ett hem som sparades innan en kontroll skärptes. Varken
        # kopian eller exempelhemmet används i stället, för då skrivs hemmet över.
        sys.exit(_unreadable_message(error))
    except store.SchemaOutdatedError as error:
        sys.exit(str(error))


def prepare_history(settings: Settings) -> history.History | None:
    """Skrivaren av historiken. Utan databas sparas ingen historik.

    Hur länge historiken sparas ställs in av `förbered-databasen`, eller av
    `open_home` när kärnan själv får förbereda databasen.
    """
    if settings.database is None:
        return None
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


def _unreadable_message(error: HomeFileError) -> str:
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
