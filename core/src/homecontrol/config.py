"""Inställningar som läses från miljövariabler.

Hemligheter läggs i en gitignorerad .env-fil i repots rot (se .env.example)
och skickas in i containern av Docker Compose.
"""

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

HISTORY_FULL_RESOLUTION = timedelta(days=1)
"""Hur länge varje punkt i historiken sparas innan bara en punkt per sekund och spår finns kvar."""
MIN_HISTORY_FULL_RESOLUTION = timedelta(hours=1)
"""Kortare tid än så hinner nedsamplingen inte med (se history.py)."""


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
    history_full_resolution: timedelta = HISTORY_FULL_RESOLUTION
    """Hur länge historiken sparas med full upplösning innan den bara finns nedsamplad."""
    history_keep: timedelta | None = None
    """Hur länge historiken sparas över huvud taget, eller None för utan tidsgräns."""

    def __post_init__(self) -> None:
        if self.history_keep is not None and self.history_keep <= self.history_full_resolution:
            raise ValueError("HISTORY_KEEP_DAYS i .env ska vara längre än HISTORY_FULL_HOURS")

    @classmethod
    def from_env(cls) -> "Settings":
        """Läser inställningarna. Ett felaktigt värde ger ValueError som säger vilket."""
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
            history_full_resolution=_parse_duration(
                "HISTORY_FULL_HOURS",
                os.environ.get("HOMECONTROL_HISTORY_FULL_HOURS", ""),
                timedelta(hours=1),
                minimum=MIN_HISTORY_FULL_RESOLUTION,
            )
            or HISTORY_FULL_RESOLUTION,
            history_keep=_parse_duration(
                "HISTORY_KEEP_DAYS",
                os.environ.get("HOMECONTROL_HISTORY_KEEP_DAYS", ""),
                timedelta(days=1),
                minimum=timedelta(days=1),
            ),
        )


def _parse_duration(
    name: str, text: str, unit: timedelta, *, minimum: timedelta
) -> timedelta | None:
    """Läser ett antal `unit` från `text`, eller None om inget är angivet.

    `name` är inställningens namn i .env, så att felmeddelandet går att följa.
    """
    if not text.strip():
        return None
    try:
        count = float(text)
        duration = unit * count
    except (ValueError, OverflowError):
        raise ValueError(
            f"{name} i .env ska vara ett tal, till exempel 48 (inte {text!r})"
        ) from None
    if duration < minimum:
        raise ValueError(f"{name} i .env ska vara minst {minimum / unit:g} (inte {count:g})")
    return duration
