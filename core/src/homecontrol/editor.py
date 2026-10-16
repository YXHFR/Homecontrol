"""Kartredigerarens API: hemmet och bilderna av planritningarna.

* ``GET /api/home`` ger hemmet i samma form som exportfilen (se homefile.py).
* ``PUT /api/home`` ersätter hemmet. Det kontrolleras precis som en import, och
  ett fel ger svar 422 med förklaringen i ``detail``.
* ``GET``, ``PUT`` och ``DELETE`` på ``/api/floors/{floor_id}/plan-image``
  läser, ersätter och tar bort bilden av våningens planritning. Bilden skickas
  som den är (PNG, JPEG eller WebP). Var den ligger och hur den är skalad står
  i hemmet (``Floor.plan_image``).

Ett sparat hem lämnas till kärnan direkt (`Controller.replace_home`), så att
livevyn visar ändringen utan omstart.
"""

import asyncio
import logging
from collections.abc import Callable, Set
from typing import Any, Protocol, TypeVar

import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from homecontrol import homefile
from homecontrol.controller import Controller
from homecontrol.home import Home
from homecontrol.store import StoredImage
from homecontrol.trust import trusted

logger = logging.getLogger(__name__)

MAX_HOME_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
"""Webbgränssnittet förminskar bilden innan den skickas, så den här gränsen nås sällan."""

T = TypeVar("T")


class HomeStore(Protocol):
    """Där kartredigerarens ändringar sparas. Anropen får blockera."""

    def save_home(self, home: Home) -> None: ...

    def save_plan_image(self, floor_id: str, image: StoredImage) -> None: ...

    def load_plan_image(self, floor_id: str) -> StoredImage | None: ...

    def delete_plan_image(self, floor_id: str) -> None: ...


class MemoryStore:
    """Sparar bara så länge kärnan är igång. Används när ingen databas är inställd."""

    def __init__(self) -> None:
        self.home: Home | None = None
        self.images: dict[str, StoredImage] = {}

    def save_home(self, home: Home) -> None:
        self.home = home
        floor_ids = {floor.id for floor in home.floors}
        self.images = {k: v for k, v in self.images.items() if k in floor_ids}

    def save_plan_image(self, floor_id: str, image: StoredImage) -> None:
        self.images[floor_id] = image

    def load_plan_image(self, floor_id: str) -> StoredImage | None:
        return self.images.get(floor_id)

    def delete_plan_image(self, floor_id: str) -> None:
        self.images.pop(floor_id, None)


def create_editor_router(
    controller: Controller, home_store: HomeStore, allowed_hosts: Set[str] = frozenset()
) -> APIRouter:
    """`allowed_hosts` är värdnamn utöver localhost och IP-adresser som kärnan får nås på."""

    def only_trusted(request: Request) -> None:
        # Utan kontrollen kunde en främmande sida som någon i hemmet besöker
        # läsa planritningen eller ändra hemmet genom webbläsaren.
        if not trusted(request.headers, allowed_hosts):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Okänd adress eller avsändare")

    router = APIRouter(prefix="/api", dependencies=[Depends(only_trusted)])
    # Två sparningar i taget får inte hamna i olika ordning i databasen och i kärnan.
    saving = asyncio.Lock()

    async def in_store(call: Callable[..., T], *args: Any) -> T:
        try:
            return await anyio.to_thread.run_sync(call, *args)
        except Exception:
            logger.exception("Kunde inte nå lagringen")
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Kunde inte spara, försök igen om en stund"
            ) from None

    def known_floor(floor_id: str) -> str:
        if all(floor.id != floor_id for floor in controller.home.floors):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Våningen finns inte")
        return floor_id

    @router.get("/home")
    def get_home() -> dict[str, Any]:
        return homefile.to_document(controller.home)

    @router.put("/home", status_code=status.HTTP_204_NO_CONTENT)
    async def put_home(request: Request) -> None:
        body = await _read_body(request, MAX_HOME_BYTES)
        try:
            home = homefile.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, homefile.HomeFileError) as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
        async with saving:
            await in_store(home_store.save_home, home)
            controller.replace_home(home)
        logger.info(
            "Hemmet ändrades i kartredigeraren (%d rum, %d lampor)",
            len(home.rooms),
            len(home.lamps),
        )

    @router.get("/floors/{floor_id}/plan-image")
    async def get_plan_image(floor_id: str = Depends(known_floor)) -> Response:
        image = await in_store(home_store.load_plan_image, floor_id)
        if image is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Våningen har ingen bild")
        return Response(
            image.data,
            media_type=image.media_type,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @router.put("/floors/{floor_id}/plan-image", status_code=status.HTTP_204_NO_CONTENT)
    async def put_plan_image(request: Request, floor_id: str = Depends(known_floor)) -> None:
        data = await _read_body(request, MAX_IMAGE_BYTES)
        media_type = _image_type(data)
        if media_type is None:
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Bilden ska vara PNG, JPEG eller WebP"
            )
        await in_store(home_store.save_plan_image, floor_id, StoredImage(media_type, data))

    @router.delete("/floors/{floor_id}/plan-image", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_plan_image(floor_id: str = Depends(known_floor)) -> None:
        await in_store(home_store.delete_plan_image, floor_id)

    return router


def _image_type(data: bytes) -> str | None:
    """Bildens format, känt på de första byten och inte på vad avsändaren påstår.

    Bilden visas i webbläsaren och ska inte kunna vara något annat än en bild.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def _read_body(request: Request, limit: int) -> bytes:
    """Läser hela anropet, men högst `limit` byte."""
    too_large = HTTPException(
        status.HTTP_413_CONTENT_TOO_LARGE, f"Får vara högst {limit // (1024 * 1024)} MB"
    )
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > limit:
        raise too_large
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise too_large
    return bytes(body)
