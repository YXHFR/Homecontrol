"""Livevyn med riktiga rumskort: varningar, nya kort, godkänd uppdatering och tidsfartens spärr."""

from collections.abc import Callable
from typing import Any

import pytest
from aioesphomeapi.model import UpdateState
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from fake_cards import CARD, SECRET, FakeNetwork, card_home, recording
from homecontrol.app import create_app
from homecontrol.esphome import NOT_ANSWERING, Discovered
from homecontrol.home import Home
from homecontrol.homefile import dumps
from homes import FLOOR, one_room_home


def app(home: Home, network: FakeNetwork, secret: str | None = SECRET) -> FastAPI:
    return create_app(
        allowed_hosts={"testserver"}, home=home, esphome_secret=secret, card_network=network
    )


def in_loop(client: TestClient, call: Callable[..., None], *args: Any) -> None:
    """Anropar `call` i kärnans händelseloop, där kortens länkar rapporterar."""
    assert client.portal is not None
    client.portal.call(call, *args)


def receive_until(ws: Any, condition: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    for _ in range(30):
        message: dict[str, Any] = ws.receive_json()
        if message["type"] == "state" and condition(message):
            return message
    raise AssertionError("Läget blev aldrig det väntade")


def test_with_cards_the_simulation_starts_off_and_a_silent_card_warns() -> None:
    network = FakeNetwork()
    with (
        TestClient(app(card_home(), network)) as client,
        client.websocket_connect("/api/live") as ws,
    ):
        home = ws.receive_json()
        assert home["floors"][0]["nodes"] == [
            {"id": CARD, "name": "Rumskort", "room_id": "vardagsrum", "x": 2.5, "y": 0}
        ]
        state = receive_until(ws, lambda s: s["cards"])
        assert state["simulation"]["enabled"] is False
        assert state["simulation"]["speed_locked"] is True
        assert state["rooms"][0]["status"] == "okänt"
        [card] = state["cards"]
        assert card["id"] == CARD
        assert card["placed"] is True
        assert card["online"] is False
        assert card["problems"] == [NOT_ANSWERING]

        # Tiden går inte fortare när riktiga kort används.
        ws.send_json({"type": "set_simulation", "enabled": True})
        receive_until(ws, lambda s: s["simulation"]["enabled"])
        ws.send_json({"type": "set_speed", "speed": 60})
        ws.send_json({"type": "set_daylight", "lux": 5})
        state = receive_until(ws, lambda s: s["simulation"]["daylight"] == 5)
        assert state["simulation"]["speed"] == 1


def test_without_cards_the_simulation_starts_on_and_time_can_go_faster() -> None:
    with (
        TestClient(app(one_room_home(), FakeNetwork(), secret=None)) as client,
        client.websocket_connect("/api/live") as ws,
    ):
        ws.receive_json()
        state = ws.receive_json()
        assert state["simulation"]["enabled"] is True
        assert state["simulation"]["speed_locked"] is False
        assert state["cards"] is None
        ws.send_json({"type": "set_speed", "speed": 60})
        receive_until(ws, lambda s: s["simulation"]["speed"] == 60)


def test_placing_a_card_brings_time_back_to_normal() -> None:
    network = FakeNetwork()
    with (
        TestClient(app(card_home(placed=False), network)) as client,
        client.websocket_connect("/api/live") as ws,
    ):
        ws.send_json({"type": "set_simulation", "enabled": True})
        ws.send_json({"type": "set_speed", "speed": 60})
        receive_until(ws, lambda s: s["simulation"]["speed"] == 60)

        response = client.put("/api/home", content=dumps(card_home()))
        assert response.status_code == 204

        state = receive_until(ws, lambda s: s["simulation"]["speed_locked"])
        assert state["simulation"]["speed"] == 1
        assert CARD in network.links


def test_a_new_card_is_listed_for_the_editor_and_the_live_view() -> None:
    network = FakeNetwork()
    application = app(card_home(placed=False), network)
    with TestClient(application) as client, client.websocket_connect("/api/live") as ws:
        source = application.state.cards
        in_loop(client, source.found, Discovered(CARD, "Homecontrol vardagsrum"))

        state = receive_until(ws, lambda s: s["cards"])
        [card] = state["cards"]
        assert (card["id"], card["name"], card["placed"]) == (
            CARD,
            "Homecontrol vardagsrum",
            False,
        )
        assert client.get("/api/cards").json() == state["cards"]
        assert network.links == {}


def test_card_list_is_refused_to_other_sites() -> None:
    with TestClient(app(card_home(), FakeNetwork())) as client:
        response = client.get("/api/cards", headers={"origin": "http://elak.example"})
        assert response.status_code == 403
        response = client.get("http://elak.example/api/cards")
        assert response.status_code == 403


def connected_card_with_update(client: TestClient, network: FakeNetwork) -> int:
    """Kortet ansluter och säger att det finns en ny version. Returnerar uppdateringens nyckel."""
    walk = recording("person_gar_in")
    link = network.links[CARD]
    [firmware] = [entity for entity in walk.entities if entity.name == "Firmware"]

    def connect() -> None:
        link.events.connected(walk.entities)
        for _, state in walk.states:
            link.events.state(state)

    in_loop(client, connect)
    return firmware.key


def test_approved_update_is_installed() -> None:
    network = FakeNetwork()
    with (
        TestClient(app(card_home(), network)) as client,
        client.websocket_connect("/api/live") as ws,
    ):
        key = connected_card_with_update(client, network)
        state = receive_until(ws, lambda s: s["cards"][0]["update"])
        assert state["cards"][0]["update"] == {
            "version": "0.5.0",
            "in_progress": False,
            "progress": None,
        }
        assert network.links[CARD].installs == []

        ws.send_json({"type": "install_update", "card": CARD})
        in_loop(
            client,
            network.links[CARD].events.state,
            UpdateState.from_dict(
                {"key": key, "current_version": "0.4.0", "latest_version": "0.5.0"}
            ),
        )
        receive_until(ws, lambda s: True)

        assert network.links[CARD].installs == [key]


def test_update_cannot_be_started_from_another_site() -> None:
    network = FakeNetwork()
    with TestClient(app(card_home(), network)) as client:
        connected_card_with_update(client, network)
        for url, origin in (
            ("/api/live", "http://elak.example"),
            ("ws://elak.example/api/live", "http://elak.example"),
        ):
            with (
                pytest.raises((WebSocketDisconnect, WebSocketDenialResponse)),
                client.websocket_connect(url, headers={"origin": origin}) as ws,
            ):
                ws.send_json({"type": "install_update", "card": CARD})
                ws.receive_json()

        assert network.links[CARD].installs == []


def test_invalid_update_requests_are_ignored() -> None:
    network = FakeNetwork()
    with (
        TestClient(app(card_home(), network)) as client,
        client.websocket_connect("/api/live") as ws,
    ):
        connected_card_with_update(client, network)
        ws.send_json({"type": "install_update", "card": "homecontrol-kok"})
        ws.send_json({"type": "install_update", "card": "x" * 1000})
        ws.send_json({"type": "install_update"})
        ws.send_json({"type": "add_person", "floor_id": FLOOR, "x": 1, "y": 1})
        receive_until(ws, lambda s: s["simulation"] is not None)

        assert network.links[CARD].installs == []
