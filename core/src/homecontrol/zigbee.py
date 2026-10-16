"""Lamputgången Zigbee2MQTT: styr Zigbee-lampor genom Zigbee2MQTT över MQTT.

Kärnan pratar aldrig Zigbee själv. Zigbee2MQTT gör det och berättar om
lamporna på MQTT-brokern (Mosquitto), där den här utgången lyssnar och
skickar kommandon. Utgången följer kärnans gränssnitt för lamputgångar
(se connectors.py): den tar emot `LampCommand` och berättar hur lamporna är
med `LampStateReported`.

* **Upptäckt och godkännande.** Zigbee2MQTT listar alla enheter i
  ``bridge/devices``. Lamporna där visas i webbgränssnittet som nya lampor att
  placera (`ZigbeeOutput.lamps`). En lampa används först när den har
  placerats på kartan, som en lampa med id:t ``zigbee-<IEEE-adress>`` (se
  `lamp_id`). Andra lampor i hemmet är virtuella och rörs inte.
* **Läget läses först.** Kärnan antar aldrig att en lampa är släckt. När
  utgången ansluter, och när en lampa svarar igen, frågar den lampan om dess
  läge (``<lampa>/get``). Tills svaret har kommit skickas inga kommandon till
  lampan; när läget har lästs räknar kärnan om och skickar det som behövs.
* **Rumsgrupper.** Rum med minst två Zigbee-lampor får en Zigbee-grupp som
  heter ``homecontrol-<rummets id>``, och utgången håller gruppens
  medlemmar i takt med kartan. Ska alla lamporna i rummet ändras på samma
  sätt går det ut som ett enda gruppkommando, så att de tänds och släcks
  samtidigt med samma övergång.
* **Ändringar utifrån.** Ett besked från en lampa strax efter ett kommando
  från kärnan är lampans svar på kommandot. Ändras lampan utan att kärnan
  har bett om det, med fjärrkontroll eller app, skickas beskedet med
  ``external=True``, och kärnan räknar det som manuell styrning.
* **Tillgänglighet.** En lampa som inte svarar, som Zigbee2MQTT säger är
  borta (``<lampa>/availability``), eller som inte längre finns i Zigbee-
  nätet rapporteras med läget okänt (``on=None``). Samma sak gäller alla
  lampor när MQTT eller Zigbee2MQTT är borta.
* **Lampprofiler.** Hur kommandot ser ut avgörs av lampans profil (se
  `Profile`), till exempel IKEA TRÅDFRI:s egenheter.

Inga gränser för hur ofta lamporna får styras finns än; alla kommandon går
ut genom `ZigbeeOutput._set`, där en sådan gräns i så fall läggs in.

Utgången arbetar i kärnans händelseloop. Meddelanden från brokern lämnas till
`ZigbeeOutput.handle` och meddelanden ut läggs i en kö som skickas i
bakgrunden (`ZigbeeOutput.run`), så `send` blockerar aldrig.
"""

import asyncio
import contextlib
import json
import logging
import math
import re
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol

import aiomqtt

from homecontrol.config import MqttSettings
from homecontrol.connectors import LampCommand, LampStateReported, ObservationSink
from homecontrol.home import Home, LampProfile
from homecontrol.lampdevices import FoundLamp

logger = logging.getLogger(__name__)

BASE_TOPIC = "zigbee2mqtt"
"""Zigbee2MQTT:s förvalda bastopic. Alla dess meddelanden ligger under den."""
LAMP_ID_PREFIX = "zigbee-"
GROUP_PREFIX = "homecontrol-"
"""Grupperna som kärnan sköter heter så här följt av rummets id. Andra grupper rörs inte."""

TRANSITION_SECONDS = 1.0
"""Övergången när lamporna tänds, dimras och släcks."""
ECHO_SECONDS = 5.0
"""Så länge efter övergången som lampans besked räknas som svar på kärnans kommando."""
READ_TIMEOUT_SECONDS = 10.0
"""Har lampan inte svarat på frågan om sitt läge efter så här lång tid räknas den som borta."""
READ_RETRY_SECONDS = 60.0
"""Så ofta en lampa som inte har svarat frågas igen."""
BRIGHTNESS_TOLERANCE = 2
"""Så många procent som ljusstyrkan kan skilja av avrundning utan att räknas som en ändring."""
RECONNECT_SECONDS = 5.0
MAX_OUTBOX = 1000
"""Så många meddelanden kan vänta på att skickas. Fler tappas hellre än att kärnan väntar."""

ZIGBEE_BRIGHTNESS = 254
"""Full ljusstyrka i Zigbee. Kärnan räknar i procent."""

_IEEE = re.compile(r"^0x[0-9a-f]{16}$")


def lamp_id(ieee_address: str) -> str:
    """Id:t som en Zigbee-lampa får i hemmet: ``zigbee-`` och IEEE-adressen utan 0x."""
    return LAMP_ID_PREFIX + ieee_address.lower().removeprefix("0x")


def ieee_address(lamp_id: str) -> str | None:
    """IEEE-adressen för en placerad Zigbee-lampa, eller None om lampan inte är en Zigbee-lampa."""
    if not lamp_id.startswith(LAMP_ID_PREFIX):
        return None
    address = "0x" + lamp_id.removeprefix(LAMP_ID_PREFIX)
    return address if _IEEE.match(address) else None


def zigbee_lamps(home: Home) -> frozenset[str]:
    """Lamporna i hemmet som är riktiga Zigbee-lampor. De andra är virtuella."""
    return frozenset(lamp.id for lamp in home.lamps if ieee_address(lamp.id) is not None)


# --- Lampprofiler ---


@dataclass(frozen=True)
class Setting:
    """Det en lampa ska ställas in på."""

    on: bool
    brightness: int = 100
    """Ljusstyrkan i procent (1–100) när lampan är tänd."""
    color_temp: int | None = None
    """Färgtemperaturen i kelvin, eller None för att lämna den som den är."""

    @classmethod
    def of(cls, command: LampCommand) -> "Setting":
        return cls(command.on, command.brightness, command.color_temp)


@dataclass(frozen=True)
class Profile:
    """Hur kommandon till en sorts lampa ser ut i Zigbee2MQTT.

    Kommandot för att tända har alltid med ljusstyrkan, eftersom en lampa som
    släcktes med övergång annars kan tändas på lägsta ljusstyrkan igen (så gör
    TRÅDFRI). Övergångstiden står alltid med.
    """

    min_brightness: int
    """Lägsta ljusstyrkan i Zigbee (1–254) som lampan klarar att lysa med."""
    one_attribute_per_transition: bool
    """Lampan klarar bara att ändra en sak i taget med övergång (ljusstyrka eller färg)."""

    def payloads(self, setting: Setting, transition: float) -> tuple[dict[str, Any], ...]:
        """Meddelandena till ``<lampa>/set`` för att ställa in lampan, i tur och ordning."""
        if not setting.on:
            return ({"state": "OFF", "transition": transition},)
        level = {
            "state": "ON",
            "brightness": self.zigbee_brightness(setting.brightness),
            "transition": transition,
        }
        if setting.color_temp is None:
            return (level,)
        mired = round(1_000_000 / setting.color_temp)
        if not self.one_attribute_per_transition:
            return ({**level, "color_temp": mired},)
        # Färgen byts utan övergång, så att bara ljusstyrkan glider. Den skickas
        # efter att lampan har tänts, eftersom en släckt lampa inte tar emot färg.
        return (level, {"color_temp": mired, "transition": 0})

    def zigbee_brightness(self, percent: int) -> int:
        return max(
            self.min_brightness, min(ZIGBEE_BRIGHTNESS, round(percent * ZIGBEE_BRIGHTNESS / 100))
        )


GENERIC = Profile(min_brightness=1, one_attribute_per_transition=False)
IKEA_TRADFRI = Profile(min_brightness=2, one_attribute_per_transition=True)
"""IKEA TRÅDFRI: lyser inte stadigt på ljusstyrka 1, och ändrar bara en sak i taget med övergång."""

PROFILES: Mapping[LampProfile, Profile] = {
    LampProfile.GENERIC: GENERIC,
    LampProfile.IKEA_TRADFRI: IKEA_TRADFRI,
}


def group_profile(profiles: Sequence[LampProfile]) -> Profile:
    """Profilen för ett gruppkommando: den försiktigaste bland lamporna i gruppen."""
    chosen = [PROFILES[profile] for profile in profiles]
    return Profile(
        min_brightness=max((p.min_brightness for p in chosen), default=1),
        one_attribute_per_transition=any(p.one_attribute_per_transition for p in chosen),
    )


def percent(zigbee_brightness: int) -> int:
    """Zigbees ljusstyrka (0–254) i procent (1–100)."""
    return max(1, min(100, round(zigbee_brightness * 100 / ZIGBEE_BRIGHTNESS)))


# --- Lamporna som Zigbee2MQTT känner till ---


@dataclass(frozen=True)
class ZigbeeLamp:
    """En lampa i Zigbee-nätet."""

    ieee_address: str
    friendly_name: str
    """Namnet i Zigbee2MQTT, som också är lampans topic."""
    description: str
    """Vad det är för lampa, enligt Zigbee2MQTT."""
    profile: LampProfile
    """Profilen som passar lampan. Den föreslås när lampan placeras."""

    @property
    def lamp_id(self) -> str:
        return lamp_id(self.ieee_address)

    @property
    def name(self) -> str:
        """Ett namn att visa: namnet i Zigbee2MQTT om någon har gett den ett, annars vad den är."""
        if self.friendly_name != self.ieee_address:
            return self.friendly_name
        return self.description or self.ieee_address


def _parse_devices(devices: object) -> dict[str, ZigbeeLamp]:
    """Lamporna i ``bridge/devices``: enheter med ljus som har gått med i nätet."""
    lamps: dict[str, ZigbeeLamp] = {}
    if not isinstance(devices, list):
        raise ValueError("bridge/devices är inte en lista")
    for device in devices:
        if not isinstance(device, dict):
            continue
        address = str(device.get("ieee_address", "")).lower()
        definition = device.get("definition")
        if (
            not _IEEE.match(address)
            or not isinstance(definition, dict)
            or device.get("disabled") is True
            or device.get("interview_completed") is False
            or not _has_light(definition.get("exposes"))
        ):
            continue
        vendor = str(definition.get("vendor", ""))
        lamps[address] = ZigbeeLamp(
            ieee_address=address,
            friendly_name=str(device.get("friendly_name") or address),
            description=" ".join(
                part for part in (vendor, str(definition.get("description", ""))) if part
            ),
            profile=LampProfile.IKEA_TRADFRI if vendor == "IKEA" else LampProfile.GENERIC,
        )
    return lamps


def _has_light(exposes: object) -> bool:
    return isinstance(exposes, list) and any(
        isinstance(expose, dict) and expose.get("type") == "light" for expose in exposes
    )


def _parse_groups(groups: object) -> dict[str, frozenset[str]]:
    """Grupperna i ``bridge/groups`` som kärnan sköter, med medlemmarnas IEEE-adresser."""
    if not isinstance(groups, list):
        raise ValueError("bridge/groups är inte en lista")
    ours: dict[str, frozenset[str]] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        name = group.get("friendly_name")
        members = group.get("members")
        if not isinstance(name, str) or not name.startswith(GROUP_PREFIX):
            continue
        ours[name] = frozenset(
            str(member.get("ieee_address", "")).lower()
            for member in (members if isinstance(members, list) else [])
            if isinstance(member, dict)
        )
    return ours


def _online(payload: object) -> bool:
    """Zigbee2MQTT skriver ``{"state": "online"}``, eller i äldre inställningar bara ``online``."""
    state = payload.get("state") if isinstance(payload, dict) else payload
    return state == "online"


# --- MQTT ---


class MqttConnection(Protocol):
    """En ansluten MQTT-klient. Testerna byter ut den mot en låtsasbroker."""

    async def subscribe(self, topic: str) -> None: ...

    async def publish(self, topic: str, payload: bytes) -> None: ...

    def messages(self) -> AsyncIterator[tuple[str, bytes]]:
        """Meddelandena från brokern. Tar slut eller ger ConnectionError när anslutningen bryts."""
        ...


Connect = Callable[[], AbstractAsyncContextManager[MqttConnection]]
"""Ansluter till brokern. Ger ConnectionError om det inte går."""


def mqtt_connect(settings: MqttSettings) -> Connect:
    """Anslutningen till den riktiga brokern med aiomqtt."""

    @contextlib.asynccontextmanager
    async def connect() -> AsyncIterator[MqttConnection]:
        client = aiomqtt.Client(
            settings.host,
            settings.port,
            username=settings.username,
            password=settings.password,
            identifier="homecontrol",
        )
        try:
            async with client:
                yield _AiomqttConnection(client)
        except aiomqtt.MqttError as error:
            raise ConnectionError(str(error)) from None

    return connect


class _AiomqttConnection:
    def __init__(self, client: aiomqtt.Client) -> None:
        self._client = client

    async def subscribe(self, topic: str) -> None:
        await self._client.subscribe(topic, qos=1)

    async def publish(self, topic: str, payload: bytes) -> None:
        await self._client.publish(topic, payload, qos=1)

    async def messages(self) -> AsyncIterator[tuple[str, bytes]]:
        async for message in self._client.messages:
            payload = message.payload
            if isinstance(payload, str):
                payload = payload.encode()
            elif not isinstance(payload, bytes | bytearray):
                payload = b""
            yield message.topic.value, bytes(payload)


def defer_in_loop(callback: Callable[[], None]) -> None:
    """Kör `callback` när händelseloopen har gjort klart det den håller på med."""
    asyncio.get_running_loop().call_soon(callback)


# --- Utgången ---


@dataclass
class _LampStatus:
    """Vad utgången vet om en placerad lampa."""

    read: bool = False
    """Lampan har svarat med sitt läge sedan utgången anslöt eller lampan kom tillbaka."""
    asked_at: float | None = None
    """När lampan senast fick frågan om sitt läge."""
    gone: bool = False
    """Lampan har rapporterats som borta, och har inte svarat sedan dess."""
    on: bool | None = None
    brightness: int = 100
    """Det lampan senast sa."""
    answer_until: float = -math.inf
    """Till dess räknas lampans besked som svar på kärnans senaste kommando."""


@dataclass(frozen=True)
class _Pending:
    """Ett kommando som väntar på att skickas, med det som behövs för att skicka det."""

    friendly_name: str
    ieee_address: str
    profile: LampProfile
    room_id: str
    setting: Setting


Publish = Callable[[str, dict[str, Any]], None]


class ZigbeeOutput:
    """Lamputgången Zigbee2MQTT. Se modulens beskrivning.

    `defer` kör en funktion när kärnan har lämnat alla kommandon från ett
    beslut, så att ett rums lampor kan skickas som ett gruppkommando.
    `now` är riktig tid i sekunder. Den påverkas inte av simuleringens tidsfart.
    """

    def __init__(
        self,
        home: Home,
        *,
        defer: Callable[[Callable[[], None]], None] = defer_in_loop,
        now: Callable[[], float] = time.monotonic,
        base_topic: str = BASE_TOPIC,
    ) -> None:
        self._home = home
        self._defer = defer
        self._now = now
        self._base = base_topic
        self._sink: ObservationSink | None = None
        self._publish: Publish | None = None
        """Lägger ett meddelande i kön ut. None när utgången inte är ansluten."""
        self._bridge_online = False
        self._devices: dict[str, ZigbeeLamp] | None = None
        """Lamporna i Zigbee-nätet efter IEEE-adress, eller None innan listan har kommit."""
        self._names: dict[str, str] = {}
        """IEEE-adressen för varje namn i Zigbee2MQTT."""
        self._unavailable: set[str] = set()
        """IEEE-adresserna som Zigbee2MQTT säger är borta."""
        self._groups: dict[str, frozenset[str]] | None = None
        self._group_requests: set[tuple[str, str, str]] = set()
        """Ändringar av grupperna som har skickats men inte syns i ``bridge/groups`` än."""
        self._lamps: dict[str, _LampStatus] = {}
        """De placerade lamporna, efter id i hemmet."""
        self._pending: dict[str, _Pending] = {}
        self._flush_scheduled = False
        self._place_lamps(home)

    # --- Det kärnan anropar ---

    def start(self, sink: ObservationSink) -> None:
        """Lampornas besked lämnas till `sink` från och med nu."""
        self._sink = sink

    def send(self, command: LampCommand) -> None:
        """Skickar kommandot om lampan är en placerad Zigbee-lampa vars läge är läst."""
        status = self._lamps.get(command.lamp_id)
        address = ieee_address(command.lamp_id)
        device = None if self._devices is None or address is None else self._devices.get(address)
        if status is None or device is None:
            return
        if not status.read:
            # Kärnan får lampans läge snart och räknar då om vad som behövs.
            logger.debug("%s har inte svarat med sitt läge än; väntar", command.lamp_id)
            return
        lamp = next(lamp for lamp in self._home.lamps if lamp.id == command.lamp_id)
        # Rummet och namnet bestäms nu, så att en lampa som just har tagits bort
        # ur hemmet ändå går att släcka.
        self._pending[command.lamp_id] = _Pending(
            device.friendly_name,
            device.ieee_address,
            lamp.profile,
            self._home.room_of(command.lamp_id).id,
            Setting.of(command),
        )
        if not self._flush_scheduled:
            self._flush_scheduled = True
            self._defer(self._flush)

    def use_home(self, home: Home) -> None:
        """Byter till ett ändrat hem, till exempel när en ny lampa har placerats."""
        self._home = home
        self._place_lamps(home)
        self._sync_groups()
        self._ask_lamps()

    def lamps(self) -> list[FoundLamp]:
        """Lamporna i Zigbee-nätet, för webbgränssnittet. De oplacerade väntar på att placeras."""
        devices = {} if self._devices is None else self._devices
        return [
            FoundLamp(lamp.lamp_id, lamp.name, lamp.profile, placed=lamp.lamp_id in self._lamps)
            for lamp in sorted(devices.values(), key=lambda lamp: lamp.name)
        ]

    # --- Anslutningen ---

    async def run(self, sink: ObservationSink, connect: Connect) -> None:
        """Ansluter till brokern och håller anslutningen uppe tills den avbryts."""
        self.start(sink)
        while True:
            try:
                async with connect() as connection:
                    logger.info("Ansluten till MQTT-brokern")
                    await self._serve(connection)
            except (ConnectionError, OSError) as error:
                logger.warning("Ingen kontakt med MQTT-brokern (%s). Försöker igen.", error)
            except Exception:
                # Utgången får inte dö tyst; då styrs inga lampor förrän kärnan startas om.
                logger.exception("Fel i lamputgången Zigbee2MQTT. Ansluter igen.")
            finally:
                self.disconnected()
            await asyncio.sleep(RECONNECT_SECONDS)

    async def _serve(self, connection: MqttConnection) -> None:
        outbox: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue(MAX_OUTBOX)

        def publish(topic: str, payload: dict[str, Any]) -> None:
            try:
                outbox.put_nowait((topic, json.dumps(payload).encode()))
            except asyncio.QueueFull:
                logger.warning("För många meddelanden till Zigbee2MQTT väntar; ett tappas")

        await connection.subscribe(f"{self._base}/#")
        self.connected(publish)
        tasks = [
            asyncio.create_task(self._read(connection)),
            asyncio.create_task(self._write(connection, outbox)),
            asyncio.create_task(self._tick_every_second()),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        for task in done:
            task.result()

    async def _read(self, connection: MqttConnection) -> None:
        async for topic, payload in connection.messages():
            self.handle(topic, payload)
        raise ConnectionError("brokern stängde anslutningen")

    async def _write(
        self, connection: MqttConnection, outbox: asyncio.Queue[tuple[str, bytes]]
    ) -> None:
        while True:
            topic, payload = await outbox.get()
            await connection.publish(topic, payload)

    async def _tick_every_second(self) -> None:
        while True:
            await asyncio.sleep(1)
            self.tick()

    def connected(self, publish: Publish) -> None:
        """Utgången är ansluten. Zigbee2MQTT:s sparade meddelanden kommer strax."""
        self._publish = publish

    def disconnected(self) -> None:
        """Utgången har tappat brokern: ingen lampa går att nå, och lägena gäller inte längre."""
        self._publish = None
        self._bridge_online = False
        self._group_requests.clear()
        self._pending.clear()
        for lamp_id in self._lamps:
            self._gone(lamp_id)

    def tick(self) -> None:
        """Anropas varje sekund: lampor som inte har svarat räknas som borta och frågas igen."""
        now = self._now()
        for lamp_id, status in self._lamps.items():
            if (
                not status.read
                and status.asked_at is not None
                and now - status.asked_at >= READ_TIMEOUT_SECONDS
            ):
                # Lampan frågas igen om en stund, inte direkt.
                self._gone(lamp_id, ask_again_now=False)
        self._ask_lamps()

    # --- Meddelanden från Zigbee2MQTT ---

    def handle(self, topic: str, payload: bytes) -> None:
        """Tolkar ett meddelande från brokern. Trasiga meddelanden loggas och hoppas över."""
        if not topic.startswith(f"{self._base}/"):
            return
        name = topic.removeprefix(f"{self._base}/")
        if name.endswith(("/set", "/get")) or name.startswith("bridge/request/"):
            return  # kärnans egna meddelanden kommer tillbaka
        try:
            data = json.loads(payload) if payload else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            if payload in (b"online", b"offline"):
                data = payload.decode()
            else:
                logger.warning("Trasigt meddelande från Zigbee2MQTT på %s hoppas över", topic)
                return
        try:
            self._dispatch(name, data)
        except (ValueError, TypeError, KeyError) as error:
            logger.warning(
                "Oväntat meddelande från Zigbee2MQTT på %s hoppas över: %s", topic, error
            )

    def _dispatch(self, name: str, data: object) -> None:
        match name:
            case "bridge/state":
                self._bridge_state(_online(data))
            case "bridge/devices":
                self._bridge_devices(_parse_devices(data))
            case "bridge/groups":
                self._groups = _parse_groups(data)
                self._group_requests.clear()
                self._sync_groups()
            case _ if name.startswith("bridge/response/"):
                if isinstance(data, dict) and data.get("status") == "error":
                    logger.warning("Zigbee2MQTT sa nej till %s: %s", name, data.get("error"))
            case _ if name.startswith("bridge/"):
                pass
            case _ if name.endswith("/availability"):
                address = self._names.get(name.removesuffix("/availability"))
                if address is not None:
                    self._availability(address, _online(data))
            case _:
                address = self._names.get(name)
                if address is not None and isinstance(data, dict):
                    self._device_state(address, data)

    def _bridge_state(self, online: bool) -> None:
        if online == self._bridge_online:
            return
        self._bridge_online = online
        if online:
            self._sync_groups()
            self._ask_lamps()
        else:
            logger.warning("Zigbee2MQTT är borta; lampornas läge är okänt")
            for lamp_id in self._lamps:
                self._gone(lamp_id)

    def _bridge_devices(self, devices: dict[str, ZigbeeLamp]) -> None:
        before = set() if self._devices is None else set(self._devices)
        self._devices = devices
        self._names = {lamp.friendly_name: address for address, lamp in devices.items()}
        new = [lamp for address, lamp in devices.items() if address not in before]
        waiting = [lamp for lamp in new if lamp.lamp_id not in self._lamps]
        if waiting:
            logger.info("%d nya Zigbee-lampor väntar på att placeras på kartan", len(waiting))
        for lamp_id in self._lamps:
            address = ieee_address(lamp_id)
            if address not in devices:
                self._gone(lamp_id)
        self._sync_groups()
        self._ask_lamps()

    def _availability(self, address: str, online: bool) -> None:
        if online:
            self._unavailable.discard(address)
            self._ask_lamps()
            return
        self._unavailable.add(address)
        status_id = lamp_id(address)
        if status_id in self._lamps:
            self._gone(status_id)

    def _device_state(self, address: str, data: Mapping[str, object]) -> None:
        status_id = lamp_id(address)
        status = self._lamps.get(status_id)
        state = data.get("state")
        if status is None or state not in ("ON", "OFF"):
            return  # en oplacerad lampa, eller ett besked om något annat än ljuset
        on = state == "ON"
        brightness = data.get("brightness")
        level = (
            percent(int(brightness))
            if isinstance(brightness, int | float) and not isinstance(brightness, bool)
            else status.brightness
        )
        if not status.read:
            external = False  # lampans läge när den läses, inte en ändring
            status.read = True
            logger.info("Läste läget för %s", status_id)
        elif self._now() <= status.answer_until:
            external = False
        else:
            external = on != status.on or (
                on and abs(level - status.brightness) > BRIGHTNESS_TOLERANCE
            )
        status.gone = False
        status.on = on
        status.brightness = level
        self._report(LampStateReported(status_id, on, level, external=external))

    # --- Hjälp ---

    def _place_lamps(self, home: Home) -> None:
        """Håller reda på vilka Zigbee-lampor som är placerade på kartan."""
        placed = zigbee_lamps(home)
        self._lamps = {lamp: self._lamps.get(lamp, _LampStatus()) for lamp in sorted(placed)}
        if self._devices is not None:
            for lamp in placed:
                if ieee_address(lamp) not in self._devices:
                    self._gone(lamp)

    def _ask_lamps(self) -> None:
        """Frågar de placerade lampor som går att nå om deras läge, om de inte redan har svarat."""
        if self._publish is None or not self._bridge_online or self._devices is None:
            return
        now = self._now()
        for status_id, status in self._lamps.items():
            address = ieee_address(status_id)
            device = self._devices.get(address) if address is not None else None
            if (
                device is None
                or status.read
                or device.ieee_address in self._unavailable
                or (status.asked_at is not None and now - status.asked_at < READ_RETRY_SECONDS)
            ):
                continue
            status.asked_at = now
            self._publish(
                f"{self._base}/{device.friendly_name}/get", {"state": "", "brightness": ""}
            )

    def _gone(self, status_id: str, *, ask_again_now: bool = True) -> None:
        """Lampan går inte att nå. Kärnan får veta att läget är okänt, en gång.

        Lampan frågas om sitt läge igen så fort den går att nå, eller med
        `ask_again_now` falskt först efter en stund.
        """
        status = self._lamps[status_id]
        status.read = False
        if ask_again_now:
            status.asked_at = None
        if status.gone:
            return
        status.gone = True
        status.on = None
        self._report(LampStateReported(status_id, None))

    def _report(self, report: LampStateReported) -> None:
        if self._sink is None:
            return
        try:
            self._sink(report)
        except Exception:
            logger.exception("Kärnan kunde inte ta emot lampans läge")

    def _desired_groups(self) -> dict[str, frozenset[str]]:
        """Grupperna som ska finnas: en per rum med minst två lampor i Zigbee-nätet."""
        devices = {} if self._devices is None else self._devices
        desired = {}
        for room in self._home.rooms:
            members = frozenset(
                address
                for lamp in room.lamps
                if (address := ieee_address(lamp.id)) is not None and address in devices
            )
            if len(members) >= 2:
                desired[GROUP_PREFIX + room.id] = members
        return desired

    def _sync_groups(self) -> None:
        """Ber Zigbee2MQTT ändra rumsgrupperna så att de stämmer med kartan."""
        if self._publish is None or self._groups is None or self._devices is None:
            return
        desired = self._desired_groups()
        changes: list[tuple[str, str, str]] = []
        for name, members in desired.items():
            if name not in self._groups:
                changes.append(("add", name, ""))
                continue
            current = self._groups[name]
            changes += [("members/add", name, address) for address in sorted(members - current)]
            changes += [("members/remove", name, address) for address in sorted(current - members)]
        for name, current in self._groups.items():
            if name not in desired:
                changes += [("members/remove", name, address) for address in sorted(current)]
        for change in changes:
            if change in self._group_requests:
                continue
            self._group_requests.add(change)
            action, name, address = change
            payload = (
                {"friendly_name": name} if action == "add" else {"group": name, "device": address}
            )
            self._publish(f"{self._base}/bridge/request/group/{action}", payload)

    def _flush(self) -> None:
        """Skickar de väntande kommandona, med ett gruppkommando per rum där det går."""
        self._flush_scheduled = False
        pending, self._pending = self._pending, {}
        by_room: dict[str, dict[str, _Pending]] = {}
        for status_id, item in pending.items():
            by_room.setdefault(item.room_id, {})[status_id] = item
        for room_id, items in by_room.items():
            group = GROUP_PREFIX + room_id
            settings = {item.setting for item in items.values()}
            members = frozenset(item.ieee_address for item in items.values())
            if (
                len(settings) == 1
                and len(members) >= 2
                and self._groups is not None
                and self._groups.get(group) == members
                and self._desired_groups().get(group) == members
            ):
                self._set(
                    group,
                    group_profile([item.profile for item in items.values()]),
                    settings.pop(),
                    list(items),
                )
                continue
            for status_id, item in items.items():
                self._set(item.friendly_name, PROFILES[item.profile], item.setting, [status_id])

    def _set(self, target: str, profile: Profile, setting: Setting, lamp_ids: list[str]) -> None:
        """Skickar en inställning till en lampa eller grupp. Alla lampkommandon går ut här."""
        if self._publish is None:
            return
        answer_until = self._now() + TRANSITION_SECONDS + ECHO_SECONDS
        for status_id in lamp_ids:
            if status_id in self._lamps:
                self._lamps[status_id].answer_until = answer_until
        for payload in profile.payloads(setting, TRANSITION_SECONDS):
            self._publish(f"{self._base}/{target}/set", payload)
