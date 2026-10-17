"""Sammanvägningen som enhet: spår, hopp, numrering och källbortfall.

Hur närvaron känns för den som bor i hemmet prövas i test_scenarios.py.
Här prövas det som inte syns lika lätt där, till exempel att spåren följer
personerna och inte radarns numrering.
"""

import math

import pytest

from homecontrol.connectors import MotionObserved, RadarTarget, RadarTargetsObserved, SourceLost
from homecontrol.fusion import HOLD_SECONDS, STILL_HOLD_MAX_SECONDS, Fusion, Occupancy, Presence
from homecontrol.home import Capability, Home, Point, RadarModel, RoomSettings, ZoneKind
from homes import home_with_zones, one_room_home, radar_sees, room, side_by_side_home, zone

HOME = one_room_home()
ROOM = HOME.rooms[0]
LD2450 = RadarModel.LD2450
NODE = ROOM.nodes[0]


def positions(fusion: Fusion) -> list[tuple[float, float]]:
    return [(round(t.position.x, 1), round(t.position.y, 1)) for t in fusion.tracks()]


def test_unreasonable_jump_starts_a_new_track_instead() -> None:
    fusion = Fusion(HOME, now=0)
    fusion.radar(radar_sees((1, 1)), now=0)

    fusion.radar(radar_sees((4, 3.5)), now=0.1)  # 3,5 meter på en tiondels sekund

    assert positions(fusion) == [(1, 1), (4, 3.5)]
    assert [t.held for t in fusion.tracks()] == [True, False]


def test_track_is_smoothed_while_someone_stands_still() -> None:
    fusion = Fusion(HOME, now=0)
    jitter = [(2.0, 2.0), (2.3, 1.8), (1.8, 2.2), (2.2, 2.1), (1.9, 1.9)]

    for step, point in enumerate(jitter):
        fusion.radar(radar_sees(point), now=step / 10)

    [track] = fusion.tracks()
    assert math.dist(track.position, (2, 2)) < 0.15


def test_tracks_follow_the_persons_and_not_the_radar_numbering() -> None:
    """Två personer går förbi varandra. Radarn byter deras nummer varje gång, men spåren inte."""
    fusion = Fusion(HOME, now=0)
    for step in range(31):
        anna = (1 + step * 0.1, 2.0)
        bo = (4 - step * 0.1, 2.4)
        seen = radar_sees(anna, bo)
        if step % 2:
            seen = RadarTargetsObserved(
                seen.source_id, seen.node_id, seen.targets[::-1], seen.radar
            )
        fusion.radar(seen, now=step / 10)

    assert positions(fusion) == [(4, 2), (1, 2.4)]
    assert not any(track.held for track in fusion.tracks())


def test_each_track_keeps_its_own_random_id_and_not_the_radar_numbering() -> None:
    fusion = Fusion(HOME, now=0)
    fusion.radar(radar_sees((1, 2), (4, 2)), now=0)
    first = [track.id for track in fusion.tracks()]

    fusion.radar(radar_sees((4.1, 2), (1.1, 2)), now=0.1)

    assert [track.id for track in fusion.tracks()] == first
    assert len(set(first)) == 2
    assert not set(first) & {"0", "1", "2"}


def test_lost_track_is_released_when_its_time_is_up_even_if_asked_later() -> None:
    fusion = Fusion(HOME, now=0)
    fusion.radar(radar_sees((2, 2)), now=0)
    fusion.radar(radar_sees(), now=1)
    assert fusion.wake_at == 1 + HOLD_SECONDS

    fusion.update(now=100)

    assert fusion.tracks() == ()
    room = fusion.rooms()[ROOM.id]
    assert room.status is Occupancy.VACANT
    assert room.since == 1 + HOLD_SECONDS


def test_radar_finds_a_held_track_again() -> None:
    fusion = Fusion(HOME, now=0)
    fusion.radar(radar_sees((2, 2)), now=0)
    fusion.radar(radar_sees(), now=1)

    fusion.radar(radar_sees((2.5, 2.2)), now=5)

    assert positions(fusion) == [(2.5, 2.2)]
    assert not fusion.tracks()[0].held


def test_lost_source_makes_the_room_unknown_until_it_is_heard_again() -> None:
    fusion = Fusion(HOME, now=0)
    fusion.radar(radar_sees((2, 2), source_id="kort"), now=0)

    fusion.source_lost(SourceLost("kort"), now=1)
    assert fusion.tracks() == ()
    assert fusion.rooms()[ROOM.id].status is Occupancy.UNKNOWN

    fusion.radar(radar_sees(source_id="kort"), now=2)
    assert fusion.rooms()[ROOM.id].status is Occupancy.VACANT


def test_measurements_from_unknown_nodes_and_bad_values_are_ignored() -> None:
    fusion = Fusion(HOME, now=0)

    fusion.radar(RadarTargetsObserved("sim", "finns-inte", (RadarTarget(0, 2),), LD2450), now=0)
    fusion.radar(RadarTargetsObserved("sim", NODE.id, (RadarTarget(math.nan, 2),), LD2450), now=0)
    fusion.motion(MotionObserved("sim", NODE.id, True, score=1.5), now=0)
    fusion.motion(MotionObserved("sim", NODE.id, True, score=math.nan), now=0)

    assert fusion.tracks() == ()
    # Wi-Fi-rörelsen kom aldrig in, så soffan skulle inte hållas kvar av den.
    fusion.radar(radar_sees((2, 2)), now=1)
    fusion.radar(radar_sees(), now=2)
    assert fusion.wake_at == 2 + HOLD_SECONDS


def test_tracks_of_a_moved_node_are_forgotten() -> None:
    fusion = Fusion(HOME, now=0)
    fusion.radar(radar_sees((2, 2)), now=0)
    floor = HOME.floors[0]
    turned = room("vardagsrum", "Vardagsrum", 0, 0, 5, 4, lamp="taklampa").model_copy(
        update={"nodes": (NODE.model_copy(update={"rotation": 80.0}),)}
    )

    fusion.replace_home(
        HOME.model_copy(update={"floors": (floor.model_copy(update={"rooms": (turned,)}),)}),
        now=1,
    )

    assert fusion.tracks() == ()


def test_tracks_of_an_unchanged_node_are_kept() -> None:
    fusion = Fusion(HOME, now=0)
    fusion.radar(radar_sees((2, 2)), now=0)

    fusion.replace_home(one_room_home(), now=1)

    assert positions(fusion) == [(2, 2)]
    assert fusion.rooms()[ROOM.id].since == pytest.approx(0)


@pytest.mark.parametrize(("radar", "limit"), [(RadarModel.LD2450, 3), (RadarModel.LD2461, 5)])
def test_more_targets_than_the_radar_follows_are_cut(
    radar: RadarModel, limit: int, caplog: pytest.LogCaptureFixture
) -> None:
    fusion = Fusion(HOME, now=0)
    many = [(0.5 + 0.6 * i, 3.0) for i in range(7)]

    fusion.radar(radar_sees(*many, radar=radar), now=0)

    assert len(fusion.tracks()) == limit
    assert "för många mål" in caplog.text


def test_an_ld2461_follows_five_people_without_warning(caplog: pytest.LogCaptureFixture) -> None:
    fusion = Fusion(HOME, now=0)
    five = [(0.5 + i, 3.0) for i in range(5)]

    fusion.radar(radar_sees(*five, radar=RadarModel.LD2461), now=0)

    assert sorted(positions(fusion)) == pytest.approx(sorted(five))
    assert fusion.rooms()[ROOM.id].status is Occupancy.OCCUPIED
    assert "för många mål" not in caplog.text


def test_node_coordinates_are_turned_into_home_coordinates() -> None:
    """Radarn ser någon 2 meter rakt fram, och kortet sitter i (2.5, 0) och tittar nedåt."""
    fusion = Fusion(HOME, now=0)
    straight_ahead = math.hypot(2, NODE.height - 1)  # radarn mäter snett ned mot kroppen

    fusion.radar(
        RadarTargetsObserved("sim", NODE.id, (RadarTarget(0, straight_ahead),), LD2450), now=0
    )

    [track] = fusion.tracks()
    assert track.position.x == pytest.approx(2.5)
    assert track.position.y == pytest.approx(2.0)


def seen_from(home: Home, node_id: str, *points: tuple[float, float]) -> RadarTargetsObserved:
    """Det noden `node_id` i hemmet ser när personer står i `points` (hemmets meter)."""
    node = next(n for r in home.rooms for n in r.nodes if n.id == node_id)
    targets = tuple(RadarTarget(*node.home_to_radar(Point(x, y))) for x, y in points)
    return RadarTargetsObserved("sim", node_id, targets, LD2450)


@pytest.mark.parametrize(
    ("doorway", "in_a", "in_b"),
    [
        (True, (4.7, 2.0), (5.3, 2.2)),  # en halvmeter isär i dörröppningen
        (False, (4.9, 2.0), (5.1, 2.0)),  # tätt intill varandra, men med en vägg emellan
    ],
)
def test_persons_on_each_side_of_the_wall_keep_their_own_rooms(
    doorway: bool, in_a: tuple[float, float], in_b: tuple[float, float]
) -> None:
    """Två personer står på var sin sida om gränsen mellan rummen. Båda radarerna ser båda.

    Varje radar kastar personen i grannrummet, och spåret i grannrummet tas
    inte över av den som står på andra sidan.
    """
    home = side_by_side_home(doorway=doorway)
    fusion = Fusion(home, now=0)
    for frame in range(20):
        node = "nod-a" if frame % 2 == 0 else "nod-b"
        fusion.radar(seen_from(home, node, in_a, in_b), now=frame * 0.05)
        rooms = fusion.rooms()
        if frame > 0:
            assert rooms["a"].status is Occupancy.OCCUPIED
            assert rooms["b"].status is Occupancy.OCCUPIED
            assert rooms["a"].since == 0
    assert positions(fusion) == [in_a, in_b]


def test_late_wifi_motion_does_not_revive_a_released_track() -> None:
    """Spåret i soffan skulle ha släppts vid 11 s. Rörelse som kommer efteråt ändrar inte det."""
    home = home_with_sofa()
    fusion = Fusion(home, now=0)
    fusion.radar(radar_sees((1, 3), home=home), now=0)
    fusion.radar(radar_sees(home=home), now=1)
    assert fusion.wake_at == 1 + HOLD_SECONDS

    fusion.motion(MotionObserved("sim", NODE.id, True), now=15)

    assert fusion.tracks() == ()
    assert fusion.rooms()[ROOM.id].since == 1 + HOLD_SECONDS


def test_weak_wifi_motion_does_not_hold_the_sofa_with_the_default_weight() -> None:
    """Vikten 0,5 gånger poängen 0,4 är under gränsen för att hålla kvar spåret."""
    home = home_with_sofa()
    fusion = Fusion(home, now=0)
    fusion.motion(MotionObserved("sim", NODE.id, True, score=0.4), now=0)
    fusion.radar(radar_sees((1, 3), home=home), now=0)
    fusion.radar(radar_sees(home=home), now=1)
    assert fusion.wake_at == 1 + HOLD_SECONDS

    fusion.motion(MotionObserved("sim", NODE.id, True, score=0.8), now=2)
    assert fusion.wake_at == 1 + STILL_HOLD_MAX_SECONDS


def test_absurd_values_from_the_radar_are_ignored_and_warned_about_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fusion = Fusion(HOME, now=0)
    absurd = RadarTargetsObserved(
        "sim", NODE.id, (RadarTarget(1e200, 2), RadarTarget(2, -1e200)), LD2450
    )

    for step in range(10):
        fusion.radar(absurd, now=step / 10)

    assert fusion.tracks() == ()
    assert caplog.text.count("Ogiltigt mål") == 1


def test_unknown_nodes_are_warned_about_once_each_and_not_without_end(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fusion = Fusion(HOME, now=0)

    for i in range(500):
        for _ in range(3):
            fusion.radar(RadarTargetsObserved("sim", f"okänd-{i}\nfalsk rad", (), LD2450), now=0)

    assert caplog.text.count("ignoreras") == 100
    assert caplog.text.count("Fler problem") == 1
    # Nod-id:t skrivs med citattecken, så en radbrytning i det kan inte göra en falsk loggrad.
    assert "\nfalsk rad" not in caplog.text


def home_with_sofa() -> Home:
    return home_with_zones(zone("soffa", ZoneKind.STILL, 0.5, 2.5, 2, 1.5))


@pytest.mark.parametrize("doorway", [True, False])
def test_track_lost_for_a_moment_is_not_taken_through_a_wall(doorway: bool) -> None:
    """Radarn i A tappar Anna en kort stund, när Bo står en halvmeter bort i B.

    Genom en vägg kan Bo inte ta över Annas spår. Genom en dörröppning kan
    han det, men då finns Anna kvar i A så snart radarn ser henne igen.
    """
    home = side_by_side_home(doorway=doorway)
    fusion = Fusion(home, now=0)
    fusion.radar(seen_from(home, "nod-a", (4.8, 2)), now=0)
    fusion.radar(seen_from(home, "nod-a"), now=0.1)  # ett kort avbrott

    fusion.radar(seen_from(home, "nod-b", (5.3, 2)), now=0.2)
    if not doorway:
        assert fusion.rooms()["a"].status is Occupancy.OCCUPIED
    fusion.radar(seen_from(home, "nod-a", (4.8, 2)), now=0.3)

    assert fusion.rooms()["a"].status is Occupancy.OCCUPIED
    assert fusion.rooms()["b"].status is Occupancy.OCCUPIED


def test_weaker_wifi_motion_releases_the_sofa_from_now_and_not_before() -> None:
    """Rörelsen blir för svag för att hålla kvar spåret. Rummet är tomt från då, inte före."""
    home = home_with_sofa()
    fusion = Fusion(home, now=0)
    fusion.motion(MotionObserved("sim", NODE.id, True, score=0.9), now=0)
    fusion.radar(radar_sees((1, 3), home=home), now=0)
    fusion.radar(radar_sees(home=home), now=1)

    fusion.motion(MotionObserved("sim", NODE.id, True, score=0.3), now=600)

    assert fusion.tracks() == ()
    assert fusion.rooms()[ROOM.id] == Presence(Occupancy.VACANT, 600)


def test_wifi_from_a_lost_source_releases_the_sofa_from_then() -> None:
    """Wi-Fi-mätningen kommer från en annan källa än radarn och tystnar."""
    home = home_with_sofa()
    fusion = Fusion(home, now=0)
    fusion.motion(MotionObserved("wifi", NODE.id, True), now=0)
    fusion.radar(radar_sees((1, 3), home=home), now=0)
    fusion.radar(radar_sees(home=home), now=1)

    fusion.source_lost(SourceLost("wifi"), now=600)

    assert fusion.tracks() == ()
    assert fusion.rooms()[ROOM.id].since == 600


def test_a_part_that_never_answered_makes_the_room_unknown() -> None:
    fusion = Fusion(HOME, now=0)

    fusion.source_lost(SourceLost("kort/radar", NODE.id, Capability.RADAR), now=0)
    assert fusion.rooms()[ROOM.id].status is Occupancy.UNKNOWN

    fusion.radar(radar_sees(source_id="kort/radar"), now=1)
    assert fusion.rooms()[ROOM.id].status is Occupancy.VACANT


def test_wifi_still_talking_does_not_hide_a_silent_radar() -> None:
    fusion = Fusion(HOME, now=0)
    fusion.radar(radar_sees((2, 2), source_id="kort/radar"), now=0)
    fusion.motion(MotionObserved("kort/csi", NODE.id, False), now=0)

    fusion.source_lost(SourceLost("kort/radar"), now=1)
    fusion.motion(MotionObserved("kort/csi", NODE.id, False), now=2)

    assert fusion.tracks() == ()
    assert fusion.rooms()[ROOM.id].status is Occupancy.UNKNOWN


def test_silent_wifi_does_not_matter_in_a_room_that_does_not_use_it() -> None:
    home = home_with_zones(settings=RoomSettings(wifi_weight=0))
    fusion = Fusion(home, now=0)
    fusion.radar(radar_sees(home=home, source_id="kort/radar"), now=0)

    fusion.source_lost(SourceLost("kort/csi", NODE.id, Capability.CSI), now=1)

    assert fusion.rooms()[ROOM.id].status is Occupancy.VACANT
