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

Bara den som har kortets API-nyckel kan styra lysdioden, i praktiken kärnan,
så ett besked från kortet räknas aldrig som en ändring utifrån. Startar
kortet om är lysdioden släckt; är kortet snart anslutet igen får det kärnans
senaste kommando på nytt (se `RESEND_WITHIN_SECONDS`). En testlampa som inte
finns på kartan hålls släckt.
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
from homecontrol.rules import FULL_BRIGHTNESS

LAMP_ID_PREFIX = "led-"
RELEASE_SECONDS = 1.0
"""Hur ofta kommandon som styrgränsen har skjutit upp prövas igen."""
RESEND_WITHIN_SECONDS = 60.0
"""Kommer kortet tillbaka inom så här lång tid får det kärnans senaste kommando igen.

Det täcker en omstart eller ett kort Wi-Fi-avbrott, då rummet sällan hinner
ändras. Efter ett längre avbrott kan rummet ha tömts, så då tänds lysdioden
inte av ett gammalt kommando; den förblir släckt tills regeln tänder den igen.
"""


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

    def has_led(self, name: str) -> bool | None: ...

    def subscribe_leds(self, listener: Callable[[str], None]) -> Callable[[], None]: ...


_NEVER_REPORTED = LedState(on=False, brightness=0)
"""Står för att inget har rapporterats om lampan än. Skiljer sig från alla riktiga lägen
och från None, som betyder att lampan inte svarar."""


class CardLedOutput:
    """Lamputgången för testlamporna på rumskorten.

    `now` är tiden i sekunder, för styrgränsen och för hur länge ett kort har varit borta.
    """

    def __init__(
        self, cards: CardLeds, home: Home, now: Callable[[], float] = time.monotonic
    ) -> None:
        self._cards = cards
        self._home = home
        self._now = now
        self._limit = LampLimit(now)
        self._sink: ObservationSink | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._lamps: frozenset[str] = frozenset()
        """Testlamporna som är placerade i hemmet."""
        self._wanted: dict[str, LampCommand] = {}
        """Kärnans senaste kommando till varje testlampa."""
        self._held: dict[str, LampCommand] = {}
        """Kommandon som styrgränsen har skjutit upp, det senaste per lampa.

        Ligger kvar även för en lampa som just har tagits bort från kartan, så
        att den ändå blir släckt."""
        self._reported: dict[str, LedState | None] = {}
        """Det som senast rapporterades till kärnan om varje lampa."""
        self._lost_at: dict[str, float] = {}
        """När varje lampa slutade svara, för lampor som inte svarar."""

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
        for lamps in (self._wanted, self._reported, self._lost_at):
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
        self._send_or_hold(command)

    def release_held(self) -> None:
        """Skickar de uppskjutna kommandona till lampor som får styras igen."""
        for lamp_id, command in list(self._held.items()):
            if self._limit.allow(lamp_id):
                del self._held[lamp_id]
                self._send_to_card(command)

    def _send_or_hold(self, command: LampCommand) -> None:
        if self._limit.allow(command.lamp_id):
            self._send_to_card(command)
        else:
            self._held[command.lamp_id] = command

    def _send_to_card(self, command: LampCommand) -> None:
        card = led_card(command.lamp_id)
        if card is not None:
            # Svarar inte kortet nu avgör `_led_changed` vad det får när det är tillbaka.
            self._cards.set_led(card, command.on, command.brightness)

    # --- Det kortet berättar ---

    def _state(self, lamp_id: str) -> LedState | None:
        card = led_card(lamp_id)
        return None if card is None else self._cards.led(card)

    def _led_changed(self, card: str) -> None:
        lamp_id = card_led_lamp_id(card)
        state = self._cards.led(card)
        if lamp_id not in self._lamps:
            if state is not None and state.on:
                # Lampan har tagits bort från kartan medan kortet inte svarade.
                self._send_or_hold(LampCommand(lamp_id, False, "Testlampan finns inte på kartan"))
            return
        lost_for = self._lost_for(lamp_id, state)
        self._report(lamp_id, state)
        wanted = self._wanted.get(lamp_id)
        if lost_for is None or wanted is None or _already(state, wanted):
            return
        if not wanted.on or lost_for <= RESEND_WITHIN_SECONDS:
            # Kortet har varit borta en kort stund, till exempel startat om, och
            # glömt kommandot. Ett släck-kommando är alltid säkert att skicka igen.
            self._send_or_hold(wanted)

    def _lost_for(self, lamp_id: str, state: LedState | None) -> float | None:
        """Hur länge lampan var borta, om den svarar igen nu. Annars None."""
        if state is None:
            self._lost_at.setdefault(lamp_id, self._now())
            return None
        lost_at = self._lost_at.pop(lamp_id, None)
        return None if lost_at is None else self._now() - lost_at

    def _report(self, lamp_id: str, state: LedState | None) -> None:
        if self._reported.get(lamp_id, _NEVER_REPORTED) == state:
            return
        self._reported[lamp_id] = state
        if self._sink is None:
            return
        if state is None:
            self._sink(LampStateReported(lamp_id, None))
        else:
            # En släckt lampa rapporteras med full ljusstyrka, som i `LampStateReported`.
            brightness = state.brightness if state.on else FULL_BRIGHTNESS
            self._sink(LampStateReported(lamp_id, state.on, brightness))

    # --- Kartredigeraren (se lampdevices.py) ---

    def lamps(self) -> list[FoundLamp]:
        """En testlampa för varje placerat kort, så att den kan placeras på kartan.

        Kort som har svarat utan någon testlampa (äldre firmware) hoppas över.
        Namnet bygger på kortets eget namn, inte på det kortet säger om sig själv
        med mDNS, som vem som helst i nätet kan förfalska.
        """
        return [
            FoundLamp(
                card_led_lamp_id(card.name),
                f"LED på rumskort {card.name}",
                LampProfile.GENERIC,
                placed=card_led_lamp_id(card.name) in self._lamps,
            )
            for card in self._cards.cards()
            if card.placed and self._cards.has_led(card.name) is not False
        ]


def _already(state: LedState | None, command: LampCommand) -> bool:
    """Lysdioden är redan som kommandot ber om."""
    if state is None or state.on != command.on:
        return False
    return not command.on or state.brightness == command.brightness
