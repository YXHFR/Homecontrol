"""Zigbee-utgångens läge för livevyn: om kärnan når MQTT-brokern och Zigbee2MQTT."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_zigbee import FakeBroker
from zigbee_examples import TAKLAMPA, recorded, zigbee_home

from homecontrol.app import create_app
from homecontrol.clock import SystemClock
from homecontrol.connectors import LampCommand
from homecontrol.controller import Controller
from homecontrol.live import create_live_router, zigbee_message
from homecontrol.zigbee import MqttConnection, ZigbeeOutput, ZigbeeView
from homes import one_room_home

START = datetime(2026, 10, 17, 19, 42, tzinfo=UTC)
BRIDGE = "zigbee2mqtt/bridge/state"
ONLINE = b'{"state": "online"}'
OFFLINE = b'{"state": "offline"}'


class Status:
    """Utgången med en styrd väggklocka, utan broker. `changes` räknar meddelandena till livevyn."""

    def __init__(self) -> None:
        self.wall = START
        self.changes = 0
        self.published: list[tuple[str, dict[str, Any]]] = []
        self.output = ZigbeeOutput(
            zigbee_home(), defer=lambda action: action(), wall_clock=lambda: self.wall
        )
        self.output.start(lambda _: None)
        self.output.subscribe(self._changed)

    def _changed(self) -> None:
        self.changes += 1

    def later(self, seconds: float = 60) -> None:
        self.wall += timedelta(seconds=seconds)

    def connect(self) -> None:
        self.output.connected(lambda topic, payload: self.published.append((topic, payload)))

    def receive(self, *names: str) -> None:
        for name in names:
            for topic, payload in recorded(name):
                self.output.handle(topic, payload)

    def bridge(self, payload: bytes) -> None:
        self.output.handle(BRIDGE, payload)

    @property
    def view(self) -> ZigbeeView:
        return self.output.view()


def online() -> Status:
    """Utgången ansluten till brokern, med Zigbee2MQTT igång och lamporna lästa."""
    status = Status()
    status.connect()
    status.receive("start", "lampornas_svar", "grupp_skapad", "grupp_klar")
    status.changes = 0
    status.later()
    return status


# --- Läget ---


def test_before_the_broker_is_reached_nothing_is_connected() -> None:
    status = Status()

    assert status.view == ZigbeeView(mqtt_connected=False, bridge_online=None, since=START)


def test_connecting_to_the_broker_is_connected_but_zigbee2mqtt_is_still_unknown() -> None:
    status = Status()
    status.later()

    status.connect()

    assert status.view == ZigbeeView(
        mqtt_connected=True, bridge_online=None, since=START + timedelta(seconds=60)
    )
    assert status.changes == 1


def test_zigbee2mqtt_saying_online_makes_the_bridge_online() -> None:
    status = Status()
    status.connect()
    status.later()

    status.bridge(ONLINE)

    assert status.view == ZigbeeView(
        mqtt_connected=True, bridge_online=True, since=START + timedelta(seconds=60)
    )


def test_the_recorded_start_makes_everything_online() -> None:
    status = online()

    assert status.view.mqtt_connected is True
    assert status.view.bridge_online is True


def test_the_broker_disappearing_is_not_connected_and_the_bridge_is_unknown() -> None:
    status = online()

    status.output.disconnected()

    assert status.view == ZigbeeView(
        mqtt_connected=False, bridge_online=None, since=START + timedelta(seconds=60)
    )
    assert status.changes == 1


def test_zigbee2mqtt_going_offline_makes_the_bridge_offline() -> None:
    status = online()

    status.receive("bryggan_borta")

    assert status.view == ZigbeeView(
        mqtt_connected=True, bridge_online=False, since=START + timedelta(seconds=60)
    )
    assert status.changes == 1


def test_old_style_offline_without_json_also_makes_the_bridge_offline() -> None:
    status = online()

    status.bridge(b"offline")

    assert status.view.bridge_online is False


def test_both_coming_back_are_true_again() -> None:
    status = online()
    status.output.disconnected()
    status.later()

    status.connect()
    assert status.view.mqtt_connected is True
    assert status.view.bridge_online is None
    status.later()
    status.bridge(ONLINE)

    assert status.view == ZigbeeView(
        mqtt_connected=True, bridge_online=True, since=START + timedelta(seconds=180)
    )
    assert status.changes == 3


def test_zigbee2mqtt_coming_back_after_being_offline_is_online_again() -> None:
    status = online()
    status.bridge(OFFLINE)
    status.later()

    status.bridge(ONLINE)

    assert status.view == ZigbeeView(
        mqtt_connected=True, bridge_online=True, since=START + timedelta(seconds=120)
    )
    assert status.changes == 2


def test_since_is_only_moved_when_something_changes() -> None:
    status = online()
    since = status.view.since

    status.later()
    status.bridge(ONLINE)
    status.later()
    status.connect()

    assert status.view.since == since
    assert status.changes == 0


def test_a_repeated_offline_does_not_move_since() -> None:
    status = online()
    status.bridge(OFFLINE)
    since = status.view.since

    status.later()
    status.bridge(OFFLINE)
    status.receive("bryggan_borta")

    assert status.view.since == since
    assert status.changes == 1


def test_losing_the_broker_twice_is_one_change() -> None:
    status = online()

    status.output.disconnected()
    status.later()
    status.output.disconnected()

    assert status.view.since == START + timedelta(seconds=60)
    assert status.changes == 1


def test_a_bridge_state_without_a_broker_does_not_count() -> None:
    """Utan anslutning är Zigbee2MQTT:s läge okänt, även om ett gammalt meddelande kommer."""
    status = Status()

    status.bridge(ONLINE)

    assert status.view == ZigbeeView(mqtt_connected=False, bridge_online=None, since=START)
    assert status.changes == 0


def test_the_bridge_is_unknown_after_reconnecting_until_zigbee2mqtt_has_spoken() -> None:
    """Zigbee2MQTT kan ha försvunnit medan kärnan var borta; det gamla svaret gäller inte."""
    status = online()
    status.output.disconnected()

    status.connect()

    assert status.view.bridge_online is None


# --- Bara förändringar skickas ---


def test_lamp_messages_do_not_tell_the_live_view_anything_new() -> None:
    status = online()

    status.receive("lampornas_svar", "fjarrkontroll", "appen_dimrar", "lampan_borta")
    status.output.send(LampCommand(TAKLAMPA, on=True, reason="test", brightness=100))
    status.output.tick()

    assert status.changes == 0
    assert status.view.since == START


def test_a_listener_that_has_unsubscribed_hears_nothing() -> None:
    status = online()
    heard: list[None] = []
    unsubscribe = status.output.subscribe(lambda: heard.append(None))

    status.bridge(OFFLINE)
    unsubscribe()
    status.bridge(ONLINE)

    assert len(heard) == 1
    assert status.changes == 2


def test_a_failing_listener_does_not_stop_the_others(caplog: pytest.LogCaptureFixture) -> None:
    status = Status()

    def fails() -> None:
        raise RuntimeError("trasig lyssnare")

    status.output.subscribe(fails)
    heard: list[None] = []
    status.output.subscribe(lambda: heard.append(None))

    status.connect()

    assert heard == [None]
    assert status.view.mqtt_connected is True
    assert "Kunde inte berätta om Zigbee-utgångens läge" in caplog.text


# --- Loggen ---


def zigbee_log(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "homecontrol.zigbee" and record.levelno >= logging.INFO
    ]


def test_zigbee2mqtt_going_and_coming_is_one_log_line_each(
    caplog: pytest.LogCaptureFixture,
) -> None:
    status = online()
    caplog.clear()
    caplog.set_level(logging.DEBUG, "homecontrol")

    status.bridge(OFFLINE)
    status.bridge(OFFLINE)
    status.receive("bryggan_borta")
    status.bridge(ONLINE)
    status.bridge(ONLINE)

    assert zigbee_log(caplog) == [
        "Zigbee2MQTT är borta; lampornas läge är okänt",
        "Zigbee2MQTT är igång",
    ]


def test_turning_lamps_on_and_off_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    status = online()
    caplog.clear()
    caplog.set_level(logging.DEBUG, "homecontrol")

    status.output.send(LampCommand(TAKLAMPA, on=True, reason="test", brightness=100))
    status.receive("fjarrkontroll", "appen_dimrar")
    status.output.send(LampCommand(TAKLAMPA, on=False, reason="test"))

    assert zigbee_log(caplog) == []


def test_the_broker_going_and_coming_is_logged_once_each(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Medan brokern är borta försöker utgången igen och igen, men loggen får en rad."""
    monkeypatch.setattr("homecontrol.zigbee.RECONNECT_SECONDS", 0)
    caplog.set_level(logging.INFO, "homecontrol")

    async def scenario() -> None:
        broker = FakeBroker()
        output = ZigbeeOutput(zigbee_home())
        views: list[ZigbeeView] = []
        output.subscribe(lambda: views.append(output.view()))
        task = asyncio.create_task(output.run(lambda _: None, broker.connect))

        async def until(condition: Callable[[], bool]) -> None:
            for _ in range(200):
                if condition():
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("hände aldrig")

        await until(lambda: broker.connections == 1)
        broker.incoming.put_nowait((BRIDGE, ONLINE))
        await until(lambda: output.view().bridge_online is True)

        # Brokern stänger och vägrar tre försök innan den svarar igen.
        broker.refuse = 3
        broker.incoming.put_nowait(None)
        await until(lambda: broker.connections == 2)
        broker.incoming.put_nowait((BRIDGE, ONLINE))
        await until(lambda: output.view().bridge_online is True)
        seen = list(views)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        # Tre misslyckade försök i rad är ett enda avbrott.
        assert [(view.mqtt_connected, view.bridge_online) for view in seen] == [
            (True, None),
            (True, True),
            (False, None),
            (True, None),
            (True, True),
        ]

    asyncio.run(scenario())

    log = zigbee_log(caplog)
    assert log[0] == "Ansluten till MQTT-brokern"
    assert log[1] == "Zigbee2MQTT är igång"
    assert log[2].startswith("Ingen kontakt med MQTT-brokern (brokern stängde anslutningen)")
    assert log[3:] == ["Ansluten till MQTT-brokern", "Zigbee2MQTT är igång"]


# --- Livemeddelandet ---


def test_the_message_has_the_three_fields_and_since_in_utc_with_z() -> None:
    view = ZigbeeView(mqtt_connected=True, bridge_online=None, since=START)

    assert zigbee_message(view) == {
        "mqtt_connected": True,
        "bridge_online": None,
        "since": "2026-10-17T19:42:00Z",
    }


def test_since_in_another_time_zone_is_sent_in_utc() -> None:
    swedish_summer = timezone(timedelta(hours=2))
    since = datetime(2026, 10, 17, 21, 42, 7, 123456, tzinfo=swedish_summer)

    message = zigbee_message(ZigbeeView(False, None, since))

    assert message["since"] == "2026-10-17T19:42:07Z"


def receive_until(ws: Any, condition: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    for _ in range(20):
        state: dict[str, Any] = ws.receive_json()
        if condition(state):
            return state
    raise AssertionError("Läget blev aldrig det väntade")


def test_the_live_view_gets_the_zigbee_status_and_its_changes() -> None:
    status = Status()
    controller = Controller(one_room_home(), SystemClock(), [])
    application = FastAPI()
    application.include_router(
        create_live_router(controller, None, {"testserver"}, zigbee=status.output)
    )

    with TestClient(application) as client, client.websocket_connect("/api/live") as ws:
        ws.receive_json()
        state = ws.receive_json()
        assert state["zigbee"] == {
            "mqtt_connected": False,
            "bridge_online": None,
            "since": "2026-10-17T19:42:00Z",
        }

        status.later()
        status.connect()
        status.bridge(ONLINE)
        state = receive_until(ws, lambda s: s["zigbee"]["bridge_online"] is True)
        assert state["zigbee"] == {
            "mqtt_connected": True,
            "bridge_online": True,
            "since": "2026-10-17T19:43:00Z",
        }

        status.later()
        status.bridge(OFFLINE)
        state = receive_until(ws, lambda s: s["zigbee"]["bridge_online"] is False)
        assert state["zigbee"]["since"] == "2026-10-17T19:44:00Z"

        status.later()
        status.output.disconnected()
        state = receive_until(ws, lambda s: not s["zigbee"]["mqtt_connected"])
        assert state["zigbee"] == {
            "mqtt_connected": False,
            "bridge_online": None,
            "since": "2026-10-17T19:45:00Z",
        }


def test_without_zigbee_the_field_is_null() -> None:
    application = create_app(allowed_hosts={"testserver"}, home=one_room_home())

    with TestClient(application) as client, client.websocket_connect("/api/live") as ws:
        ws.receive_json()
        state = ws.receive_json()

    assert "zigbee" in state
    assert state["zigbee"] is None


@contextlib.asynccontextmanager
async def no_broker() -> AsyncIterator[MqttConnection]:
    raise ConnectionError("ingen broker i testet")
    yield  # pragma: no cover


def test_with_zigbee_but_no_broker_the_live_view_says_not_connected() -> None:
    application = create_app(home=zigbee_home(), mqtt=no_broker, allowed_hosts={"testserver"})

    with TestClient(application) as client, client.websocket_connect("/api/live") as ws:
        ws.receive_json()
        state = ws.receive_json()

    assert state["zigbee"]["mqtt_connected"] is False
    assert state["zigbee"]["bridge_online"] is None
    assert state["zigbee"]["since"].endswith("Z")
