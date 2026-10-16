import pytest

from homecontrol.connectors import LampCommand
from homecontrol.fusion import Occupancy, Presence
from homecontrol.home import DEFAULT_OFF_AFTER_EMPTY_SECONDS as OFF_AFTER_EMPTY_SECONDS
from homecontrol.home import RoomSettings
from homecontrol.rules import RoomState, evaluate
from homes import home_with_settings, one_room_home

HOME = one_room_home()
ROOM = HOME.rooms[0].id
LAMP = HOME.lamps[0].id


def rooms(*, vacant_since: float | None, lux: float | None = 10) -> dict[str, RoomState]:
    if vacant_since is None:
        return {ROOM: RoomState(Presence(Occupancy.OCCUPIED, 0), lux)}
    return {ROOM: RoomState(Presence(Occupancy.VACANT, vacant_since), lux)}


def test_is_pure_same_input_gives_same_output() -> None:
    first = evaluate(HOME, rooms(vacant_since=None), {LAMP: False}, now=5)
    second = evaluate(HOME, rooms(vacant_since=None), {LAMP: False}, now=5)

    assert first == second


def test_turns_on_below_30_lux_but_not_at_30() -> None:
    dark = evaluate(HOME, rooms(vacant_since=None, lux=29.9), {LAMP: False}, now=0)
    bright = evaluate(HOME, rooms(vacant_since=None, lux=30), {LAMP: False}, now=0)

    assert dark.commands == (LampCommand(LAMP, on=True, reason=dark.explanations[LAMP]),)
    assert bright.commands == ()


def test_wakes_when_empty_room_should_be_switched_off() -> None:
    result = evaluate(HOME, rooms(vacant_since=100), {LAMP: True}, now=150)

    assert result.commands == ()
    assert result.wake_at == 100 + OFF_AFTER_EMPTY_SECONDS


def test_switches_off_when_delay_has_passed() -> None:
    result = evaluate(
        HOME, rooms(vacant_since=100), {LAMP: True}, now=100 + OFF_AFTER_EMPTY_SECONDS
    )

    assert [command.on for command in result.commands] == [False]
    assert result.wake_at is None


def test_nothing_to_wait_for_when_lamp_is_off() -> None:
    result = evaluate(HOME, rooms(vacant_since=0), {LAMP: False}, now=1)

    assert result.commands == ()
    assert result.wake_at is None
    assert result.explanations[LAMP] == "Släckt: Vardagsrum är tomt."


def test_short_delays_are_explained_in_seconds() -> None:
    settings = RoomSettings(off_after_empty_seconds=10)
    home = home_with_settings(settings)

    result = evaluate(home, rooms(vacant_since=100), {LAMP: True}, now=105)

    assert "släcks när det varit tomt i 10 sekunder" in result.explanations[LAMP]


@pytest.mark.parametrize("on", [True, False])
def test_unknown_room_leaves_the_lamp_as_it_is(on: bool) -> None:
    unknown = {ROOM: RoomState(Presence(Occupancy.UNKNOWN, 0), lux=5)}

    result = evaluate(HOME, unknown, {LAMP: on}, now=24 * 60 * 60)

    assert result.commands == ()
    assert result.wake_at is None
    assert "okänt" in result.explanations[LAMP]
