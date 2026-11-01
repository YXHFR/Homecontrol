"""Testlampan: lysdioden på ett rumskort, styrd som en lampa (se homecontrol/cardled.py).

Kortet spelar upp en inspelning där en person går in i ett mörkt rum.
Nätverket är låtsat: kortets svar på ett ljuskommando skickas tillbaka som
det riktiga kortet gör, med lysdiodens nya läge. Allt annat är den riktiga
kärnan: sammanvägningen, regeln, styrgränsen och ljusvillkoret.
"""

from collections.abc import Callable

from aioesphomeapi.model import EntityInfo, EntityState, LightInfo, LightState, SensorState
from fastapi.testclient import TestClient

from fake_cards import CARD, SECRET, FakeLink, FakeNetwork, Recording, card_home, recording
from homecontrol.app import create_app
from homecontrol.cardled import (
    CardLedOutput,
    card_led_lamp_id,
    card_led_lamps,
    led_card,
)
from homecontrol.clock import ManualClock
from homecontrol.connectors import LampCommand, LampStateReported, Observation
from homecontrol.controller import Controller
from homecontrol.esphome import CARD_LED, EsphomeSource
from homecontrol.home import Home, Lamp, Point
from homecontrol.lamplimit import LIMIT_BURST
from homecontrol.simulation import SimulatedLampOutput

LED = card_led_lamp_id(CARD)
LED_KEY = 123456


def led_home() -> Home:
    """Vardagsrummet med rumskortet, där taklampan är testlampan på kortet."""
    home = card_home()
    room = home.rooms[0]
    lamp = Lamp(id=LED, name="Testlampa", position=Point(2.5, 2))
    room = room.model_copy(update={"lamps": (lamp,)})
    floor = home.floors[0].model_copy(update={"rooms": (room,)})
    return home.model_copy(update={"floors": (floor,)})


def light(key: int, on: bool, brightness: float) -> LightState:
    return LightState.from_dict({"key": key, "state": on, "brightness": brightness})


def with_led(recorded: Recording) -> list[EntityInfo]:
    return [
        *recorded.entities,
        LightInfo.from_dict({"object_id": "test_lamp", "key": LED_KEY, "name": CARD_LED}),
    ]


class Harness:
    """Kärnan med ESPHome-kopplingen, testlamporna och ett låtsat kort som svarar."""

    def __init__(self) -> None:
        self.home = led_home()
        self.now = 0.0
        self.clock = ManualClock()
        self.network = FakeNetwork()
        self._deferred: list[Callable[[], None]] = []
        self.observed: list[Observation] = []
        self.source = EsphomeSource(
            SECRET, self.home, self.network, defer=self._deferred.append, clock=lambda: self.now
        )
        self.leds = CardLedOutput(self.source, self.home, now=lambda: self.now)
        self.virtual = SimulatedLampOutput(card_led_lamps)
        self.controller = Controller(self.home, self.clock, [self.virtual, self.leds])
        self.virtual.report_new_lamps(self.home, self.controller.observe)
        self.source.start(self._observe)
        self.leds.start(self._observe)
        self.answered = 0
        self.walk = recording("person_gar_in")
        self._keys = {entity.name: entity.key for entity in self.walk.entities}

    def _observe(self, observation: Observation) -> None:
        self.observed.append(observation)
        self.controller.observe(observation)

    @property
    def link(self) -> FakeLink:
        return self.network.links[CARD]

    def settle(self) -> None:
        """Låter kortet svara på ljuskommandona, som det riktiga kortet gör."""
        while self._deferred or self.answered < len(self.link.lights):
            while self._deferred:
                self._deferred.pop(0)()
            for key, on, brightness in self.link.lights[self.answered :]:
                self.answered += 1
                if self.link.running:
                    self.link.events.state(light(key, on, brightness))

    def connect(self, entities: list[EntityInfo] | None = None) -> None:
        self.link.events.connected(with_led(self.walk) if entities is None else entities)
        self.link.events.state(light(LED_KEY, False, 1.0))
        self.settle()

    def send(self, state: EntityState) -> None:
        self.link.events.state(state)
        self.settle()

    def sensor(self, name: str, value: float) -> SensorState:
        return SensorState.from_dict({"key": self._keys[name], "state": value})

    def walk_in(self, *, lux: bool | None = True) -> None:
        """Spelar upp promenaden. Med `lux=None` hoppas inspelningens ljusnivå (12,5 lux) över."""
        for _, state in self.walk.states:
            if lux is None and state.key == self._keys["Illuminance"]:
                continue
            self.link.events.state(state)
        self.settle()

    def everyone_leaves(self) -> None:
        for n in (1, 2, 3):
            for axis in ("X", "Y"):
                self.link.events.state(self.sensor(f"Radar Target {n} {axis}", 0))
        self.settle()

    def wait(self, seconds: float, lux: float = 12.5) -> None:
        """Låter tiden gå medan kortet fortsätter att skicka ljus och Wi-Fi som vanligt."""
        for _ in range(int(seconds / 5)):
            self.now += 5
            self.clock.advance_to(self.clock.now() + 5)
            self.link.events.state(self.sensor("CSI Movement Score", 0))
            self.link.events.state(self.sensor("Illuminance", lux))
            self.settle()
            self.source.check()
            self.leds.release_held()
            self.controller.tick()
            self.settle()

    def lamp(self) -> tuple[bool | None, bool]:
        """Testlampans läge i livevyn, och om den svarar."""
        [lamp] = self.controller.snapshot().lamps
        return lamp.on, lamp.responding

    def commands(self) -> list[tuple[bool, float]]:
        return [(on, brightness) for _, on, brightness in self.link.lights]


def test_lamp_ids_name_the_card() -> None:
    assert LED == "led-homecontrol-vardagsrum"
    assert led_card(LED) == CARD
    assert led_card("zigbee-0x0123456789abcdef") is None
    assert led_card("led-inte ett kort") is None
    assert card_led_lamps(led_home()) == {LED}
    assert card_led_lamps(card_home()) == frozenset()


def test_presence_in_the_dark_lights_the_led_and_empty_turns_it_off_after_the_delay() -> None:
    harness = Harness()
    assert harness.lamp() == (None, False)  # okänd tills kortet svarar

    harness.connect()
    assert harness.lamp() == (False, True)

    harness.walk_in()
    assert harness.commands() == [(True, 1.0)]
    assert harness.lamp() == (True, True)

    harness.everyone_leaves()
    settings = harness.home.rooms[0].settings
    dim_at = settings.off_after_empty_seconds - settings.dim_before_off_seconds
    harness.wait(dim_at - 10)
    assert harness.commands() == [(True, 1.0)]  # fortfarande tänd strax innan

    harness.wait(20)
    assert harness.commands()[-1] == (True, settings.dim_percent / 100)  # varningen
    harness.wait(settings.dim_before_off_seconds)
    assert harness.commands()[-1][0] is False
    assert harness.lamp() == (False, True)
    assert all(
        not report.external for report in harness.observed if isinstance(report, LampStateReported)
    )


def test_presence_in_daylight_leaves_the_led_off() -> None:
    harness = Harness()
    harness.connect()
    harness.send(harness.sensor("Illuminance", 500))

    harness.walk_in(lux=None)

    assert harness.commands() == []
    assert harness.lamp() == (False, True)


def test_the_lamp_limit_holds_back_commands_and_sends_the_latest_later() -> None:
    harness = Harness()
    harness.connect()
    for n in range(LIMIT_BURST + 3):
        harness.leds.send(LampCommand(LED, on=n % 2 == 0, reason="test"))
    harness.settle()
    assert len(harness.commands()) == LIMIT_BURST

    harness.now += 10
    harness.leds.release_held()
    harness.settle()
    # Bara det senaste uppskjutna kommandot skickas.
    assert len(harness.commands()) == LIMIT_BURST + 1
    assert harness.commands()[-1] == (False, 1.0)


def test_a_card_that_restarts_gets_the_latest_command_again() -> None:
    harness = Harness()
    harness.connect()
    harness.walk_in()
    assert harness.lamp() == (True, True)

    harness.link.events.disconnected()
    harness.settle()
    assert harness.lamp() == (None, False)

    # Efter omstarten är lysdioden släckt (restore_mode ALWAYS_OFF).
    harness.connect()
    assert harness.commands() == [(True, 1.0), (True, 1.0)]
    assert harness.lamp() == (True, True)


def test_a_card_without_the_led_still_works_and_the_lamp_does_not_answer() -> None:
    harness = Harness()
    harness.link.events.connected(harness.walk.entities)
    harness.settle()
    harness.walk_in()

    [card] = harness.source.cards()
    assert card.problems == ()  # testlampan är valfri
    assert harness.commands() == []
    assert harness.lamp() == (None, False)


def test_other_lamps_are_left_alone() -> None:
    harness = Harness()
    harness.connect()
    harness.leds.send(LampCommand("zigbee-0x0123456789abcdef", on=True, reason="test"))
    harness.leds.send(LampCommand("taklampa", on=True, reason="test"))
    harness.settle()
    assert harness.commands() == []


def test_the_editor_lists_an_led_for_each_placed_card() -> None:
    network = FakeNetwork()
    app = create_app(
        allowed_hosts={"testserver"}, home=card_home(), esphome_secret=SECRET, card_network=network
    )
    with TestClient(app) as client:
        lamps = client.get("/api/lamps").json()
    assert lamps == [
        {"id": LED, "name": f"LED på rumskort {CARD}", "profile": "generisk", "placed": False}
    ]


def test_without_cards_the_editor_lists_no_leds_and_the_led_is_virtual() -> None:
    app = create_app(allowed_hosts={"testserver"}, home=led_home())
    with TestClient(app) as client:
        assert client.get("/api/lamps").json() == []
    assert app.state.simulation.enabled
