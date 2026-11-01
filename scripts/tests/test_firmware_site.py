import http.client
import importlib.util
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import firmware_site
import pytest
from firmware_site import (
    RADARS,
    REPO,
    ROOMS_FILE,
    build_site,
    firmware_builds,
    main,
    manifest,
    parse_rooms,
    validate_room,
)

TOO_LONG = "x" * (firmware_site.MAX_ROOM_LENGTH + 1)


@pytest.mark.parametrize("room", ["kok", "sovrum-2", "a", "x" * firmware_site.MAX_ROOM_LENGTH])
def test_valid_room_names(room: str) -> None:
    assert validate_room(room) is None


@pytest.mark.parametrize(
    "room",
    ["kök", "Kok", "sov rum", "-kok", "kok-", "sov--rum", "kok_1", TOO_LONG, "", "ld2461-kok"],
)
def test_invalid_room_names(room: str) -> None:
    assert validate_room(room) is not None


def test_parse_rooms_skips_comments_and_blank_lines() -> None:
    assert parse_rooms("# rum\n\nkok\n  hall  \n") == ["kok", "hall"]


def test_parse_rooms_reports_every_bad_line() -> None:
    with pytest.raises(ValueError) as error:
        parse_rooms("kök\nkok\nkok\nSovrum\n")

    message = str(error.value)
    assert "'kök'" in message
    assert "'kok': finns redan" in message
    assert "'Sovrum'" in message


def test_parse_rooms_rejects_empty_list() -> None:
    with pytest.raises(ValueError, match="inga rum"):
        parse_rooms("# bara en kommentar\n")


def test_rooms_in_repo_are_valid() -> None:
    assert parse_rooms(ROOMS_FILE.read_text(encoding="utf-8"))


def test_manifest_has_only_the_usb_install() -> None:
    data = manifest("kok", "1.2.3")

    assert data["name"] == "Homecontrol kok"
    assert data["version"] == "1.2.3"
    assert data["builds"] == [
        {
            "chipFamily": "ESP32-S3",
            "parts": [{"path": "firmware/kok.bin", "offset": 0}],
        }
    ]


def test_ld2461_manifest_points_at_its_own_files() -> None:
    ld2461 = next(radar for radar in RADARS if radar.id == "ld2461")

    data = manifest("kok", "1.2.3", ld2461)

    assert data["name"] == "Homecontrol kok (LD2461)"
    assert data["builds"] == [
        {
            "chipFamily": "ESP32-S3",
            "parts": [{"path": "firmware/ld2461-kok.bin", "offset": 0}],
        }
    ]


def test_every_room_is_built_for_both_radars() -> None:
    assert firmware_builds(["kok", "hall"]) == [
        {"room": "kok", "radar": "ld2450", "config": "homecontrol.yaml", "name": "kok"},
        {"room": "hall", "radar": "ld2450", "config": "homecontrol.yaml", "name": "hall"},
        {
            "room": "kok",
            "radar": "ld2461",
            "config": "homecontrol-ld2461.yaml",
            "name": "ld2461-kok",
        },
        {
            "room": "hall",
            "radar": "ld2461",
            "config": "homecontrol-ld2461.yaml",
            "name": "ld2461-hall",
        },
    ]


@pytest.mark.parametrize("config", [radar.config for radar in RADARS] + ["common.yaml"])
def test_room_cards_never_fetch_firmware_over_the_network(config: str) -> None:
    """Rumskorten uppdateras bara över USB: ingen update, ota eller http_request."""
    text = (REPO / "firmware" / config).read_text(encoding="utf-8")

    assert not re.search(r"^(update|ota|http_request):", text, re.MULTILINE)
    assert "update_source" not in text


def write_builds(directory: Path, rooms: list[str]) -> None:
    directory.mkdir(exist_ok=True)
    for radar in RADARS:
        for room in rooms:
            name = radar.name(room)
            (directory / f"{name}.factory.bin").write_bytes(f"factory-{name}".encode())


def test_build_site_writes_firmware_manifests_and_pages(tmp_path: Path) -> None:
    write_builds(tmp_path / "builds", ["kok", "hall"])
    out = tmp_path / "site"

    build_site(["kok", "hall"], tmp_path / "builds", out, "1.2.3", "https://example.invalid/src")

    assert (out / "firmware" / "kok.bin").read_bytes() == b"factory-kok"
    assert not list(out.glob("firmware/*.ota.bin"))
    assert (out / "firmware" / "ld2461-kok.bin").read_bytes() == b"factory-ld2461-kok"
    assert (out / "manifest-ld2461-hall.json").is_file()
    info = json.loads((out / "info.json").read_text())
    assert info == {
        "version": "1.2.3",
        "source": "https://example.invalid/src",
        "rooms": ["kok", "hall"],
        "radars": [
            {"id": "ld2450", "label": RADARS[0].label, "prefix": ""},
            {"id": "ld2461", "label": RADARS[1].label, "prefix": "ld2461-"},
        ],
    }
    for page in ["index.html", "starta.py", "starta.bat", "starta.sh"]:
        assert (out / page).is_file(), page


def test_manifest_paths_point_at_written_files(tmp_path: Path) -> None:
    write_builds(tmp_path / "builds", ["kok"])
    out = tmp_path / "site"

    build_site(["kok"], tmp_path / "builds", out, "1", "src")

    manifest_file = out / "manifest-kok.json"
    build = json.loads(manifest_file.read_text())["builds"][0]
    path = build["parts"][0]["path"]
    # Relativ, så att sidan fungerar från vilken adress som helst (även localhost).
    assert not path.startswith("/") and "://" not in path
    assert (manifest_file.parent / path).read_bytes() == b"factory-kok"


@pytest.mark.parametrize("missing", ["hall.factory.bin", "ld2461-hall.factory.bin"])
def test_build_site_fails_when_firmware_is_missing(tmp_path: Path, missing: str) -> None:
    write_builds(tmp_path, ["kok", "hall"])
    (tmp_path / missing).unlink()

    with pytest.raises(FileNotFoundError, match=missing):
        build_site(["kok", "hall"], tmp_path, tmp_path / "site", "1", "src")


def test_main_prints_rooms_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["rooms"]) == 0

    assert json.loads(capsys.readouterr().out) == parse_rooms(ROOMS_FILE.read_text())


def test_main_prints_builds_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["builds"]) == 0

    rooms = parse_rooms(ROOMS_FILE.read_text())
    assert json.loads(capsys.readouterr().out) == firmware_builds(rooms)


def test_main_site_reports_missing_firmware(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["site", "--builds", str(tmp_path), "--out", str(tmp_path / "site")]

    assert main([*args, "--version", "1", "--source", "src"]) == 1

    assert "Firmware saknas" in capsys.readouterr().err


def test_main_reports_bad_rooms_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rooms_file = tmp_path / "rooms.txt"
    rooms_file.write_text("Kök\n", encoding="utf-8")
    monkeypatch.setattr(firmware_site, "ROOMS_FILE", rooms_file)

    assert main(["rooms"]) == 1

    assert "Felaktiga rum" in capsys.readouterr().err


def test_windows_start_script_has_crlf_line_endings() -> None:
    """cmd.exe läser .bat-filer fel med bara LF (se .gitattributes)."""
    data = (REPO / "firmware" / "installer" / "starta.bat").read_bytes()

    assert data.count(b"\n") == data.count(b"\r\n") > 0


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def get(port: int, path: str, host: str = "localhost") -> tuple[int | str, bytes]:
    """Filtypen och innehållet från servern på 127.0.0.1, eller felkoden."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path, headers={"Host": f"{host}:{port}"})
        response = connection.getresponse()
        if response.status != 200:
            return response.status, b""
        return response.getheader("Content-Type", ""), response.read()
    finally:
        connection.close()


def load_start_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "starta", REPO / "firmware" / "installer" / "starta.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_start_script_names_the_file_types_itself() -> None:
    """Windows register kan göra .js till text/plain; då körs inte ESP Web Tools."""
    extensions = load_start_script().Handler.extensions_map

    assert extensions[".js"].startswith("text/javascript")
    assert extensions[".json"] == "application/json"
    assert extensions[".html"].startswith("text/html")


def test_start_script_does_not_share_its_port() -> None:
    assert load_start_script().Server.allow_reuse_address is False


def test_start_script_serves_the_installer_on_localhost(tmp_path: Path) -> None:
    write_builds(tmp_path / "builds", ["kok"])
    out = tmp_path / "site"
    build_site(["kok"], tmp_path / "builds", out, "1", "src")
    (out / "esp-web-tools").mkdir()
    (out / "esp-web-tools" / "install-button.js").write_text("export {};\n")
    port = free_port()

    server = subprocess.Popen(
        [sys.executable, "-I", str(out / "starta.py"), "--no-browser", "--port", str(port)],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            if server.poll() is not None:
                pytest.fail("starta.py avslutades innan den svarade")
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("starta.py svarade inte")

        content_type, _ = get(port, "/esp-web-tools/install-button.js")
        # En modul körs bara om filtypen är JavaScript.
        assert isinstance(content_type, str)
        assert content_type.startswith("text/javascript")
        # Bara som localhost (mot DNS-rebinding), och ingen mapplistning.
        assert get(port, "/manifest-kok.json", host="elak.example")[0] == 403
        assert get(port, "/firmware/")[0] == 404
        _, body = get(port, "/manifest-kok.json")
        path = json.loads(body)["builds"][0]["parts"][0]["path"]
        assert get(port, "/" + path)[1] == b"factory-kok"
    finally:
        server.terminate()
        server.wait(timeout=5)
