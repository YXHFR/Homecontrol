"""Inställningar som läses från miljövariabler.

Hemligheter läggs i en gitignorerad .env-fil i repots rot (se .env.example)
och skickas in i containern av Docker Compose.
"""

import base64
import binascii
import ipaddress
import os
import re
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import overload

HISTORY_FULL_RESOLUTION = timedelta(days=1)
"""Hur länge varje punkt i historiken sparas innan bara en punkt per sekund och spår finns kvar."""
MIN_HISTORY_FULL_RESOLUTION = timedelta(hours=1)
"""Kortare tid än så hinner nedsamplingen inte med (se migrations/0003_historik.sql)."""


@dataclass(frozen=True)
class MqttSettings:
    """MQTT-brokern där Zigbee2MQTT finns, och kärnans inloggning där."""

    host: str
    port: int = 1883
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    """Visas aldrig när inställningarna skrivs ut, till exempel i en logg."""


NTFY_TOPIC = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
"""Ett ämne i ntfy: bokstäver a–z, siffror, - och _ (som ntfy själv kräver)."""
DOORBELL_API_KEY_BYTES = 32
"""ESPHome:s krypteringsnyckel är 32 byte, skriven som base64."""


@dataclass(frozen=True)
class DoorbellSettings:
    """Dörrklockan (se doorbell.py). Alla delar utom notisen är valfria.

    Allt här är personligt eller hemligt och visas därför inte när
    inställningarna skrivs ut.
    """

    ntfy_topic: str = field(repr=False)
    """Ämnet i ntfy som notiserna skickas till. Den som kan gissa det kan läsa dem."""
    ntfy_url: str = "https://ntfy.sh"
    ntfy_token: str | None = field(default=None, repr=False)
    """Inloggning hos ntfy, för en egen ntfy-server eller ett reserverat ämne."""
    camera_host: str | None = field(default=None, repr=False)
    """Kamerans (XIAO ESP32S3 Sense) fasta IP-adress. Utan den skickas notisen utan bild."""
    camera_key: bytes | None = field(default=None, repr=False)
    """Nyckeln till kamerans krypterade ESPHome-API, samma som i firmware/secrets.yaml."""
    speaker_host: str | None = field(default=None, repr=False)
    """Google Home-högtalarens fasta IP-adress. Utan den spelas inget ljud."""
    speaker_volume: int = 50
    """Volymen i procent (0–100) som dingdong spelas med. Högtalarens volym återställs sedan."""
    media_host: str | None = field(default=None, repr=False)
    """Datorns adress som högtalaren hämtar ljudet från. Utan den räknas den ut."""
    sound_file: Path | None = None
    """Ljudet som spelas. Utan det används dingdong.mp3 som följer med."""
    lamp: str | None = field(default=None, repr=False)
    """Lampans namn i Zigbee2MQTT. Utan det tänds ingen lampa."""

    def __post_init__(self) -> None:
        if not NTFY_TOPIC.match(self.ntfy_topic):
            raise ValueError(
                "DOORBELL_NTFY_TOPIC i .env får bara innehålla a–z, A–Z, 0–9, - och _ "
                "(högst 64 tecken)"
            )
        if not self.ntfy_url.startswith(("https://", "http://")):
            raise ValueError("DOORBELL_NTFY_URL i .env ska börja med https://")
        if (self.camera_host is None) != (self.camera_key is None):
            raise ValueError(
                "DOORBELL_CAMERA_HOST och DOORBELL_CAMERA_KEY i .env ska fyllas i tillsammans"
            )
        for name, host in (
            ("DOORBELL_CAMERA_HOST", self.camera_host),
            ("DOORBELL_SPEAKER_HOST", self.speaker_host),
            ("DOORBELL_MEDIA_HOST", self.media_host),
        ):
            if host is not None:
                _parse_ip(name, host)
        if not 0 <= self.speaker_volume <= 100:
            raise ValueError("DOORBELL_SPEAKER_VOLUME i .env ska vara 0–100")
        if self.lamp is not None and (
            any(char in self.lamp for char in "+#\0") or self.lamp.startswith("bridge")
        ):
            raise ValueError("DOORBELL_LAMP i .env ska vara lampans namn i Zigbee2MQTT")


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    web_dir: Path
    allowed_hosts: frozenset[str] = frozenset()
    """Värdnamn utöver localhost och IP-adresser som livevyn får nås på."""
    database: str | None = None
    """Anslutningen till Postgres, till exempel "host=db dbname=homecontrol user=homecontrol".

    Lösenordet står inte här utan läses av databasbiblioteket från PGPASSWORD.
    Utan databas används exempelhemmet, och inget sparas.
    """
    prepare_database: bool = True
    """Om kärnan själv skapar och uppdaterar tabellerna och ställer in historiken.

    Med Docker Compose görs det i stället av `homecontrol förbered-databasen`
    före kärnan, och kärnan ansluter med en roll som bara får läsa och skriva.
    """
    home_copy: Path | None = None
    """Filen där kärnan har en kopia av hemmet, att starta med när databasen inte svarar.

    Utan den väntar kärnan på databasen vid start (se homecopy.py).
    """
    core_database_password: str | None = field(default=None, repr=False)
    """Lösenordet som kärnans databasroll får av `homecontrol förbered-databasen`."""
    esphome_secret: str | None = field(default=None, repr=False)
    """Hemligheten som rumskortens nycklar räknas fram ur (se esphome.py).

    Utan den ansluts inga rumskort.
    """
    mqtt: MqttSettings | None = None
    """MQTT-brokern där Zigbee2MQTT finns (se zigbee.py).

    Lösenordet läses från HOMECONTROL_MQTT_PASSWORD. Utan broker styrs inga
    riktiga lampor. Utan rumskort och riktiga lampor är simuleringsläget
    påslaget från start.
    """
    history_full_resolution: timedelta = HISTORY_FULL_RESOLUTION
    """Hur länge historiken sparas med full upplösning innan den bara finns nedsamplad."""
    history_keep: timedelta | None = None
    """Hur länge historiken sparas över huvud taget, eller None för utan tidsgräns."""
    doorbell: DoorbellSettings | None = None
    """Dörrklockan (se doorbell.py). Utan den är dörrklockan avstängd."""

    def __post_init__(self) -> None:
        if self.history_keep is not None and self.history_keep <= self.history_full_resolution:
            raise ValueError(
                f"HISTORY_KEEP_DAYS i .env ({self.history_keep / timedelta(days=1):g} dagar)"
                " ska vara längre än HISTORY_FULL_HOURS"
                f" ({self.history_full_resolution / timedelta(hours=1):g} timmar)"
            )

    @classmethod
    def from_env(cls) -> Settings:
        """Läser inställningarna. Ett felaktigt värde ger ValueError som säger vilket."""
        return cls(
            host=os.environ.get("HOMECONTROL_HOST", "127.0.0.1"),
            port=_parse_port(os.environ.get("HOMECONTROL_PORT", "8000")),
            web_dir=Path(os.environ.get("HOMECONTROL_WEB_DIR", "web")),
            allowed_hosts=frozenset(
                name.strip().lower()
                for name in os.environ.get("HOMECONTROL_ALLOWED_HOSTS", "").split(",")
                if name.strip()
            ),
            database=os.environ.get("HOMECONTROL_DATABASE", "").strip() or None,
            prepare_database=_parse_bool(
                "HOMECONTROL_PREPARE_DATABASE",
                os.environ.get("HOMECONTROL_PREPARE_DATABASE", ""),
                default=True,
            ),
            history_full_resolution=_parse_duration(
                "HISTORY_FULL_HOURS",
                os.environ.get("HOMECONTROL_HISTORY_FULL_HOURS", ""),
                timedelta(hours=1),
                minimum=MIN_HISTORY_FULL_RESOLUTION,
                default=HISTORY_FULL_RESOLUTION,
            ),
            history_keep=_parse_duration(
                "HISTORY_KEEP_DAYS",
                os.environ.get("HOMECONTROL_HISTORY_KEEP_DAYS", ""),
                timedelta(days=1),
                minimum=timedelta(days=1),
                default=None,
            ),
            home_copy=_optional_path(os.environ.get("HOMECONTROL_HOME_COPY", "")),
            core_database_password=os.environ.get("HOMECONTROL_CORE_DATABASE_PASSWORD") or None,
            esphome_secret=os.environ.get("HOMECONTROL_ESPHOME_SECRET", "").strip() or None,
            mqtt=_mqtt_from_env(),
            doorbell=_doorbell_from_env(),
        )


def _optional_path(text: str) -> Path | None:
    return Path(text.strip()) if text.strip() else None


def _parse_port(text: str, name: str = "HOMECONTROL_PORT") -> int:
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"{name} ska vara ett portnummer (inte {text!r})") from None


def _parse_bool(name: str, text: str, *, default: bool) -> bool:
    value = text.strip().lower()
    if not value:
        return default
    if value in ("true", "1"):
        return True
    if value in ("false", "0"):
        return False
    raise ValueError(f"{name} ska vara true eller false (inte {text!r})")


@overload
def _parse_duration(
    name: str, text: str, unit: timedelta, *, minimum: timedelta, default: timedelta
) -> timedelta: ...
@overload
def _parse_duration(
    name: str, text: str, unit: timedelta, *, minimum: timedelta, default: None
) -> timedelta | None: ...
def _parse_duration(
    name: str, text: str, unit: timedelta, *, minimum: timedelta, default: timedelta | None
) -> timedelta | None:
    """Läser ett antal `unit` från `text`, eller `default` om inget är angivet.

    `name` är inställningens namn i .env, så att felmeddelandet går att följa.
    """
    if not text.strip():
        return default
    try:
        count = float(text)
        duration = unit * count
    except ValueError, OverflowError:
        raise ValueError(
            f"{name} i .env ska vara ett tal, till exempel 48 (inte {text!r})"
        ) from None
    if duration < minimum:
        raise ValueError(f"{name} i .env ska vara minst {minimum / unit:g} (inte {count:g})")
    return duration


def _mqtt_from_env() -> MqttSettings | None:
    host = os.environ.get("HOMECONTROL_MQTT_HOST", "").strip()
    if not host:
        return None
    return MqttSettings(
        host=host,
        port=_parse_port(os.environ.get("HOMECONTROL_MQTT_PORT", "1883"), "HOMECONTROL_MQTT_PORT"),
        username=os.environ.get("HOMECONTROL_MQTT_USER", "").strip() or None,
        password=os.environ.get("HOMECONTROL_MQTT_PASSWORD") or None,
    )


def _env(name: str) -> str | None:
    return os.environ.get(name, "").strip() or None


def _doorbell_from_env() -> DoorbellSettings | None:
    """Dörrklockan är påslagen bara när den har ett ämne i ntfy att skicka notiser till."""
    topic = _env("HOMECONTROL_DOORBELL_NTFY_TOPIC")
    if topic is None:
        return None
    volume = _env("HOMECONTROL_DOORBELL_SPEAKER_VOLUME")
    key = _env("HOMECONTROL_DOORBELL_CAMERA_KEY")
    sound = _env("HOMECONTROL_DOORBELL_SOUND_FILE")
    try:
        speaker_volume = 50 if volume is None else int(volume)
    except ValueError:
        raise ValueError(
            f"DOORBELL_SPEAKER_VOLUME i .env ska vara ett tal 0–100 (inte {volume!r})"
        ) from None
    return DoorbellSettings(
        ntfy_topic=topic,
        ntfy_url=(_env("HOMECONTROL_DOORBELL_NTFY_URL") or "https://ntfy.sh").rstrip("/"),
        ntfy_token=_env("HOMECONTROL_DOORBELL_NTFY_TOKEN"),
        camera_host=_env("HOMECONTROL_DOORBELL_CAMERA_HOST"),
        camera_key=None if key is None else _parse_api_key(key),
        speaker_host=_env("HOMECONTROL_DOORBELL_SPEAKER_HOST"),
        speaker_volume=speaker_volume,
        media_host=_env("HOMECONTROL_DOORBELL_MEDIA_HOST"),
        sound_file=None if sound is None else Path(sound),
        lamp=_env("HOMECONTROL_DOORBELL_LAMP"),
    )


def _parse_api_key(text: str) -> bytes:
    try:
        key = base64.b64decode(text, validate=True)
    except binascii.Error, ValueError:
        key = b""
    if len(key) != DOORBELL_API_KEY_BYTES:
        raise ValueError(
            "DOORBELL_CAMERA_KEY i .env ska vara 32 byte i base64. "
            "Skapa en med: openssl rand -base64 32"
        )
    return key


def _parse_ip(name: str, text: str) -> None:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        raise ValueError(
            f"{name} i .env ska vara en IP-adress, till exempel 192.168.1.30"
        ) from None
