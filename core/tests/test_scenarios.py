"""Scenarier för regeln och sammanvägningen, i snabbspolad tid.

Om inget annat anges är hemmet ett vardagsrum på 5 × 4 meter med en taklampa
i mitten och ett rumskort mitt på övre väggen.

En person som försvinner mitt i rummet hålls kvar i 10 sekunder innan rummet
räknas som tomt (se fusion.py), så lampan släcks 3 minuter och 10 sekunder
efter att personen gick.
"""

import pytest

from homecontrol.home import RoomSettings, ZoneKind
from homes import home_with_settings, home_with_zones, side_by_side_home, two_floor_home, zone
from scenario import Scenario

SOFA = zone("soffa", ZoneKind.STILL, 0.5, 2.5, 2, 1.5)
"""En soffa i vardagsrummets nedre vänstra hörn."""

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
        .wait_for_hold()
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
        .wait_for_hold()
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
        .wait_for_hold()
        .wait(minutes=3)
        .expect_lamp_off()
    )


def test_lost_source_makes_the_room_unknown_and_leaves_the_lamp() -> None:
    """Rumskortet slutar svara: läget blir okänt och lampan lämnas som den är.

    När kortet svarar igen och ingen syns är rummet tomt från och med då.
    """
    (
        Scenario()
        .light_level(10)
        .person_enters()
        .expect_lamp_on()
        .source_lost()
        .expect_room("okänt")
        .wait(minutes=30)
        .expect_lamp_on()
        .expect_explanation("okänt")
        .source_returns()
        .light_level(10)  # ljusnivån glömdes när källan tystnade
        .expect_room("tomt")
        .wait(minutes=2, seconds=59)
        .expect_lamp_on()
        .wait(seconds=1)
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
        .wait_for_hold()
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
        .wait_for_hold()
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
        .wait_for_hold()
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


def test_switching_off_the_simulation_makes_the_rooms_unknown() -> None:
    """Simuleringsläget slås av: de virtuella korten tystnar, så läget blir okänt."""
    (
        Scenario()
        .daylight(10)
        .person_enters()
        .expect_lamp_on()
        .simulation_off()
        .expect_room("okänt")
        .wait(minutes=10)
        .expect_lamp_on()
    )


# --- Sammanvägningen ---


def test_dot_seen_through_the_wall_does_not_light_the_neighbour() -> None:
    """Radarn i rum A ser genom väggen in i rum B. Personen i B tänder bara B."""
    (
        Scenario(side_by_side_home())
        .daylight(10)
        .person_moves_to(x=5.5, y=2)  # i B, men bara en halvmeter från väggen mot A
        .expect_room("tomt", room="a")
        .expect_room("upptaget", room="b")
        .expect_lamp_off(lamp="lampa-a")
        .expect_lamp_on(lamp="lampa-b")
        .expect_tracks(1)
    )


def test_ignore_zone_gives_no_presence() -> None:
    """Något som rör sig vid fläkten i ignorera-zonen tänder inte lampan."""
    (
        Scenario(home_with_zones(zone("flakt", ZoneKind.IGNORE, 3.5, 2.5, 1.5, 1.5)))
        .light_level(10)
        .person_moves_to(x=4.2, y=3.2)
        .expect_room("tomt")
        .expect_tracks(0)
        .expect_lamp_off()
        .person_moves_to(x=2, y=2)  # utanför zonen räknas personen som vanligt
        .expect_lamp_on()
    )


def test_still_zone_keeps_presence_while_wifi_senses_motion() -> None:
    """Radarn tappar personen i soffan. Wi-Fi känner rörelse, så lampan förblir tänd."""
    (
        Scenario(home_with_zones(SOFA))
        .light_level(10)
        .person_moves_to(x=1, y=3)
        .expect_lamp_on()
        .expect_zone("upptaget", zone="soffa")
        .wifi_motion()
        .radar_loses()
        .expect_tracks(1, held=1)
        .wait(minutes=20)
        .expect_room("upptaget")
        .expect_lamp_on()
        .wifi_motion_stops()  # personen har somnat, eller gått ut utan att radarn såg det
        .expect_room("tomt")
        .expect_tracks(0)
        .wait(minutes=2, seconds=59)
        .expect_lamp_on()
        .wait(seconds=1)
        .expect_lamp_off()
    )


def test_still_zone_holds_presence_at_most_30_minutes() -> None:
    """Även om Wi-Fi fortsätter känna rörelse släpps det tappade spåret efter 30 minuter."""
    (
        Scenario(home_with_zones(SOFA))
        .light_level(10)
        .wifi_motion()
        .person_moves_to(x=1, y=3)
        .radar_loses()
        .wait(minutes=29, seconds=59)
        .expect_room("upptaget")
        .wait(seconds=1)
        .expect_room("tomt")
        .wait(minutes=3)
        .expect_lamp_off()
    )


def test_still_zone_without_wifi_motion_holds_like_the_rest_of_the_room() -> None:
    """Utan Wi-Fi-rörelse hålls ett tappat spår i soffan bara de vanliga 10 sekunderna."""
    (
        Scenario(home_with_zones(SOFA))
        .light_level(10)
        .person_moves_to(x=1, y=3)
        .radar_loses()
        .wait(seconds=9)
        .expect_room("upptaget")
        .wait(seconds=1)
        .expect_room("tomt")
    )


def test_passer_by_does_not_take_the_track_of_the_person_on_the_sofa() -> None:
    """Någon går förbi soffan medan radarn har tappat den som sitter där, och går sedan ut.

    Spåret i soffan ska ligga kvar, så rummet är fortfarande upptaget.
    """
    (
        Scenario(home_with_zones(SOFA))
        .light_level(10)
        .wifi_motion()
        .person_moves_to(x=1, y=3)
        .radar_loses()
        .wait(minutes=1)
        .person_moves_to(x=3.5, y=2.2, who="Bo")
        .walk(seconds=2, Bo=(4.5, 1))
        .person_leaves(who="Bo")
        .wait(minutes=5)
        .expect_tracks(1, held=1)
        .expect_zone("upptaget", zone="soffa")
        .expect_lamp_on()
    )


def test_lost_track_in_the_middle_of_the_room_is_gone_after_10_seconds() -> None:
    """Radarn tappar personen mitt i rummet: spåret hålls kvar i 10 s, sedan är rummet tomt."""
    (
        Scenario()
        .light_level(10)
        .person_enters()
        .radar_loses()
        .expect_tracks(1, held=1)
        .wait(seconds=9)
        .expect_room("upptaget")
        .wait(seconds=1)
        .expect_room("tomt")
        .expect_tracks(0)
    )


def test_wifi_motion_does_not_keep_a_track_outside_the_still_zone() -> None:
    """Wi-Fi förlänger bara i stillhetszoner; mitt i rummet gäller 10 sekunder ändå."""
    (
        Scenario()
        .light_level(10)
        .wifi_motion()
        .person_enters()
        .radar_loses()
        .wait_for_hold()
        .expect_room("tomt")
    )


def test_person_who_disappears_in_the_entrance_zone_is_gone_at_once() -> None:
    """Personen går till dörren och försvinner där: rummet är tomt direkt."""
    (
        Scenario(home_with_zones(zone("dorr", ZoneKind.ENTRANCE, 4, 2.5, 1, 1.5)))
        .light_level(10)
        .person_enters()
        .walk(seconds=2, personen=(4.5, 3.2))
        .expect_zone("upptaget", zone="dorr")
        .person_leaves()
        .expect_room("tomt")
        .expect_tracks(0)
        .wait(minutes=2, seconds=59)
        .expect_lamp_on()
        .wait(seconds=1)
        .expect_lamp_off()
    )


def test_two_persons_crossing_keep_their_own_rooms() -> None:
    """Anna och Bo byter rum genom dörröppningen samtidigt och möts på vägen.

    Det är fortfarande två spår, och båda rummen är upptagna. När Anna sedan
    går hemifrån släcks bara hennes nya rum.
    """
    (
        Scenario(side_by_side_home())
        .daylight(10)
        .person_moves_to(x=2, y=2, who="Anna")
        .person_moves_to(x=8, y=2.2, who="Bo")
        .expect_tracks(2)
        .walk(seconds=4, Anna=(8, 2.2), Bo=(2, 2))
        .expect_tracks(2)
        .expect_room("upptaget", room="a")
        .expect_room("upptaget", room="b")
        .person_leaves(who="Anna")
        .wait_for_hold()
        .wait(minutes=3)
        .expect_lamp_off(lamp="lampa-b")
        .expect_lamp_on(lamp="lampa-a")
        .expect_lamp_switches(True, lamp="lampa-a")
    )


def test_wifi_motion_alone_does_not_start_presence() -> None:
    """Med standardvikten förstärker Wi-Fi bara radarn. Rörelse ensam tänder inget."""
    (
        Scenario()
        .light_level(10)
        .wifi_motion()
        .expect_room("tomt")
        .expect_lamp_off()
    )


def test_full_wifi_weight_lets_wifi_motion_start_presence() -> None:
    """Med vikten 1 räcker Wi-Fi-rörelse ensam, till exempel i ett rum som radarn inte täcker."""
    (
        Scenario(home_with_zones(settings=RoomSettings(wifi_weight=1)))
        .light_level(10)
        .wifi_motion()
        .expect_room("upptaget")
        .expect_lamp_on()
        .wifi_motion_stops()
        .expect_room("tomt")
    )


def test_zero_wifi_weight_ignores_wifi_in_the_still_zone() -> None:
    """Med vikten 0 används inte Wi-Fi, så soffan håller bara de vanliga 10 sekunderna."""
    (
        Scenario(home_with_zones(SOFA, settings=RoomSettings(wifi_weight=0)))
        .light_level(10)
        .wifi_motion()
        .person_moves_to(x=1, y=3)
        .radar_loses()
        .wait_for_hold()
        .expect_room("tomt")
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
