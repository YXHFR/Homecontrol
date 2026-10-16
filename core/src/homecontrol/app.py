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
from homecontrol.editor import HomeStore, MemoryStore, create_editor_router
from homecontrol.history import History
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
    home_store: HomeStore | None = None,
    history: History | None = None,
) -> FastAPI:
    """`home` är hemmet från databasen. Utan det används exempelhemmet.

    `home_store` är där kartredigerarens ändringar sparas. Utan den sparas de
    bara så länge kärnan är igång.

    `history` sparar spåren, rummens läge och förklaringarna, utom medan
    simuleringsläget är på. Utan den sparas ingen historik.
    """
    clock = clock if clock is not None else SystemClock()
    home = home if home is not None else example_home()
    # Än så länge finns bara Simulering: låtsaspersoner och virtuella lampor.
    # Riktiga kopplingar ansluts på samma sätt. Eftersom inga riktiga källor
    # eller lampor finns börjar simuleringsläget påslaget, så att det finns
    # något att se. När riktiga kopplingar läggs till ska läget börja avslaget,
    # och tidsfarten spärras så länge riktiga lampor styrs (se Simulation).
    lamps = SimulatedLampOutput()
    controller = Controller(home, clock, [lamps])
    lamps.report_new_lamps(home, controller.observe)
    source = SimulatedSource(home)
    simulation = Simulation(
        source, controller.observe, clock, floor_ids=(floor.id for floor in home.floors)
    )
    if history is not None:
        # Låtsaspersonerna ska inte hamna i historiken bland de riktiga spåren.
        history.pause_while(lambda: simulation.enabled)
        history.record(controller.snapshot())
        controller.subscribe(lambda: history.record(controller.snapshot()))

    def use_home(home: Home) -> None:
        simulation.use_home(home)
        lamps.report_new_lamps(home, controller.observe)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if history is not None:
            history.start()
        timer = asyncio.create_task(run_timer(controller, clock))
        try:
            yield
        finally:
            timer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await timer
            if history is not None:
                await asyncio.to_thread(history.close)

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

    app.include_router(create_live_router(controller, simulation, allowed_hosts, _virtual_lamps))
    app.include_router(
        create_editor_router(
            controller,
            home_store if home_store is not None else MemoryStore(),
            allowed_hosts,
            on_home_replaced=use_home,
        )
    )

    # Webbgränssnittet monteras sist så att API-vägarna ovan har företräde.
    if web_dir is not None:
        if web_dir.is_dir():
            app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
        else:
            logger.warning("Hittar inte webbgränssnittet i %s, serverar bara API:t", web_dir)

    return app


def _virtual_lamps(home: Home) -> frozenset[str]:
    """Alla lampor är virtuella tills en riktig lamputgång styr dem.

    Frågan ställs om varje nytt hem, så att en lampa som läggs till i
    kartredigeraren blir virtuell direkt.
    """
    return frozenset(lamp.id for lamp in home.lamps)
