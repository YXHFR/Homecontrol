"""Källkopplingen ESPHome: rumskorten (ESP32) med radar, Wi-Fi-mätning och ljussensor.

Korten heter ``homecontrol-<rum>`` (se firmware/homecontrol.yaml) och hittas
med mDNS. Ett kort som hittas visas som ett nytt kort att placera, men
kärnan ansluter inte till det förrän det har placerats på kartan. Ett
placerat kort är en nod vars id är kortets namn.

Anslutningen är krypterad. Varje kort får en egen nyckel som räknas fram ur
hemligheten i .env och kortets namn, så nycklarna sparas ingenstans. Ett
nyinstallerat kort har ingen nyckel; kärnan ger det sin nyckel första gången
det ansluts, efter att det har placerats.

Varje kort är tre delar, var och en en egen källa för kärnan: radarn,
Wi-Fi-mätningen och ljussensorn. Tystnar en del, till exempel radarn när
kabeln lossnar, skickas `SourceLost` för just den delen, så att rummet blir
okänt i stället för tomt. Ett placerat kort som inte svarar räknas som tyst i
alla delar, också innan det har svarat en enda gång.

Uppdatering av kortens firmware (OTA) sker bara när någon har godkänt den i
livevyn (`EsphomeSource.install_update`). Kortet hämtar då själv filen.

Modulen är uppdelad så att tolkningen går att pröva utan nätverk:

* `CardReader` tolkar det ett kort skickar (se tests/esphome_recordings).
* `EsphomeSource` håller reda på korten och lämnar observationerna till kärnan.
* `AioesphomeNetwork` pratar med korten (aioesphomeapi) och söker efter dem (mDNS).

Loggen nämner kort och delar men aldrig var någon är eller vad radarn ser.
"""

import asyncio
import base64
import contextlib
import hashlib
import hmac
import logging
import math
import re
import time
from collections.abc import Callable, Coroutine, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from aioesphomeapi.client import APIClient
from aioesphomeapi.core import (
    ZERO_NOISE_PSK,
    APIConnectionError,
    EncryptionPlaintextAPIError,
    InvalidEncryptionKeyAPIError,
)
from aioesphomeapi.model import (
    BinarySensorState,
    EntityInfo,
    EntityState,
    SensorState,
    TextSensorState,
    UpdateCommand,
    UpdateInfo,
    UpdateState,
)
from aioesphomeapi.reconnect_logic import ReconnectLogic
from zeroconf import ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from homecontrol.connectors import (
    LightLevelObserved,
    MotionObserved,
    Observation,
    ObservationSink,
    RadarTarget,
    RadarTargetsObserved,
    SourceLost,
)
from homecontrol.home import Capability, Home

logger = logging.getLogger(__name__)

SOURCE_ID = "esphome"
CARD_NAME = re.compile(r"^homecontrol-[a-z0-9][a-z0-9-]{0,18}$")
"""Kortens namn: homecontrol- och rummet, som i webbinstalleraren (se firmware/rooms.txt)."""
API_PORT = 6053
MDNS_SERVICE = "_esphomelib._tcp.local."
MIN_SECRET_LENGTH = 32
"""Hemligheten som korten nycklar räknas fram ur ska vara lång och slumpad."""

STALE_SECONDS = 30.0
"""Så länge Wi-Fi-mätningen eller ljussensorn får vara tyst innan den räknas som borta.

Wi-Fi-mätningen skickar sin rörelsepoäng flera gånger i sekunden och
ljussensorn var 5:e sekund. Radarn skickar bara när något ändras, så den
räknas som borta när kortet själv säger det (Radar Online, efter 3 sekunder).
"""
CHECK_INTERVAL = 5.0
"""Så ofta det kontrolleras om någon del har tystnat."""
MAX_DISCOVERED = 50
"""Fler okända kort än så här visas inte, så att ingen kan fylla listan via mDNS."""

# Entiteterna som firmware-paketet exponerar (firmware/homecontrol.yaml).
# Namnen ändras inte, så att kärnan kan lita på dem (se firmware/README.md).
RADAR_TARGETS = tuple((f"Radar Target {n} X", f"Radar Target {n} Y") for n in (1, 2, 3))
RADAR_ONLINE = "Radar Online"
CSI_MOTION = "CSI Motion"
CSI_MOVEMENT = "CSI Movement Score"
CSI_CALIBRATING = "CSI Calibrating"
ILLUMINANCE = "Illuminance"
WIFI_SIGNAL = "WiFi Signal"
FIRMWARE_VERSION = "Firmware Version"
FIRMWARE_UPDATE = "Firmware"
REQUIRED_ENTITIES = (
    *(name for pair in RADAR_TARGETS for name in pair),
    RADAR_ONLINE,
    CSI_MOTION,
    CSI_MOVEMENT,
    CSI_CALIBRATING,
    ILLUMINANCE,
    FIRMWARE_UPDATE,
)

PARTS = (Capability.RADAR, Capability.CSI, Capability.LUX)
PART_NAMES = {
    Capability.RADAR: "Radarn",
    Capability.CSI: "Wi-Fi-mätningen",
    Capability.LUX: "Ljussensorn",
}

NOT_ANSWERING = "Kortet svarar inte."
OTHER_KEY = (
    "Kortet har en annan nyckel än kärnan. Radera kortet och installera om det "
    "(se firmware/README.md)."
)
NO_ENCRYPTION = "Kortet tar inte emot krypterade anslutningar. Installera om firmware."


def card_key(secret: str, name: str) -> bytes:
    """Kortets nyckel (32 byte), framräknad ur hemligheten och kortets namn."""
    return hmac.new(
        secret.encode(), b"homecontrol esphome-api " + name.encode(), hashlib.sha256
    ).digest()


def part_source(name: str, part: Capability) -> str:
    """Källans id för en del av ett kort, till exempel ``esphome/homecontrol-kok/radar``."""
    return f"{SOURCE_ID}/{name}/{part.value}"


def is_card_name(name: str) -> bool:
    return CARD_NAME.fullmatch(name) is not None


# --- Tolkningen ---


@dataclass
class _Part:
    alive: bool = False
    heard_at: float = -math.inf
    changed: bool = False
    """Något nytt ska skickas till kärnan."""


@dataclass(frozen=True)
class FirmwareUpdate:
    current_version: str
    latest_version: str
    in_progress: bool
    progress: float | None
    """Hur långt uppdateringen har kommit, 0–100, om kortet säger det."""

    @property
    def available(self) -> bool:
        return bool(self.latest_version) and self.latest_version != self.current_version


class CardReader:
    """Tolkar det ett kort skickar till observationer. Den pratar inte med nätverket.

    `use_entities` ska anropas med kortets entiteter vid varje anslutning och
    `read` med varje värde. `observations` ger det som har ändrats sedan
    förra gången, så att alla värden från en radarram blir en enda
    observation. Tiden `now` är riktig tid i sekunder.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._names: dict[int, str] = {}
        self._parts = {part: _Part() for part in PARTS}
        self._values: dict[str, float] = {}
        self._csi_motion = False
        self._csi_calibrating = False
        self.wifi_signal: float | None = None
        self.firmware_version: str | None = None
        self.update: FirmwareUpdate | None = None
        self.update_key: int | None = None
        self.missing: tuple[str, ...] = ()
        """Entiteter som firmware-paketet ska ha men som kortet saknar."""

    def use_entities(self, entities: Iterable[EntityInfo]) -> None:
        """Kortet har anslutit. Värdena från förra anslutningen gäller inte längre."""
        entities = list(entities)
        self._names = {entity.key: entity.name for entity in entities}
        names = set(self._names.values())
        self.missing = tuple(name for name in REQUIRED_ENTITIES if name not in names)
        self.update_key = next(
            (e.key for e in entities if isinstance(e, UpdateInfo) and e.name == FIRMWARE_UPDATE),
            None,
        )
        self._values.clear()
        self.update = None
        self.disconnected()

    def disconnected(self) -> None:
        """Alla delar är tysta tills kortet hörs av igen."""
        for part in self._parts.values():
            if part.alive:
                part.changed = True
            part.alive = False
            part.heard_at = -math.inf
        self._csi_motion = self._csi_calibrating = False

    def alive(self, part: Capability) -> bool:
        return self._parts[part].alive

    def read(self, state: EntityState, now: float) -> None:
        name = self._names.get(state.key)
        if name is None:
            return
        match state:
            case SensorState():
                self._sensor(name, state, now)
            case BinarySensorState():
                self._binary(name, state, now)
            case TextSensorState() if name == FIRMWARE_VERSION and not state.missing_state:
                self.firmware_version = state.state[:32]
            case UpdateState() if name == FIRMWARE_UPDATE and not state.missing_state:
                self.update = FirmwareUpdate(
                    current_version=state.current_version[:32],
                    latest_version=state.latest_version[:32],
                    in_progress=state.in_progress,
                    progress=state.progress if state.has_progress else None,
                )

    def _sensor(self, name: str, state: SensorState, now: float) -> None:
        value = math.nan if state.missing_state else state.state
        if name == ILLUMINANCE:
            if math.isfinite(value) and value >= 0:
                self._values[name] = value
                self._hear(Capability.LUX, now, changed=True)
            else:
                # Ljussensorn svarar inte (kortet skickar NaN).
                self._silence(Capability.LUX)
        elif name == CSI_MOVEMENT:
            if math.isfinite(value):
                self._values[name] = value
                self._hear(Capability.CSI, now)
        elif name == WIFI_SIGNAL:
            self.wifi_signal = value if math.isfinite(value) else None
        elif any(name in pair for pair in RADAR_TARGETS):
            self._values[name] = value
            self._parts[Capability.RADAR].changed = True

    def _binary(self, name: str, state: BinarySensorState, now: float) -> None:
        on = state.state and not state.missing_state
        if name == RADAR_ONLINE:
            if on:
                self._hear(Capability.RADAR, now, changed=True)
            else:
                self._silence(Capability.RADAR)
        elif name == CSI_MOTION:
            self._csi_motion = on
            self._hear(Capability.CSI, now, changed=True)
        elif name == CSI_CALIBRATING:
            self._csi_calibrating = on
            self._hear(Capability.CSI, now, changed=True)

    def _hear(self, part: Capability, now: float, *, changed: bool = False) -> None:
        state = self._parts[part]
        if not state.alive or changed:
            state.changed = True
        state.alive = True
        state.heard_at = now

    def _silence(self, part: Capability) -> None:
        state = self._parts[part]
        if state.alive:
            state.changed = True
        state.alive = False

    def check(self, now: float) -> None:
        """Wi-Fi-mätningen och ljussensorn räknas som tysta om de inte har hörts på länge."""
        for part in (Capability.CSI, Capability.LUX):
            if now - self._parts[part].heard_at > STALE_SECONDS:
                self._silence(part)

    def observations(self, room_id: str | None) -> list[Observation]:
        """Det som har ändrats sedan förra anropet. `room_id` är rummet som kortet sitter i."""
        result: list[Observation] = []
        for part, state in self._parts.items():
            if not state.changed:
                continue
            state.changed = False
            source_id = part_source(self.name, part)
            if not state.alive:
                result.append(SourceLost(source_id, self.name, part))
            elif part is Capability.RADAR:
                result.append(RadarTargetsObserved(source_id, self.name, self._targets()))
            elif part is Capability.CSI:
                # Under kalibreringen vet mätningen inget; den räknas då som stilla.
                moving = self._csi_motion and not self._csi_calibrating
                result.append(MotionObserved(source_id, self.name, moving))
            elif room_id is not None and ILLUMINANCE in self._values:
                result.append(LightLevelObserved(source_id, room_id, self._values[ILLUMINANCE]))
        return result

    def _targets(self) -> tuple[RadarTarget, ...]:
        """Radarns mål i meter. En tom plats skickas som 0, 0 (eller utan värde)."""
        targets = []
        for x_name, y_name in RADAR_TARGETS:
            x, y = self._values.get(x_name, math.nan), self._values.get(y_name, math.nan)
            if math.isfinite(x) and math.isfinite(y) and (x, y) != (0, 0):
                targets.append(RadarTarget(x / 1000, y / 1000))
        return tuple(targets)

    def problems(self) -> list[str]:
        """Vad som är fel med ett anslutet kort, i klartext."""
        problems = [f"{PART_NAMES[part]} skickar inget." for part in PARTS if not self.alive(part)]
        if self.missing:
            problems.append(f"Kortet saknar {', '.join(self.missing)}. Uppdatera kortets firmware.")
        return problems

    @property
    def calibrating(self) -> bool:
        return self._csi_calibrating


# --- Korten ---


class CardEvents(Protocol):
    """Det en länk till ett kort rapporterar. Anropas från händelseloopen."""

    def connected(self, entities: Sequence[EntityInfo]) -> None: ...

    def state(self, state: EntityState) -> None: ...

    def disconnected(self) -> None: ...

    def problem(self, text: str | None) -> None:
        """Varför kortet inte går att ansluta, eller None när det är löst."""


class CardLink(Protocol):
    """En anslutning till ett kort, som försöker igen tills den stoppas."""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def install_update(self, key: int) -> None: ...


@dataclass(frozen=True)
class Discovered:
    name: str
    friendly_name: str


class Network(Protocol):
    """Det som pratar med korten. Anropas från händelseloopen."""

    def link(self, name: str, key: bytes, events: CardEvents) -> CardLink: ...

    async def discover(
        self, found: Callable[[Discovered], None], gone: Callable[[str], None]
    ) -> None:
        """Söker efter kort tills den avbryts."""
        ...

    async def close(self) -> None: ...


@dataclass(frozen=True)
class CardView:
    """Det som går att visa om ett kort."""

    name: str
    friendly_name: str
    placed: bool
    online: bool
    problems: tuple[str, ...]
    """Varningar i klartext. Tom när allt fungerar."""
    calibrating: bool
    wifi_signal: float | None
    firmware_version: str | None
    update: FirmwareUpdate | None


Listener = Callable[[], None]


@dataclass(eq=False)
class _Card:
    source: "EsphomeSource"
    name: str
    reader: CardReader
    link: CardLink | None = None
    online: bool = False
    connect_problem: str | None = None
    """Varför kortet inte går att ansluta, om det är känt."""
    room_id: str | None = None
    flush_pending: bool = False

    # CardEvents

    def connected(self, entities: Sequence[EntityInfo]) -> None:
        if self.link is None:
            return  # kortet har hunnit tas bort från kartan
        logger.info("Kortet %s är anslutet", self.name)
        self.online = True
        self.connect_problem = None
        self.reader.use_entities(entities)
        if self.reader.missing:
            logger.warning("Kortet %s saknar %s", self.name, ", ".join(self.reader.missing))
        self.source._changed(self)

    def state(self, state: EntityState) -> None:
        if self.link is None:
            return
        self.reader.read(state, self.source._now())
        self.source._changed(self)

    def disconnected(self) -> None:
        if self.online:
            logger.warning("Kortet %s tappade anslutningen", self.name)
        self.online = False
        self.reader.disconnected()
        self.source._changed(self)

    def problem(self, text: str | None) -> None:
        if text != self.connect_problem and text is not None:
            logger.warning("Kortet %s: %s", self.name, text)
        self.connect_problem = text
        self.source._notify()


class EsphomeSource:
    """Källkopplingen för rumskorten. Den ansluter bara till kort som är placerade.

    `defer` schemalägger ett anrop strax efter det som pågår, så att alla
    värden från en radarram hinner komma fram innan observationen skickas.
    `clock` är riktig tid; kortens delar tystnar i riktig tid, även när
    simuleringens tid går fortare.
    """

    def __init__(
        self,
        secret: str,
        home: Home,
        network: Network,
        *,
        defer: Callable[[Callable[[], None]], object] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if len(secret) < MIN_SECRET_LENGTH:
            raise ValueError(f"Hemligheten för korten ska vara minst {MIN_SECRET_LENGTH} tecken")
        self._secret = secret
        self._home = home
        self._network = network
        self._defer = defer
        self._clock = clock
        self._sink: ObservationSink | None = None
        self._cards: dict[str, _Card] = {}
        self._discovered: dict[str, Discovered] = {}
        self._listeners: list[Listener] = []

    @property
    def source_id(self) -> str:
        return SOURCE_ID

    def placed_cards(self) -> frozenset[str]:
        """Korten som är placerade på kartan (deras nod-id)."""
        return frozenset(
            node.id for room in self._home.rooms for node in room.nodes if is_card_name(node.id)
        )

    # Source

    def start(self, sink: ObservationSink) -> None:
        self._sink = sink
        self.use_home(self._home)

    def stop(self) -> None:
        for name in list(self._cards):
            self._remove(name)
        self._sink = None

    async def run(self, sink: ObservationSink) -> None:
        """Startar källan, söker efter nya kort och ser efter tystnade delar tills den avbryts."""
        self.start(sink)
        discovery = asyncio.get_running_loop().create_task(self._discover())
        try:
            while True:
                await asyncio.sleep(CHECK_INTERVAL)
                self.check()
        finally:
            discovery.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await discovery
            self.stop()
            await self._network.close()

    async def _discover(self) -> None:
        try:
            await self._network.discover(self.found, self.gone)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Placerade kort hittas ändå med sina namn; bara nya kort syns inte.
            logger.exception("Kunde inte söka efter nya kort med mDNS")

    def check(self) -> None:
        """Ser efter om någon del av ett anslutet kort har tystnat."""
        now = self._now()
        for card in self._cards.values():
            if card.online:
                card.reader.check(now)
                self._changed(card)

    def use_home(self, home: Home) -> None:
        """Ansluter till kort som har placerats och kopplar från kort som har tagits bort."""
        self._home = home
        if self._sink is None:
            return
        rooms = {node.id: room.id for room in home.rooms for node in room.nodes}
        placed = self.placed_cards()
        for name in set(self._cards) - placed:
            self._remove(name)
        for name in sorted(placed):
            card = self._cards.get(name)
            if card is None:
                card = self._add(name)
            if card.room_id != rooms[name]:
                # Ljusnivån gällde det gamla rummet.
                self._emit(SourceLost(part_source(name, Capability.LUX)))
                card.room_id = rooms[name]
            self._changed(card)
        self._notify()

    def _add(self, name: str) -> _Card:
        logger.info("Kortet %s är placerat och ansluts", name)
        card = _Card(self, name, CardReader(name))
        self._cards[name] = card
        # Tills kortet svarar är alla dess delar tysta, så att rummet blir okänt.
        for part in PARTS:
            self._emit(SourceLost(part_source(name, part), name, part))
        card.link = self._network.link(name, card_key(self._secret, name), card)
        card.link.start()
        return card

    def _remove(self, name: str) -> None:
        card = self._cards.pop(name)
        if card.link is not None:
            card.link.stop()
            card.link = None
        for part in PARTS:
            self._emit(SourceLost(part_source(name, part)))
        logger.info("Kortet %s är inte längre placerat och kopplas från", name)

    # Upptäckt

    def found(self, card: Discovered) -> None:
        if not is_card_name(card.name):
            return
        if card.name not in self._discovered and len(self._discovered) >= MAX_DISCOVERED:
            return
        if card.name not in self._discovered:
            logger.info("Hittade kortet %s", card.name)
        self._discovered[card.name] = Discovered(card.name, card.friendly_name[:100])
        self._notify()

    def gone(self, name: str) -> None:
        if self._discovered.pop(name, None) is not None:
            self._notify()

    # Livevyn

    def cards(self) -> tuple[CardView, ...]:
        """Placerade kort och kort som har hittats men inte placerats, i namnordning."""
        placed = self.placed_cards()
        views = []
        for name in sorted(placed | self._discovered.keys()):
            card = self._cards.get(name)
            found = self._discovered.get(name)
            friendly = found.friendly_name if found is not None else name
            if card is None:
                views.append(
                    CardView(name, friendly, name in placed, False, (), False, None, None, None)
                )
                continue
            problems = (
                card.reader.problems() if card.online else [card.connect_problem or NOT_ANSWERING]
            )
            views.append(
                CardView(
                    name=name,
                    friendly_name=friendly,
                    placed=True,
                    online=card.online,
                    problems=tuple(problems),
                    calibrating=card.online and card.reader.calibrating,
                    wifi_signal=card.reader.wifi_signal if card.online else None,
                    firmware_version=card.reader.firmware_version,
                    update=card.reader.update if card.online else None,
                )
            )
        return tuple(views)

    def install_update(self, name: str) -> bool:
        """Startar en uppdatering som användaren har godkänt. Returnerar om den startade."""
        card = self._cards.get(name)
        if card is None or card.link is None or not card.online:
            return False
        update = card.reader.update
        key = card.reader.update_key
        if update is None or key is None or not update.available or update.in_progress:
            return False
        logger.info(
            "Uppdaterar kortet %s från %s till %s",
            name,
            update.current_version,
            update.latest_version,
        )
        card.link.install_update(key)
        return True

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Anropar `listener` när något om korten ändras. Returnerar en funktion som avslutar."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    # Internt

    def _now(self) -> float:
        return self._clock()

    def _changed(self, card: _Card) -> None:
        """Skickar det som har ändrats, strax efter, så att en hel radarram hinner fram."""
        if card.flush_pending:
            return
        if self._defer is None:
            self._flush(card)
        else:
            card.flush_pending = True
            self._defer(lambda: self._flush(card))

    def _flush(self, card: _Card) -> None:
        card.flush_pending = False
        if self._cards.get(card.name) is not card:
            return
        for observation in card.reader.observations(card.room_id):
            self._emit(observation)
        self._notify()

    def _emit(self, observation: Observation) -> None:
        if self._sink is not None:
            self._sink(observation)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()


def defer_in_loop(callback: Callable[[], None]) -> object:
    return asyncio.get_running_loop().call_soon(callback)


# --- Nätverket ---


class AioesphomeNetwork:
    """Ansluter till korten med aioesphomeapi och söker efter dem med mDNS."""

    def __init__(self) -> None:
        self._zeroconf: AsyncZeroconf | None = None

    def _zc(self) -> AsyncZeroconf:
        if self._zeroconf is None:
            self._zeroconf = AsyncZeroconf()
        return self._zeroconf

    def link(self, name: str, key: bytes, events: CardEvents) -> CardLink:
        return _AioesphomeLink(self._zc(), name, key, events)

    async def discover(
        self, found: Callable[[Discovered], None], gone: Callable[[str], None]
    ) -> None:
        zeroconf = self._zc()
        tasks: set[asyncio.Task[None]] = set()

        async def look_up(service_type: str, service: str, name: str) -> None:
            info = AsyncServiceInfo(service_type, service)
            friendly = name
            if await info.async_request(zeroconf.zeroconf, 3000):
                friendly = info.decoded_properties.get("friendly_name") or name
            found(Discovered(name, friendly))

        def on_change(
            zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange
        ) -> None:
            card = name.removesuffix("." + service_type)
            if not is_card_name(card):
                return
            if state_change is ServiceStateChange.Removed:
                gone(card)
            else:
                task = asyncio.get_running_loop().create_task(look_up(service_type, name, card))
                tasks.add(task)
                task.add_done_callback(tasks.discard)

        browser = AsyncServiceBrowser(zeroconf.zeroconf, MDNS_SERVICE, handlers=[on_change])
        try:
            await asyncio.Event().wait()
        finally:
            await browser.async_cancel()
            for task in tasks:
                task.cancel()

    async def close(self) -> None:
        if self._zeroconf is not None:
            await self._zeroconf.async_close()
            self._zeroconf = None


class _AioesphomeLink:
    def __init__(self, zeroconf: AsyncZeroconf, name: str, key: bytes, events: CardEvents) -> None:
        self._zeroconf = zeroconf
        self._name = name
        self._key = key
        self._events = events
        self._client = self._new_client(base64.b64encode(key).decode())
        self._logic = ReconnectLogic(
            client=self._client,
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
            zeroconf_instance=zeroconf,
            name=name,
            on_connect_error=self._on_connect_error,
        )
        self._tasks: set[asyncio.Task[None]] = set()

    def _new_client(self, noise_psk: str) -> APIClient:
        return APIClient(
            f"{self._name}.local",
            API_PORT,
            None,
            client_info="homecontrol",
            noise_psk=noise_psk,
            expected_name=self._name,
            zeroconf_instance=self._zeroconf,
        )

    def start(self) -> None:
        self._spawn(self._logic.start())

    def stop(self) -> None:
        self._spawn(self._shutdown())

    def install_update(self, key: int) -> None:
        self._client.update_command(key, UpdateCommand.INSTALL)

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _shutdown(self) -> None:
        await self._logic.stop()
        await self._client.disconnect()

    async def _on_connect(self) -> None:
        try:
            entities, _ = await self._client.list_entities_services()
        except APIConnectionError:
            logger.warning("Kunde inte läsa entiteterna från kortet %s", self._name)
            await self._client.disconnect()
            return
        self._events.connected(entities)
        self._client.subscribe_states(self._events.state)

    async def _on_disconnect(self, expected_disconnect: bool) -> None:
        self._events.disconnected()

    async def _on_connect_error(self, error: Exception) -> None:
        if isinstance(error, EncryptionPlaintextAPIError):
            self._events.problem(NO_ENCRYPTION)
        elif isinstance(error, InvalidEncryptionKeyAPIError):
            # Ett nyinstallerat kort har ingen nyckel än och får kärnans.
            if not await self._provision():
                self._events.problem(OTHER_KEY)
        else:
            self._events.problem(None)

    async def _provision(self) -> bool:
        """Ger ett kort utan nyckel sin nyckel. Returnerar om det lyckades."""
        client = self._new_client(ZERO_NOISE_PSK)
        try:
            await client.connect(login=False)
            info = await client.device_info()
            if not info.api_encryption_provisionable:
                return False
            ok = await client.noise_encryption_set_key(self._key)
        except APIConnectionError:
            return False
        finally:
            await client.disconnect()
        if ok:
            logger.info("Kortet %s har fått sin nyckel", self._name)
        return ok
