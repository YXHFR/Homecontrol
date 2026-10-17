"""Dörrklockan utan hårdvara: ring, notiser, dörrlampan och fönstret då kameran visas."""

import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator, AsyncIterator

import pytest

from homecontrol.doorbell import (
    LAMP_SECONDS,
    RADAR_MESSAGE,
    RADAR_TITLE,
    RADAR_TOPIC,
    RING_COOLDOWN_SECONDS,
    RING_TITLE,
    RING_TOPIC,
    VIEW_SECONDS,
    Doorbell,
    TooManyViewers,
    ViewClosed,
    simulate_ring,
)
from homecontrol.doorbell_devices import sound_url
from homecontrol.zigbee import MqttConnection

STILL = b"\xff\xd8 en bild av dorren \xff\xd9"


class Clock:
    """Riktig tid i testerna, som testet flyttar fram."""

    def __init__(self) -> None:
        self.time = 1000.0

    def now(self) -> float:
        return self.time


class Notifier:
    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[tuple[str, str, bytes | None]] = []
        self.error = error

    async def send(self, title: str, message: str, image: bytearray | None = None) -> None:
        self.sent.append((title, message, None if image is None else bytes(image)))
        if self.error is not None:
            raise self.error


class Camera:
    """En kamera som minns hur den har använts."""

    def __init__(self, frames: int = 3, *, broken: bool = False) -> None:
        self.stills: list[bytearray] = []
        self.streams_started = 0
        self.streaming = False
        self.frame_count = frames
        self.broken = broken

    async def snapshot(self) -> bytearray | None:
        if self.broken:
            raise ConnectionError("kameran svarar inte")
        still = bytearray(STILL)
        self.stills.append(still)
        return still

    async def frames(self) -> AsyncGenerator[bytes]:
        self.streams_started += 1
        self.streaming = True
        try:
            for n in range(self.frame_count):
                yield f"bild {n}".encode()
                await asyncio.sleep(0)
        finally:
            self.streaming = False


class UntouchableCamera:
    """En kamera som inte får röras."""

    async def snapshot(self) -> bytearray | None:
        raise AssertionError("kameran startades")

    def frames(self) -> AsyncGenerator[bytes]:
        raise AssertionError("kameran startades")


class Speaker:
    def __init__(self) -> None:
        self.plays = 0

    async def play(self) -> None:
        self.plays += 1


def press(press_id: str) -> bytes:
    return json.dumps({"tryck": press_id}).encode()


async def settle(doorbell: Doorbell) -> None:
    """Väntar tills allt som ringen startade är klart."""
    for _ in range(100):
        await asyncio.sleep(0)


def ring_messages(doorbell: Doorbell, messages: list[tuple[float, bytes]], clock: Clock) -> None:
    async def scenario() -> None:
        for at, payload in messages:
            clock.time = at
            doorbell.handle(RING_TOPIC, payload)
            await settle(doorbell)

    asyncio.run(scenario())


# --- Ett tryck ger exakt en notis ---


def test_a_ring_sends_one_notice_with_the_still_and_plays_the_sound() -> None:
    clock, notifier, camera, speaker = Clock(), Notifier(), Camera(), Speaker()
    doorbell = Doorbell(notifier, camera=camera, speaker=speaker, now=clock.now)

    ring_messages(doorbell, [(1000.0, press("a-1"))], clock)

    assert notifier.sent == [(RING_TITLE, notifier.sent[0][1], STILL)]
    assert speaker.plays == 1


def test_a_bouncing_button_rings_once() -> None:
    clock, notifier = Clock(), Notifier()
    doorbell = Doorbell(notifier, camera=Camera(), now=clock.now)

    # Kontakten studsar: flera tryck inom några hundradels sekunder.
    ring_messages(doorbell, [(1000.0 + n * 0.02, press(f"a-{n}")) for n in range(5)], clock)

    assert len(notifier.sent) == 1


def test_a_message_that_mqtt_delivers_again_rings_once() -> None:
    clock, notifier = Clock(), Notifier()
    doorbell = Doorbell(notifier, now=clock.now)

    # Samma tryck levereras igen, också långt efteråt (till exempel efter en återanslutning).
    ring_messages(
        doorbell, [(1000.0, press("a-1")), (1000.1, press("a-1")), (1300.0, press("a-1"))], clock
    )

    assert len(notifier.sent) == 1


def test_two_presses_a_while_apart_ring_twice() -> None:
    clock, notifier = Clock(), Notifier()
    doorbell = Doorbell(notifier, now=clock.now)

    ring_messages(
        doorbell,
        [(1000.0, press("a-1")), (1000.0 + RING_COOLDOWN_SECONDS + 25, press("a-2"))],
        clock,
    )

    assert len(notifier.sent) == 2


def test_a_ring_without_an_id_still_rings_once() -> None:
    clock, notifier = Clock(), Notifier()
    doorbell = Doorbell(notifier, now=clock.now)

    ring_messages(doorbell, [(1000.0, b""), (1000.5, b"ring"), (1001.0, b"")], clock)

    assert len(notifier.sent) == 1


def test_an_oversized_message_is_ignored() -> None:
    clock, notifier = Clock(), Notifier()
    doorbell = Doorbell(notifier, now=clock.now)

    ring_messages(doorbell, [(1000.0, press("x" * 1000))], clock)

    assert notifier.sent == []


# --- Stillbilden ---


def test_the_still_is_erased_once_the_notice_is_sent() -> None:
    clock, notifier, camera = Clock(), Notifier(), Camera()
    doorbell = Doorbell(notifier, camera=camera, now=clock.now)

    ring_messages(doorbell, [(1000.0, press("a-1"))], clock)

    assert notifier.sent[0][2] == STILL
    assert camera.stills == [bytearray()]


def test_the_still_is_erased_even_when_the_notice_fails() -> None:
    clock, camera = Clock(), Camera()
    doorbell = Doorbell(Notifier(error=OSError("ntfy svarar inte")), camera=camera, now=clock.now)

    ring_messages(doorbell, [(1000.0, press("a-1"))], clock)

    assert camera.stills == [bytearray()]


def test_without_a_camera_answer_the_notice_is_sent_without_a_still() -> None:
    clock, notifier = Clock(), Notifier()
    doorbell = Doorbell(notifier, camera=Camera(broken=True), now=clock.now)

    ring_messages(doorbell, [(1000.0, press("a-1"))], clock)

    assert notifier.sent == [(RING_TITLE, notifier.sent[0][1], None)]


# --- Radarn vid dörren ---


def test_the_radar_only_sends_a_text_notice_and_never_touches_the_camera() -> None:
    clock, notifier = Clock(), Notifier()
    doorbell = Doorbell(notifier, camera=UntouchableCamera(), now=clock.now)

    async def scenario() -> None:
        doorbell.handle(RADAR_TOPIC, b"ON")
        await settle(doorbell)

    asyncio.run(scenario())

    assert notifier.sent == [(RADAR_TITLE, RADAR_MESSAGE, None)]
    assert doorbell.view_seconds_left() == 0
    with pytest.raises(ViewClosed):
        asyncio.run(anext(doorbell.frames()))


def test_the_radar_notifies_at_most_every_few_minutes_and_only_on_presence() -> None:
    clock, notifier = Clock(), Notifier()
    doorbell = Doorbell(notifier, now=clock.now)

    async def scenario() -> None:
        for at, payload in [
            (1000.0, b'{"narvaro": true}'),
            (1010.0, b"ON"),
            (1400.0, b"OFF"),
            (1401.0, b'{"narvaro": false}'),
            (1402.0, b"true"),
        ]:
            clock.time = at
            doorbell.handle(RADAR_TOPIC, payload)
            await settle(doorbell)

    asyncio.run(scenario())

    assert len(notifier.sent) == 2


# --- Fönstret då kameran visas ---


def test_there_is_no_stream_without_a_ring() -> None:
    clock, camera = Clock(), Camera()
    doorbell = Doorbell(Notifier(), camera=camera, now=clock.now)

    with pytest.raises(ViewClosed):
        asyncio.run(anext(doorbell.frames()))
    assert camera.streams_started == 0


def collect(doorbell: Doorbell, clock: Clock, *, close_after: int | None = None) -> list[bytes]:
    async def scenario() -> list[bytes]:
        got = []
        async with contextlib.aclosing(doorbell.frames()) as frames:
            async for frame in frames:
                got.append(frame)
                if close_after is not None and len(got) == close_after:
                    clock.time += VIEW_SECONDS
        return got

    return asyncio.run(scenario())


def test_a_ring_opens_the_stream_for_two_minutes() -> None:
    clock, camera = Clock(), Camera()
    doorbell = Doorbell(Notifier(), camera=camera, now=clock.now)

    ring_messages(doorbell, [(1000.0, press("a-1"))], clock)
    clock.time = 1000.0 + VIEW_SECONDS - 1

    frames = collect(doorbell, clock)
    # En mottagare som inte hinner med får hoppa över bilder, men ser den senaste.
    assert frames[0] == b"bild 0"
    assert frames[-1] == b"bild 2"
    assert not camera.streaming


def test_the_stream_ends_and_the_camera_disconnects_when_the_window_closes() -> None:
    clock, camera = Clock(), Camera(frames=100)
    doorbell = Doorbell(Notifier(), camera=camera, now=clock.now)
    doorbell.open_view()

    assert len(collect(doorbell, clock, close_after=2)) == 2
    assert not camera.streaming


def test_the_stream_is_closed_after_two_minutes() -> None:
    clock, camera = Clock(), Camera()
    doorbell = Doorbell(Notifier(), camera=camera, now=clock.now)

    ring_messages(doorbell, [(1000.0, press("a-1"))], clock)
    clock.time = 1000.0 + VIEW_SECONDS

    with pytest.raises(ViewClosed):
        asyncio.run(anext(doorbell.frames()))
    assert camera.streams_started == 0


def test_show_the_door_opens_the_window_without_a_ring() -> None:
    clock, notifier = Clock(), Notifier()
    doorbell = Doorbell(notifier, camera=Camera(), now=clock.now)

    doorbell.open_view()

    assert doorbell.view_seconds_left() == VIEW_SECONDS
    assert notifier.sent == []


def test_at_most_two_can_watch_at_once() -> None:
    clock = Clock()
    doorbell = Doorbell(Notifier(), camera=Camera(frames=100), now=clock.now)
    doorbell.open_view()

    async def scenario() -> None:
        first, second = doorbell.frames(), doorbell.frames()
        await anext(first)
        await anext(second)
        with pytest.raises(TooManyViewers):
            await anext(doorbell.frames())
        await first.aclose()
        await anext(doorbell.frames())
        await second.aclose()

    asyncio.run(scenario())


# --- Dörrlampan ---


class Lights:
    def __init__(self) -> None:
        self.lit: list[tuple[str, float]] = []

    def light_for(self, friendly_name: str, seconds: float) -> None:
        self.lit.append((friendly_name, seconds))


def test_a_ring_lights_the_door_lamp_for_30_seconds() -> None:
    clock, notifier, lights = Clock(), Notifier(), Lights()
    doorbell = Doorbell(notifier, lamp="Dörrlampan", now=clock.now)
    doorbell.light_lamp_with(lights)

    ring_messages(doorbell, [(1000.0, press("a-1"))], clock)

    assert lights.lit == [("Dörrlampan", LAMP_SECONDS)]
    assert len(notifier.sent) == 1


def test_without_a_door_lamp_no_lamp_is_lit() -> None:
    clock, lights = Clock(), Lights()
    doorbell = Doorbell(Notifier(), now=clock.now)
    doorbell.light_lamp_with(lights)

    ring_messages(doorbell, [(1000.0, press("a-1"))], clock)

    assert lights.lit == []


# --- MQTT ---


class FakeBroker:
    """En broker med ett meddelande att leverera, som minns allt kärnan skickar."""

    def __init__(self, incoming: list[tuple[str, bytes]]) -> None:
        self.incoming = incoming
        self.subscribed: list[str] = []
        self.published: list[tuple[str, bytes]] = []

    @contextlib.asynccontextmanager
    async def connect(self) -> AsyncIterator[MqttConnection]:
        yield self

    async def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)

    async def publish(self, topic: str, payload: bytes) -> None:
        self.published.append((topic, payload))

    async def messages(self) -> AsyncIterator[tuple[str, bytes]]:
        for message in self.incoming:
            yield message
        await asyncio.Event().wait()


def test_the_doorbell_listens_on_the_door_topics_and_rings_on_a_press() -> None:
    clock, notifier = Clock(), Notifier()
    doorbell = Doorbell(notifier, now=clock.now)
    broker = FakeBroker([(RING_TOPIC, press("a-1")), (RING_TOPIC, press("a-1"))])

    async def scenario() -> None:
        task = asyncio.create_task(doorbell.run(broker.connect))
        for _ in range(500):
            await asyncio.sleep(0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert broker.subscribed == [RING_TOPIC, RADAR_TOPIC]
    assert len(notifier.sent) == 1
    assert broker.published == []


def test_the_test_command_publishes_a_ring_like_the_button() -> None:
    broker = FakeBroker([])

    asyncio.run(simulate_ring(broker.connect))

    [(topic, payload)] = broker.published
    assert topic == RING_TOPIC
    assert json.loads(payload)["tryck"].startswith("test-")


def test_the_speaker_fetches_the_sound_from_this_computer() -> None:
    assert (
        sound_url("192.0.2.20", 8000, "192.0.2.5") == "http://192.0.2.5:8000/api/dorr/dingdong.mp3"
    )
    assert sound_url("192.0.2.20", 8000, "2001:db8::5") == (
        "http://[2001:db8::5]:8000/api/dorr/dingdong.mp3"
    )
    # Utan inställd adress används datorns egen adress mot högtalaren.
    assert sound_url("127.0.0.1", 8000) == "http://127.0.0.1:8000/api/dorr/dingdong.mp3"
