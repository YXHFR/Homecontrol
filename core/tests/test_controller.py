import math

import pytest

from homecontrol.clock import ManualClock
from homecontrol.connectors import LampCommand, LightLevelObserved, SourceLost, TargetsObserved
from homecontrol.controller import Controller
from homecontrol.simulation import SimulatedLampOutput
from homes import at, one_room_home


def make() -> tuple[Controller, ManualClock, SimulatedLampOutput]:
    clock = ManualClock()
    lamps = SimulatedLampOutput()
    return Controller(one_room_home(), clock, [lamps]), clock, lamps


def test_targets_from_several_sources_are_combined() -> None:
    controller, _, _ = make()
    controller.observe(TargetsObserved("a", (at(1, 1),)))
    controller.observe(TargetsObserved("b", (at(9, 9),)))
    controller.observe(TargetsObserved("a", ()))

    snapshot = controller.snapshot()

    assert snapshot.targets == (at(9, 9),)
    assert not snapshot.rooms[0].occupied


def test_lost_source_forgets_its_targets() -> None:
    controller, _, _ = make()
    controller.observe(TargetsObserved("a", (at(1, 1),)))

    controller.observe(SourceLost("a"))

    assert controller.snapshot().targets == ()
    assert not controller.snapshot().rooms[0].occupied


def test_lost_source_forgets_only_its_own_light_levels() -> None:
    controller, _, _ = make()
    controller.observe(LightLevelObserved("a", "vardagsrum", 10))
    controller.observe(SourceLost("b"))
    assert controller.snapshot().rooms[0].lux == 10

    controller.observe(SourceLost("a"))

    assert controller.snapshot().rooms[0].lux is None


def test_latest_light_level_wins_and_the_other_source_remains() -> None:
    controller, _, _ = make()
    controller.observe(LightLevelObserved("sensor", "vardagsrum", 40))
    controller.observe(LightLevelObserved("sim", "vardagsrum", 10))
    assert controller.snapshot().rooms[0].lux == 10

    controller.observe(SourceLost("sim"))

    assert controller.snapshot().rooms[0].lux == 40


class BrokenOutput:
    def send(self, command: LampCommand) -> None:
        raise ConnectionError("borta")


def test_broken_output_does_not_stop_the_others(caplog: pytest.LogCaptureFixture) -> None:
    lamps = SimulatedLampOutput()
    controller = Controller(one_room_home(), ManualClock(), [BrokenOutput(), lamps])

    controller.observe(LightLevelObserved("sim", "vardagsrum", 5))
    controller.observe(TargetsObserved("sim", (at(1, 1),)))

    assert lamps.states == {"taklampa": True}
    assert "kunde inte ta emot" in caplog.text


def test_log_says_which_lamp_but_not_where_someone_is(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="homecontrol.controller")
    controller, _, _ = make()

    controller.observe(LightLevelObserved("sim", "vardagsrum", 5))
    controller.observe(TargetsObserved("sim", (at(1, 1),)))

    assert "Tänder taklampa" in caplog.text
    assert "Vardagsrum" not in caplog.text


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
    controller.observe(TargetsObserved("sim", (at(1, 1),)))
    controller.observe(TargetsObserved("sim", ()))
    wake_at = controller.wake_at
    assert wake_at is not None

    clock.advance_to(wake_at - 0.001)
    controller.tick()

    assert lamps.states == {"taklampa": True}


def test_manual_clock_cannot_go_backwards() -> None:
    clock = ManualClock(10)

    with pytest.raises(ValueError, match="bakåt"):
        clock.advance_to(9)
