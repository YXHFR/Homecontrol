import math

import pytest

from homecontrol.clock import ManualClock
from homecontrol.connectors import LampCommand, LampStateReported, LightLevelObserved, SourceLost
from homecontrol.controller import MAX_EVENTS, Controller
from homecontrol.fusion import Occupancy
from homecontrol.simulation import SimulatedLampOutput
from homes import one_room_home, radar_sees, room


def make() -> tuple[Controller, ManualClock, SimulatedLampOutput]:
    clock = ManualClock()
    lamps = SimulatedLampOutput()
    return Controller(one_room_home(), clock, [lamps]), clock, lamps


def test_radar_targets_become_tracks_in_the_home() -> None:
    controller, _, _ = make()
    controller.observe(radar_sees((1, 1)))

    snapshot = controller.snapshot()

    [track] = snapshot.tracks
    assert track.floor_id == "plan"
    assert track.position.x == pytest.approx(1)
    assert track.position.y == pytest.approx(1)
    assert snapshot.rooms[0].occupied


def test_lost_source_forgets_its_tracks_and_makes_the_room_unknown() -> None:
    controller, _, _ = make()
    controller.observe(radar_sees((1, 1)))

    controller.observe(SourceLost("sim"))

    assert controller.snapshot().tracks == ()
    assert controller.snapshot().rooms[0].status is Occupancy.UNKNOWN


def test_other_sources_going_quiet_do_not_affect_the_room() -> None:
    controller, _, _ = make()
    controller.observe(radar_sees())

    controller.observe(SourceLost("någon-annan"))

    assert controller.snapshot().rooms[0].status is Occupancy.VACANT


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
    controller.observe(radar_sees((1, 1)))

    assert lamps.states == {"taklampa": True}
    assert "kunde inte ta emot" in caplog.text


def test_log_says_which_lamp_but_not_where_someone_is(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="homecontrol.controller")
    controller, _, _ = make()

    controller.observe(LightLevelObserved("sim", "vardagsrum", 5))
    controller.observe(radar_sees((1, 1)))

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
    controller.observe(radar_sees((1, 1)))
    controller.observe(radar_sees())
    wake_at = controller.wake_at
    assert wake_at is not None

    clock.advance_to(wake_at - 0.001)
    controller.tick()

    assert lamps.states == {"taklampa": True}


def test_manual_clock_cannot_go_backwards() -> None:
    clock = ManualClock(10)

    with pytest.raises(ValueError, match="bakåt"):
        clock.advance_to(9)


def test_replaced_home_keeps_what_still_applies() -> None:
    controller, _, lamps = make()
    controller.observe(LightLevelObserved("sim", "vardagsrum", 5))
    controller.observe(radar_sees((1, 1)))
    assert lamps.states == {"taklampa": True}
    changes: list[None] = []
    controller.subscribe(lambda: changes.append(None))

    home = one_room_home()
    floor = home.floors[0]
    hall = room("hall", "Hall", 5, 0, 2, 4, lamp="hallampa")
    controller.replace_home(
        home.model_copy(
            update={"floors": (floor.model_copy(update={"rooms": (*floor.rooms, hall)}),)}
        )
    )

    snapshot = controller.snapshot()
    assert changes == [None]
    assert [(r.room_id, r.occupied, r.lux) for r in snapshot.rooms] == [
        ("vardagsrum", True, 5),
        ("hall", False, None),
    ]
    assert [(lamp.lamp_id, lamp.on) for lamp in snapshot.lamps] == [
        ("taklampa", True),
        ("hallampa", None),  # ingen har sagt hur hallens lampa är än
    ]
    # Lampan var redan tänd, så inget nytt kommando behövdes.
    assert len(lamps.history) == 1


def test_person_outside_a_moved_room_leaves_it() -> None:
    controller, _, _ = make()
    controller.observe(radar_sees((1, 1)))

    home = one_room_home()
    floor = home.floors[0]
    moved = room("vardagsrum", "Vardagsrum", 10, 10, 5, 4, lamp="taklampa")
    controller.replace_home(
        home.model_copy(update={"floors": (floor.model_copy(update={"rooms": (moved,)}),)})
    )

    assert not controller.snapshot().rooms[0].occupied


def test_lamp_of_a_removed_room_is_turned_off() -> None:
    controller, _, lamps = make()
    controller.observe(LightLevelObserved("sim", "vardagsrum", 5))
    controller.observe(radar_sees((1, 1)))
    assert lamps.states == {"taklampa": True}

    home = one_room_home()
    controller.replace_home(
        home.model_copy(update={"floors": (home.floors[0].model_copy(update={"rooms": ()}),)})
    )

    assert lamps.states == {"taklampa": False}
    assert controller.snapshot().lamps == ()


def test_lamps_are_unknown_until_they_report_and_the_report_is_used() -> None:
    controller, _, lamps = make()
    assert controller.snapshot().lamps[0].on is None

    controller.observe(LampStateReported("taklampa", on=True, brightness=40))
    controller.observe(LampStateReported("finns-inte", on=True))
    controller.observe(LampStateReported("taklampa", on=True, brightness=0))  # ogiltig

    [lamp] = controller.snapshot().lamps
    assert (lamp.on, lamp.brightness) == (True, 40)
    controller.observe(LampStateReported("taklampa", on=False, brightness=0))  # släckt: går bra
    assert controller.snapshot().lamps[0].on is False
    assert not lamps.history


def test_lamp_reported_lit_in_an_empty_room_is_switched_off_after_the_delay() -> None:
    controller, clock, lamps = make()
    controller.observe(radar_sees())
    controller.observe(LampStateReported("taklampa", on=True))

    clock.advance_to(10_000)
    controller.tick()

    assert [c.on for c in lamps.history] == [False]


def test_events_are_kept_with_their_clock_time_and_only_the_latest_ones() -> None:
    clock = ManualClock()
    controller = Controller(one_room_home(), clock, [], wall_clock=lambda: 1_700_000_000)
    for i in range(MAX_EVENTS + 5):
        controller.set_lamp("taklampa", on=i % 2 == 0)

    events = controller.snapshot().events
    assert len(events) == MAX_EVENTS
    assert {event.at for event in events} == {1_700_000_000}
    assert events[-1].text.startswith("Taklampa tändes för hand")


def test_a_lamp_changed_from_outside_counts_as_manual_control() -> None:
    controller, clock, lamps = make()
    controller.observe(LampStateReported("taklampa", on=False))
    clock.advance_to(10)

    controller.observe(LampStateReported("taklampa", on=True, brightness=40, external=True))

    snapshot = controller.snapshot()
    assert snapshot.rooms[0].manual
    assert snapshot.lamps[0].on is True
    assert snapshot.lamps[0].brightness == 40
    assert snapshot.events[-1].text == (
        "Taklampa ställdes på 40 % med fjärrkontroll eller app. Automatiken i Vardagsrum "
        "pausar tills rummet har varit tomt i 3 minuter."
    )
    assert not lamps.history  # lampan är redan ändrad; kärnan skickar inget eget kommando


def test_a_report_that_is_not_from_outside_does_not_pause_the_automation() -> None:
    controller, _, _ = make()

    controller.observe(LampStateReported("taklampa", on=True))

    assert not controller.snapshot().rooms[0].manual


def test_a_lamp_that_does_not_respond_is_shown_as_not_responding() -> None:
    controller, _, _ = make()
    controller.observe(LampStateReported("taklampa", on=True))

    controller.observe(LampStateReported("taklampa", on=None))

    [lamp] = controller.snapshot().lamps
    assert lamp.on is None
    assert not lamp.responding

    controller.observe(LampStateReported("taklampa", on=False))
    assert controller.snapshot().lamps[0].responding
