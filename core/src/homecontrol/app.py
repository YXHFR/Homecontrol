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
from homecontrol.calibration_api import calibration_saver, create_calibration_router
from homecontrol.cards import create_cards_router
from homecontrol.clock import SystemClock
from homecontrol.connectors import defer_in_loop
from homecontrol.controller import Controller
from homecontrol.editor import HomeChanges, HomeStore, MemoryStore, create_editor_router
from homecontrol.esphome import (
    AioesphomeNetwork,
    EsphomeSource,
    Network,
    placed_cards,
)
from homecontrol.history import History
from homecontrol.home import Home
from homecontrol.homefile import example_home
from homecontrol.lampdevices import create_lamp_devices_router
from homecontrol.live import create_live_router, run_timer
from homecontrol.simulation import (
    SimulatedLampOutput,
    SimulatedSource,
    Simulation,
    run_light_sensors,
)
from homecontrol.zigbee import Connect, ZigbeeOutput, zigbee_lamps

logger = logging.getLogger(__name__)


def create_app(
    web_dir: Path | None = None,
    clock: SystemClock | None = None,
    allowed_hosts: Set[str] = frozenset(),
    home: Home | None = None,
    home_store: HomeStore | None = None,
    history: History | None = None,
    esphome_secret: str | None = None,
    card_network: Network | None = None,
    mqtt: Connect | None = None,
) -> FastAPI:
    """`home` är hemmet från databasen. Utan det används exempelhemmet.

    `home_store` är där kartredigerarens ändringar sparas. Utan den sparas de
    bara så länge kärnan är igång.

    `history` sparar spåren, rummens läge och förklaringarna, utom medan
    simuleringsläget är på. Utan den sparas ingen historik.

    Med `esphome_secret` ansluts rumskorten som är placerade på kartan (se
    esphome.py), via `card_network` eller annars det riktiga nätverket.

    Med `mqtt` styrs de Zigbee-lampor som är placerade på kartan genom
    Zigbee2MQTT (se zigbee.py). Övriga lampor är virtuella.
    """
    clock = clock if clock is not None else SystemClock()
    home = home if home is not None else example_home()
    zigbee = None if mqtt is None else ZigbeeOutput(home, defer=defer_in_loop)
    real_lamps = zigbee_lamps if zigbee is not None else _nothing_real
    lamps = SimulatedLampOutput(real_lamps)
    controller = Controller(home, clock, [lamps] if zigbee is None else [lamps, zigbee])
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
    real_nodes = placed_cards if cards is not None else _nothing_real
    # Simuleringsläget börjar påslaget bara när hemmet inte har några riktiga
    # kort eller lampor, så att det finns något att se. Tidsfarten gäller hela
    # kärnan och spärras därför till 1× så länge hemmet har riktiga kort eller
    # lampor (se Simulation).
    source = SimulatedSource(home, real_nodes=real_nodes)

    def real_hardware(changed: Home) -> bool:
        return bool(real_nodes(changed) or real_lamps(changed))

    simulation = Simulation(
        source,
        controller.observe,
        clock,
        floor_ids=(floor.id for floor in home.floors),
        enabled=not real_hardware(home),
        real_hardware=real_hardware,
        lamps=lamps,
    )
    if history is not None:
        # Låtsaspersonerna ska inte hamna i historiken bland de riktiga spåren.
        history.pause_while(lambda: simulation.enabled)
        history.record(controller.snapshot())
        controller.subscribe(lambda: history.record(controller.snapshot()))

    def use_home(changed: Home) -> None:
        simulation.use_home(changed)
        lamps.report_new_lamps(changed, controller.observe)
        if cards is not None:
            cards.use_home(changed)
        if zigbee is not None:
            zigbee.use_home(changed)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if history is not None:
            history.start()
        tasks = [
            asyncio.create_task(run_timer(controller, clock)),
            asyncio.create_task(run_light_sensors(simulation, clock)),
        ]
        if cards is not None:
            tasks.append(asyncio.create_task(cards.run(controller.observe)))
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
    app.state.cards = cards
    app.state.zigbee = zigbee

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    def virtual_lamps(home: Home) -> frozenset[str]:
        # Frågan ställs om varje nytt hem, så att en lampa som läggs till i
        # kartredigeraren visas rätt direkt.
        return frozenset(lamp.id for lamp in home.lamps) - real_lamps(home)

    app.include_router(
        create_live_router(controller, simulation, allowed_hosts, virtual_lamps, cards)
    )
    app.include_router(create_cards_router(cards, allowed_hosts))
    app.include_router(create_lamp_devices_router(zigbee, allowed_hosts))
    changes = HomeChanges(
        controller,
        home_store if home_store is not None else MemoryStore(),
        on_home_replaced=use_home,
    )
    controller.on_calibrated(calibration_saver(changes))
    app.include_router(create_editor_router(changes, allowed_hosts))
    app.include_router(create_calibration_router(controller, allowed_hosts))

    # Webbgränssnittet monteras sist så att API-vägarna ovan har företräde.
    if web_dir is not None:
        if web_dir.is_dir():
            app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
        else:
            logger.warning("Hittar inte webbgränssnittet i %s, serverar bara API:t", web_dir)

    return app


def _nothing_real(_: Home) -> frozenset[str]:
    return frozenset()
