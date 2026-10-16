"""Scenarier för den minimala regeln, i snabbspolad tid.

Om inget annat anges är hemmet ett vardagsrum på 5 × 4 meter med en taklampa i mitten.
"""

import pytest

from homecontrol.home import RoomSettings
from homes import home_with_settings, two_floor_home
from scenario import Scenario

# Stegen står ett per rad så att de läses som en berättelse. Formateraren
# skulle annars slå ihop korta scenarier till en enda rad.
# fmt: off


def test_dark_room_person_enters_lamp_turns_on() -> None:
    """Mörkt rum, en person går in: lampan tänds."""
    (
        Scenario()
        .light_level(10)
        .expect_lamp_off()
        .person_enters()
        .expect_lamp_on()
        .expect_explanation("det är mörkt")
    )


def test_room_empties_lamp_off_after_3_minutes() -> None:
    """Rummet töms: lampan lyser i 3 minuter till och släcks sedan."""
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
    """Ljust rum (30 lux räknas som ljust), en person går in: lampan förblir släckt."""
    (
        Scenario()
        .light_level(30)
        .person_enters()
        .expect_lamp_off()
        .expect_explanation("ljust nog")
    )


def test_unknown_light_level_lamp_stays_off() -> None:
    """Ingen vet hur ljust det är: lampan tänds inte på chans."""
    (
        Scenario()
        .person_enters()
        .expect_lamp_off()
        .expect_explanation("okänd")
    )


def test_darkness_while_present_turns_lamp_on() -> None:
    """Någon sitter i rummet när det skymmer: lampan tänds."""
    (
        Scenario()
        .light_level(200)
        .person_enters()
        .expect_lamp_off()
        .light_level(5)
        .expect_lamp_on()
    )


def test_lamp_stays_on_when_room_gets_bright() -> None:
    """Lampan gör själv rummet ljusare, så den släcks inte av det."""
    (
        Scenario()
        .light_level(10)
        .person_enters()
        .light_level(400)
        .wait(minutes=10)
        .expect_lamp_on()
    )


def test_returning_restarts_the_delay() -> None:
    """Den som kommer tillbaka innan 3 minuter gått får nya 3 minuter när den går igen."""
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
    """Att gå ut ur rummet räknas som att lämna det, även om personen syns kvar."""
    (
        Scenario()
        .light_level(10)
        .person_moves_to(x=2, y=2)  # inne i rummet
        .expect_lamp_on()
        .person_moves_to(x=7, y=2)  # rummet är 5 m brett, så det här är utanför
        .wait(minutes=3)
        .expect_lamp_off()
    )


def test_source_that_disappears_does_not_keep_the_room_occupied() -> None:
    """En källa som tystnar räknas inte som att någon är kvar i rummet."""
    (
        Scenario()
        .light_level(10)
        .person_enters()
        .source_lost()
        .wait(minutes=3)
        .expect_lamp_off()
    )


def test_lamp_switches_exactly_once_per_change() -> None:
    """Lampan tänds en gång och släcks en gång, inte fler."""
    (
        Scenario()
        .light_level(10)
        .person_enters()
        .person_leaves()
        .wait(minutes=30)
        .expect_lamp_switches(True, False)
    )


def test_person_upstairs_does_not_light_the_room_below() -> None:
    """Rummen ligger ovanför varandra, men bara våningen där personen är räknas."""
    (
        Scenario(two_floor_home())
        .light_level(10, room="vardagsrum")
        .light_level(10, room="sovrum")
        .person_enters(room="sovrum")
        .expect_lamp_on(lamp="lampa-uppe")
        .expect_lamp_off(lamp="lampa-nere")
        .person_moves_to(x=2.5, y=2, floor="nere")
        .expect_lamp_on(lamp="lampa-nere")
        .wait(minutes=3)
        .expect_lamp_off(lamp="lampa-uppe")
        .expect_lamp_on(lamp="lampa-nere")
    )


def test_room_settings_decide_darkness_and_delay() -> None:
    """Ett rum som räknas som mörkt under 50 lux och släcks efter en minut."""
    settings = RoomSettings(dark_below_lux=50, off_after_empty_seconds=60)
    (
        Scenario(home_with_settings(settings))
        .light_level(40)
        .person_enters()
        .expect_lamp_on()
        .expect_explanation("under 50")
        .person_leaves()
        .wait(seconds=59)
        .expect_lamp_on()
        .expect_explanation("släcks när det varit tomt i 1 minut")
        .wait(seconds=1)
        .expect_lamp_off()
    )


def test_lamp_stays_on_while_someone_is_left() -> None:
    """Två personer i rummet: lampan lyser så länge en av dem är kvar."""
    (
        Scenario()
        .daylight(10)
        .person_enters(who="Anna")
        .person_enters(who="Bo")
        .person_leaves(who="Anna")
        .wait(minutes=10)
        .expect_lamp_on()
        .person_leaves(who="Bo")
        .wait(minutes=3)
        .expect_lamp_off()
    )


def test_daylight_slider_decides_darkness_in_every_room() -> None:
    """Dagsljuset gäller alla rum: ljust ute tänder inget, mörkt ute tänder."""
    (
        Scenario(two_floor_home())
        .daylight(200)
        .person_enters(room="sovrum")
        .expect_lamp_off(lamp="lampa-uppe")
        .daylight(5)
        .expect_lamp_on(lamp="lampa-uppe")
        .expect_explanation("5 lux", lamp="lampa-uppe")
    )


# fmt: on


def test_a_day_of_waiting_takes_no_time() -> None:
    """Ett helt dygn spolas fram direkt."""
    scenario = Scenario().wait(minutes=24 * 60)

    assert scenario.clock.now() == 24 * 60 * 60


def test_failed_expectation_explains_why() -> None:
    """Ett scenario som går fel säger varför lampan gjorde som den gjorde."""
    with pytest.raises(AssertionError, match=r"skulle vara tänd.*ljusnivån i Vardagsrum är okänd"):
        Scenario().person_enters().expect_lamp_on()


def test_negative_wait_is_refused() -> None:
    with pytest.raises(ValueError, match="negativ"):
        Scenario().wait(seconds=-1)
