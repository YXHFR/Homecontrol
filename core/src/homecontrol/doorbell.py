"""Dörrklockan: en fristående tilläggsmodul som är avstängd tills den är inställd i .env.

Dörrklockan (XIAO ESP32S3 Sense, se firmware/dorrklocka.yaml) publicerar
``dorr/ringer`` på MQTT när någon trycker på knappen. Då

* hämtas en stillbild från kameran och skickas med en push-notis (ntfy),
* spelas dingdong på Google Home-högtalaren,
* tänds dörrlampan (Zigbee2MQTT) i 30 sekunder, och får sedan tillbaka sitt
  tidigare läge,
* öppnas fönstret då sidan Dörren visar kameran: 2 minuter.

**Integritet.** Kameran används bara efter ett ring eller när någon har
tryckt på Visa dörren, och bara inom fönstret. Kärnan ansluter till kameran
först då och kopplar ner när fönstret stängs. Stillbilden finns bara i
minnet och töms så fort notisen har skickats (eller inte gick att skicka);
den skrivs aldrig till disk. En radar vid dörren (``dorr/radar``) ger bara en
textnotis och rör aldrig kameran.

**Ett ring, en notis.** Knappen avstudsas i dörrklockan. Varje tryck har
dessutom ett eget id, så att ett meddelande som MQTT levererar två gånger
bara räknas en gång, och tryck tätare än `RING_COOLDOWN_SECONDS` räknas som
samma ring.

Modulen är uppdelad så att allt går att pröva utan hårdvara: kameran,
notiserna och högtalaren är gränssnitt (`Camera`, `Notifier`, `Speaker`)
med riktiga kopplingar i doorbell_devices.py, och lampan styrs genom samma
MQTT-anslutning som ringen kommer på.
"""

import asyncio
import contextlib
import json
import logging
import math
import secrets
import time
from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Protocol

from homecontrol.zigbee import BASE_TOPIC, ZIGBEE_BRIGHTNESS, Connect, MqttConnection

logger = logging.getLogger(__name__)

RING_TOPIC = "dorr/ringer"
RADAR_TOPIC = "dorr/radar"

VIEW_SECONDS = 120.0
"""Så länge sidan Dörren visar kameran efter ett ring eller efter Visa dörren."""
RING_COOLDOWN_SECONDS = 5.0
"""Tryck tätare än så räknas som samma ring."""
RADAR_NOTICE_SECONDS = 300.0
"""Radarn vid dörren ger högst en notis så här ofta."""
LAMP_SECONDS = 30.0
"""Så länge dörrlampan lyser efter ett ring."""
LAMP_READ_SECONDS = 3.0
"""Så länge kärnan väntar på lampans läge innan den tänds."""
MAX_VIEWERS = 2
"""Så många kan titta på kameran samtidigt. Kameran orkar inte med fler."""
MAX_PAYLOAD = 256
"""Större meddelanden på dörrens ämnen hoppas över."""
REMEMBERED_PRESSES = 64
"""Så många tryck-id:n som kärnan minns för att känna igen dubbletter."""
RECONNECT_SECONDS = 5.0

RING_TITLE = "Det ringer på dörren"
RING_MESSAGE = "Öppna Homecontrol och välj Dörren för att se vem det är (i 2 minuter)."
RADAR_TITLE = "Någon är vid dörren"
RADAR_MESSAGE = "Radarn vid dörren känner av någon. Kameran är inte på."


class Camera(Protocol):
    """Kameran i dörrklockan. Den ansluts bara medan en bild eller ström behövs."""

    async def snapshot(self) -> bytearray | None:
        """En stillbild (JPEG), eller None om kameran inte svarar."""
        ...

    def frames(self) -> AsyncGenerator[bytes]:
        """Bilder (JPEG) i kamerans takt, tills den som läser slutar."""
        ...


class Notifier(Protocol):
    """Push-notiser till telefonen."""

    async def send(self, title: str, message: str, image: bytearray | None = None) -> None:
        """Skickar notisen. Ger ett fel om den inte gick fram."""
        ...


class Speaker(Protocol):
    """Högtalaren som spelar dingdong."""

    async def play(self) -> None: ...


class ViewClosed(Exception):
    """Kameran visas bara inom fönstret efter ett ring eller Visa dörren."""


class TooManyViewers(Exception):
    """Fler än `MAX_VIEWERS` försöker titta samtidigt."""


Publish = Callable[[str, dict[str, Any]], None]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class _LampState:
    on: bool
    brightness: int | None = None
    """Ljusstyrkan i Zigbee (0–254), om lampan sa den."""


class LampLender(Protocol):
    """Lamputgången som annars styr dörrlampan (se `ZigbeeOutput.borrow`)."""

    def borrow(self, friendly_name: str) -> None: ...

    def give_back(self, friendly_name: str) -> None: ...


class DoorLamp:
    """Dörrlampan i Zigbee2MQTT: tänds vid ett ring och får sedan tillbaka sitt läge.

    Den styrs med namnet i Zigbee2MQTT och behöver inte vara placerad på
    kartan. Är den placerad lånas den från kärnans lamputgång (`lend_from`)
    medan den lyser, så att automatiken inte tar blinket för manuell
    styrning. Efteråt tar automatiken över lampan igen.
    """

    def __init__(
        self,
        name: str,
        *,
        base_topic: str = BASE_TOPIC,
        now: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.name = name
        self.topic = f"{base_topic}/{name}"
        self._lender: LampLender | None = None
        self._now = now
        self._sleep = sleep
        self._publish: Publish | None = None
        self._state: _LampState | None = None
        self._state_changed = asyncio.Event()
        self._until = -math.inf
        self._flashing = False

    def lend_from(self, lender: LampLender) -> None:
        """Lampan lånas från `lender` medan den lyser efter ett ring."""
        self._lender = lender

    def connected(self, publish: Publish) -> None:
        self._publish = publish

    def disconnected(self) -> None:
        self._publish = None
        self._state = None

    def handle(self, payload: bytes) -> None:
        """Lampans besked om sitt läge från Zigbee2MQTT."""
        try:
            data = json.loads(payload)
        except ValueError, RecursionError:
            return
        if not isinstance(data, dict) or data.get("state") not in ("ON", "OFF"):
            return
        brightness = data.get("brightness")
        self._state = _LampState(
            on=data["state"] == "ON",
            brightness=(
                round(brightness)
                if isinstance(brightness, int | float)
                and not isinstance(brightness, bool)
                and 0 <= brightness <= ZIGBEE_BRIGHTNESS
                else None
            ),
        )
        self._state_changed.set()

    async def flash(self) -> None:
        """Tänder lampan i `LAMP_SECONDS` och ger den sedan tillbaka sitt tidigare läge.

        Ett nytt ring medan lampan lyser förlänger tiden; läget som återställs
        är fortfarande det från före det första ringet. Har någon ändrat lampan
        under tiden får den vara som den är.
        """
        self._until = self._now() + LAMP_SECONDS
        if self._flashing:
            return
        self._flashing = True
        if self._lender is not None:
            self._lender.borrow(self.name)
        try:
            before = await self._read()
            lit = _LampState(on=True, brightness=ZIGBEE_BRIGHTNESS)
            self._set(lit)
            while (left := self._until - self._now()) > 0:
                await self._sleep(left)
            if self._state is not None and not _same(self._state, lit):
                logger.info("Dörrlampan ändrades medan den lyste; läget återställs inte")
                return
            # Svarade lampan inte med sitt läge var den troligen släckt.
            self._set(before if before is not None else _LampState(on=False))
        finally:
            self._flashing = False
            if self._lender is not None:
                self._lender.give_back(self.name)

    async def _read(self) -> _LampState | None:
        if self._state is not None or self._publish is None:
            return self._state
        self._state_changed.clear()
        self._publish(f"{self.topic}/get", {"state": "", "brightness": ""})
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._state_changed.wait(), LAMP_READ_SECONDS)
        return self._state

    def _set(self, state: _LampState) -> None:
        if self._publish is None:
            logger.warning("Ingen kontakt med MQTT-brokern; dörrlampan styrs inte")
            return
        payload: dict[str, Any] = {"state": "ON" if state.on else "OFF"}
        if state.on and state.brightness is not None:
            payload["brightness"] = state.brightness
        self._publish(f"{self.topic}/set", payload)


def _same(reported: _LampState, lit: _LampState) -> bool:
    """Om lampan fortfarande är som kärnan tände den (ljusstyrkan avrundas av lampan)."""
    return reported.on and (
        reported.brightness is None
        or lit.brightness is None
        or abs(reported.brightness - lit.brightness) <= 5
    )


class Doorbell:
    """Dörrklockan. Se modulens beskrivning.

    `now` är riktig tid i sekunder.
    """

    def __init__(
        self,
        notifier: Notifier,
        *,
        camera: Camera | None = None,
        speaker: Speaker | None = None,
        lamp: DoorLamp | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._notifier = notifier
        self._camera = camera
        self._speaker = speaker
        self._lamp = lamp
        self._now = now
        self._view_until = -math.inf
        self._last_ring = -math.inf
        self._last_radar_notice = -math.inf
        self._presses: deque[str] = deque(maxlen=REMEMBERED_PRESSES)
        self._viewers = 0
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def has_camera(self) -> bool:
        return self._camera is not None

    @property
    def lamp(self) -> DoorLamp | None:
        return self._lamp

    # --- Fönstret då kameran visas ---

    def open_view(self) -> None:
        """Visa dörren: kameran får visas i `VIEW_SECONDS` från nu."""
        self._view_until = max(self._view_until, self._now() + VIEW_SECONDS)

    def view_seconds_left(self) -> float:
        """Hur länge till kameran får visas, eller 0 när fönstret är stängt."""
        return max(0.0, self._view_until - self._now())

    async def frames(self) -> AsyncGenerator[bytes]:
        """Kamerans bilder så länge fönstret är öppet.

        Kameran ansluts när den första bilden behövs och kopplas ner när
        fönstret stängs eller den som tittar slutar, även om kameran tystnar.
        Ger `ViewClosed` när fönstret är stängt (eller ingen kamera finns) och
        `TooManyViewers` när för många redan tittar.
        """
        camera = self._camera
        if camera is None or self.view_seconds_left() <= 0:
            raise ViewClosed
        if self._viewers >= MAX_VIEWERS:
            raise TooManyViewers
        self._viewers += 1
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)

        async def pump() -> None:
            async with contextlib.aclosing(camera.frames()) as frames:
                async for frame in frames:
                    # Bara den senaste bilden väntar; en långsam mottagare
                    # får hoppa över bilder i stället för att se dem sent.
                    if queue.full():
                        queue.get_nowait()
                    queue.put_nowait(frame)

        reader = asyncio.create_task(pump())
        try:
            while (left := self.view_seconds_left()) > 0:
                getter = asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait(
                    {getter, reader}, timeout=left, return_when=asyncio.FIRST_COMPLETED
                )
                if getter in done:
                    yield getter.result()
                    continue
                getter.cancel()
                if reader in done:
                    reader.result()  # ett fel från kameran
                    return
        finally:
            self._viewers -= 1
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader

    # --- Meddelanden från dörren ---

    def handle(self, topic: str, payload: bytes) -> None:
        """Tolkar ett meddelande från brokern. Ett ring eller radarbesked startas i bakgrunden."""
        if len(payload) > MAX_PAYLOAD:
            logger.warning("För stort meddelande på %s hoppas över", topic)
            return
        if topic == RING_TOPIC:
            if self.accept_ring(payload):
                self._spawn(self.ring())
        elif topic == RADAR_TOPIC:
            if _presence(payload) and self._accept_radar():
                self._spawn(self.radar_notice())
        elif self._lamp is not None and topic == self._lamp.topic:
            self._lamp.handle(payload)

    def accept_ring(self, payload: bytes) -> bool:
        """Om meddelandet är ett nytt ring, och inte en dubblett eller ett studsande tryck."""
        press = _press_id(payload)
        if press is not None:
            if press in self._presses:
                return False
            self._presses.append(press)
        now = self._now()
        if now - self._last_ring < RING_COOLDOWN_SECONDS:
            return False
        self._last_ring = now
        return True

    def _accept_radar(self) -> bool:
        now = self._now()
        if now - self._last_radar_notice < RADAR_NOTICE_SECONDS:
            return False
        self._last_radar_notice = now
        return True

    async def ring(self) -> None:
        """Det ringer: öppnar fönstret, skickar notisen, spelar ljudet och tänder lampan."""
        logger.info("Det ringer på dörren")
        self.open_view()
        parts: dict[str, Coroutine[Any, Any, None]] = {"notisen": self._ring_notice()}
        if self._speaker is not None:
            parts["ljudet"] = self._speaker.play()
        if self._lamp is not None:
            parts["dörrlampan"] = self._lamp.flash()
        results = await asyncio.gather(*parts.values(), return_exceptions=True)
        for name, result in zip(parts, results, strict=True):
            if isinstance(result, Exception):
                logger.warning("Dörrklockan: %s misslyckades (%s)", name, result)

    async def _ring_notice(self) -> None:
        still = None
        if self._camera is not None:
            try:
                still = await self._camera.snapshot()
            except Exception as error:
                logger.warning("Fick ingen bild från kameran (%s); notisen skickas utan", error)
        try:
            await self._notifier.send(RING_TITLE, RING_MESSAGE, still)
        finally:
            if still is not None:
                _erase(still)

    async def radar_notice(self) -> None:
        """Någon är vid dörren enligt radarn: bara en textnotis. Kameran rörs aldrig."""
        try:
            await self._notifier.send(RADAR_TITLE, RADAR_MESSAGE)
        except Exception as error:
            logger.warning("Dörrklockan: radarnotisen misslyckades (%s)", error)

    # --- Anslutningen ---

    async def run(self, connect: Connect) -> None:
        """Lyssnar på dörrens ämnen tills den avbryts, och ansluter igen när brokern försvinner."""
        try:
            while True:
                try:
                    async with connect() as connection:
                        logger.info("Dörrklockan är ansluten till MQTT-brokern")
                        await self._serve(connection)
                except (ConnectionError, OSError) as error:
                    logger.warning("Dörrklockan har ingen kontakt med MQTT-brokern (%s)", error)
                except Exception:
                    logger.exception("Fel i dörrklockan. Ansluter igen.")
                finally:
                    if self._lamp is not None:
                        self._lamp.disconnected()
                await asyncio.sleep(RECONNECT_SECONDS)
        finally:
            for task in list(self._tasks):
                task.cancel()

    async def _serve(self, connection: MqttConnection) -> None:
        outbox: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue(100)

        def publish(topic: str, payload: dict[str, Any]) -> None:
            with contextlib.suppress(asyncio.QueueFull):
                outbox.put_nowait((topic, json.dumps(payload).encode()))

        await connection.subscribe(RING_TOPIC)
        await connection.subscribe(RADAR_TOPIC)
        if self._lamp is not None:
            await connection.subscribe(self._lamp.topic)
            self._lamp.connected(publish)

        async def read() -> None:
            async for topic, payload in connection.messages():
                self.handle(topic, payload)
            raise ConnectionError("brokern stängde anslutningen")

        async def write() -> None:
            while True:
                topic, payload = await outbox.get()
                await connection.publish(topic, payload)

        tasks = [asyncio.create_task(read()), asyncio.create_task(write())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        for task in done:
            task.result()

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


async def simulate_ring(connect: Connect) -> None:
    """Publicerar ett ring på ``dorr/ringer``, som dörrklockan gör. För `homecontrol ringa`."""
    press = f"test-{secrets.token_hex(4)}"
    async with connect() as connection:
        await connection.publish(RING_TOPIC, json.dumps({"tryck": press}).encode())


def _press_id(payload: bytes) -> str | None:
    """Tryckets id i ``{"tryck": "<id>"}``, eller None om meddelandet inte har något."""
    try:
        data = json.loads(payload)
    except ValueError, RecursionError:
        return None
    press = data.get("tryck") if isinstance(data, dict) else None
    return str(press) if isinstance(press, str | int) and not isinstance(press, bool) else None


def _presence(payload: bytes) -> bool:
    """Om radarn säger att någon är där: ``ON``, ``true``, ``1`` eller ``{"narvaro": true}``."""
    text = payload.strip().lower()
    if text in (b"on", b"true", b"1"):
        return True
    try:
        data = json.loads(payload)
    except ValueError, RecursionError:
        return False
    return isinstance(data, dict) and data.get("narvaro") is True


def _erase(still: bytearray) -> None:
    """Skriver över bilden i minnet och tömmer den."""
    still[:] = bytes(len(still))
    still.clear()
