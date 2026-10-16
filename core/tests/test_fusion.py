"""Sammanvägningen som enhet: spår, hopp, numrering och källbortfall.

Hur närvaron känns för den som bor i hemmet prövas i test_scenarios.py.
Här prövas det som inte syns lika lätt där, till exempel att spåren följer
personerna och inte radarns numrering.
"""

import math

import pytest

from homecontrol.connectors import MotionObserved, RadarTarget, RadarTargetsObserved
from homecontrol.fusion import HOLD_SECONDS, Fusion, Occupancy
from homes import one_room_home, radar_sees, room

HOME = one_room_home()
ROOM = HOME.rooms[0]
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
            seen = RadarTargetsObserved(seen.source_id, seen.node_id, seen.targets[::-1])
        fusion.radar(seen, now=step / 10)

    assert positions(fusion) == [(4, 2), (1, 2.4)]
    assert not any(track.held for track in fusion.tracks())


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

    fusion.source_lost("kort", now=1)
    assert fusion.tracks() == ()
    assert fusion.rooms()[ROOM.id].status is Occupancy.UNKNOWN

    fusion.radar(radar_sees(source_id="kort"), now=2)
    assert fusion.rooms()[ROOM.id].status is Occupancy.VACANT


def test_measurements_from_unknown_nodes_and_bad_values_are_ignored() -> None:
    fusion = Fusion(HOME, now=0)

    fusion.radar(RadarTargetsObserved("sim", "finns-inte", (RadarTarget(0, 2),)), now=0)
    fusion.radar(RadarTargetsObserved("sim", NODE.id, (RadarTarget(math.nan, 2),)), now=0)
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


def test_too_many_targets_are_cut() -> None:
    fusion = Fusion(HOME, now=0)
    many = [(0.5 + 0.2 * i, 3.0) for i in range(20)]

    fusion.radar(radar_sees(*many), now=0)

    assert len(fusion.tracks()) == 16


def test_node_coordinates_are_turned_into_home_coordinates() -> None:
    """Radarn ser någon 2 meter rakt fram, och kortet sitter i (2.5, 0) och tittar nedåt."""
    fusion = Fusion(HOME, now=0)
    straight_ahead = math.hypot(2, NODE.height - 1)  # radarn mäter snett ned mot kroppen

    fusion.radar(RadarTargetsObserved("sim", NODE.id, (RadarTarget(0, straight_ahead),)), now=0)

    [track] = fusion.tracks()
    assert track.position.x == pytest.approx(2.5)
    assert track.position.y == pytest.approx(2.0)
