"""Dörrklockans riktiga kopplingar: kameran, notiserna och högtalaren (se doorbell.py).

* `EsphomeCamera` hämtar bilder från dörrklockan genom ESPHome:s krypterade
  API. Kameran har ingen öppen webbserver, så bara den som har nyckeln (kärnan)
  kan se något, och kärnan ansluter bara medan en bild behövs.
* `NtfyNotifier` skickar push-notisen till ett ämne i ntfy, med stillbilden
  som bilaga.
* `ChromecastSpeaker` spelar dingdong på en Google Home-högtalare med fast IP.
  Högtalaren hämtar ljudet från kärnan (``/api/dorr/dingdong.mp3``).

Kärnans egna loggrader nämner aldrig ntfy-ämnet, nycklar eller adresser.
"""

import asyncio
import base64
import contextlib
import ipaddress
import logging
import socket
import time
import urllib.request
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from email.header import Header

import pychromecast
from aioesphomeapi.client import APIClient
from aioesphomeapi.core import APIConnectionError
from aioesphomeapi.model import CameraInfo, CameraState, EntityState

from homecontrol.doorbell import SOUND_PATH

logger = logging.getLogger(__name__)

CAMERA_NAME = "homecontrol-dorr"
"""Dörrklockans namn i ESPHome (firmware/dorrklocka.yaml). Kärnan ansluter bara till den."""
API_PORT = 6053
CAMERA_TIMEOUT_SECONDS = 5.0
"""Så länge kärnan väntar på att kameran ska ansluta eller skicka en bild."""
STREAM_RENEW_SECONDS = 4.0
"""ESPHome skickar ström i 5 sekunder per begäran, så den förnyas lite oftare."""
NTFY_TIMEOUT_SECONDS = 15.0
CAST_PORT = 8009
CAST_TIMEOUT_SECONDS = 10.0
PLAY_SECONDS = 4.0
"""Så länge dingdong får spela innan högtalarens volym återställs."""


class EsphomeCamera:
    """Kameran i dörrklockan, genom ESPHome:s krypterade API."""

    def __init__(self, host: str, key: bytes, *, port: int = API_PORT) -> None:
        self._host = host
        self._port = port
        self._psk = base64.b64encode(key).decode()

    async def snapshot(self) -> bytearray | None:
        try:
            async with self._connected() as (client, images):
                client.request_single_image()
                return bytearray(await asyncio.wait_for(images.get(), CAMERA_TIMEOUT_SECONDS))
        except (ConnectionError, TimeoutError) as error:
            logger.warning("Kameran i dörrklockan svarar inte (%s)", type(error).__name__)
            return None

    async def frames(self) -> AsyncGenerator[bytes]:
        async with self._connected() as (client, images):
            while True:
                client.request_image_stream()
                renew_at = time.monotonic() + STREAM_RENEW_SECONDS
                while (left := renew_at - time.monotonic()) > 0:
                    try:
                        yield await asyncio.wait_for(images.get(), max(left, 0.1))
                    except TimeoutError:
                        break

    @contextlib.asynccontextmanager
    async def _connected(self) -> AsyncIterator[tuple[APIClient, asyncio.Queue[bytes]]]:
        """Ansluter till kameran och kopplar ner när blocket är klart."""
        client = APIClient(
            self._host,
            self._port,
            None,
            client_info="homecontrol-dorr",
            noise_psk=self._psk,
            expected_name=CAMERA_NAME,
        )
        images: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)

        def on_state(state: EntityState) -> None:
            if isinstance(state, CameraState):
                if images.full():
                    images.get_nowait()
                images.put_nowait(state.data)

        try:
            await asyncio.wait_for(client.connect(login=True), CAMERA_TIMEOUT_SECONDS)
            entities, _ = await client.list_entities_services()
            if not any(isinstance(entity, CameraInfo) for entity in entities):
                raise ConnectionError("dörrklockan har ingen kamera")
            client.subscribe_states(on_state)
            yield client, images
        except APIConnectionError as error:
            raise ConnectionError(type(error).__name__) from None
        finally:
            await client.disconnect()


class NtfyNotifier:
    """Push-notiser genom ntfy (https://ntfy.sh eller en egen ntfy-server)."""

    def __init__(self, url: str, topic: str, token: str | None = None) -> None:
        self._endpoint = f"{url}/{topic}"
        self._token = token

    async def send(self, title: str, message: str, image: bytearray | None = None) -> None:
        await asyncio.to_thread(self._send, title, message, image)

    def _send(self, title: str, message: str, image: bytearray | None) -> None:
        # Svenska tecken skickas kodade (RFC 2047), som ntfy förstår.
        headers = {"Title": _encoded(title), "Tags": "bell", "Priority": "high"}
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"
        if image:
            headers["Message"] = _encoded(message)
            headers["Filename"] = "dorren.jpg"
            request = urllib.request.Request(  # noqa: S310 (adressen är kontrollerad i config.py)
                self._endpoint, data=image, headers=headers, method="PUT"
            )
        else:
            request = urllib.request.Request(  # noqa: S310
                self._endpoint, data=message.encode(), headers=headers, method="POST"
            )
        with urllib.request.urlopen(request, timeout=NTFY_TIMEOUT_SECONDS):  # noqa: S310
            pass


def _encoded(text: str) -> str:
    return text if text.isascii() else Header(text, "utf-8").encode()


class ChromecastSpeaker:
    """En Google Home-högtalare (Chromecast) med fast IP-adress.

    Högtalaren hämtar ljudet från kärnan på `port`, på `media_host` eller annars
    datorns egen adress mot högtalaren. Högtalarens volym sätts till `volume`
    procent medan dingdong spelar och återställs sedan.
    """

    def __init__(self, host: str, volume: int, port: int, media_host: str | None = None) -> None:
        self._host = host
        self._volume = volume / 100
        self._port = port
        self._media_host = media_host

    async def play(self) -> None:
        await asyncio.to_thread(self._play)

    def _play(self) -> None:
        cast = pychromecast.get_chromecast_from_host(
            (self._host, CAST_PORT, uuid.uuid5(uuid.NAMESPACE_URL, self._host), None, None),
            tries=1,
            timeout=CAST_TIMEOUT_SECONDS,
        )
        try:
            cast.wait(timeout=CAST_TIMEOUT_SECONDS)
            receiver = cast.socket_client.receiver_controller
            before = None if cast.status is None else cast.status.volume_level
            receiver.set_volume(self._volume)
            media = cast.media_controller
            # Adressen räknas ut nu, så att ett nät som inte är uppe vid start inte stoppar kärnan.
            url = sound_url(self._host, self._port, self._media_host)
            media.play_media(url, "audio/mpeg", title="Dingdong", stream_type="BUFFERED")
            media.block_until_active(timeout=CAST_TIMEOUT_SECONDS)
            time.sleep(PLAY_SECONDS)
            if before is not None:
                receiver.set_volume(before)
        finally:
            cast.disconnect(timeout=CAST_TIMEOUT_SECONDS)


def sound_url(speaker_host: str, port: int, media_host: str | None = None) -> str:
    """Adressen som högtalaren hämtar dingdong från: datorns adress i hemnätet och kärnans port.

    Utan `media_host` används adressen som datorn själv skulle använda för
    att nå högtalaren.
    """
    host = media_host if media_host is not None else local_address(speaker_host)
    if ipaddress.ip_address(host).version == 6:
        host = f"[{host}]"
    return f"http://{host}:{port}{SOUND_PATH}"


def local_address(towards: str) -> str:
    """Datorns egen IP-adress mot `towards`. Inget skickas; bara vägen slås upp."""
    family = socket.AF_INET6 if ipaddress.ip_address(towards).version == 6 else socket.AF_INET
    with socket.socket(family, socket.SOCK_DGRAM) as probe:
        probe.connect((towards, 9))
        address: str = probe.getsockname()[0]
    return address
