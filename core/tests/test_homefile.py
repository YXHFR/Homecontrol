"""Hemfilen: export och import till en läsbar fil, och exempelhemmet."""

import json
from importlib import resources

import pytest

from homecontrol.home import Capability, LampProfile, WallKind, ZoneKind
from homecontrol.homefile import HomeFileError, dumps, example_home, loads
from homes import demo_home

EXAMPLE_FILE = resources.files("homecontrol").joinpath("example_home.json").read_text("utf-8")


def test_export_and_import_give_the_same_model() -> None:
    home = example_home()

    assert loads(dumps(home)) == home


def test_example_file_is_exactly_what_export_writes() -> None:
    """Exempelfilen ändras inte av att importeras och exporteras igen."""
    assert dumps(loads(EXAMPLE_FILE)) == EXAMPLE_FILE


def test_file_is_readable_json_with_points_on_one_line() -> None:
    text = dumps(demo_home())

    assert text.startswith('{\n  "format": "homecontrol-hem",\n  "version": 1,')
    assert "[0.0, 0.0],\n" in text
    assert json.loads(text)["home"]["floors"][0]["rooms"][0]["walls"] == ["gips"] * 4


def test_example_home_uses_every_concept_on_two_floors() -> None:
    home = example_home()
    rooms = home.rooms
    zones = [zone for room in rooms for zone in room.zones]
    nodes = [node for room in rooms for node in room.nodes]

    assert len(home.floors) == 2
    assert {kind for room in rooms for kind in room.walls} == set(WallKind)
    assert {zone.kind for zone in zones} >= {ZoneKind.STILLHET, ZoneKind.IGNORERA, ZoneKind.ENTRE}
    assert {cap for node in nodes for cap in node.capabilities} == set(Capability)
    assert any(node.mirrored for node in nodes)
    assert all(floor.signal_sources for floor in home.floors)
    assert any(lamp.zone_id is not None for lamp in home.lamps)
    assert {lamp.profile for lamp in home.lamps} == set(LampProfile)
    assert any(room.settings != rooms[0].settings for room in rooms)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("inte json", "inte giltig JSON"),
        ("[]", "inte en hemfil"),
        ('{"format": "något annat"}', "inte en hemfil"),
        ('{"format": "homecontrol-hem", "version": 2, "home": {}}', "version 2"),
        ('{"format": "homecontrol-hem", "version": 1, "home": {}, "extra": 1}', "extra"),
    ],
)
def test_broken_files_are_refused_with_a_reason(text: str, message: str) -> None:
    with pytest.raises(HomeFileError, match=message):
        loads(text)


def test_invalid_home_says_where_the_error_is() -> None:
    document = json.loads(dumps(demo_home()))
    document["home"]["floors"][0]["rooms"][0]["polygon"][1] = [-1, 0]

    with pytest.raises(HomeFileError) as error:
        loads(json.dumps(document))

    assert "floors.0.rooms.0.polygon.1" in str(error.value)
    assert "mellan 0 och 1000" in str(error.value)
