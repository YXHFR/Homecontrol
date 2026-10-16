"""Regelmotorn som ren funktion. Beteendet i hela kärnan prövas i test_scenarios.py."""

from collections.abc import Mapping

import pytest

from homecontrol.connectors import LampCommand
from homecontrol.fusion import Occupancy, Presence
from homecontrol.home import DEFAULT_OFF_AFTER_EMPTY_SECONDS as OFF_AFTER_EMPTY_SECONDS
from homecontrol.home import Home, Lamp, Point, RoomSettings
from homecontrol.rules import (
    BRIGHT_SINCE,
    DEFAULT_RULES,
    MANUAL,
    Decision,
    LampState,
    Memory,
    RoomState,
    Situation,
    Switch,
    Verdict,
    adjusted_lux,
    duration,
    evaluate,
)
from homes import home_with_settings, one_room_home

HOME = one_room_home()
ROOM = HOME.rooms[0].id
LAMP = HOME.lamps[0].id
ON = LampState(on=True)
OFF = LampState(on=False)
UNKNOWN = LampState(on=None)


def occupied(lux: float | None = 10, memory: Memory | None = None) -> dict[str, RoomState]:
    return {ROOM: RoomState(Presence(Occupancy.OCCUPIED, 0), lux, memory or {})}


def vacant(
    since: float, lux: float | None = 10, memory: Memory | None = None
) -> dict[str, RoomState]:
    return {ROOM: RoomState(Presence(Occupancy.VACANT, since), lux, memory or {})}


def lamp(state: LampState) -> dict[str, LampState]:
    return {LAMP: state}


def home_with_lamp_light(lux: float) -> Home:
    """Enrumshemmet, där taklampan lyser upp ljussensorn med `lux` vid full styrka."""
    return home_with_settings(RoomSettings(), lamp_lux=lux)


def test_is_pure_same_input_gives_same_output() -> None:
    first = evaluate(HOME, occupied(), lamp(OFF), now=5)
    second = evaluate(HOME, occupied(), lamp(OFF), now=5)

    assert first == second


def test_turns_on_below_30_lux_but_not_at_30() -> None:
    dark = evaluate(HOME, occupied(lux=29.9), lamp(OFF), now=0)
    bright = evaluate(HOME, occupied(lux=30), lamp(OFF), now=0)

    assert dark.commands == (LampCommand(LAMP, on=True, reason=dark.explanations[ROOM]),)
    assert "29.9 lux, under 30" in dark.explanations[ROOM]
    assert bright.commands == ()


def test_switching_is_an_event_but_standing_still_is_not() -> None:
    switched = evaluate(HOME, occupied(lux=5), lamp(OFF), now=0)
    unchanged = evaluate(HOME, occupied(lux=5), lamp(ON), now=0)

    assert [event.text for event in switched.events] == [switched.explanations[ROOM]]
    assert unchanged.events == ()


def test_dims_before_switching_off_and_wakes_at_each_step() -> None:
    before_dim = evaluate(HOME, vacant(since=100), lamp(ON), now=150)
    dimming = evaluate(HOME, vacant(since=100), lamp(ON), now=100 + OFF_AFTER_EMPTY_SECONDS - 30)

    assert before_dim.commands == ()
    assert before_dim.wake_at == 100 + OFF_AFTER_EMPTY_SECONDS - 30
    [command] = dimming.commands
    assert (command.on, command.brightness) == (True, 50)
    assert dimming.wake_at == 100 + OFF_AFTER_EMPTY_SECONDS


def test_switches_off_when_delay_has_passed() -> None:
    dimmed = LampState(on=True, brightness=50)

    result = evaluate(HOME, vacant(since=100), lamp(dimmed), now=100 + OFF_AFTER_EMPTY_SECONDS)

    assert [command.on for command in result.commands] == [False]
    assert result.wake_at is None


def test_presence_brings_dimmed_lamps_back_up() -> None:
    result = evaluate(HOME, occupied(lux=500), lamp(LampState(on=True, brightness=50)), now=0)

    [command] = result.commands
    assert (command.on, command.brightness) == (True, 100)
    assert "Upp igen" in command.reason


def test_without_warning_dimming_the_lamp_is_simply_switched_off() -> None:
    home = home_with_settings(RoomSettings(dim_before_off_seconds=0))

    result = evaluate(home, vacant(since=0), lamp(ON), now=OFF_AFTER_EMPTY_SECONDS - 1)

    assert result.commands == ()
    assert result.wake_at == OFF_AFTER_EMPTY_SECONDS


def test_nothing_to_wait_for_when_lamp_is_off() -> None:
    result = evaluate(HOME, vacant(since=0), lamp(OFF), now=1)

    assert result.commands == ()
    assert result.wake_at is None
    assert result.explanations[ROOM] == "Vardagsrum är tomt."


def test_durations_are_explained_in_minutes_and_seconds() -> None:
    assert duration(10) == "10 sekunder"
    assert duration(60) == "1 minut"
    assert duration(150) == "2 minuter och 30 sekunder"
    assert duration(600) == "10 minuter"


@pytest.mark.parametrize("state", [ON, OFF, UNKNOWN])
def test_unknown_room_leaves_the_lamp_as_it_is(state: LampState) -> None:
    unknown = {ROOM: RoomState(Presence(Occupancy.UNKNOWN, 0), lux=5)}

    result = evaluate(HOME, unknown, lamp(state), now=24 * 60 * 60)

    assert result.commands == ()
    assert result.wake_at is None
    assert "okänt" in result.explanations[ROOM]


# --- Okänt lampläge ---


def test_unknown_lamp_is_switched_on_when_the_room_should_be_lit() -> None:
    result = evaluate(HOME, occupied(lux=5), {}, now=0)

    assert [command.on for command in result.commands] == [True]


@pytest.mark.parametrize("now", [0, 160, 10_000])
def test_unknown_lamp_in_an_empty_room_gets_no_commands(now: float) -> None:
    """Kärnan vet inte om lampan lyser, så den varken dimrar eller släcker på chans."""
    result = evaluate(HOME, vacant(since=0), lamp(UNKNOWN), now=now)

    assert result.commands == ()
    assert result.wake_at is None


def test_unknown_lamp_in_a_bright_room_gets_no_commands() -> None:
    result = evaluate(HOME, occupied(lux=5000), lamp(UNKNOWN), now=0)

    assert result.commands == ()


# --- Dagsljus och lampornas eget ljus ---


def test_adjusted_lux_subtracts_the_light_of_lit_lamps_scaled_by_brightness() -> None:
    room = home_with_lamp_light(80).rooms[0]

    assert adjusted_lux(room, 100, lamp(ON)) == 20
    assert adjusted_lux(room, 100, lamp(LampState(on=True, brightness=50))) == 60
    assert adjusted_lux(room, 100, lamp(OFF)) == 100
    assert adjusted_lux(room, 50, lamp(ON)) == 0  # aldrig under 0
    assert adjusted_lux(room, 100, lamp(UNKNOWN)) == 20  # kanske tänd: räkna med ljuset
    assert adjusted_lux(room, None, lamp(ON)) is None


def test_daylight_remembers_when_it_got_bright_and_switches_off_10_minutes_later() -> None:
    first = evaluate(HOME, occupied(lux=150), lamp(ON), now=1000)
    assert first.commands == ()
    assert first.memory[ROOM] == {BRIGHT_SINCE: 1000}
    assert first.wake_at == 1600

    later = evaluate(HOME, occupied(lux=150, memory=first.memory[ROOM]), lamp(ON), now=1600)

    assert [command.on for command in later.commands] == [False]
    assert "över 100 lux i 10 minuter (150 lux nu" in later.explanations[ROOM]
    assert BRIGHT_SINCE not in later.memory[ROOM]


def test_daylight_streak_is_broken_when_it_gets_darker() -> None:
    result = evaluate(HOME, occupied(lux=100, memory={BRIGHT_SINCE: 0}), lamp(ON), now=599)

    assert BRIGHT_SINCE not in result.memory[ROOM]


def test_the_lamps_own_light_is_not_daylight() -> None:
    home = home_with_lamp_light(150)

    result = evaluate(home, occupied(lux=160, memory={BRIGHT_SINCE: 0}), lamp(ON), now=10_000)

    assert result.commands == ()
    assert BRIGHT_SINCE not in result.memory[ROOM]


def test_windowless_room_is_always_dark() -> None:
    home = home_with_settings(RoomSettings(windowless=True))

    result = evaluate(home, occupied(lux=5000, memory={BRIGHT_SINCE: 0}), lamp(OFF), now=10_000)

    assert [command.on for command in result.commands] == [True]
    assert "saknar fönster" in result.explanations[ROOM]
    lit = evaluate(home, occupied(lux=5000, memory={BRIGHT_SINCE: 0}), lamp(ON), now=10_000)
    assert lit.commands == ()


# --- Manuellt läge ---


def test_manual_mode_leaves_the_lamps_while_someone_is_there() -> None:
    result = evaluate(HOME, occupied(lux=5, memory={MANUAL: 0}), lamp(OFF), now=10_000)

    assert result.commands == ()
    assert result.memory[ROOM] == {MANUAL: 0}
    assert "Manuellt läge" in result.explanations[ROOM]


def test_manual_mode_ends_when_the_room_has_been_empty_for_the_delay() -> None:
    # Ingen varningsdimring heller: automatiken pausar.
    for now in (200, 100 + OFF_AFTER_EMPTY_SECONDS - 1):
        waiting = evaluate(HOME, vacant(since=100, memory={MANUAL: 50}), lamp(ON), now=now)
        assert waiting.commands == ()
        assert waiting.wake_at is not None
        assert waiting.wake_at <= 100 + OFF_AFTER_EMPTY_SECONDS

    resumed = evaluate(
        HOME, vacant(since=100, memory={MANUAL: 50}), lamp(ON), now=100 + OFF_AFTER_EMPTY_SECONDS
    )

    assert resumed.memory[ROOM] == {}
    assert [command.on for command in resumed.commands] == [False]
    assert resumed.events[0].text.startswith("Automatiken tar över igen")


def test_manual_mode_counts_from_the_manual_switch_in_an_already_empty_room() -> None:
    result = evaluate(HOME, vacant(since=0, memory={MANUAL: 500}), lamp(ON), now=600)

    assert result.commands == ()
    assert result.wake_at == 500 + OFF_AFTER_EMPTY_SECONDS


# --- Nya regeltyper ---


class NightLight:
    """En påhittad ny regeltyp: på natten tänds lamporna bara svagt."""

    def __init__(self, night: Mapping[str, bool]) -> None:
        self._night = night

    def decide(self, situation: Situation) -> Verdict:
        if not self._night.get(situation.room.id) or situation.presence.status is not (
            Occupancy.OCCUPIED
        ):
            return Verdict()
        return Verdict(Decision("Nattljus.", Switch(on=True, brightness=10)))


def test_a_new_rule_type_is_added_without_changing_the_existing_ones() -> None:
    rules = (*DEFAULT_RULES[:2], NightLight({ROOM: True}), *DEFAULT_RULES[2:])

    night = evaluate(HOME, occupied(lux=5), lamp(OFF), now=0, rules=rules)
    unknown = evaluate(
        HOME, {ROOM: RoomState(Presence(Occupancy.UNKNOWN, 0), 5)}, lamp(OFF), now=0, rules=rules
    )

    [command] = night.commands
    assert (command.on, command.brightness, command.reason) == (True, 10, "Nattljus.")
    assert unknown.commands == ()  # reglerna före den nya gäller fortfarande


def test_lamp_without_light_contribution_by_default() -> None:
    assert Lamp(id="x", name="X", position=Point(1, 1)).lux_contribution == 0
