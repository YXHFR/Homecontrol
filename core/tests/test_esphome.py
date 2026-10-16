"""Källkopplingen ESPHome, prövad mot inspelade exempelmeddelanden från ett rumskort.

Exemplen i esphome_exempel/ har samma form som aioesphomeapi lämnar dem och
samma entiteter som firmware-paketet. Nätverket byts mot en låtsad länk, så
testerna styr när kortet ansluter, vad det skickar och när det tystnar.
Allt går genom den riktiga kärnan (sammanvägningen och regeln).
"""

from collections.abc import Callable
from typing import Any

import pytest
from aioesphomeapi import model
from aioesphomeapi.model import UpdateState

from fake_cards import CARD, SECRET, FakeLink, FakeNetwork, Recording, card_home, recording
from homecontrol.clock import ManualClock
from homecontrol.connectors import (
    LightLevelObserved,
    MotionObserved,
    Observation,
    RadarTarget,
    RadarTargetsObserved,
    SourceLost,
)
from homecontrol.controller import Controller
from homecontrol.esphome import (
    MAX_DISCOVERED,
    NOT_ANSWERING,
    STALE_SECONDS,
    CardReader,
    Discovered,
    EsphomeSource,
    card_key,
    part_source,
)
from homecontrol.fusion import Occupancy
from homecontrol.home import Capability, Home
from homecontrol.simulation import SimulatedLampOutput


class Harness:
    """Kärnan med ESPHome-kopplingen och ett låtsat nätverk.

    Observationerna skickas när ett ögonblick i inspelningen är slut, som när
    händelseloopen har läst klart allt kortet skickade på en gång.
    """

    def __init__(self, home: Home | None = None) -> None:
        self.home = home if home is not None else card_home()
        self.now = 0.0
        self.clock = ManualClock()
        self.lamps = SimulatedLampOutput()
        self.controller = Controller(self.home, self.clock, [self.lamps])
        self.network = FakeNetwork()
        self._deferred: list[Callable[[], None]] = []
        self.observed: list[Observation] = []
        self.source = EsphomeSource(
            SECRET,
            self.home,
            self.network,
            defer=self._deferred.append,
            clock=lambda: self.now,
        )
        self.source.start(self._observe)

    def _observe(self, observation: Observation) -> None:
        self.observed.append(observation)
        self.controller.observe(observation)

    @property
    def link(self) -> FakeLink:
        return self.network.links[CARD]

    def settle(self) -> None:
        while self._deferred:
            self._deferred.pop(0)()

    def connect(self, recorded: Recording) -> None:
        self.link.events.connected(recorded.entities)
        self.settle()

    def play(self, recorded: Recording, *, until: float = float("inf")) -> None:
        """Spelar upp det kortet skickade, fram till och med tiden `until`."""
        for at, state in recorded.states:
            if at > until:
                break
            if at > self.now:
                self.settle()
                self.advance(at - self.now)
            self.link.events.state(state)
        self.settle()

    def advance(self, seconds: float) -> None:
        self.now += seconds
        self.clock.advance_to(self.clock.now() + seconds)
        self.controller.tick()

    def status(self) -> Occupancy:
        return self.controller.snapshot().rooms[0].status

    def lux(self) -> float | None:
        return self.controller.snapshot().rooms[0].lux

    def lamp_on(self) -> bool:
        return self.controller.snapshot().lamps[0].on

    def card(self) -> Any:
        [card] = self.source.cards()
        return card


def test_a_placed_card_is_unknown_until_it_answers() -> None:
    harness = Harness()

    assert harness.link.running
    assert harness.status() is Occupancy.UNKNOWN
    assert harness.card().problems == (NOT_ANSWERING,)


def test_person_walking_in_becomes_a_dot_wifi_motion_and_lux() -> None:
    harness = Harness()
    walk = recording("person_gar_in")
    harness.connect(walk)

    harness.play(walk, until=0)
    assert harness.status() is Occupancy.VACANT
    assert harness.lux() == 12.5
    assert not harness.lamp_on()

    harness.play(walk)
    tracks = harness.controller.snapshot().tracks
    assert len(tracks) == 2
    # Radarn sitter två meter upp och ser personen 2,2 meter snett fram,
    # alltså knappt två meter in i rummet längs golvet. Utjämningen hänger
    # efter en bit när personen just har stannat.
    first = min(tracks, key=lambda track: abs(track.position.x - 2.5))
    assert first.position.x == pytest.approx(2.5, abs=0.25)
    assert first.position.y == pytest.approx(1.96, abs=0.25)
    assert harness.status() is Occupancy.OCCUPIED
    assert harness.lux() == 12.7
    assert harness.lamp_on()
    assert harness.card().problems == ()


def test_one_radar_frame_is_one_observation_in_meters() -> None:
    harness = Harness()
    walk = recording("person_gar_in")
    harness.connect(walk)
    harness.play(walk, until=1.0)

    radar = [o for o in harness.observed if isinstance(o, RadarTargetsObserved)]
    assert radar[-1] == RadarTargetsObserved(
        part_source(CARD, Capability.RADAR), CARD, (RadarTarget(-0.6, 3.2),)
    )
    # Ögonblicket vid anslutningen och den första ramen, inte ett per värde.
    assert len(radar) == 2
    assert MotionObserved(part_source(CARD, Capability.CSI), CARD, True) in harness.observed
    assert (
        LightLevelObserved(part_source(CARD, Capability.LUX), "vardagsrum", 12.5)
        in harness.observed
    )


def test_silent_radar_makes_the_room_unknown_while_wifi_keeps_talking() -> None:
    harness = Harness()
    silent = recording("radarn_tystnar")
    harness.connect(silent)
    harness.play(silent, until=3.0)
    assert harness.status() is Occupancy.OCCUPIED

    harness.play(silent, until=5.0)

    assert harness.controller.snapshot().tracks == ()
    assert harness.status() is Occupancy.UNKNOWN
    assert harness.card().problems == ("Radarn skickar inget.",)
    assert SourceLost(part_source(CARD, Capability.RADAR), CARD, Capability.RADAR) in (
        harness.observed
    )

    harness.play(silent)
    assert harness.status() is Occupancy.OCCUPIED
    assert harness.card().problems == ()


def test_silent_wifi_makes_the_room_unknown_only_if_the_room_uses_wifi() -> None:
    for weight, expected in ((0.5, Occupancy.UNKNOWN), (0.0, Occupancy.VACANT)):
        harness = Harness(card_home(wifi_weight=weight))
        walk = recording("person_gar_in")
        harness.connect(walk)
        harness.play(walk, until=0)
        assert harness.status() is Occupancy.VACANT

        harness.advance(STALE_SECONDS + 1)
        harness.source.check()
        harness.settle()

        assert harness.status() is expected
        assert "Wi-Fi-mätningen skickar inget." in harness.card().problems


def test_calibrating_wifi_is_not_motion_and_a_broken_light_sensor_is_unknown_lux() -> None:
    harness = Harness()
    calibration = recording("kalibrering_och_trasig_ljussensor")
    harness.connect(calibration)

    harness.play(calibration, until=1)
    motion = [o for o in harness.observed if isinstance(o, MotionObserved)]
    assert motion and not motion[-1].moving
    assert harness.card().calibrating
    assert harness.lux() == 12.5

    harness.play(calibration)
    assert harness.lux() is None
    assert "Ljussensorn skickar inget." in harness.card().problems
    assert harness.status() is Occupancy.VACANT


def test_lost_connection_warns_and_makes_the_room_unknown() -> None:
    harness = Harness()
    walk = recording("person_gar_in")
    harness.connect(walk)
    harness.play(walk)
    assert harness.lamp_on()

    harness.link.events.disconnected()
    harness.settle()

    assert harness.status() is Occupancy.UNKNOWN
    assert harness.controller.snapshot().tracks == ()
    assert harness.lux() is None
    assert harness.lamp_on()  # lamporna lämnas som de är
    card = harness.card()
    assert not card.online
    assert card.problems == (NOT_ANSWERING,)


def test_new_cards_are_listed_but_used_only_when_placed() -> None:
    harness = Harness(card_home(placed=False))
    harness.source.found(Discovered(CARD, "Homecontrol vardagsrum"))

    [card] = harness.source.cards()
    assert (card.name, card.friendly_name, card.placed) == (CARD, "Homecontrol vardagsrum", False)
    assert harness.network.links == {}

    placed = card_home()
    harness.controller.replace_home(placed)
    harness.source.use_home(placed)

    assert harness.link.running
    assert harness.source.cards()[0].placed
    assert harness.status() is Occupancy.UNKNOWN

    harness.controller.replace_home(card_home(placed=False))
    harness.source.use_home(card_home(placed=False))
    assert not harness.link.running


def test_only_well_named_cards_are_listed_and_not_too_many() -> None:
    harness = Harness(card_home(placed=False))
    for name in ("esphome-web-1a2b3c", "homecontrol-", "homecontrol-Kök", "homecontrol-x.local"):
        harness.source.found(Discovered(name, name))
    assert harness.source.cards() == ()

    for n in range(MAX_DISCOVERED + 5):
        harness.source.found(Discovered(f"homecontrol-rum{n}", "Rum"))
    assert len(harness.source.cards()) == MAX_DISCOVERED

    harness.source.gone("homecontrol-rum0")
    assert len(harness.source.cards()) == MAX_DISCOVERED - 1


def test_firmware_update_is_offered_but_installed_only_when_approved() -> None:
    harness = Harness()
    walk = recording("person_gar_in")
    harness.connect(walk)
    harness.play(walk)

    update = harness.card().update
    assert update.available
    assert (update.current_version, update.latest_version) == ("0.4.0", "0.5.0")
    assert harness.link.installs == []  # inget installeras av sig självt

    assert harness.source.install_update(CARD)
    [firmware] = [e for e in walk.entities if e.name == "Firmware"]
    assert harness.link.installs == [firmware.key]

    harness.link.events.state(
        UpdateState.from_dict(
            {
                "key": firmware.key,
                "current_version": "0.4.0",
                "latest_version": "0.5.0",
                "in_progress": True,
            }
        )
    )
    assert not harness.source.install_update(CARD)  # pågår redan
    assert not harness.source.install_update("homecontrol-kok")  # finns inte

    harness.link.events.disconnected()
    assert not harness.source.install_update(CARD)
    assert harness.link.installs == [firmware.key]


def test_card_without_a_newer_version_is_not_updated() -> None:
    harness = Harness()
    walk = recording("person_gar_in")
    harness.connect(walk)
    [firmware] = [e for e in walk.entities if e.name == "Firmware"]
    harness.link.events.state(
        UpdateState.from_dict(
            {"key": firmware.key, "current_version": "0.5.0", "latest_version": "0.5.0"}
        )
    )

    assert harness.card().update is not None
    assert not harness.card().update.available
    assert not harness.source.install_update(CARD)


def test_old_firmware_without_the_needed_entities_is_reported() -> None:
    harness = Harness()
    walk = recording("person_gar_in")
    harness.link.events.connected([e for e in walk.entities if e.name != "Radar Online"])

    assert any("Radar Online" in problem for problem in harness.card().problems)


def test_each_card_gets_its_own_key_from_the_secret() -> None:
    key = card_key(SECRET, CARD)
    assert len(key) == 32
    assert card_key(SECRET, CARD) == key
    assert card_key(SECRET, "homecontrol-kok") != key
    assert card_key(SECRET + "x", CARD) != key
    assert Harness().link.key == key


def test_short_secret_is_refused() -> None:
    with pytest.raises(ValueError, match="minst"):
        EsphomeSource("kort", card_home(), FakeNetwork())


def test_reader_ignores_values_it_does_not_know() -> None:
    reader = CardReader(CARD)
    walk = recording("person_gar_in")
    reader.use_entities(walk.entities)

    reader.read(model.SensorState.from_dict({"key": 12345, "state": 1.0}), now=0)

    assert reader.observations("vardagsrum") == []
