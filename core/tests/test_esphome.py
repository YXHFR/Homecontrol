"""Källkopplingen ESPHome, prövad mot inspelade exempelmeddelanden från ett rumskort.

Exemplen i esphome_exempel/ har samma form som aioesphomeapi lämnar dem och
samma entiteter som firmware-paketet. Nätverket byts mot en låtsad länk, så
testerna styr när kortet ansluter, vad det skickar och när det tystnar.
Allt går genom den riktiga kärnan (sammanvägningen och regeln).
"""

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from aioesphomeapi import model
from aioesphomeapi.core import (
    APIConnectionError,
    EncryptionPlaintextAPIError,
    InvalidEncryptionKeyAPIError,
)
from aioesphomeapi.model import EntityInfo, EntityState, UpdateState

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
    CARD_SILENT_SECONDS,
    MAX_DISCOVERED,
    NO_ENCRYPTION,
    NOT_ANSWERING,
    OTHER_KEY,
    PROVISION_SECONDS,
    STALE_SECONDS,
    CardReader,
    Discovered,
    EsphomeSource,
    _AioesphomeLink,
    card_key,
    part_source,
)
from homecontrol.fusion import Occupancy
from homecontrol.home import Capability, Home, Point, RadarModel
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

    def lamp_on(self) -> bool | None:
        """None när lampans läge är okänt."""
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
        part_source(CARD, Capability.RADAR), CARD, (RadarTarget(-0.6, 3.2),), RadarModel.LD2450
    )
    # Ögonblicket vid anslutningen och den första ramen, inte ett per värde.
    assert len(radar) == 2
    assert MotionObserved(part_source(CARD, Capability.CSI), CARD, True) in harness.observed
    assert (
        LightLevelObserved(part_source(CARD, Capability.LUX), "vardagsrum", 12.5)
        in harness.observed
    )


def test_an_ld2450_card_says_it_has_an_ld2450() -> None:
    harness = Harness()
    walk = recording("person_gar_in")
    harness.connect(walk)
    harness.play(walk)

    radar = [o for o in harness.observed if isinstance(o, RadarTargetsObserved)]
    assert {o.radar for o in radar} == {RadarModel.LD2450}


def test_an_ld2461_card_sends_four_and_then_five_people_in_meters() -> None:
    """Påhittade meddelanden: LD2461 har inte provats på riktigt än."""
    harness = Harness()
    five = recording("ld2461_fem_personer")
    harness.connect(five)

    harness.play(five, until=1.0)
    radar = [o for o in harness.observed if isinstance(o, RadarTargetsObserved)]
    assert radar[-1].radar is RadarModel.LD2461
    assert len(radar[-1].targets) == 4
    # Första personen står i (1, 1,5) i hemmet. Kortet skickar mm, kärnan får meter.
    seen = harness.home.rooms[0].nodes[0].home_to_radar(Point(1.0, 1.5))
    assert radar[-1].targets[0].x == pytest.approx(seen.x, abs=0.001)
    assert radar[-1].targets[0].y == pytest.approx(seen.y, abs=0.001)

    harness.play(five)
    radar = [o for o in harness.observed if isinstance(o, RadarTargetsObserved)]
    assert len(radar[-1].targets) == 5
    tracks = harness.controller.snapshot().tracks
    assert len(tracks) == 5
    assert harness.status() is Occupancy.OCCUPIED
    assert harness.lamp_on()
    assert harness.card().problems == ()


def test_a_card_installed_again_with_the_other_radar_is_read_as_that_radar() -> None:
    """Samma kort (samma namn) får firmware för LD2461 och sedan för LD2450 igen."""
    harness = Harness()
    five = recording("ld2461_fem_personer")
    harness.connect(five)
    harness.play(five)
    assert len(harness.controller.snapshot().tracks) == 5

    harness.link.events.disconnected()
    walk = recording("person_gar_in")
    harness.connect(walk)
    harness.play(walk, until=1.0)

    radar = [o for o in harness.observed if isinstance(o, RadarTargetsObserved)]
    assert radar[-1].radar is RadarModel.LD2450
    assert radar[-1].targets == (RadarTarget(-0.6, 3.2),)


def test_an_ld2461_card_without_people_sends_no_targets() -> None:
    harness = Harness()
    five = recording("ld2461_fem_personer")
    harness.connect(five)

    harness.play(five, until=0)

    radar = [o for o in harness.observed if isinstance(o, RadarTargetsObserved)]
    assert radar[-1] == RadarTargetsObserved(
        part_source(CARD, Capability.RADAR), CARD, (), RadarModel.LD2461
    )
    assert harness.status() is Occupancy.VACANT


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

        # Ljussensorn hörs var 5:e sekund, men Wi-Fi-mätningen tystnar.
        lux = next(s for _, s in walk.states if s.key == entity_key(walk, "Illuminance"))
        for _ in range(int(STALE_SECONDS // 5) + 1):
            harness.advance(5)
            harness.link.events.state(lux)
        harness.source.check()
        harness.settle()

        assert harness.status() is expected
        assert harness.card().problems == ("Wi-Fi-mätningen skickar inget.",)


def test_a_card_that_goes_completely_silent_is_unknown_before_the_connection_drops() -> None:
    """Strömmen går: inget mer kommer, men anslutningen har ännu inte brutits."""
    harness = Harness(card_home(wifi_weight=0))
    walk = recording("person_gar_in")
    harness.connect(walk)
    harness.play(walk, until=0)

    harness.advance(CARD_SILENT_SECONDS + 1)
    harness.source.check()
    harness.settle()

    assert harness.status() is Occupancy.UNKNOWN
    assert "Radarn skickar inget." in harness.card().problems


def test_calibrating_wifi_makes_the_room_unknown_and_a_broken_light_sensor_unknown_lux() -> None:
    harness = Harness()
    calibration = recording("kalibrering_och_trasig_ljussensor")
    harness.connect(calibration)

    harness.play(calibration, until=1)
    assert not any(isinstance(o, MotionObserved) and o.moving for o in harness.observed)
    assert harness.card().calibrating
    assert harness.card().problems == ("Wi-Fi-mätningen kalibrerar. Håll rummet stilla en stund.",)
    assert harness.status() is Occupancy.UNKNOWN
    assert harness.lux() == 12.5

    harness.play(calibration)
    assert harness.lux() is None
    assert harness.card().problems == ("Ljussensorn skickar inget.",)
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
    reader.use_entities(walk.entities, now=0)

    reader.read(model.SensorState.from_dict({"key": 12345, "state": 1.0}), now=0)

    assert reader.observations("vardagsrum") == []


def test_only_a_just_placed_card_gets_its_key_and_only_once() -> None:
    # Placerat redan när kärnan startade: ingen nyckel, så att ingen som
    # låtsas vara kortet kan locka ut den.
    assert not Harness().link.events.may_provision()

    harness = Harness(card_home(placed=False))
    harness.controller.replace_home(card_home())
    harness.source.use_home(card_home())
    events = harness.link.events
    assert events.may_provision()

    events.provisioned()
    assert not events.may_provision()


def test_the_key_is_offered_only_for_a_while_after_placing() -> None:
    harness = Harness(card_home(placed=False))
    harness.source.use_home(card_home())

    harness.advance(PROVISION_SECONDS + 1)

    assert not harness.link.events.may_provision()


class FakeClient:
    """Det som länken använder av aioesphomeapi:s APIClient."""

    def __init__(self, *, provisionable: bool = True, broken: bool = False) -> None:
        self.provisionable = provisionable
        self.broken = broken
        self.calls: list[str] = []

    async def connect(self, login: bool = False) -> None:
        self.calls.append("connect")

    async def device_info(self) -> model.DeviceInfo:
        return model.DeviceInfo.from_dict({"api_encryption_provisionable": self.provisionable})

    async def noise_encryption_set_key(self, key: bytes) -> bool:
        self.calls.append("set_key")
        return True

    async def disconnect(self) -> None:
        self.calls.append("disconnect")

    async def list_entities_services(self) -> tuple[list[EntityInfo], list[Any]]:
        if self.broken:
            raise APIConnectionError("borta")
        return [], []

    def subscribe_states(self, on_state: Callable[[EntityState], None]) -> None:
        self.calls.append("subscribe")

    def update_command(self, key: int, command: Any) -> None:
        if self.broken:
            raise APIConnectionError("borta")
        self.calls.append(f"update {key}")


class FakeLogic:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def start(self) -> None:
        self.calls.append("start")

    async def stop(self) -> None:
        self.calls.append("stop")


class LinkEvents:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[str] = []

    def connected(self, entities: Any) -> None:
        self.calls.append("connected")

    def state(self, state: EntityState) -> None:
        pass

    def disconnected(self) -> None:
        self.calls.append("disconnected")

    def problem(self, text: str | None) -> None:
        self.calls.append(f"problem: {text}")

    def may_provision(self) -> bool:
        return self.allowed

    def provisioned(self) -> None:
        self.calls.append("provisioned")


def bare_link(events: LinkEvents, client: FakeClient, provisioning: FakeClient) -> Any:
    """En länk utan nätverk: aioesphomeapi byts mot låtsade delar."""
    link: Any = _AioesphomeLink.__new__(_AioesphomeLink)
    link._name = CARD
    link._key = card_key(SECRET, CARD)
    link._events = events
    link._client = client
    link._logic = FakeLogic()
    link._tasks = set()
    link._new_client = lambda noise_psk: provisioning
    return link


def run_link(call: Callable[[], Any]) -> None:
    async def scenario() -> None:
        await call()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_a_new_card_gets_its_key_and_is_connected_again_at_once() -> None:
    events, provisioning = LinkEvents(), FakeClient()
    link = bare_link(events, FakeClient(), provisioning)

    run_link(lambda: link._on_connect_error(InvalidEncryptionKeyAPIError("fel nyckel")))

    assert provisioning.calls == ["connect", "set_key", "disconnect"]
    assert events.calls == ["provisioned"]
    assert link._logic.calls == ["stop", "start"]


@pytest.mark.parametrize(
    ("allowed", "provisionable"), [(False, True), (True, False)], ids=["inte nu", "har nyckel"]
)
def test_a_card_that_rejects_the_key_otherwise_warns(allowed: bool, provisionable: bool) -> None:
    events, provisioning = LinkEvents(allowed=allowed), FakeClient(provisionable=provisionable)
    link = bare_link(events, FakeClient(), provisioning)

    run_link(lambda: link._on_connect_error(InvalidEncryptionKeyAPIError("fel nyckel")))

    assert "set_key" not in provisioning.calls
    assert events.calls == [f"problem: {OTHER_KEY}"]
    assert link._logic.calls == []


def test_connection_errors_become_warnings() -> None:
    events = LinkEvents()
    link = bare_link(events, FakeClient(), FakeClient())

    run_link(lambda: link._on_connect_error(EncryptionPlaintextAPIError("okrypterat")))
    run_link(lambda: link._on_connect_error(APIConnectionError("svarar inte")))

    assert events.calls == [f"problem: {NO_ENCRYPTION}", "problem: None"]


def test_a_card_whose_entities_cannot_be_read_is_disconnected() -> None:
    events, client = LinkEvents(), FakeClient(broken=True)
    link = bare_link(events, client, FakeClient())

    run_link(link._on_connect)

    assert events.calls == []
    assert client.calls == ["disconnect"]

    working = FakeClient()
    link = bare_link(events, working, FakeClient())
    run_link(link._on_connect)
    assert events.calls == ["connected"]
    assert working.calls == ["subscribe"]


def test_an_update_to_a_card_that_just_dropped_is_not_sent() -> None:
    assert bare_link(LinkEvents(), FakeClient(), FakeClient()).install_update(7)
    assert not bare_link(LinkEvents(), FakeClient(broken=True), FakeClient()).install_update(7)


def entity_key(recorded: Recording, name: str) -> int:
    [entity] = [e for e in recorded.entities if e.name == name]
    return entity.key
