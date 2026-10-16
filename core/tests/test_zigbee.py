"""Kopplingstester för lamputgången Zigbee2MQTT, mot exempelmeddelanden i zigbee_exempel/."""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from zigbee_examples import (
    FONSTERLAMPA,
    GROUP_SET,
    HUE,
    TAKLAMPA,
    recorded,
    zigbee_home,
    zigbee_lamp,
)

from homecontrol.clock import ManualClock
from homecontrol.connectors import (
    LampCommand,
    LampStateReported,
    LightLevelObserved,
    Observation,
)
from homecontrol.controller import Controller
from homecontrol.home import Home, LampProfile, Point
from homecontrol.zigbee import (
    GENERIC,
    IKEA_TRADFRI,
    READ_RETRY_SECONDS,
    READ_TIMEOUT_SECONDS,
    Setting,
    ZigbeeOutput,
    group_profile,
    ieee_address,
    lamp_id,
    zigbee_lamps,
)
from homes import radar_sees


class Harness:
    """Utgången med en styrd klocka, utan broker: det den skickar samlas i `published`."""

    def __init__(self, home: Home | None = None) -> None:
        self.now = 0.0
        self.deferred: list[Callable[[], None]] = []
        self.published: list[tuple[str, dict[str, Any]]] = []
        self.reports: list[LampStateReported] = []
        self.output = ZigbeeOutput(
            home if home is not None else zigbee_home(),
            defer=self.deferred.append,
            now=lambda: self.now,
        )
        self.output.start(self._report)
        self.output.connected(lambda topic, payload: self.published.append((topic, payload)))

    def _report(self, observation: Observation) -> None:
        assert isinstance(observation, LampStateReported)
        self.reports.append(observation)

    def receive(self, *names: str) -> None:
        for name in names:
            for topic, payload in recorded(name):
                self.output.handle(topic, payload)

    def send(self, *commands: LampCommand) -> None:
        """Lämnar kommandona som kärnan gör efter ett beslut, och låter utgången skicka dem."""
        for command in commands:
            self.output.send(command)
        self.run_deferred()

    def run_deferred(self) -> None:
        while self.deferred:
            self.deferred.pop(0)()

    def take(self) -> list[tuple[str, dict[str, Any]]]:
        published = list(self.published)
        self.published.clear()
        return published

    def take_reports(self) -> list[LampStateReported]:
        reports = list(self.reports)
        self.reports.clear()
        return reports


def started(home: Home | None = None) -> Harness:
    """Utgången efter att lamporna har lästs och vardagsrummets grupp är klar."""
    harness = Harness(home)
    harness.receive("start", "lampornas_svar", "grupp_skapad", "grupp_klar")
    harness.take()
    harness.take_reports()
    return harness


def on(lamp: str, brightness: int = 100) -> LampCommand:
    return LampCommand(lamp, on=True, reason="test", brightness=brightness)


def off(lamp: str) -> LampCommand:
    return LampCommand(lamp, on=False, reason="test")


# --- Lampornas läge läses först ---


def test_placed_lamps_are_asked_for_their_state_when_the_output_starts() -> None:
    harness = Harness()

    harness.receive("start")

    gets = [(topic, payload) for topic, payload in harness.take() if topic.endswith("/get")]
    assert gets == [
        ("zigbee2mqtt/0x000b57fffe0000a1/get", {"state": "", "brightness": ""}),
        ("zigbee2mqtt/fonsterlampa/get", {"state": "", "brightness": ""}),
    ]
    assert harness.reports == []  # inget antas om lamporna innan de har svarat


def test_the_lamps_answers_are_their_state_and_not_changes() -> None:
    harness = Harness()
    harness.receive("start")

    harness.receive("lampornas_svar")

    assert harness.reports == [
        LampStateReported(TAKLAMPA, on=True, brightness=100),
        LampStateReported(FONSTERLAMPA, on=False, brightness=1),
    ]


def test_nothing_is_sent_to_a_lamp_before_its_state_is_read() -> None:
    harness = Harness()
    harness.receive("start")
    harness.take()

    harness.send(on(TAKLAMPA))
    assert harness.take() == []

    harness.receive("lampornas_svar")
    harness.send(on(TAKLAMPA))
    assert harness.take() == [
        (
            "zigbee2mqtt/0x000b57fffe0000a1/set",
            {"state": "ON", "brightness": 254, "transition": 1.0},
        )
    ]


def test_core_never_assumes_the_lamps_are_off_at_start() -> None:
    clock = ManualClock()
    home = zigbee_home()
    deferred: list[Callable[[], None]] = []
    zigbee = ZigbeeOutput(home, defer=deferred.append, now=clock.now)
    controller = Controller(home, clock, [zigbee])
    zigbee.start(controller.observe)
    published: list[tuple[str, dict[str, Any]]] = []
    zigbee.connected(lambda topic, payload: published.append((topic, payload)))

    for topic, payload in recorded("start"):
        zigbee.handle(topic, payload)
    assert [lamp.on for lamp in controller.snapshot().lamps[:2]] == [None, None]

    for topic, payload in recorded("lampornas_svar"):
        zigbee.handle(topic, payload)
    assert [lamp.on for lamp in controller.snapshot().lamps[:2]] == [True, False]


def test_a_room_that_should_be_lit_before_the_lamps_are_read_is_lit_after() -> None:
    clock = ManualClock()
    home = zigbee_home()
    deferred: list[Callable[[], None]] = []
    zigbee = ZigbeeOutput(home, defer=deferred.append, now=clock.now)
    controller = Controller(home, clock, [zigbee])
    zigbee.start(controller.observe)
    published: list[tuple[str, dict[str, Any]]] = []
    zigbee.connected(lambda topic, payload: published.append((topic, payload)))
    for topic, payload in recorded("start"):
        zigbee.handle(topic, payload)

    controller.observe(LightLevelObserved("sensor", "vardagsrum", 5))
    controller.observe(radar_sees((2, 2), home=home))
    while deferred:
        deferred.pop(0)()
    assert [topic for topic, _ in published if topic.endswith("/set")] == []

    # När lamporna har svarat att de är släckta räknar kärnan om och tänder dem.
    for name in ("0x000b57fffe0000a1", "fonsterlampa"):
        zigbee.handle(f"zigbee2mqtt/{name}", b'{"state": "OFF", "brightness": 1}')
    while deferred:
        deferred.pop(0)()
    on = {"state": "ON", "brightness": 254, "transition": 1.0}
    assert [(topic, payload) for topic, payload in published if topic.endswith("/set")] == [
        ("zigbee2mqtt/0x000b57fffe0000a1/set", on),
        ("zigbee2mqtt/fonsterlampa/set", on),
    ]


def test_a_lamp_that_does_not_answer_is_shown_as_not_responding_and_asked_again() -> None:
    harness = Harness()
    harness.receive("start")
    harness.take()

    harness.now = READ_TIMEOUT_SECONDS
    harness.output.tick()
    assert harness.take_reports() == [
        LampStateReported(TAKLAMPA, None),
        LampStateReported(FONSTERLAMPA, None),
    ]
    assert harness.take() == []

    harness.now = READ_RETRY_SECONDS
    harness.output.tick()
    assert [topic for topic, _ in harness.take()] == [
        "zigbee2mqtt/0x000b57fffe0000a1/get",
        "zigbee2mqtt/fonsterlampa/get",
    ]
    harness.output.tick()
    assert harness.take_reports() == []  # bara en gång


# --- Rumsgrupper ---


def test_a_room_with_several_zigbee_lamps_gets_a_group_with_those_lamps() -> None:
    harness = Harness()

    harness.receive("start")
    assert [
        (topic, payload) for topic, payload in harness.take() if "/bridge/request/" in topic
    ] == [("zigbee2mqtt/bridge/request/group/add", {"friendly_name": "homecontrol-vardagsrum"})]

    harness.receive("grupp_skapad")
    assert harness.take() == [
        (
            "zigbee2mqtt/bridge/request/group/members/add",
            {"group": "homecontrol-vardagsrum", "device": "0x000b57fffe0000a1"},
        ),
        (
            "zigbee2mqtt/bridge/request/group/members/add",
            {"group": "homecontrol-vardagsrum", "device": "0x000b57fffe0000a2"},
        ),
    ]

    harness.receive("grupp_klar")
    assert harness.take() == []


def test_a_lamp_moved_out_of_the_room_leaves_its_group() -> None:
    harness = started()
    home = zigbee_home()
    floor = home.floors[0]
    living_room, kitchen = floor.rooms
    moved = living_room.lamps[1].model_copy(update={"position": Point(7, 3)})
    changed = home.model_copy(
        update={
            "floors": (
                floor.model_copy(
                    update={
                        "rooms": (
                            living_room.model_copy(update={"lamps": living_room.lamps[:1]}),
                            kitchen.model_copy(update={"lamps": (*kitchen.lamps, moved)}),
                        )
                    }
                ),
            )
        }
    )

    harness.output.use_home(changed)

    assert harness.take() == [
        (
            "zigbee2mqtt/bridge/request/group/members/remove",
            {"group": "homecontrol-vardagsrum", "device": "0x000b57fffe0000a1"},
        ),
        (
            "zigbee2mqtt/bridge/request/group/members/remove",
            {"group": "homecontrol-vardagsrum", "device": "0x000b57fffe0000a2"},
        ),
    ]


def test_the_whole_room_turns_on_and_off_with_one_group_command_and_a_transition() -> None:
    harness = started()

    harness.send(on(TAKLAMPA), on(FONSTERLAMPA))
    assert harness.take() == [
        (GROUP_SET, {"state": "ON", "brightness": 254, "transition": 1.0}),
    ]

    harness.send(off(TAKLAMPA), off(FONSTERLAMPA))
    assert harness.take() == [(GROUP_SET, {"state": "OFF", "transition": 1.0})]


def test_lamps_that_get_different_commands_are_sent_one_by_one() -> None:
    harness = started()

    harness.send(on(TAKLAMPA), on(FONSTERLAMPA, brightness=50))

    assert harness.take() == [
        (
            "zigbee2mqtt/0x000b57fffe0000a1/set",
            {"state": "ON", "brightness": 254, "transition": 1.0},
        ),
        ("zigbee2mqtt/fonsterlampa/set", {"state": "ON", "brightness": 127, "transition": 1.0}),
    ]


def test_one_lamp_of_the_room_is_sent_on_its_own() -> None:
    harness = started()

    harness.send(off(FONSTERLAMPA))

    assert harness.take() == [("zigbee2mqtt/fonsterlampa/set", {"state": "OFF", "transition": 1.0})]


def test_the_group_is_not_used_before_zigbee2mqtt_has_added_the_lamps() -> None:
    harness = Harness()
    harness.receive("start", "lampornas_svar", "grupp_skapad")
    harness.take()

    harness.send(off(TAKLAMPA), off(FONSTERLAMPA))

    assert [topic for topic, _ in harness.take()] == [
        "zigbee2mqtt/0x000b57fffe0000a1/set",
        "zigbee2mqtt/fonsterlampa/set",
    ]


# --- Lampprofilen för IKEA TRÅDFRI ---


def test_tradfri_never_gets_a_brightness_below_2() -> None:
    assert IKEA_TRADFRI.zigbee_brightness(0) == 2
    assert all(IKEA_TRADFRI.zigbee_brightness(p) >= 2 for p in range(0, 101))
    assert GENERIC.zigbee_brightness(0) == 1


def test_tradfri_changes_one_thing_at_a_time_with_a_transition() -> None:
    payloads = IKEA_TRADFRI.payloads(Setting(on=True, brightness=50, color_temp=2700), 1.0)

    assert payloads == (
        {"state": "ON", "brightness": 127, "transition": 1.0},
        {"color_temp": 370, "transition": 0},
    )


def test_tradfri_always_gets_a_transition() -> None:
    for setting in (
        Setting(on=True),
        Setting(on=True, brightness=1),
        Setting(on=False),
        Setting(on=True, color_temp=4000),
    ):
        assert all("transition" in payload for payload in IKEA_TRADFRI.payloads(setting, 1.0))


def test_lamps_are_always_turned_on_with_a_brightness() -> None:
    # En TRÅDFRI som släcktes med övergång tänds annars på lägsta ljusstyrkan.
    for profile in (GENERIC, IKEA_TRADFRI):
        [payload] = profile.payloads(Setting(on=True), 1.0)
        assert payload["brightness"] == 254


def test_a_generic_lamp_gets_everything_in_one_message() -> None:
    assert GENERIC.payloads(Setting(on=True, brightness=50, color_temp=2700), 1.0) == (
        {"state": "ON", "brightness": 127, "transition": 1.0, "color_temp": 370},
    )


def test_a_group_with_a_tradfri_lamp_follows_the_tradfri_quirks() -> None:
    assert group_profile([LampProfile.GENERIC, LampProfile.IKEA_TRADFRI]) == IKEA_TRADFRI
    assert group_profile([LampProfile.GENERIC]) == GENERIC


def test_a_dimmed_tradfri_lamp_is_sent_through_its_profile() -> None:
    harness = started()

    harness.send(LampCommand(TAKLAMPA, on=True, reason="test", brightness=50, color_temp=2700))

    assert harness.take() == [
        (
            "zigbee2mqtt/0x000b57fffe0000a1/set",
            {"state": "ON", "brightness": 127, "transition": 1.0},
        ),
        ("zigbee2mqtt/0x000b57fffe0000a1/set", {"color_temp": 370, "transition": 0}),
    ]


# --- Egna kommandon och ändringar utifrån ---


def test_the_answers_to_a_group_command_are_not_changes_from_outside() -> None:
    harness = started()
    harness.send(on(TAKLAMPA), on(FONSTERLAMPA))

    harness.now = 1.5
    harness.receive("gruppen_tands")

    assert harness.take_reports() == [
        LampStateReported(TAKLAMPA, on=True, brightness=100),
        LampStateReported(FONSTERLAMPA, on=True, brightness=100),
        LampStateReported(TAKLAMPA, on=True, brightness=80),
    ]


def test_a_remote_control_is_a_change_from_outside() -> None:
    harness = started()

    harness.now = 100
    harness.receive("fjarrkontroll")

    assert harness.take_reports() == [
        LampStateReported(TAKLAMPA, on=False, brightness=100, external=True)
    ]


def test_dimming_in_an_app_is_a_change_from_outside() -> None:
    harness = started()
    harness.send(on(FONSTERLAMPA))
    harness.receive("gruppen_tands")
    harness.take_reports()

    harness.now = 100
    harness.receive("appen_dimrar")

    assert harness.take_reports() == [
        LampStateReported(FONSTERLAMPA, on=True, brightness=30, external=True)
    ]


def test_a_repeated_report_of_the_same_state_is_not_a_change() -> None:
    harness = started()

    harness.now = 100
    harness.receive("lampornas_svar")

    assert all(not report.external for report in harness.take_reports())


def test_a_change_long_after_the_core_s_own_command_is_from_outside() -> None:
    harness = started()
    harness.send(on(FONSTERLAMPA))
    harness.receive("gruppen_tands")
    harness.take_reports()

    harness.now = 60
    harness.receive("fjarrkontroll")

    assert harness.take_reports()[-1].external


def test_remote_control_pauses_the_automation_like_a_click_in_the_live_view() -> None:
    clock = ManualClock()
    home = zigbee_home()
    deferred: list[Callable[[], None]] = []
    zigbee = ZigbeeOutput(home, defer=deferred.append, now=clock.now)
    controller = Controller(home, clock, [zigbee])
    zigbee.start(controller.observe)
    published: list[tuple[str, dict[str, Any]]] = []
    zigbee.connected(lambda topic, payload: published.append((topic, payload)))

    def receive(name: str) -> None:
        for topic, payload in recorded(name):
            zigbee.handle(topic, payload)

    def run_deferred() -> None:
        while deferred:
            deferred.pop(0)()

    receive("start")
    for name in ("0x000b57fffe0000a1", "fonsterlampa"):
        zigbee.handle(f"zigbee2mqtt/{name}", b'{"state": "OFF", "brightness": 1}')
    receive("grupp_skapad")
    receive("grupp_klar")
    published.clear()

    # Någon går in i det mörka vardagsrummet: hela rummet tänds med ett gruppkommando.
    controller.observe(LightLevelObserved("sensor", "vardagsrum", 5))
    controller.observe(radar_sees((2, 2), home=home))
    run_deferred()
    assert published == [(GROUP_SET, {"state": "ON", "brightness": 254, "transition": 1.0})]
    clock.advance_to(1)
    receive("gruppen_tands")
    assert not controller.snapshot().rooms[0].manual

    # Taklampan släcks med fjärrkontrollen.
    clock.advance_to(60)
    receive("fjarrkontroll")

    snapshot = controller.snapshot()
    assert snapshot.rooms[0].manual
    assert snapshot.lamps[0].on is False
    assert snapshot.events[-1].text == (
        "Taklampa släcktes med fjärrkontroll eller app. Automatiken i Vardagsrum pausar "
        "tills rummet har varit tomt i 3 minuter."
    )
    run_deferred()
    assert [topic for topic, _ in published if topic.endswith("/set")] == [GROUP_SET]


# --- Tillgänglighet ---


def test_a_lamp_that_zigbee2mqtt_says_is_gone_is_not_responding_until_it_answers() -> None:
    harness = started()

    harness.receive("lampan_borta")

    assert harness.take_reports() == [LampStateReported(TAKLAMPA, None)]
    assert harness.take() == [
        ("zigbee2mqtt/0x000b57fffe0000a1/get", {"state": "", "brightness": ""})
    ]
    harness.send(off(TAKLAMPA))
    assert harness.take() == []  # inget skickas förrän lampan har svarat


def test_lamps_are_unknown_when_zigbee2mqtt_goes_away_and_read_again_when_it_returns() -> None:
    harness = started()

    harness.receive("bryggan_borta")
    assert harness.take_reports() == [
        LampStateReported(TAKLAMPA, None),
        LampStateReported(FONSTERLAMPA, None),
    ]

    harness.output.handle("zigbee2mqtt/bridge/state", b'{"state": "online"}')
    assert [topic for topic, _ in harness.take() if topic.endswith("/get")] == [
        "zigbee2mqtt/0x000b57fffe0000a1/get",
        "zigbee2mqtt/fonsterlampa/get",
    ]


def test_old_style_bridge_state_without_json_is_understood() -> None:
    harness = started()

    harness.output.handle("zigbee2mqtt/bridge/state", b"offline")

    assert len(harness.take_reports()) == 2


def test_lamps_are_unknown_when_the_broker_is_lost() -> None:
    harness = started()

    harness.output.disconnected()

    assert harness.take_reports() == [
        LampStateReported(TAKLAMPA, None),
        LampStateReported(FONSTERLAMPA, None),
    ]


def test_a_placed_lamp_that_is_not_in_the_zigbee_network_is_not_responding() -> None:
    home = zigbee_home()
    floor = home.floors[0]
    living_room = floor.rooms[0]
    missing = zigbee_lamp("zigbee-000b57fffe0000ee", "Borta", 1, 1, LampProfile.IKEA_TRADFRI)
    with_missing = home.model_copy(
        update={
            "floors": (
                floor.model_copy(
                    update={
                        "rooms": (
                            living_room.model_copy(update={"lamps": (*living_room.lamps, missing)}),
                            floor.rooms[1],
                        )
                    }
                ),
            )
        }
    )
    harness = Harness(with_missing)

    harness.receive("start")

    assert harness.take_reports() == [LampStateReported(missing.id, None)]


def test_broken_messages_are_skipped(caplog: pytest.LogCaptureFixture) -> None:
    harness = started()

    harness.output.handle("zigbee2mqtt/bridge/devices", b"{trasig")
    harness.output.handle("zigbee2mqtt/bridge/devices", b'{"inte": "en lista"}')
    harness.output.handle("zigbee2mqtt/fonsterlampa", b'{"state": "ON", "brightness": "mycket"}')

    assert "hoppas över" in caplog.text
    assert {lamp.id for lamp in harness.output.lamps()} == {TAKLAMPA, FONSTERLAMPA, HUE}


# --- Upptäckt och godkännande ---


def test_new_lamps_are_found_but_not_used_until_placed() -> None:
    harness = started()

    found = {lamp.id: lamp for lamp in harness.output.lamps()}
    assert set(found) == {TAKLAMPA, FONSTERLAMPA, HUE}  # inte fjärrkontrollen eller koordinatorn
    assert found[HUE].placed is False
    assert found[HUE].name == "Philips Hue White A60 bulb E27/B22"
    assert found[HUE].profile is LampProfile.GENERIC
    assert found[TAKLAMPA].profile is LampProfile.IKEA_TRADFRI
    assert found[FONSTERLAMPA].name == "fonsterlampa"

    harness.send(on(HUE))
    harness.output.handle("zigbee2mqtt/0x0017880100000c01", b'{"state": "OFF"}')
    assert harness.take() == []
    assert harness.take_reports() == []


def test_a_lamp_placed_on_the_map_is_read_and_then_used() -> None:
    harness = started()

    harness.output.use_home(zigbee_home(hue_placed=True))
    assert harness.take() == [
        ("zigbee2mqtt/0x0017880100000c01/get", {"state": "", "brightness": ""})
    ]
    assert next(lamp for lamp in harness.output.lamps() if lamp.id == HUE).placed

    harness.output.handle("zigbee2mqtt/0x0017880100000c01", b'{"state": "OFF", "brightness": 1}')
    harness.send(on(HUE))
    # Köket har bara en Zigbee-lampa och ingen grupp.
    assert harness.take() == [
        (
            "zigbee2mqtt/0x0017880100000c01/set",
            {"state": "ON", "brightness": 254, "transition": 1.0},
        )
    ]


def test_a_lamp_removed_from_the_map_can_still_be_turned_off() -> None:
    harness = started()
    removed = zigbee_home().model_copy(deep=True)
    floor = removed.floors[0]
    without = removed.model_copy(
        update={
            "floors": (
                floor.model_copy(
                    update={
                        "rooms": (
                            floor.rooms[0].model_copy(update={"lamps": floor.rooms[0].lamps[1:]}),
                            floor.rooms[1],
                        )
                    }
                ),
            )
        }
    )

    # Kärnan släcker lampan innan utgången får det nya hemmet (se Controller.replace_home).
    harness.output.send(off(TAKLAMPA))
    harness.output.use_home(without)
    harness.run_deferred()

    assert ("zigbee2mqtt/0x000b57fffe0000a1/set", {"state": "OFF", "transition": 1.0}) in (
        harness.take()
    )


def test_virtual_lamps_are_not_touched() -> None:
    harness = started()

    harness.send(on("kokslampa"))

    assert harness.take() == []


def test_lamp_ids_follow_the_ieee_address() -> None:
    assert lamp_id("0x000B57FFFE0000A1") == TAKLAMPA
    assert ieee_address(TAKLAMPA) == "0x000b57fffe0000a1"
    assert ieee_address("taklampa") is None
    assert ieee_address("zigbee-inte-en-adress") is None
    assert zigbee_lamps(zigbee_home()) == {TAKLAMPA, FONSTERLAMPA}


# --- Anslutningen ---


class FakeBroker:
    """En låtsasbroker: det som läggs i `incoming` levereras, och det som publiceras sparas."""

    def __init__(self) -> None:
        self.connections = 0
        self.subscriptions: list[str] = []
        self.published: list[tuple[str, dict[str, Any]]] = []
        self.incoming: asyncio.Queue[tuple[str, bytes] | None] = asyncio.Queue()
        self.refuse = 0
        """Så många anslutningsförsök till som misslyckas."""

    @contextlib.asynccontextmanager
    async def connect(self) -> AsyncIterator["FakeBroker"]:
        if self.refuse:
            self.refuse -= 1
            raise ConnectionError("vägrar")
        self.connections += 1
        yield self

    async def subscribe(self, topic: str) -> None:
        self.subscriptions.append(topic)

    async def publish(self, topic: str, payload: bytes) -> None:
        self.published.append((topic, json.loads(payload)))

    async def messages(self) -> AsyncIterator[tuple[str, bytes]]:
        while (message := await self.incoming.get()) is not None:
            yield message


def test_the_output_subscribes_reads_the_lamps_and_reconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("homecontrol.zigbee.RECONNECT_SECONDS", 0)

    async def scenario() -> None:
        broker = FakeBroker()
        broker.refuse = 1
        reports: list[Observation] = []
        output = ZigbeeOutput(zigbee_home())
        task = asyncio.create_task(output.run(reports.append, broker.connect))

        async def until(condition: Callable[[], bool]) -> None:
            for _ in range(200):
                if condition():
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("hände aldrig")

        for message in recorded("start") + recorded("lampornas_svar"):
            broker.incoming.put_nowait(message)
        # Utan broker svarar ingen lampa; när den finns läses lamporna.
        await until(lambda: len(reports) == 4)
        assert reports == [
            LampStateReported(TAKLAMPA, None),
            LampStateReported(FONSTERLAMPA, None),
            LampStateReported(TAKLAMPA, on=True),
            LampStateReported(FONSTERLAMPA, on=False, brightness=1),
        ]
        assert broker.subscriptions == ["zigbee2mqtt/#"]
        assert ("zigbee2mqtt/fonsterlampa/get", {"state": "", "brightness": ""}) in (
            broker.published
        )

        output.send(on(TAKLAMPA))
        await until(lambda: any(topic.endswith("a1/set") for topic, _ in broker.published))

        # Brokern försvinner: lamporna blir okända, och utgången ansluter igen.
        broker.incoming.put_nowait(None)
        await until(lambda: broker.connections == 2)
        assert reports[-2:] == [
            LampStateReported(TAKLAMPA, None),
            LampStateReported(FONSTERLAMPA, None),
        ]
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
