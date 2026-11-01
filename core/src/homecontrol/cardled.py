"""Testlampan: lysdioden på ett rumskort, styrd som en lampa.

Med testlampan går hela idén att prova innan det finns någon Zigbee-lampa:
radarn ser någon, ljussensorn säger att det är mörkt och kärnan tänder
lysdioden på kortet. Den följer samma regler, samma styrgräns (`LampLimit`)
och samma ljusvillkor som en Zigbee-lampa, eftersom kärnan inte vet vilken
utgång en lampa har.

En testlampa är en lampa i hemmet med id:t ``led-<kortets namn>``, till
exempel ``led-homecontrol-kok``. Kommandona går genom kortets befintliga,
krypterade ESPHome-anslutning (se esphome.py); ingen ny nätverkstjänst behövs.
Kortet tänder eller släcker ett stift (se firmware/common.yaml) och berättar
sedan hur lysdioden är. Det beskedet lämnas till kärnan som `LampStateReported`.

Lysdioden går bara att styra från kärnan, så ett besked från kortet är aldrig
en ändring utifrån. Startar kortet om är lysdioden släckt; när kortet är
anslutet igen får det kärnans senaste kommando på nytt.
"""

import asyncio
import time
from collections.abc import Callable, Sequence
from typing import Protocol

from homecontrol.cards import CardView
from homecontrol.connectors import LampCommand, LampStateReported, ObservationSink
from homecontrol.esphome import LedState, is_card_name
from homecontrol.home import Home, LampProfile
from homecontrol.lampdevices import FoundLamp
from homecontrol.lamplimit import LampLimit

LAMP_ID_PREFIX = "led-"
RELEASE_SECONDS = 1.0
"""Hur ofta kommandon som styrgränsen har skjutit upp prövas igen."""


def card_led_lamp_id(card: str) -> str:
    """Id:t för testlampan på ett kort, till exempel ``led-homecontrol-kok``."""
    return LAMP_ID_PREFIX + card


def led_card(lamp_id: str) -> str | None:
    """Kortet som en testlampa sitter på, eller None om lampan inte är en testlampa."""
    if not lamp_id.startswith(LAMP_ID_PREFIX):
        return None
    card = lamp_id.removeprefix(LAMP_ID_PREFIX)
    return card if is_card_name(card) else None


def card_led_lamps(home: Home) -> frozenset[str]:
    """Hemmets lampor som är testlampor på rumskort."""
    return frozenset(lamp.id for lamp in home.lamps if led_card(lamp.id) is not None)


class CardLeds(Protocol):
    """Rumskorten med sina testlampor, det vill säga ESPHome-kopplingen (se esphome.py)."""

    def cards(self) -> Sequence[CardView]: ...

    def led(self, name: str) -> LedState | None: ...

    def set_led(self, name: str, on: bool, brightness: int) -> bool: ...

    def subscribe_leds(self, listener: Callable[[str], None]) -> Callable[[], None]: ...


_UNKNOWN = LedState(on=False, brightness=0)
"""Står för att inget har rapporterats än (skiljer sig från alla riktiga lägen)."""


class CardLedOutput:
    """Lamputgången för testlamporna på rumskorten.

    `now` är tiden för styrgränsen i sekunder.
    """

    def __init__(
        self, cards: CardLeds, home: Home, now: Callable[[], float] = time.monotonic
    ) -> None:
        self._cards = cards
        self._home = home
        self._limit = LampLimit(now)
        self._sink: ObservationSink | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._lamps: frozenset[str] = frozenset()
        """Testlamporna som är placerade i hemmet."""
        self._wanted: dict[str, LampCommand] = {}
        """Kärnans senaste kommando till varje testlampa."""
        self._held: dict[str, LampCommand] = {}
        """Kommandon som styrgränsen har skjutit upp, det senaste per lampa."""
        self._reported: dict[str, LedState | None] = {}
        """Det som senast rapporterades till kärnan om varje lampa."""

    # --- Livscykeln ---

    def start(self, sink: ObservationSink) -> None:
        self._sink = sink
        self._unsubscribe = self._cards.subscribe_leds(self._led_changed)
        self.use_home(self._home)

    def stop(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._sink = None

    async def run(self, sink: ObservationSink) -> None:
        """Startar utgången och skickar uppskjutna kommandon tills den avbryts."""
        self.start(sink)
        try:
            while True:
                await asyncio.sleep(RELEASE_SECONDS)
                self.release_held()
        finally:
            self.stop()

    def use_home(self, home: Home) -> None:
        """Byter till ett ändrat hem och berättar hur nyplacerade testlampor är."""
        self._home = home
        self._lamps = card_led_lamps(home)
        for lamps in (self._wanted, self._held, self._reported):
            for lamp_id in set(lamps) - self._lamps:
                del lamps[lamp_id]
        if self._sink is None:
            return
        for lamp_id in sorted(self._lamps - self._reported.keys()):
            self._report(lamp_id, self._state(lamp_id))

    # --- LampOutput ---

    def send(self, command: LampCommand) -> None:
        if command.lamp_id not in self._lamps:
            return  # en annan utgångs lampa
        self._wanted[command.lamp_id] = command
        # Ett nytt kommando ersätter ett som har skjutits upp.
        self._held.pop(command.lamp_id, None)
        self._try(command)

    def release_held(self) -> None:
        """Skickar de uppskjutna kommandona till lampor som får styras igen."""
        for lamp_id, command in list(self._held.items()):
            if self._limit.allow(lamp_id):
                del self._held[lamp_id]
                self._set(command)

    def _try(self, command: LampCommand) -> None:
        if self._limit.allow(command.lamp_id):
            self._set(command)
        else:
            self._held[command.lamp_id] = command

    def _set(self, command: LampCommand) -> None:
        card = led_card(command.lamp_id)
        if card is not None:
            # Svarar inte kortet nu får det kommandot när det ansluter igen.
            self._cards.set_led(card, command.on, command.brightness)

    # --- Det kortet berättar ---

    def _state(self, lamp_id: str) -> LedState | None:
        card = led_card(lamp_id)
        return None if card is None else self._cards.led(card)

    def _led_changed(self, card: str) -> None:
        lamp_id = card_led_lamp_id(card)
        if lamp_id not in self._lamps:
            return
        state = self._cards.led(card)
        came_back = self._reported.get(lamp_id, _UNKNOWN) is None and state is not None
        self._report(lamp_id, state)
        wanted = self._wanted.get(lamp_id)
        if came_back and wanted is not None and not _matches(state, wanted):
            # Kortet har varit borta, till exempel startat om, och glömt kommandot.
            self._try(wanted)

    def _report(self, lamp_id: str, state: LedState | None) -> None:
        if self._reported.get(lamp_id, _UNKNOWN) == state:
            return
        self._reported[lamp_id] = state
        if self._sink is None:
            return
        if state is None:
            self._sink(LampStateReported(lamp_id, None))
        else:
            self._sink(LampStateReported(lamp_id, state.on, state.brightness if state.on else 100))

    # --- Kartredigeraren (se lampdevices.py) ---

    def lamps(self) -> list[FoundLamp]:
        """En testlampa för varje placerat kort, så att den kan placeras på kartan."""
        return [
            FoundLamp(
                card_led_lamp_id(card.name),
                f"LED på rumskort {card.friendly_name}"[:100],
                LampProfile.GENERIC,
                placed=card_led_lamp_id(card.name) in self._lamps,
            )
            for card in self._cards.cards()
            if card.placed
        ]


def _matches(state: LedState | None, command: LampCommand) -> bool:
    if state is None or state.on != command.on:
        return False
    return not command.on or state.brightness == command.brightness
