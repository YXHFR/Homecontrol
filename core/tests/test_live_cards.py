"""Livevyn med riktiga rumskort: varningar, nya kort, godkänd uppdatering och tidsfartens spärr."""

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

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


def test_cards_have_no_network_update() -> None:
    """Korten uppdateras bara över USB: inget update-fält, och install_update finns inte."""
    network = FakeNetwork()
    with (
        TestClient(app(card_home(), network)) as client,
        client.websocket_connect("/api/live") as ws,
    ):
        walk = recording("person_gar_in")

        def connect() -> None:
            network.links[CARD].events.connected(walk.entities)

        in_loop(client, connect)
        state = receive_until(ws, lambda s: s["cards"] and s["cards"][0]["online"])
        assert "update" not in state["cards"][0]

        ws.send_json({"type": "install_update", "card": CARD})
        ws.send_json({"type": "add_person", "floor_id": FLOOR, "x": 1, "y": 1})
        receive_until(ws, lambda s: s["simulation"] is not None)
