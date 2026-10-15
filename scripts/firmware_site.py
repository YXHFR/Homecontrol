#!/usr/bin/env python3
"""Bygger webbinstalleraren för rumskortens firmware (ESP Web Tools).

Användning:
    python3 scripts/firmware_site.py rooms
        Skriver rummen i firmware/rooms.txt som en JSON-lista (för CI).

    python3 scripts/firmware_site.py site --builds DIR --out DIR \\
            --version VERSION --source URL
        Lägger ihop färdiga firmware-filer till en sida för GitHub Pages.
        DIR ska innehålla <rum>.factory.bin (hela kortet, för installation
        över USB) och <rum>.ota.bin (bara appen, för uppdatering över
        nätverket) för varje rum i rooms.txt.

Sidans layout:
    index.html, info.json        installationssidan och rumslistan
    manifest-<rum>.json          läses av ESP Web Tools och av kortet självt
    firmware/<rum>.bin           hela kortet
    firmware/<rum>.ota.bin       bara appen

Skriptet använder bara standardbiblioteket.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROOMS_FILE = REPO / "firmware" / "rooms.txt"
INSTALLER_DIR = REPO / "firmware" / "installer"

HOSTNAME_PREFIX = "homecontrol-"
# ESPHome tillåter högst 31 tecken i namnet.
MAX_ROOM_LENGTH = 31 - len(HOSTNAME_PREFIX)
ROOM_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
CHIP_FAMILY = "ESP32-S3"


def validate_room(room: str) -> str | None:
    """Returnerar ett felmeddelande, eller None om rumsnamnet går att använda."""
    if not ROOM_NAME.fullmatch(room):
        return f"{room!r}: bara a–z, 0–9 och enstaka bindestreck mellan dem"
    if len(room) > MAX_ROOM_LENGTH:
        return f"{room!r}: högst {MAX_ROOM_LENGTH} tecken"
    return None


def parse_rooms(text: str) -> list[str]:
    """Läser rumslistan. Tomma rader och rader som börjar med # hoppas över."""
    rooms: list[str] = []
    errors: list[str] = []
    for line in text.splitlines():
        room = line.strip()
        if not room or room.startswith("#"):
            continue
        if error := validate_room(room):
            errors.append(error)
        elif room in rooms:
            errors.append(f"{room!r}: finns redan")
        else:
            rooms.append(room)
    if errors:
        raise ValueError("Felaktiga rum i rooms.txt:\n  " + "\n  ".join(errors))
    if not rooms:
        raise ValueError("rooms.txt innehåller inga rum")
    return rooms


def manifest(room: str, version: str, ota_md5: str, source: str) -> dict[str, object]:
    """Manifest för ett rum.

    ESP Web Tools läser "parts" vid installation över USB. Kortet läser "ota"
    när det letar efter uppdateringar. Sökvägarna är relativa till manifestet.
    """
    return {
        "name": f"Homecontrol {room}",
        "version": version,
        # Vid ny installation får man välja att radera kortet. Vid uppdatering
        # raderas inget, så Wi-Fi och API-nyckeln finns kvar.
        "new_install_prompt_erase": True,
        "builds": [
            {
                "chipFamily": CHIP_FAMILY,
                "parts": [{"path": f"firmware/{room}.bin", "offset": 0}],
                "ota": {
                    "path": f"firmware/{room}.ota.bin",
                    "md5": ota_md5,
                    "summary": f"Homecontrol {version}",
                    "release_url": source,
                },
            }
        ],
    }


def build_site(rooms: list[str], builds: Path, out: Path, version: str, source: str) -> None:
    """Skriver hela sidan till out. Se layouten överst i filen."""
    missing = [
        f"{room}.{kind}.bin"
        for room in rooms
        for kind in ("factory", "ota")
        if not (builds / f"{room}.{kind}.bin").is_file()
    ]
    if missing:
        raise FileNotFoundError("Firmware saknas: " + ", ".join(missing))

    (out / "firmware").mkdir(parents=True, exist_ok=True)
    for room in rooms:
        shutil.copyfile(builds / f"{room}.factory.bin", out / "firmware" / f"{room}.bin")
        ota = builds / f"{room}.ota.bin"
        shutil.copyfile(ota, out / "firmware" / f"{room}.ota.bin")
        # ESPHome kräver md5. Den skyddar mot trasig överföring; äktheten kommer från https.
        ota_md5 = hashlib.md5(ota.read_bytes(), usedforsecurity=False).hexdigest()
        text = json.dumps(manifest(room, version, ota_md5, source), ensure_ascii=False, indent=2)
        (out / f"manifest-{room}.json").write_text(text + "\n", encoding="utf-8")

    info = {"version": version, "source": source, "rooms": rooms}
    (out / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n")
    for page in INSTALLER_DIR.iterdir():
        if page.is_file():
            shutil.copyfile(page, out / page.name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("rooms", help="skriv rummen som JSON")
    site = commands.add_parser("site", help="bygg sidan för GitHub Pages")
    site.add_argument("--builds", type=Path, required=True)
    site.add_argument("--out", type=Path, required=True)
    site.add_argument("--version", required=True)
    site.add_argument("--source", required=True, help="länk till källkoden för just denna version")
    args = parser.parse_args(argv)

    try:
        rooms = parse_rooms(ROOMS_FILE.read_text(encoding="utf-8"))
        if args.command == "rooms":
            print(json.dumps(rooms))
        else:
            build_site(rooms, args.builds, args.out, args.version, args.source)
    except (ValueError, FileNotFoundError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
