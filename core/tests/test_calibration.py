"""Lampkalibreringen i snabbspolad tid: varje lampas eget ljus mäts och används av reglerna.

De virtuella lamporna lyser upp den simulerade ljussensorn i rumskortet, och
ljuset avtar med kvadraten på avståndet (se simulation.py). I enrumshemmet
sitter taklampan 2 meter från rumskortet och lyser alltså upp sensorn med en
fjärdedel av `LAMP_LUX_AT_ONE_METER`.
"""

import pytest

from homecontrol.calibration import STEP_TIMEOUT_SECONDS
from homecontrol.connectors import LightLevelObserved
from homecontrol.home import RoomSettings
from homecontrol.simulation import LAMP_LUX_AT_ONE_METER
from homes import home_with_settings, one_room_home, two_lamp_home
from scenario import Scenario

CEILING_LAMP = LAMP_LUX_AT_ONE_METER / 2**2
"""Taklampans ljus vid sensorn, 2 meter bort."""
FLOOR_LAMP = LAMP_LUX_AT_ONE_METER / 5
"""Golvlampans ljus vid sensorn, √5 meter bort."""

# fmt: off


def test_measures_each_lamp_and_puts_the_lamps_back() -> None:
    """Varje lampa mäts för sig, och lamporna är som förut när kalibreringen är klar."""
    (
        Scenario(two_lamp_home(), lamp_light=True)
        .daylight(10)
        .person_enters()
        .expect_lamp_on("taklampa")
        .expect_lamp_on("golvlampa")
        .start_calibration()
        .expect_calibration("pågår")
        .expect_lamp_off("taklampa")
        .expect_lamp_off("golvlampa")
        .wait_for_calibration()
        .expect_calibration("klar")
        .expect_lamp_light(CEILING_LAMP, "taklampa")
        .expect_lamp_light(FLOOR_LAMP, "golvlampa")
        .expect_lamp_on("taklampa")
        .expect_lamp_on("golvlampa")
        .expect_event("Lampkalibreringen i Vardagsrum är klar")
    )


@pytest.mark.parametrize("phase", [0.0, 1.0, 2.5, 4.0])
def test_a_faint_lamp_in_daylight_is_measured_whenever_the_sensor_measures(phase: float) -> None:
    """Sensorn mäter var femte sekund. Värden från innan lampan ändrades får aldrig räknas med.

    Lampan sitter 2,5 meter från rumskortet, och dess ljus är mindre än vad
    dagsljuset får skifta under en mätning.
    """
    (
        Scenario(lamp_light=True, sensor_phase=phase)
        .daylight(300)
        .move_lamp("taklampa", x=2.5, y=2.5)
        .start_calibration()
        .wait_for_calibration()
        .expect_calibration("klar")
        .expect_lamp_light(LAMP_LUX_AT_ONE_METER / 2.5**2)
    )


def test_each_measurement_ends_when_the_light_has_been_even_long_enough() -> None:
    """Kalibreringen väntar inte på nästa avläsning när ljuset redan har varit jämnt länge nog.

    Sensorn mäter efter 4, 9 och 14 sekunder. Ljuset utan lampor har då varit
    jämnt i 12 sekunder efter 16 sekunder, och då tänds taklampan.
    """
    (
        Scenario(lamp_light=True, sensor_phase=4)
        .daylight(10)
        .start_calibration()
        .wait(seconds=15.9)
        .expect_lamp_off()
        .wait(seconds=0.1)
        .expect_lamp_on()
    )


def test_a_silent_sensor_stops_the_calibration_without_spinning() -> None:
    """Slutar sensorn mäta, utan att kortet tappas, väntar kalibreringen en stund och ger upp."""
    (
        Scenario()  # sensorn mäter bara när dagsljuset ställs
        .daylight(10)
        .start_calibration()
        .wait(minutes=3)
        .expect_calibration("avbruten", "har inte mätt något")
        .expect_lamp_light(0)
    )


def test_another_sensor_in_the_room_does_not_disturb_the_measurement() -> None:
    """Ett rum med två rumskort mäts med det som mätte senast när kalibreringen startade."""
    scenario = Scenario(lamp_light=True).daylight(10).start_calibration()
    for _ in range(60):
        scenario.controller.observe(LightLevelObserved("annat-kort", "vardagsrum", 500))
        scenario.wait(seconds=2)
    scenario.expect_calibration("klar").expect_lamp_light(CEILING_LAMP)


def test_automation_pauses_during_the_calibration() -> None:
    """Ingen regel tänder lamporna medan de mäts, fast någon kommer in i det mörka rummet."""
    (
        Scenario(lamp_light=True)
        .daylight(10)
        .start_calibration()
        .person_enters()
        .wait(seconds=10)
        .expect_lamp_off()
        .expect_explanation("Lampkalibrering pågår i Vardagsrum")
        .wait_for_calibration()
        .expect_calibration("klar")
        .expect_lamp_on()  # automatiken tar över igen: det är mörkt och någon är där
    )


def test_the_measured_light_keeps_the_lamp_from_switching_itself_off() -> None:
    """Med kalibreringen räknas lampans eget ljus inte längre som dagsljus."""
    # Mörkt under 95 lux och ljust över 100. Lampan lyser upp sensorn med 20 lux.
    close_to_bright = home_with_settings(RoomSettings(dark_below_lux=95, bright_above_lux=100))
    (
        Scenario(close_to_bright, lamp_light=True)
        .daylight(90)
        .person_enters()
        .expect_lamp_on()
        .wait(minutes=11)
        # Okalibrerad räknas lampans eget ljus som dagsljus: 110 lux, så den släcker sig själv.
        .expect_event("Släcktes: dagsljuset")
    )
    calibrated = (
        Scenario(close_to_bright, lamp_light=True)
        .daylight(90)
        .start_calibration()
        .wait_for_calibration()
        .expect_lamp_light(CEILING_LAMP)
        .person_enters()
        .expect_lamp_on()
        .wait(hours=1)
        .expect_lamp_on()
    )
    assert not any("dagsljuset" in command.reason for command in calibrated.lamps.history)


def test_recalibrating_measures_again() -> None:
    """Flyttas lampan kan den kalibreras om, och det nya bidraget ersätter det gamla."""
    (
        Scenario(lamp_light=True)
        .daylight(10)
        .start_calibration()
        .wait_for_calibration()
        .expect_lamp_light(CEILING_LAMP)
        .move_lamp("taklampa", x=2.5, y=1)  # nu 1 meter från rumskortet
        .start_calibration()
        .wait_for_calibration()
        .expect_calibration("klar")
        .expect_lamp_light(LAMP_LUX_AT_ONE_METER)
    )


def test_a_cloud_during_the_calibration_stops_it() -> None:
    """Ändras dagsljuset kraftigt mellan mätningarna avbryts kalibreringen och inget sparas."""
    (
        Scenario(lamp_light=True)
        .daylight(10)
        .person_enters()
        .expect_lamp_on()
        .start_calibration()
        .wait(seconds=25)  # ljuset utan lampor är mätt, nu mäts taklampan
        .daylight(200)
        .wait_for_calibration()
        .expect_calibration("avbruten", "dagsljuset ändrades")
        .expect_lamp_light(0)
        .expect_lamp_on()  # lampan är som förut
    )


def test_light_that_never_settles_stops_the_calibration() -> None:
    """Fladdrar ljuset hela tiden avbryts kalibreringen efter en stund."""
    scenario = Scenario(lamp_light=True).daylight(10).start_calibration()
    for second in range(0, round(STEP_TIMEOUT_SECONDS) + 10, 5):
        scenario.daylight(10 if second % 10 else 60).wait(seconds=5)
    scenario.expect_calibration("avbruten", "blev aldrig jämnt").expect_lamp_light(0)


def test_losing_the_sensor_stops_the_calibration() -> None:
    (
        Scenario(lamp_light=True)
        .daylight(10)
        .start_calibration()
        .wait(seconds=5)
        .source_lost()
        .expect_calibration("avbruten", "ljussensorn")
        .expect_lamp_off()
    )


def test_a_lamp_that_stops_responding_stops_the_calibration() -> None:
    (
        Scenario(two_lamp_home(), lamp_light=True)
        .daylight(10)
        .start_calibration()
        .wait(seconds=5)
        .lamp_stops_responding("golvlampa")
        .expect_calibration("avbruten", "Golvlampa slutade svara")
    )


def test_a_remote_control_stops_the_calibration_and_wins() -> None:
    """Den som tänder lampan med fjärrkontrollen vill ha den tänd, så den släcks inte igen."""
    (
        Scenario(lamp_light=True)
        .daylight(10)
        .start_calibration()
        .wait(seconds=5)
        .lamp_changed_with_remote(True)
        .expect_calibration("avbruten", "fjärrkontroll")
        .wait(seconds=30)
        .expect_lamp_on()
        .expect_manual()
    )


def test_a_remote_control_puts_the_other_lamps_back() -> None:
    """Bara lampan som någon rörde lämnas; den andra lampan lyser inte kvar för mätningen."""
    (
        Scenario(two_lamp_home(), lamp_light=True)
        .daylight(10)
        .start_calibration()
        .wait(seconds=25)  # nu lyser taklampan för att mätas
        .expect_lamp_on("taklampa")
        .lamp_changed_with_remote(True, "golvlampa")
        .expect_calibration("avbruten", "Golvlampa ändrades")
        .expect_lamp_off("taklampa")
        .expect_lamp_on("golvlampa")
    )


def test_switching_a_lamp_by_hand_stops_the_calibration() -> None:
    (
        Scenario(lamp_light=True)
        .daylight(10)
        .start_calibration()
        .wait(seconds=20)
        .switch_lamp(False)
        .expect_calibration("avbruten", "för hand")
        .wait(seconds=30)
        .expect_lamp_off()
    )


def test_cancelling_puts_the_lamps_back() -> None:
    (
        Scenario(lamp_light=True)
        .daylight(10)
        .person_enters()
        .start_calibration()
        .expect_lamp_off()
        .cancel_calibration()
        .expect_calibration("avbruten", "tryckte på Avbryt")
        .expect_lamp_on()
    )


def test_changing_the_room_in_the_editor_stops_the_calibration() -> None:
    (
        Scenario(lamp_light=True)
        .daylight(10)
        .start_calibration()
        .remove_lamps()
        .expect_calibration("avbruten", "kartredigeraren")
    )


def test_calibration_needs_lamps_a_light_level_and_one_room_at_a_time() -> None:
    windowless = home_with_settings(RoomSettings(windowless=True))
    Scenario(windowless).daylight(10).expect_calibration_refused("saknar fönster")
    Scenario().expect_calibration_refused("Ingen ljussensor")
    Scenario(lamps_report=False).daylight(10).expect_calibration_refused("svarar inte")
    Scenario(one_room_home()).daylight(10).remove_lamps().expect_calibration_refused(
        "inga lampor"
    )
    (
        Scenario(lamp_light=True)
        .daylight(10)
        .start_calibration()
        .expect_calibration_refused("pågår redan")
    )
