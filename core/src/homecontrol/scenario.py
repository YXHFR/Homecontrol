"""Ramverk för scenariotester: läsbara steg med styrd klocka i snabbspolad tid.

Ett scenario kör kärnans riktiga delar – låtsaskällan, närvarobedömningen,
regeln och den virtuella lamputgången – men med en klocka som bara går när
scenariot säger till. Tre minuters väntan tar därför ingen tid alls.

    (
        Scenario()
        .light_level(10)
        .person_enters()
        .expect_lamp_on()
        .person_leaves()
        .wait(minutes=3)
        .expect_lamp_off()
    )

Varje steg returnerar scenariot, så stegen kan kedjas. Ett misslyckat
`expect_...` säger vad som hände och lampans egen förklaring.
"""

from collections.abc import Iterable
from typing import Self

from homecontrol.clock import ManualClock
from homecontrol.controller import Controller, LampView
from homecontrol.home import Home, Point, demo_home
from homecontrol.simulation import SimulatedLampOutput, SimulatedSource


class Scenario:
    def __init__(self, home: Home | None = None) -> None:
        self.home = home if home is not None else demo_home()
        self.clock = ManualClock()
        self.lamps = SimulatedLampOutput()
        self.controller = Controller(self.home, self.clock, [self.lamps])
        self.source = SimulatedSource()
        self.source.start(self.controller.observe)

    # --- Händelser ---

    def light_level(self, lux: float, room: str | None = None) -> Self:
        self.source.set_light_level(self._room_id(room), lux)
        return self

    def person_enters(self, room: str | None = None) -> Self:
        """Låtsaspersonen går till mitten av rummet."""
        self.source.move_person(self.home.room(self._room_id(room)).center)
        return self

    def person_moves_to(self, x: float, y: float) -> Self:
        self.source.move_person(Point(x, y))
        return self

    def person_leaves(self) -> Self:
        """Låtsaspersonen lämnar hemmet och syns inte längre."""
        self.source.remove_person()
        return self

    def wait(self, *, minutes: float = 0, seconds: float = 0) -> Self:
        """Spolar fram klockan och låter kärnan reagera vid varje tidpunkt den väntar på."""
        duration = minutes * 60 + seconds
        if duration < 0:
            raise ValueError("Ett scenario kan inte vänta en negativ tid")
        until = self.clock.now() + duration
        while (wake_at := self.controller.wake_at) is not None and wake_at <= until:
            self.clock.advance_to(max(wake_at, self.clock.now()))
            self.controller.tick()
            if self.controller.wake_at == wake_at:
                raise RuntimeError(f"Kärnan vill väckas vid {wake_at} igen efter att ha väckts")
        self.clock.advance_to(until)
        self.controller.tick()
        return self

    # --- Förväntningar ---

    def expect_lamp_on(self, lamp: str | None = None) -> Self:
        return self._expect_lamp(lamp, on=True)

    def expect_lamp_off(self, lamp: str | None = None) -> Self:
        return self._expect_lamp(lamp, on=False)

    def expect_explanation(self, text: str, lamp: str | None = None) -> Self:
        view = self._lamp_view(lamp)
        if text not in view.explanation:
            raise AssertionError(
                f"Förklaringen för {view.lamp_id} innehåller inte {text!r}: {view.explanation!r}"
            )
        return self

    def _expect_lamp(self, lamp: str | None, *, on: bool) -> Self:
        view = self._lamp_view(lamp)
        actual = self.lamps.states.get(view.lamp_id, False)
        wanted, got = ("tänd", "släckt") if on else ("släckt", "tänd")
        if actual != on:
            raise AssertionError(
                f"{view.lamp_id} skulle vara {wanted} efter {self.clock.now():g} s men är {got}. "
                f"Förklaring: {view.explanation}"
            )
        return self

    # --- Hjälpfunktioner ---

    def _lamp_view(self, lamp: str | None) -> LampView:
        lamp_id = lamp if lamp is not None else _only(lamp.id for lamp in self.home.lamps)
        for view in self.controller.snapshot().lamps:
            if view.lamp_id == lamp_id:
                return view
        raise KeyError(f"Okänd lampa: {lamp_id}")

    def _room_id(self, room: str | None) -> str:
        return room if room is not None else _only(room.id for room in self.home.rooms)


def _only(ids: Iterable[str]) -> str:
    choices = list(ids)
    if len(choices) != 1:
        raise ValueError(f"Det finns {len(choices)} att välja mellan, ange vilken: {choices}")
    return choices[0]
