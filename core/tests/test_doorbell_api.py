"""Sidan Dörren: kameran visas bara genom kärnan, inom fönstret och för kärnans egna sidor."""

from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi.testclient import TestClient

from homecontrol.app import create_app
from homecontrol.doorbell import Doorbell
from homecontrol.doorbell_api import DEFAULT_SOUND

FOREIGN = {"Origin": "http://example.com"}
TEST_HOST = frozenset({"testserver"})
"""TestClient anropar kärnan som http://testserver."""


class Notifier:
    async def send(self, title: str, message: str, image: bytearray | None = None) -> None:
        pass


class Camera:
    def __init__(self) -> None:
        self.streams_started = 0

    async def snapshot(self) -> bytearray | None:
        return bytearray(b"bild")

    async def frames(self) -> AsyncGenerator[bytes]:
        self.streams_started += 1
        for n in range(2):
            yield f"bild {n}".encode()


def client_with_camera() -> tuple[TestClient, Camera]:
    camera = Camera()
    return TestClient(
        create_app(allowed_hosts=TEST_HOST, doorbell=Doorbell(Notifier(), camera=camera))
    ), camera


def test_without_a_doorbell_there_are_no_door_routes() -> None:
    client = TestClient(create_app())

    assert client.get("/api/dorr").status_code == 404
    assert client.get("/api/dorr/strom").status_code == 404
    assert client.get("/api/dorr/dingdong.mp3").status_code == 404


def test_the_window_is_closed_until_someone_rings_or_shows_the_door() -> None:
    client, camera = client_with_camera()

    assert client.get("/api/dorr").json() == {"camera": True, "open_seconds": 0}
    response = client.get("/api/dorr/strom")

    assert response.status_code == 403
    assert "2 minuter" in response.json()["detail"]
    assert camera.streams_started == 0


def test_show_the_door_opens_the_stream_for_two_minutes() -> None:
    client, camera = client_with_camera()

    assert client.post("/api/dorr/visa").json() == {"camera": True, "open_seconds": 120}
    response = client.get("/api/dorr/strom")

    assert response.status_code == 200
    assert response.headers["content-type"] == "multipart/x-mixed-replace; boundary=bild"
    assert response.headers["cache-control"] == "no-store"
    assert b"Content-Type: image/jpeg" in response.content
    assert b"bild 1" in response.content
    assert camera.streams_started == 1


def test_a_foreign_page_can_neither_open_the_window_nor_watch() -> None:
    client, camera = client_with_camera()

    assert client.post("/api/dorr/visa", headers=FOREIGN).status_code == 403
    assert client.get("/api/dorr").json()["open_seconds"] == 0
    client.post("/api/dorr/visa")
    assert client.get("/api/dorr/strom", headers=FOREIGN).status_code == 403
    assert client.get("/api/dorr/strom", headers={"Host": "evil.example"}).status_code == 403
    assert camera.streams_started == 0


def test_without_a_camera_there_is_nothing_to_show() -> None:
    client = TestClient(create_app(allowed_hosts=TEST_HOST, doorbell=Doorbell(Notifier())))

    client.post("/api/dorr/visa")

    assert client.get("/api/dorr").json()["camera"] is False
    assert client.get("/api/dorr/strom").status_code == 403


def test_the_speaker_can_fetch_the_sound_but_not_a_foreign_page() -> None:
    client, _ = client_with_camera()

    response = client.get("/api/dorr/dingdong.mp3", headers={"Host": "192.0.2.10:8000"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == DEFAULT_SOUND.read_bytes()
    assert client.get("/api/dorr/dingdong.mp3", headers={"Host": "evil.example"}).status_code == 403


def test_the_sound_file_can_be_replaced(tmp_path: Path) -> None:
    sound = tmp_path / "egen.mp3"
    sound.write_bytes(b"ID3 eget ljud")
    client = TestClient(
        create_app(allowed_hosts=TEST_HOST, doorbell=Doorbell(Notifier()), doorbell_sound=sound)
    )

    assert client.get("/api/dorr/dingdong.mp3").content == b"ID3 eget ljud"


def test_the_bundled_sound_is_an_mp3() -> None:
    data = DEFAULT_SOUND.read_bytes()

    # En MP3 börjar med en ID3-tagg eller direkt med en ramsynk (11 ettor).
    assert data.startswith(b"ID3") or (data[0] == 0xFF and data[1] & 0xE0 == 0xE0)
