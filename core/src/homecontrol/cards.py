"""Rumskorten som de visas i webbgränssnittet, oberoende av vilken koppling som ansluter dem.

* ``GET /api/cards`` ger korten i samma form som ``cards`` i livevyns läge
  (se `cards_message`). Kartredigeraren hämtar dem för att kunna placera nya
  kort. Samma kontroll som resten av API:t (trust.py) gäller, så att en
  främmande sida inte kan läsa dem.
"""

from collections.abc import Callable, Sequence, Set
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from homecontrol.trust import trusted


@dataclass(frozen=True)
class CardView:
    """Det som går att visa om ett kort."""

    name: str
    friendly_name: str
    placed: bool
    online: bool
    problems: tuple[str, ...]
    """Varningar i klartext. Tom när allt fungerar."""
    calibrating: bool
    wifi_signal: float | None
    firmware_version: str | None


class Cards(Protocol):
    """De riktiga rumskorten, till exempel ESPHome-kopplingen (se esphome.py)."""

    def cards(self) -> Sequence[CardView]: ...

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]: ...


def cards_message(cards: Sequence[CardView]) -> list[dict[str, Any]]:
    """Korten: placerade kort med sina varningar, och nya kort som väntar på att placeras.

    Ett kort är ``"placed": false`` tills det har placerats i kartredigeraren.
    ``problems`` är varningar i klartext.
    """
    return [
        {
            "id": card.name,
            "name": card.friendly_name,
            "placed": card.placed,
            "online": card.online,
            "problems": list(card.problems),
            "calibrating": card.calibrating,
            "wifi_signal": card.wifi_signal,
            "firmware_version": card.firmware_version,
        }
        for card in cards
    ]


def create_cards_router(cards: Cards | None, allowed_hosts: Set[str] = frozenset()) -> APIRouter:
    """`allowed_hosts` är värdnamn utöver localhost och IP-adresser som kärnan får nås på."""
    router = APIRouter()

    @router.get("/api/cards")
    def get_cards(request: Request) -> JSONResponse:
        # Korten säger vilka rum som finns.
        if not trusted(request.headers, allowed_hosts):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Okänd adress eller avsändare")
        return JSONResponse(
            [] if cards is None else cards_message(cards.cards()),
            headers={"Cache-Control": "no-store"},
        )

    return router
