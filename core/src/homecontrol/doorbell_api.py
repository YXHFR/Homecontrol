"""Dörrklockan i webbgränssnittet: sidan Dörren och ljudet till högtalaren (se doorbell.py).

* ``GET /api/dorr`` säger om dörrklockan har en kamera och hur många
  sekunder till kameran får visas (``open_seconds``, 0 när fönstret är stängt).
* ``POST /api/dorr/visa`` är knappen Visa dörren: öppnar fönstret i 2 minuter.
* ``GET /api/dorr/strom`` är kamerans ström (MJPEG), bara medan fönstret är
  öppet. Den slutar när fönstret stängs. Utanför fönstret svarar den 403 och
  kameran rörs inte.
* ``GET /api/dorr/dingdong.mp3`` är ljudet som Google Home-högtalaren hämtar.

Vägarna finns bara när dörrklockan är inställd. Samma kontroll av adress och
avsändare som resten av API:t gäller (trust.py), så att en främmande sida
inte kan öppna fönstret eller hämta strömmen.
"""

import contextlib
import logging
from collections.abc import AsyncIterator, Set
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from homecontrol.doorbell import Doorbell, TooManyViewers, ViewClosed
from homecontrol.trust import trusted

logger = logging.getLogger(__name__)

DEFAULT_SOUND = Path(__file__).parent / "sounds" / "dingdong.mp3"
"""Ljudet som följer med: ett enkelt dingdong som är genererat för Homecontrol."""
BOUNDARY = "bild"
NO_STORE = {"Cache-Control": "no-store"}


def create_doorbell_router(
    doorbell: Doorbell, allowed_hosts: Set[str] = frozenset(), sound: Path = DEFAULT_SOUND
) -> APIRouter:
    """`allowed_hosts` är värdnamn utöver localhost och IP-adresser som kärnan får nås på."""
    router = APIRouter()

    def check(request: Request) -> None:
        if not trusted(request.headers, allowed_hosts):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Okänd adress eller avsändare")

    def state() -> dict[str, Any]:
        return {
            "camera": doorbell.has_camera,
            "open_seconds": round(doorbell.view_seconds_left()),
        }

    @router.get("/api/dorr")
    def get_door(request: Request) -> JSONResponse:
        check(request)
        return JSONResponse(state(), headers=NO_STORE)

    @router.post("/api/dorr/visa")
    def show_door(request: Request) -> JSONResponse:
        check(request)
        doorbell.open_view()
        return JSONResponse(state(), headers=NO_STORE)

    @router.get("/api/dorr/strom")
    async def stream(request: Request) -> StreamingResponse:
        check(request)
        frames = doorbell.frames()
        try:
            # Den första bilden hämtas innan svaret börjar, så att ett stängt
            # fönster eller en kamera som inte svarar ger ett riktigt felsvar.
            first = await anext(frames)
        except ViewClosed, StopAsyncIteration:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Dörren visas bara i 2 minuter efter ett ring eller efter Visa dörren.",
            ) from None
        except TooManyViewers:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "Kameran visas redan på två ställen."
            ) from None
        except ConnectionError, OSError, TimeoutError:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Kameran i dörrklockan svarar inte."
            ) from None

        async def body() -> AsyncIterator[bytes]:
            async with contextlib.aclosing(frames):
                yield _part(first)
                with contextlib.suppress(ConnectionError, OSError, TimeoutError):
                    async for frame in frames:
                        yield _part(frame)

        return StreamingResponse(
            body(),
            media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
            headers=NO_STORE,
        )

    @router.get("/api/dorr/dingdong.mp3")
    def dingdong(request: Request) -> FileResponse:
        check(request)
        return FileResponse(sound, media_type="audio/mpeg")

    return router


def _part(jpeg: bytes) -> bytes:
    header = f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n"
    return header.encode() + jpeg + b"\r\n"
