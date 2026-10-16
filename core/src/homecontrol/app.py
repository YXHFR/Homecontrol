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
from homecontrol.clock import SystemClock
from homecontrol.controller import Controller
from homecontrol.home import Home
from homecontrol.homefile import example_home
from homecontrol.live import create_live_router, run_timer
from homecontrol.simulation import SimulatedLampOutput, SimulatedSource, Simulation

logger = logging.getLogger(__name__)


def create_app(
    web_dir: Path | None = None,
    clock: SystemClock | None = None,
    allowed_hosts: Set[str] = frozenset(),
    home: Home | None = None,
) -> FastAPI:
    """`home` är hemmet från databasen. Utan det används exempelhemmet."""
    clock = clock if clock is not None else SystemClock()
    home = home if home is not None else example_home()
    # Än så länge finns bara Simulering: låtsaspersoner och virtuella lampor.
    # Riktiga kopplingar ansluts på samma sätt. Eftersom inga riktiga källor
    # eller lampor finns börjar simuleringsläget påslaget, så att det finns
    # något att se. När riktiga kopplingar läggs till ska läget börja avslaget,
    # och tidsfarten spärras så länge riktiga lampor styrs (se Simulation).
    lamps = SimulatedLampOutput()
    # Alla lampor är virtuella tills en riktig lamputgång styr dem.
    virtual_lamps = frozenset(lamp.id for lamp in home.lamps)
    controller = Controller(home, clock, [lamps])
    source = SimulatedSource(room_ids=(room.id for room in home.rooms))
    simulation = Simulation(
        source, controller.observe, clock, floor_ids=(floor.id for floor in home.floors)
    )

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
    app.state.simulation = simulation

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(create_live_router(controller, simulation, allowed_hosts, virtual_lamps))

    # Webbgränssnittet monteras sist så att API-vägarna ovan har företräde.
    if web_dir is not None:
        if web_dir.is_dir():
            app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
        else:
            logger.warning("Hittar inte webbgränssnittet i %s, serverar bara API:t", web_dir)

    return app
