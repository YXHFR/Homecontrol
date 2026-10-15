import math

import pytest

from homecontrol.clock import ManualClock
from homecontrol.connectors import LightLevelObserved, TargetsObserved
from homecontrol.controller import Controller
from homecontrol.home import Home, Lamp, Point, Room, demo_home
from homecontrol.simulation import SimulatedLampOutput


def make() -> tuple[Controller, ManualClock, SimulatedLampOutput]:
    clock = ManualClock()
    lamps = SimulatedLampOutput()
    return Controller(demo_home(), clock, [lamps]), clock, lamps


def test_targets_from_several_sources_are_combined() -> None:
    controller, _, _ = make()
    controller.observe(TargetsObserved("a", (Point(1, 1),)))
    controller.observe(TargetsObserved("b", (Point(9, 9),)))
    controller.observe(TargetsObserved("a", ()))

    snapshot = controller.snapshot()

    assert snapshot.targets == (Point(9, 9),)
    assert not snapshot.rooms[0].occupied


@pytest.mark.parametrize("lux", [-1.0, math.nan, math.inf])
def test_invalid_light_level_is_ignored(lux: float) -> None:
    controller, _, _ = make()

    controller.observe(LightLevelObserved("sim", "vardagsrum", lux))

    assert controller.snapshot().rooms[0].lux is None


def test_light_level_for_unknown_room_is_ignored() -> None:
    controller, _, _ = make()

    controller.observe(LightLevelObserved("sim", "finns-inte", 5))

    assert controller.snapshot().rooms[0].lux is None


def test_listeners_are_told_about_changes_until_unsubscribed() -> None:
    controller, _, _ = make()
    calls: list[None] = []
    unsubscribe = controller.subscribe(lambda: calls.append(None))

    controller.observe(LightLevelObserved("sim", "vardagsrum", 5))
    unsubscribe()
    controller.observe(LightLevelObserved("sim", "vardagsrum", 6))

    assert len(calls) == 1


def test_tick_before_wake_at_does_nothing() -> None:
    controller, clock, lamps = make()
    controller.observe(LightLevelObserved("sim", "vardagsrum", 5))
    controller.observe(TargetsObserved("sim", (Point(1, 1),)))
    controller.observe(TargetsObserved("sim", ()))
    wake_at = controller.wake_at
    assert wake_at is not None

    clock.advance_to(wake_at - 0.001)
    controller.tick()

    assert lamps.states == {"taklampa": True}


def test_home_rejects_lamp_in_unknown_room() -> None:
    with pytest.raises(ValueError, match="okänt rum"):
        Home(rooms=(), lamps=(Lamp("l", "L", "saknas", Point(0, 0)),))


def test_room_contains_its_edges_only() -> None:
    room = Room("r", "R", x=0, y=0, width=2, height=1)

    assert room.contains(Point(0, 0))
    assert room.contains(Point(2, 1))
    assert not room.contains(Point(2.01, 0.5))


def test_manual_clock_cannot_go_backwards() -> None:
    clock = ManualClock(10)

    with pytest.raises(ValueError, match="bakåt"):
        clock.advance_to(9)
