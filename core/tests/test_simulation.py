"""Simuleringsläget och låtsaskällan, sett från kärnans ingång."""

import asyncio
import contextlib

import pytest

from homecontrol.clock import SystemClock
from homecontrol.connectors import (
    LampCommand,
    LightLevelObserved,
    MotionObserved,
    Observation,
    RadarTarget,
    RadarTargetsObserved,
    SourceLost,
)
from homecontrol.home import Point, Target
from homecontrol.simulation import (
    LAMP_LUX_AT_ONE_METER,
    MAX_PERSONS,
    SimulatedLampOutput,
    SimulatedSource,
    Simulation,
    TooManyPersons,
    run_light_sensors,
)
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


def test_real_cards_are_not_simulated() -> None:
    observed: list[Observation] = []
    source = SimulatedSource(HOME, real_nodes=lambda _: {NODE.id})
    source.start(observed.append)

    source.add_person(FLOOR, Point(2.5, 2))
    source.set_daylight(5)

    assert observed == []


def test_time_cannot_go_faster_while_real_cards_are_used() -> None:
    clock = SystemClock()
    with_real_card = HOME.model_copy()
    simulation = Simulation(
        SimulatedSource(HOME),
        lambda _: None,
        clock,
        floor_ids=[FLOOR],
        real_hardware=lambda home: home is with_real_card,
    )
    simulation.set_speed(60)
    assert clock.speed == 60

    # Det första riktiga kortet placeras: simuleringen stängs av, och tiden går i vanlig takt.
    simulation.use_home(with_real_card)
    assert clock.speed == 1
    assert not simulation.enabled
    assert simulation.view().speed_locked

    # Även när någon slår på simuleringen igen går tiden i vanlig takt.
    simulation.set_enabled(True)
    simulation.set_speed(10)
    assert clock.speed == 1
    simulation.use_home(with_real_card)
    assert simulation.enabled


def test_virtual_lamps_never_report_or_control_real_lamps() -> None:
    seen: list[Observation] = []
    lamps = SimulatedLampOutput(real_lamps=lambda _: frozenset({"taklampa"}))

    lamps.report_new_lamps(HOME, seen.append)
    lamps.send(LampCommand("taklampa", on=True, reason="test"))

    assert seen == []
    assert lamps.states == {}
    assert not lamps.history


def test_virtual_lamps_light_up_the_simulated_sensor_when_it_measures() -> None:
    """Taklampan sitter 2 meter från rumskortet. Dagsljusreglaget mäter med lampans ljus nu."""
    seen: list[Observation] = []
    source = SimulatedSource(HOME, "sim")
    lamps = SimulatedLampOutput()
    simulation = Simulation(
        source, seen.append, SystemClock(), floor_ids=[FLOOR], daylight=20, lamps=lamps
    )
    lamps.send(LampCommand("taklampa", on=True, reason="test"))
    simulation.measure_light()
    lamps.send(LampCommand("taklampa", on=True, reason="test", brightness=50))
    simulation.set_daylight(30)
    levels = [o.lux for o in seen if isinstance(o, LightLevelObserved)]
    assert levels == [20, pytest.approx(20 + LAMP_LUX_AT_ONE_METER / 4), 30 + 10]


def test_simulated_sensors_measure_every_five_seconds_in_the_cores_time() -> None:
    async def run() -> int:
        simulation, seen, clock = make()
        seen.clear()
        task = asyncio.create_task(run_light_sensors(simulation, clock))
        await asyncio.sleep(0.01)
        simulation.set_speed(60)  # nu mäter de var 5/60 sekund, även mitt i en väntan
        await asyncio.sleep(0.5)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return sum(isinstance(o, LightLevelObserved) for o in seen)

    readings = asyncio.run(run())
    assert 3 <= readings <= 8
