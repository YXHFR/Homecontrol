import hashlib
import json
from pathlib import Path

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


def test_manifest_has_usb_install_and_ota_parts() -> None:
    data = manifest("kok", "1.2.3", "abc", "https://example.invalid/src")

    assert data["name"] == "Homecontrol kok"
    assert data["version"] == "1.2.3"
    assert data["builds"] == [
        {
            "chipFamily": "ESP32-S3",
            "parts": [{"path": "firmware/kok.bin", "offset": 0}],
            "ota": {
                "path": "firmware/kok.ota.bin",
                "md5": "abc",
                "summary": "Homecontrol 1.2.3",
                "release_url": "https://example.invalid/src",
            },
        }
    ]


def test_ld2461_manifest_points_at_its_own_files() -> None:
    ld2461 = next(radar for radar in RADARS if radar.id == "ld2461")

    data = manifest("kok", "1.2.3", "abc", "src", ld2461)

    assert data["name"] == "Homecontrol kok (LD2461)"
    assert data["builds"] == [
        {
            "chipFamily": "ESP32-S3",
            "parts": [{"path": "firmware/ld2461-kok.bin", "offset": 0}],
            "ota": {
                "path": "firmware/ld2461-kok.ota.bin",
                "md5": "abc",
                "summary": "Homecontrol 1.2.3",
                "release_url": "src",
            },
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


@pytest.mark.parametrize("radar", RADARS, ids=lambda radar: radar.id)
def test_each_radar_package_fetches_updates_for_its_own_radar(radar: firmware_site.Radar) -> None:
    """Ett kort med LD2461 får aldrig firmware för LD2450, och tvärtom."""
    config = (REPO / "firmware" / radar.config).read_text(encoding="utf-8")

    assert f"source: ${{update_source}}/manifest-{radar.prefix}${{rum}}.json" in config


def write_builds(directory: Path, rooms: list[str]) -> None:
    directory.mkdir(exist_ok=True)
    for radar in RADARS:
        for room in rooms:
            name = radar.name(room)
            (directory / f"{name}.factory.bin").write_bytes(f"factory-{name}".encode())
            (directory / f"{name}.ota.bin").write_bytes(f"ota-{name}".encode())


def test_build_site_writes_firmware_manifests_and_pages(tmp_path: Path) -> None:
    write_builds(tmp_path / "builds", ["kok", "hall"])
    out = tmp_path / "site"

    build_site(["kok", "hall"], tmp_path / "builds", out, "1.2.3", "https://example.invalid/src")

    assert (out / "firmware" / "kok.bin").read_bytes() == b"factory-kok"
    assert (out / "firmware" / "hall.ota.bin").read_bytes() == b"ota-hall"
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
    assert (out / "index.html").is_file()


def test_manifest_paths_point_at_written_files(tmp_path: Path) -> None:
    write_builds(tmp_path / "builds", ["kok"])
    out = tmp_path / "site"

    build_site(["kok"], tmp_path / "builds", out, "1", "src")

    manifest_file = out / "manifest-kok.json"
    build = json.loads(manifest_file.read_text())["builds"][0]
    factory = manifest_file.parent / build["parts"][0]["path"]
    ota = manifest_file.parent / build["ota"]["path"]
    assert factory.read_bytes() == b"factory-kok"
    assert build["ota"]["md5"] == hashlib.md5(ota.read_bytes(), usedforsecurity=False).hexdigest()


@pytest.mark.parametrize("missing", ["hall.factory.bin", "hall.ota.bin", "ld2461-hall.ota.bin"])
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
