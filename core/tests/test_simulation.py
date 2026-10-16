"""Simuleringsläget och låtsaskällan, sett från kärnans ingång."""

import pytest

from homecontrol.clock import SystemClock
from homecontrol.connectors import (
    LightLevelObserved,
    MotionObserved,
    Observation,
    RadarTarget,
    RadarTargetsObserved,
    SourceLost,
)
from homecontrol.home import Point, Target
from homecontrol.simulation import MAX_PERSONS, SimulatedSource, Simulation, TooManyPersons
from homes import FLOOR, one_room_home, two_floor_home

HOME = one_room_home()
NODE = HOME.rooms[0].nodes[0]
"""Rumskortet sitter mitt på övre väggen, i (2.5, 0), och tittar rakt ned i rummet."""


def at(x: float, y: float) -> Target:
    return Target(FLOOR, Point(x, y))


def radar(*points: tuple[float, float]) -> RadarTargetsObserved:
    """Det rumskortet ser när personer står i `points`, i radarns egna koordinater."""
    targets = tuple(RadarTarget(*NODE.home_to_radar(Point(x, y))) for x, y in points)
    return RadarTargetsObserved("sim", NODE.id, targets)


def make(enabled: bool = True) -> tuple[Simulation, list[Observation], SystemClock]:
    seen: list[Observation] = []
    clock = SystemClock()
    source = SimulatedSource(HOME, "sim")
    simulation = Simulation(
        source, seen.append, clock, floor_ids=[FLOOR], enabled=enabled, daylight=20
    )
    return simulation, seen, clock


def test_starts_by_reporting_no_one_and_the_daylight_in_every_room() -> None:
    _, seen, _ = make()

    assert seen == [radar(), LightLevelObserved("sim", "vardagsrum", 20)]


def test_persons_are_added_moved_and_removed_through_the_sink() -> None:
    simulation, seen, _ = make()

    simulation.add_person(FLOOR, Point(1, 1))
    simulation.add_person(FLOOR, Point(2, 2))
    simulation.move_person("1", FLOOR, Point(3, 3))
    simulation.remove_person("2")
    simulation.move_person("2", FLOOR, Point(4, 4))  # borta, ignoreras

    # Radarn numrerar målen efter avståndet, så den som flyttade längre bort kommer sist.
    assert seen[2:] == [
        radar((1, 1)),
        radar((1, 1), (2, 2)),
        radar((2, 2), (3, 3)),
        radar((3, 3)),
    ]
    assert simulation.view().persons == {"1": at(3, 3)}


def test_persons_on_unknown_floors_are_refused() -> None:
    simulation, seen, _ = make()
    simulation.add_person(FLOOR, Point(1, 1))
    before = list(seen)

    assert simulation.add_person("vinden", Point(1, 1)) is None
    simulation.move_person("1", "vinden", Point(2, 2))

    assert seen == before
    assert simulation.view().persons == {"1": at(1, 1)}


def test_too_many_persons_are_refused_without_error() -> None:
    simulation, _, _ = make()
    for _ in range(MAX_PERSONS):
        assert simulation.add_person(FLOOR, Point(1, 1)) is not None

    assert simulation.add_person(FLOOR, Point(1, 1)) is None
    assert len(simulation.view().persons) == MAX_PERSONS


def test_switching_on_twice_starts_the_source_once() -> None:
    simulation, seen, _ = make()
    before = list(seen)

    simulation.set_enabled(True)

    assert seen == before


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
    source = SimulatedSource(HOME)
    for _ in range(MAX_PERSONS):
        source.add_person(FLOOR, Point(1, 1))

    with pytest.raises(TooManyPersons):
        source.add_person(FLOOR, Point(1, 1))


def test_only_the_offered_speeds_are_allowed() -> None:
    simulation, _, _ = make()

    with pytest.raises(ValueError, match="Tidsfarten"):
        simulation.set_speed(1000)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Tidsfarten"):
        simulation.set_speed(True)  # type: ignore[arg-type]


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


def test_radar_only_sees_its_field_of_view_and_at_most_three() -> None:
    simulation, seen, _ = make()
    simulation.add_person(FLOOR, Point(0.2, 0.5))  # bredvid kortet, utanför synfältet
    assert seen[-1] == radar()

    for x in (1.0, 2.0, 3.0, 4.0):
        simulation.add_person(FLOOR, Point(x, 3))

    assert seen[-1] == radar((2, 3), (3, 3), (1, 3))


def test_radar_does_not_see_other_floors() -> None:
    home = two_floor_home()
    seen: list[Observation] = []
    source = SimulatedSource(home, "sim")
    source.start(seen.append)

    source.add_person("uppe", Point(2.5, 2))

    by_node = {o.node_id: o.targets for o in seen[-2:] if isinstance(o, RadarTargetsObserved)}
    assert by_node["nod-vardagsrum"] == ()
    assert len(by_node["nod-sovrum"]) == 1


def test_person_lost_by_the_radar_is_still_there() -> None:
    source = SimulatedSource(HOME, "sim")
    seen: list[Observation] = []
    source.start(seen.append)
    person = source.add_person(FLOOR, Point(2, 2))

    source.set_seen_by_radar(person, False)
    assert seen[-1] == radar()
    assert source.persons == {person: at(2, 2)}

    source.set_seen_by_radar(person, True)
    assert seen[-1] == radar((2, 2))


def test_wifi_motion_is_reported_and_repeated_after_a_restart() -> None:
    source = SimulatedSource(HOME, "sim")
    seen: list[Observation] = []
    source.set_motion(NODE.id, True)  # minns tills källan startar
    source.start(seen.append)
    assert MotionObserved("sim", NODE.id, True) in seen

    source.stop()
    seen.clear()
    source.start(seen.append)

    assert not any(isinstance(o, MotionObserved) for o in seen)
