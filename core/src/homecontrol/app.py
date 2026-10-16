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
from homecontrol.home import Home
from homecontrol.homefile import example_home
from homecontrol.lampdevices import create_lamp_devices_router
from homecontrol.live import create_live_router, run_timer
from homecontrol.simulation import SimulatedLampOutput, SimulatedSource, Simulation
from homecontrol.zigbee import Connect, ZigbeeOutput, zigbee_lamps

logger = logging.getLogger(__name__)


def create_app(
    web_dir: Path | None = None,
    clock: SystemClock | None = None,
    allowed_hosts: Set[str] = frozenset(),
    home: Home | None = None,
    home_store: HomeStore | None = None,
    mqtt: Connect | None = None,
) -> FastAPI:
    """`home` är hemmet från databasen. Utan det används exempelhemmet.

    `home_store` är där kartredigerarens ändringar sparas. Utan den sparas de
    bara så länge kärnan är igång.

    Med `mqtt` styrs de Zigbee-lampor som är placerade på kartan genom
    Zigbee2MQTT (se zigbee.py). Övriga lampor är virtuella.
    """
    clock = clock if clock is not None else SystemClock()
    home = home if home is not None else example_home()
    zigbee = None if mqtt is None else ZigbeeOutput(home)
    real_lamps = zigbee_lamps if zigbee is not None else _no_real_lamps
    lamps = SimulatedLampOutput(real_lamps)
    controller = Controller(home, clock, [lamps] if zigbee is None else [lamps, zigbee])
    lamps.report_new_lamps(home, controller.observe)
    # Simuleringsläget börjar påslaget bara när inga riktiga lampor kan styras,
    # så att det finns något att se. Tidsfarten gäller hela kärnan och spärras
    # därför till 1× så länge hemmet har riktiga lampor (se Simulation).
    source = SimulatedSource(home)
    simulation = Simulation(
        source,
        controller.observe,
        clock,
        floor_ids=(floor.id for floor in home.floors),
        enabled=zigbee is None,
        real_hardware=lambda changed: bool(real_lamps(changed)),
    )

    def use_home(home: Home) -> None:
        simulation.use_home(home)
        lamps.report_new_lamps(home, controller.observe)
        if zigbee is not None:
            zigbee.use_home(home)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        tasks = [asyncio.create_task(run_timer(controller, clock))]
        if zigbee is not None and mqtt is not None:
            tasks.append(asyncio.create_task(zigbee.run(controller.observe, mqtt)))
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
    app.state.zigbee = zigbee

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    def virtual_lamps(home: Home) -> frozenset[str]:
        # Frågan ställs om varje nytt hem, så att en lampa som läggs till i
        # kartredigeraren visas rätt direkt.
        return frozenset(lamp.id for lamp in home.lamps) - real_lamps(home)

    app.include_router(create_live_router(controller, simulation, allowed_hosts, virtual_lamps))
    app.include_router(create_lamp_devices_router(zigbee, allowed_hosts))
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


def _no_real_lamps(_: Home) -> frozenset[str]:
    return frozenset()
