#!/usr/bin/env python3
"""Bygger webbinstalleraren för rumskortens firmware (ESP Web Tools).

Det finns en firmware per rum och radar (`RADARS`): kortet med LD2450
(firmware/homecontrol.yaml) och kortet med LD2461
(firmware/homecontrol-ld2461.yaml). Firmwarens namn är rummet, med
radarns förled för LD2461, till exempel "kok" och "ld2461-kok".

Användning:
    python3 scripts/firmware_site.py rooms
        Skriver rummen i firmware/rooms.txt som en JSON-lista.

    python3 scripts/firmware_site.py builds
        Skriver alla firmware att bygga som en JSON-lista (för CI), en per
        rum och radar: {"room", "radar", "config", "name"}.

    python3 scripts/firmware_site.py site --builds DIR --out DIR \\
            --version VERSION --source URL
        Lägger ihop färdiga firmware-filer till webbinstalleraren.
        DIR ska innehålla <namn>.factory.bin (hela kortet, för installation
        över USB) och <namn>.ota.bin (bara appen, för uppdatering över
        nätverket) för varje rum i rooms.txt och varje radar.

Sidans layout:
    index.html, info.json        installationssidan, rummen och radarerna
    manifest-<namn>.json         läses av ESP Web Tools och av kortet självt
    firmware/<namn>.bin          hela kortet
    firmware/<namn>.ota.bin      bara appen

Skriptet använder bara standardbiblioteket.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROOMS_FILE = REPO / "firmware" / "rooms.txt"
INSTALLER_DIR = REPO / "firmware" / "installer"

HOSTNAME_PREFIX = "homecontrol-"
# ESPHome tillåter högst 31 tecken i namnet.
MAX_ROOM_LENGTH = 31 - len(HOSTNAME_PREFIX)
ROOM_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
CHIP_FAMILY = "ESP32-S3"


@dataclass(frozen=True)
class Radar:
    """En firmware-variant: vilken radar kortet har."""

    id: str
    label: str
    """Som det visas i webbinstalleraren."""
    config: str
    """ESPHome-paketet i firmware/."""
    prefix: str
    """Förledet i firmwarens namn. Tomt för LD2450, så att dess filer heter som förut."""

    def name(self, room: str) -> str:
        return f"{self.prefix}{room}"


RADARS = (
    Radar("ld2450", "LD2450 (upp till 3 personer)", "homecontrol.yaml", ""),
    Radar(
        "ld2461",
        "LD2461 (upp till 5 personer enligt tillverkaren, inte provad än)",
        "homecontrol-ld2461.yaml",
        "ld2461-",
    ),
)
LD2450 = RADARS[0]


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


def firmware_builds(rooms: list[str]) -> list[dict[str, str]]:
    """Allt som ska byggas: en firmware per rum och radar."""
    return [
        {"room": room, "radar": radar.id, "config": radar.config, "name": radar.name(room)}
        for radar in RADARS
        for room in rooms
    ]


def manifest(
    room: str, version: str, ota_md5: str, source: str, radar: Radar = LD2450
) -> dict[str, object]:
    """Manifest för ett rum med en viss radar.

    ESP Web Tools läser "parts" vid installation över USB. Kortet läser "ota"
    när det letar efter uppdateringar. Sökvägarna är relativa till manifestet.
    Kortet hämtar bara uppdateringar för sin egen radar (se `update` i
    firmware-paketen).
    """
    name = radar.name(room)
    title = f"Homecontrol {room}" if radar is LD2450 else f"Homecontrol {room} ({radar.id.upper()})"
    return {
        "name": title,
        "version": version,
        # Vid ny installation får man välja att radera kortet. Vid uppdatering
        # raderas inget, så Wi-Fi och API-nyckeln finns kvar.
        "new_install_prompt_erase": True,
        "builds": [
            {
                "chipFamily": CHIP_FAMILY,
                "parts": [{"path": f"firmware/{name}.bin", "offset": 0}],
                "ota": {
                    "path": f"firmware/{name}.ota.bin",
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
        f"{radar.name(room)}.{kind}.bin"
        for radar in RADARS
        for room in rooms
        for kind in ("factory", "ota")
        if not (builds / f"{radar.name(room)}.{kind}.bin").is_file()
    ]
    if missing:
        raise FileNotFoundError("Firmware saknas: " + ", ".join(missing))

    (out / "firmware").mkdir(parents=True, exist_ok=True)
    for radar in RADARS:
        for room in rooms:
            name = radar.name(room)
            shutil.copyfile(builds / f"{name}.factory.bin", out / "firmware" / f"{name}.bin")
            ota = builds / f"{name}.ota.bin"
            shutil.copyfile(ota, out / "firmware" / f"{name}.ota.bin")
            # ESPHome kräver md5. Den skyddar mot trasig överföring; äktheten kommer från https.
            ota_md5 = hashlib.md5(ota.read_bytes(), usedforsecurity=False).hexdigest()
            data = manifest(room, version, ota_md5, source, radar)
            text = json.dumps(data, ensure_ascii=False, indent=2)
            (out / f"manifest-{name}.json").write_text(text + "\n", encoding="utf-8")

    info = {
        "version": version,
        "source": source,
        "rooms": rooms,
        "radars": [{"id": r.id, "label": r.label, "prefix": r.prefix} for r in RADARS],
    }
    (out / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n")
    for page in INSTALLER_DIR.iterdir():
        if page.is_file():
            shutil.copyfile(page, out / page.name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("rooms", help="skriv rummen som JSON")
    commands.add_parser("builds", help="skriv allt som ska byggas som JSON")
    site = commands.add_parser("site", help="bygg webbinstalleraren")
    site.add_argument("--builds", type=Path, required=True)
    site.add_argument("--out", type=Path, required=True)
    site.add_argument("--version", required=True)
    site.add_argument("--source", required=True, help="länk till källkoden för just denna version")
    args = parser.parse_args(argv)

    try:
        rooms = parse_rooms(ROOMS_FILE.read_text(encoding="utf-8"))
        if args.command == "rooms":
            print(json.dumps(rooms))
        elif args.command == "builds":
            print(json.dumps(firmware_builds(rooms)))
        else:
            build_site(rooms, args.builds, args.out, args.version, args.source)
    except (ValueError, FileNotFoundError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
