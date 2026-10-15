"""HTTP-gränssnittet: hälsokontroll, realtidsström och det byggda webbgränssnittet.

Nya API-vägar läggs under /api så att de aldrig krockar med webbgränssnittets filer.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Set
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from homecontrol import __version__
from homecontrol.clock import Clock, SystemClock
from homecontrol.controller import Controller
from homecontrol.home import demo_home
from homecontrol.live import create_live_router, run_timer
from homecontrol.simulation import SimulatedLampOutput, SimulatedSource

logger = logging.getLogger(__name__)


def create_app(
    web_dir: Path | None = None,
    clock: Clock | None = None,
    allowed_hosts: Set[str] = frozenset(),
) -> FastAPI:
    clock = clock if clock is not None else SystemClock()
    # Än så länge finns bara Simulering: ett hårdkodat rum, en låtsasperson
    # och en virtuell lampa. Riktiga kopplingar ansluts på samma sätt.
    source = SimulatedSource()
    lamps = SimulatedLampOutput()
    controller = Controller(demo_home(), clock, [lamps])
    source.start(controller.observe)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        timer = asyncio.create_task(run_timer(controller, clock))
        try:
            yield
        finally:
            timer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await timer

    app = FastAPI(
        title="Homecontrol",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.controller = controller
    app.state.source = source

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(create_live_router(controller, source, allowed_hosts))

    # Webbgränssnittet monteras sist så att API-vägarna ovan har företräde.
    if web_dir is not None:
        if web_dir.is_dir():
            app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
        else:
            logger.warning("Hittar inte webbgränssnittet i %s, serverar bara API:t", web_dir)

    return app
