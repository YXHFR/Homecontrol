"""Inställningar som läses från miljövariabler.

Hemligheter läggs i en gitignorerad .env-fil i repots rot (se .env.example)
och skickas in i containern av Docker Compose.
"""

import os
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
            core_database_password=os.environ.get("HOMECONTROL_CORE_DATABASE_PASSWORD") or None,
            esphome_secret=os.environ.get("HOMECONTROL_ESPHOME_SECRET", "").strip() or None,
            mqtt=_mqtt_from_env(),
        )


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
