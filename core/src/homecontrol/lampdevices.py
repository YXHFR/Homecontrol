"""De riktiga lamporna som lamputgången har hittat, för kartredigeraren.

* ``GET /api/lamps`` ger lamporna som en lista. Varje lampa har det ``id``,
  ``name`` och ``profile`` den ska få när den placeras på kartan, och
  ``placed`` som säger om den redan är placerad. En lampa som inte är
  placerad används inte förrän någon har placerat den. Utan lamputgång är
  listan tom.

Samma kontroll av adress och avsändare som resten av API:t gäller (trust.py),
så att en främmande sida inte kan läsa vilka lampor som finns.
"""

from collections.abc import Sequence, Set
from dataclasses import dataclass
from typing import Protocol

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from homecontrol.home import LampProfile
from homecontrol.trust import trusted


@dataclass(frozen=True)
class FoundLamp:
    """En riktig lampa som lamputgången har hittat, placerad på kartan eller inte."""

    id: str
    """Id:t som lampan ska ha i hemmet. Med det id:t är den placerad."""
    name: str
    profile: LampProfile
    """Profilen som passar lampan."""
    placed: bool


class LampDevices(Protocol):
    """En lamputgång som hittar lampor själv, till exempel Zigbee2MQTT (se zigbee.py)."""

    def lamps(self) -> Sequence[FoundLamp]: ...


def create_lamp_devices_router(
    devices: LampDevices | None, allowed_hosts: Set[str] = frozenset()
) -> APIRouter:
    """`allowed_hosts` är värdnamn utöver localhost och IP-adresser som kärnan får nås på."""
    router = APIRouter()

    @router.get("/api/lamps")
    def get_lamps(request: Request) -> JSONResponse:
        if not trusted(request.headers, allowed_hosts):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Okänd adress eller avsändare")
        found = [] if devices is None else devices.lamps()
        return JSONResponse(
            [
                {
                    "id": lamp.id,
                    "name": lamp.name,
                    "profile": lamp.profile.value,
                    "placed": lamp.placed,
                }
                for lamp in found
            ],
            headers={"Cache-Control": "no-store"},
        )

    return router
