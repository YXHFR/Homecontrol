"""Kartredigerarens API: hemmet och bilderna av planritningarna.

* ``GET /api/home`` ger hemmet i samma form som exportfilen (se homefile.py).
* ``PUT /api/home`` ersätter hemmet. Det kontrolleras precis som en import, och
  ett fel ger svar 422 med förklaringen i ``detail``.
* ``GET /api/floors/{floor_id}/plan-image`` ger bilden av våningens planritning.
* ``PUT /api/floors/{floor_id}/plan-image?width=…`` ersätter bilden (PNG, JPEG
  eller WebP, som den är) och lägger den i övre vänstra hörnet, ungefär
  20 meter bred. ``width`` är bildens bredd i bildpunkter.
* ``DELETE /api/floors/{floor_id}/plan-image`` tar bort bilden.

Var bilden ligger och hur den är skalad står i hemmet (``Floor.plan_image``).
Kärnan ändrar bilden och hemmet tillsammans, så att de aldrig hamnar i otakt.

Varje ändring ger hemmet en ny *revision*, som skickas i svarshuvudet ``ETag``.
Den som skickar med revisionen i ``If-Match`` får svar 409 om någon annan
(till exempel redigeraren i ett annat fönster) har hunnit ändra hemmet, i
stället för att skriva över den ändringen.

Medan kärnan inte har kontakt med databasen (se databaselink.py) nekas alla
ändringar med svar 503 och en förklaring, så att kärnans kopia av hemmet och
databasen inte hamnar i otakt.

Ett sparat hem lämnas till kärnan direkt (`Controller.replace_home`), så att
livevyn visar ändringen utan omstart. Andra delar av kärnan som ändrar hemmet,
som lampkalibreringen, gör det genom samma `HomeChanges`, så att revisionen
stämmer och redigeraren inte skriver över ändringen.
"""

import asyncio
import logging
import secrets
from collections.abc import Callable, Set
from typing import Annotated, Any, Protocol, TypeVar

import anyio.to_thread
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from homecontrol import homefile
from homecontrol.controller import Controller
from homecontrol.home import Floor, Home, PlanImage, Point
from homecontrol.images import StoredImage, image_type
from homecontrol.trust import trusted

logger = logging.getLogger(__name__)

MAX_HOME_BYTES = 1024 * 1024
"""Ett hem med många våningar och rum är några tiotal kilobyte, så en megabyte räcker gott."""
MAX_IMAGE_BYTES = 10 * 1024 * 1024
"""Webbgränssnittet förminskar bilden innan den skickas, så den här gränsen nås sällan."""
MAX_IMAGE_WIDTH = 20_000
"""Bredare än så blir ingen bild från webbgränssnittet, som förminskar till 4000 bildpunkter."""
UNSCALED_IMAGE_WIDTH_METERS = 20.0
"""Så bred en nyuppladdad bild blir innan den har skalats."""

# Andra webbplatser får inte ens visa hemmet eller planritningen på sina sidor.
_PRIVATE = {"Cache-Control": "no-store", "Cross-Origin-Resource-Policy": "same-origin"}

T = TypeVar("T")


class HomeStore(Protocol):
    """Där kartredigerarens ändringar sparas. Anropen får blockera."""

    def save_home(self, home: Home) -> None: ...

    def save_plan_image(self, home: Home, floor_id: str, image: StoredImage) -> None:
        """Sparar bilden och hemmet på en gång."""

    def delete_plan_image(self, home: Home, floor_id: str) -> None:
        """Tar bort bilden och sparar hemmet på en gång."""

    def load_plan_image(self, floor_id: str) -> StoredImage | None: ...


class DatabaseConnection(Protocol):
    """Om kärnan har kontakt med databasen (se databaselink.py)."""

    @property
    def connected(self) -> bool: ...

    def mark_lost(self) -> None:
        """Kontakten har tappats; hemmet läses om från databasen när den svarar."""


class MemoryStore:
    """Sparar bara så länge kärnan är igång. Används när ingen databas är inställd."""

    def __init__(self) -> None:
        self.home: Home | None = None
        self.images: dict[str, StoredImage] = {}

    def save_home(self, home: Home) -> None:
        self.home = home
        floor_ids = {floor.id for floor in home.floors}
        self.images = {k: v for k, v in self.images.items() if k in floor_ids}

    def save_plan_image(self, home: Home, floor_id: str, image: StoredImage) -> None:
        self.save_home(home)
        self.images[floor_id] = image

    def delete_plan_image(self, home: Home, floor_id: str) -> None:
        self.save_home(home)
        self.images.pop(floor_id, None)

    def load_plan_image(self, floor_id: str) -> StoredImage | None:
        return self.images.get(floor_id)


class Conflict(Exception):
    """Hemmet har ändrats sedan revisionen som ändringen gällde."""


class StoreUnavailable(Exception):
    """Lagringen svarade inte, så ändringen sparades inte."""


class DatabaseDown(StoreUnavailable):
    """Kärnan har inte kontakt med databasen, så ändringen gjordes inte alls."""


class HomeChanges:
    """Ändrar hemmet: en ändring i taget, sparad och lämnad till kärnan, med en ny revision.

    `on_home_replaced` anropas med det nya hemmet efter varje ändring, för det
    utöver kärnan som behöver veta vilka rum och våningar som finns (simuleringen).

    Med `database` nekas ändringar medan kärnan inte har kontakt med databasen,
    och ett misslyckat sparande räknas som att kontakten har tappats.
    """

    def __init__(
        self,
        controller: Controller,
        store: HomeStore,
        on_home_replaced: Callable[[Home], None] | None = None,
        database: DatabaseConnection | None = None,
    ) -> None:
        self.controller = controller
        self.store = store
        self._on_home_replaced = on_home_replaced
        self._database = database
        # En ändring i taget, så att databasen och kärnan alltid får dem i samma ordning.
        self._lock = asyncio.Lock()
        # Revisionen börjar om när kärnan startar; den slumpade delen gör att en
        # revision från förra gången aldrig råkar stämma.
        self._startup_id = secrets.token_hex(4)
        self._count = 0

    def revision(self) -> str:
        return f'"{self._startup_id}-{self._count}"'

    async def change(
        self,
        update: Callable[[Home], Home],
        store: Callable[[Home], None],
        if_match: str | None = None,
    ) -> Home:
        """Ändrar hemmet med `update`, sparar det med `store` och lämnar det till kärnan.

        Med `if_match` görs ändringen bara om revisionen stämmer (annars
        `Conflict`). Når `store` inte lagringen blir det `StoreUnavailable`,
        och saknar kärnan kontakt med databasen `DatabaseDown`.
        """
        async with self._lock:
            if self._database is not None and not self._database.connected:
                raise DatabaseDown
            if if_match is not None and if_match != self.revision():
                raise Conflict
            home = update(self.controller.home)
            try:
                await anyio.to_thread.run_sync(store, home)
            except Exception as error:
                logger.exception("Kunde inte nå lagringen")
                if self._database is not None:
                    # Ändringen kan ha hunnit sparas; hemmet läses om från databasen.
                    self._database.mark_lost()
                raise StoreUnavailable from error
            # Nu är hemmet sparat och har en ny revision, vad som än händer i kärnan.
            self._hand_over(home)
            return home

    async def resync(self, fetch: Callable[[Home], Home]) -> None:
        """Läser hemmet med `fetch` och lämnar det till kärnan om det har ändrats.

        Används när databasen svarar igen. `fetch` får hemmet som kärnan
        använder, att spara om databasen inte har något. Inga ändringar görs
        medan hemmet läses.
        """
        async with self._lock:
            current = self.controller.home
            home = await anyio.to_thread.run_sync(fetch, current)
            if home != current:
                logger.info("Hemmet i databasen skiljer sig från kopian; använder databasens")
                self._hand_over(home)

    def _hand_over(self, home: Home) -> None:
        """Ger hemmet en ny revision och lämnar det till kärnan."""
        self._count += 1
        self.controller.replace_home(home)
        if self._on_home_replaced is not None:
            self._on_home_replaced(home)


_UNAVAILABLE = "Kunde inte nå databasen. Försök igen om en stund."
_DATABASE_DOWN = (
    "Databasen svarar inte, så ändringar kan inte sparas just nu. Lamporna styrs som "
    "vanligt med det senast sparade hemmet. Försök igen när varningen i hälsoraden är borta."
)


def create_editor_router(changes: HomeChanges, allowed_hosts: Set[str] = frozenset()) -> APIRouter:
    """`allowed_hosts` är värdnamn utöver localhost och IP-adresser som kärnan får nås på."""
    controller = changes.controller
    home_store = changes.store

    def only_trusted(request: Request) -> None:
        # Utan kontrollen kunde en främmande sida som någon i hemmet besöker
        # läsa planritningen eller ändra hemmet genom webbläsaren.
        if not trusted(request.headers, allowed_hosts):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Okänd adress eller avsändare")

    router = APIRouter(prefix="/api", dependencies=[Depends(only_trusted)])
    revision = changes.revision

    async def in_store(call: Callable[..., T], *args: Any) -> T:
        try:
            return await anyio.to_thread.run_sync(call, *args)
        except Exception:
            logger.exception("Kunde inte nå lagringen")
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _UNAVAILABLE) from None

    async def change(
        if_match: str | None, update: Callable[[Home], Home], store: Callable[[Home], None]
    ) -> Response:
        """Ändrar hemmet: kontrollerar revisionen, sparar och lämnar hemmet till kärnan."""
        try:
            await changes.change(update, store, if_match)
        except Conflict:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Hemmet har ändrats i ett annat fönster. Nu visas det senaste.",
            ) from None
        except DatabaseDown:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _DATABASE_DOWN) from None
        except StoreUnavailable:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _UNAVAILABLE) from None
        return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"ETag": revision()})

    def require_floor(home: Home, floor_id: str) -> Floor:
        """Våningen, eller svar 404 om den inte finns."""
        for floor in home.floors:
            if floor.id == floor_id:
                return floor
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Våningen finns inte")

    @router.get("/home")
    def get_home() -> Response:
        return JSONResponse(
            homefile.to_document(controller.home), headers={**_PRIVATE, "ETag": revision()}
        )

    @router.put("/home")
    async def put_home(
        request: Request, if_match: Annotated[str | None, Header()] = None
    ) -> Response:
        body = await _read_body(request, MAX_HOME_BYTES)
        try:
            home = homefile.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, homefile.HomeFileError) as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
        response = await change(if_match, lambda _: home, home_store.save_home)
        logger.info(
            "Hemmet ändrades i kartredigeraren (%d rum, %d lampor)",
            len(home.rooms),
            len(home.lamps),
        )
        return response

    @router.get("/floors/{floor_id}/plan-image")
    async def get_plan_image(floor_id: str) -> Response:
        require_floor(controller.home, floor_id)
        image = await in_store(home_store.load_plan_image, floor_id)
        if image is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Våningen har ingen bild")
        return Response(
            image.data,
            media_type=image.media_type,
            headers={**_PRIVATE, "X-Content-Type-Options": "nosniff"},
        )

    @router.put("/floors/{floor_id}/plan-image")
    async def put_plan_image(
        request: Request,
        floor_id: str,
        width: Annotated[int, Query(gt=0, le=MAX_IMAGE_WIDTH)],
        if_match: Annotated[str | None, Header()] = None,
    ) -> Response:
        require_floor(controller.home, floor_id)
        data = await _read_body(request, MAX_IMAGE_BYTES)
        media_type = image_type(data)
        if media_type is None:
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Bilden ska vara PNG, JPEG eller WebP"
            )
        image = StoredImage(media_type, data)
        plan = PlanImage(
            position=Point(0, 0),
            meters_per_pixel=min(1.0, UNSCALED_IMAGE_WIDTH_METERS / width),
        )
        # Våningen kontrolleras igen inne i ändringen, ifall den hann tas bort.
        return await change(
            if_match,
            lambda home: _with_plan_image(home, require_floor(home, floor_id), plan),
            lambda home: home_store.save_plan_image(home, floor_id, image),
        )

    @router.delete("/floors/{floor_id}/plan-image")
    async def delete_plan_image(
        floor_id: str, if_match: Annotated[str | None, Header()] = None
    ) -> Response:
        return await change(
            if_match,
            lambda home: _with_plan_image(home, require_floor(home, floor_id), None),
            lambda home: home_store.delete_plan_image(home, floor_id),
        )

    return router


def _with_plan_image(home: Home, floor: Floor, plan: PlanImage | None) -> Home:
    floors = tuple(
        f.model_copy(update={"plan_image": plan}) if f.id == floor.id else f for f in home.floors
    )
    return home.model_copy(update={"floors": floors})


async def _read_body(request: Request, limit: int) -> bytes:
    """Läser hela anropet, men högst `limit` byte."""
    too_large = HTTPException(
        status.HTTP_413_CONTENT_TOO_LARGE,
        f"Filen är för stor. Den får vara högst {limit // (1024 * 1024)} MB.",
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
