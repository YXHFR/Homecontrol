"""Inställningar som läses från miljövariabler.

Hemligheter läggs i en gitignorerad .env-fil i repots rot (se .env.example)
och skickas in i containern av Docker Compose.
"""

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from homecontrol.history import FULL_RESOLUTION


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
    history_full_resolution: timedelta = FULL_RESOLUTION
    """Hur länge historiken sparas med full upplösning innan den bara finns nedsamplad."""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.environ.get("HOMECONTROL_HOST", "127.0.0.1"),
            port=int(os.environ.get("HOMECONTROL_PORT", "8000")),
            web_dir=Path(os.environ.get("HOMECONTROL_WEB_DIR", "web")),
            allowed_hosts=frozenset(
                name.strip().lower()
                for name in os.environ.get("HOMECONTROL_ALLOWED_HOSTS", "").split(",")
                if name.strip()
            ),
            database=os.environ.get("HOMECONTROL_DATABASE", "").strip() or None,
            history_full_resolution=_hours(
                os.environ.get("HOMECONTROL_HISTORY_FULL_HOURS", ""), FULL_RESOLUTION
            ),
        )


def _hours(text: str, default: timedelta) -> timedelta:
    return timedelta(hours=float(text)) if text.strip() else default
