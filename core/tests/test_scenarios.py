"""Scenarier för den minimala regeln, i snabbspolad tid."""

import pytest

from homecontrol.scenario import Scenario


def test_dark_room_person_enters_lamp_turns_on() -> None:
    (
        Scenario()
        .light_level(10)
        .expect_lamp_off()
        .person_enters()
        .expect_lamp_on()
        .expect_explanation("det är mörkt")
    )


def test_room_empties_lamp_off_after_3_minutes() -> None:
    (
        Scenario()
        .light_level(10)
        .person_enters()
        .expect_lamp_on()
        .person_leaves()
        .wait(minutes=2, seconds=59)
        .expect_lamp_on()
        .expect_explanation("släcks när det varit tomt i 3 minuter")
        .wait(seconds=1)
        .expect_lamp_off()
        .expect_explanation("har varit tomt i 3 minuter")
    )


def test_bright_room_person_enters_lamp_stays_off() -> None:
    Scenario().light_level(30).person_enters().expect_lamp_off().expect_explanation("ljust nog")


def test_unknown_light_level_lamp_stays_off() -> None:
    Scenario().person_enters().expect_lamp_off().expect_explanation("okänd")


def test_darkness_while_present_turns_lamp_on() -> None:
    Scenario().light_level(200).person_enters().expect_lamp_off().light_level(5).expect_lamp_on()


def test_lamp_stays_on_when_room_gets_bright() -> None:
    # Lampan gör själv rummet ljusare, så den ska inte släckas av det.
    Scenario().light_level(10).person_enters().light_level(400).wait(minutes=10).expect_lamp_on()


def test_returning_restarts_the_delay() -> None:
    (
        Scenario()
        .light_level(10)
        .person_enters()
        .person_leaves()
        .wait(minutes=2)
        .person_enters()
        .wait(minutes=5)
        .expect_lamp_on()
        .person_leaves()
        .wait(minutes=2, seconds=59)
        .expect_lamp_on()
        .wait(seconds=1)
        .expect_lamp_off()
    )


def test_walking_out_of_the_room_counts_as_leaving() -> None:
    (
        Scenario()
        .light_level(10)
        .person_moves_to(2.0, 2.0)
        .expect_lamp_on()
        .person_moves_to(7.0, 2.0)
        .wait(minutes=3)
        .expect_lamp_off()
    )


def test_lamp_switches_exactly_once_per_change() -> None:
    scenario = Scenario().light_level(10).person_enters().person_leaves().wait(minutes=30)

    assert [(c.on, c.reason[:6]) for c in scenario.lamps.history] == [
        (True, "Tänd: "),
        (False, "Släckt"),
    ]


def test_long_wait_is_fast_forwarded() -> None:
    scenario = Scenario().wait(minutes=60 * 24)

    assert scenario.clock.now() == 24 * 60 * 60


def test_failed_expectation_explains_why() -> None:
    with pytest.raises(AssertionError, match=r"skulle vara tänd.*ljusnivån i Vardagsrum är okänd"):
        Scenario().person_enters().expect_lamp_on()


def test_negative_wait_is_refused() -> None:
    with pytest.raises(ValueError, match="negativ"):
        Scenario().wait(seconds=-1)
