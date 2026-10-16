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
from homecontrol.cards import create_cards_router
from homecontrol.clock import SystemClock
from homecontrol.controller import Controller
from homecontrol.editor import HomeStore, MemoryStore, create_editor_router
from homecontrol.esphome import (
    AioesphomeNetwork,
    EsphomeSource,
    Network,
    defer_in_loop,
    placed_cards,
)
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
    esphome_secret: str | None = None,
    card_network: Network | None = None,
) -> FastAPI:
    """`home` är hemmet från databasen. Utan det används exempelhemmet.

    `home_store` är där kartredigerarens ändringar sparas. Utan den sparas de
    bara så länge kärnan är igång.

    Med `esphome_secret` ansluts rumskorten som är placerade på kartan (se
    esphome.py), via `card_network` eller annars det riktiga nätverket.
    """
    clock = clock if clock is not None else SystemClock()
    home = home if home is not None else example_home()
    # Lamporna är än så länge virtuella. Riktiga lamputgångar läggs till här.
    lamps = SimulatedLampOutput()
    controller = Controller(home, clock, [lamps])
    lamps.report_new_lamps(home, controller.observe)
    cards = (
        None
        if esphome_secret is None
        else EsphomeSource(
            esphome_secret,
            home,
            card_network if card_network is not None else AioesphomeNetwork(),
            defer=defer_in_loop,
        )
    )
    # Utan kopplingen finns inga riktiga kort, och alla noder simuleras.
    real_nodes = placed_cards if cards is not None else _no_real_nodes
    # Simuleringsläget börjar påslaget bara när inga riktiga kort kan anslutas,
    # så att det finns något att se. Tidsfarten gäller hela kärnan och spärras
    # därför till 1× så länge hemmet har riktiga kort (se Simulation).
    # Riktiga lampor ska spärra den på samma sätt när de kopplas in.
    source = SimulatedSource(home, real_nodes=real_nodes)
    simulation = Simulation(
        source,
        controller.observe,
        clock,
        floor_ids=(floor.id for floor in home.floors),
        enabled=cards is None,
        real_hardware=lambda changed: bool(real_nodes(changed)),
    )

    def use_home(changed: Home) -> None:
        simulation.use_home(changed)
        lamps.report_new_lamps(changed, controller.observe)
        if cards is not None:
            cards.use_home(changed)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        tasks = [asyncio.create_task(run_timer(controller, clock))]
        if cards is not None:
            tasks.append(asyncio.create_task(cards.run(controller.observe)))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(
        title="Homecontrol",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.controller = controller
    app.state.simulation = simulation
    app.state.cards = cards

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(
        create_live_router(controller, simulation, allowed_hosts, _virtual_lamps, cards)
    )
    app.include_router(create_cards_router(cards, allowed_hosts))
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


def _no_real_nodes(_: Home) -> frozenset[str]:
    return frozenset()


def _virtual_lamps(home: Home) -> frozenset[str]:
    """Alla lampor är virtuella tills en riktig lamputgång styr dem.

    Frågan ställs om varje nytt hem, så att en lampa som läggs till i
    kartredigeraren blir virtuell direkt.
    """
    return frozenset(lamp.id for lamp in home.lamps)
