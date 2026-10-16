"""Lampkalibreringens API, och det som sparar lampornas uppmätta bidrag i hemmet.

* ``POST /api/rooms/{room_id}/calibration`` startar kalibreringen i rummet
  (se calibration.py). Svaret är 202 när den har startat, 404 om rummet inte
  finns och 409 med förklaringen i ``detail`` om den inte går att starta,
  till exempel för att rummet saknar ljussensor eller en lampa inte svarar.
* ``DELETE /api/calibration`` avbryter kalibreringen som pågår, och lamporna
  ställs tillbaka som de var.

Hur kalibreringen går syns i livevyns ström (``calibration`` i ``state``, se
live.py). När den är klar sparas bidragen i hemmet på samma sätt som en
ändring i kartredigeraren (se `editor.HomeChanges`), så att redigeraren i ett
annat fönster inte skriver över dem.

Samma kontroll av adress och avsändare som resten av API:t gäller (trust.py),
så att en främmande sida som någon i hemmet besöker inte kan tända och släcka
lamporna genom att starta en kalibrering.
"""

import asyncio
from collections.abc import Mapping, Set

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from homecontrol.calibration import with_contributions
from homecontrol.controller import CalibratedListener, Controller
from homecontrol.editor import HomeChanges, StoreUnavailable
from homecontrol.trust import trusted


def calibration_saver(changes: HomeChanges) -> CalibratedListener:
    """Sparar lampornas bidrag i hemmet när en kalibrering är klar (`Controller.on_calibrated`).

    Sparandet sker i bakgrunden. Går det inte att spara visar kalibreringen det.
    """
    saving: set[asyncio.Task[None]] = set()

    async def save(results: Mapping[str, float]) -> None:
        try:
            await changes.change(
                lambda home: with_contributions(home, results), changes.store.save_home
            )
        except StoreUnavailable:
            changes.controller.calibration_not_saved(
                "Databasen svarade inte. Försök igen om en stund."
            )

    def start_saving(results: Mapping[str, float]) -> None:
        task = asyncio.get_running_loop().create_task(save(dict(results)))
        saving.add(task)  # så att uppgiften inte städas bort innan den är klar
        task.add_done_callback(saving.discard)

    return start_saving


def create_calibration_router(
    controller: Controller, allowed_hosts: Set[str] = frozenset()
) -> APIRouter:
    """`allowed_hosts` är värdnamn utöver localhost och IP-adresser som kärnan får nås på."""

    def only_trusted(request: Request) -> None:
        if not trusted(request.headers, allowed_hosts):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Okänd adress eller avsändare")

    router = APIRouter(prefix="/api", dependencies=[Depends(only_trusted)])

    @router.post("/rooms/{room_id}/calibration", status_code=status.HTTP_202_ACCEPTED)
    async def start(room_id: str) -> Response:
        # Async, så att kärnan anropas från händelseloopen och inte från en tråd.
        if not any(room.id == room_id for room in controller.home.rooms):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Rummet finns inte")
        problem = controller.start_calibration(room_id)
        if problem is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, problem)
        return Response(status_code=status.HTTP_202_ACCEPTED)

    @router.delete("/calibration", status_code=status.HTTP_204_NO_CONTENT)
    async def cancel() -> Response:
        controller.cancel_calibration()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
