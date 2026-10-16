"""Simuleringsläget och låtsaskällan, sett från kärnans ingång."""

import pytest

from homecontrol.clock import SystemClock
from homecontrol.connectors import LightLevelObserved, Observation, SourceLost, TargetsObserved
from homecontrol.home import Point
from homecontrol.simulation import MAX_PERSONS, SimulatedSource, Simulation, TooManyPersons
from homes import FLOOR, at


def make(enabled: bool = True) -> tuple[Simulation, list[Observation], SystemClock]:
    seen: list[Observation] = []
    clock = SystemClock()
    source = SimulatedSource("sim", room_ids=["kök", "hall"])
    return Simulation(source, seen.append, clock, enabled=enabled, daylight=20), seen, clock


def test_starts_by_reporting_no_one_and_the_daylight_in_every_room() -> None:
    _, seen, _ = make()

    assert seen == [
        TargetsObserved("sim", ()),
        LightLevelObserved("sim", "kök", 20),
        LightLevelObserved("sim", "hall", 20),
    ]


def test_persons_are_added_moved_and_removed_through_the_sink() -> None:
    simulation, seen, _ = make()

    simulation.add_person(FLOOR, Point(1, 1))
    simulation.add_person(FLOOR, Point(2, 2))
    simulation.move_person("1", FLOOR, Point(3, 3))
    simulation.remove_person("2")
    simulation.move_person("2", FLOOR, Point(4, 4))  # borta, ignoreras

    assert seen[-1] == TargetsObserved("sim", (at(3, 3),))
    assert simulation.view().persons == {"1": at(3, 3)}


def test_switching_off_forgets_persons_and_resets_speed() -> None:
    simulation, seen, clock = make()
    simulation.add_person(FLOOR, Point(1, 1))
    simulation.set_speed(60)

    simulation.set_enabled(False)

    assert seen[-1] == SourceLost("sim")
    assert clock.speed == 1
    view = simulation.view()
    assert not view.enabled
    assert view.persons == {}


def test_switched_off_simulation_ignores_commands() -> None:
    simulation, seen, clock = make(enabled=False)

    simulation.add_person(FLOOR, Point(1, 1))
    simulation.set_daylight(500)
    simulation.set_speed(60)

    assert seen == []
    assert clock.speed == 1


def test_notifies_listeners_of_changes() -> None:
    simulation, _, _ = make()
    calls = 0

    def listener() -> None:
        nonlocal calls
        calls += 1

    stop = simulation.subscribe(listener)
    simulation.set_speed(10)
    stop()
    simulation.set_speed(60)

    assert calls == 1


def test_number_of_persons_is_limited() -> None:
    source = SimulatedSource()
    for _ in range(MAX_PERSONS):
        source.add_person(FLOOR, Point(1, 1))

    with pytest.raises(TooManyPersons):
        source.add_person(FLOOR, Point(1, 1))


def test_only_the_offered_speeds_are_allowed() -> None:
    simulation, _, _ = make()

    with pytest.raises(ValueError, match="Tidsfarten"):
        simulation.set_speed(1000)  # type: ignore[arg-type]


def test_faster_clock_continues_from_where_it_was() -> None:
    real = [100.0]
    clock = SystemClock(lambda: real[0])
    real[0] += 10
    assert clock.now() == 110

    clock.set_speed(60)
    real[0] += 1

    assert clock.now() == 170
    assert clock.real_seconds_until(230) == 1
    assert clock.real_seconds_until(0) == 0
    with pytest.raises(ValueError, match="framåt"):
        clock.set_speed(0)
