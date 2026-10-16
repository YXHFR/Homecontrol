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

Personerna har namn när det finns flera, till exempel
`.person_enters(who="Anna")`. Utan namn gäller stegen en och samma person.

Varje steg returnerar scenariot, så stegen kan kedjas. Ett misslyckat
`expect_...` säger vad som hände och lampans egen förklaring.

Ramverket hör till testerna och följer inte med i den körbara kärnan.
"""

from collections.abc import Iterable
from typing import Self

from homecontrol.clock import ManualClock
from homecontrol.controller import Controller, LampView
from homecontrol.home import Home, Point
from homecontrol.simulation import SimulatedLampOutput, SimulatedSource, Simulation
from homes import one_room_home

_PERSON = "personen"


class Scenario:
    def __init__(self, home: Home | None = None) -> None:
        self.home = home if home is not None else one_room_home()
        self.clock = ManualClock()
        self.lamps = SimulatedLampOutput()
        self.controller = Controller(self.home, self.clock, [self.lamps])
        # Samma simuleringsläge som i livevyn, men med en klocka som scenariot styr.
        self.source = SimulatedSource(room_ids=(room.id for room in self.home.rooms))
        self.simulation = Simulation(
            self.source,
            self.controller.observe,
            self.clock,
            floor_ids=(floor.id for floor in self.home.floors),
            daylight=None,
        )
        # Personernas namn i scenariot och deras id i låtsaskällan.
        self._persons: dict[str, str] = {}

    # --- Händelser ---

    def light_level(self, lux: float, room: str | None = None) -> Self:
        self.source.set_light_level(self._room_id(room), lux)
        return self

    def daylight(self, lux: float) -> Self:
        """Samma ljusnivå i alla rum, som dagsljusreglaget i livevyn."""
        self.simulation.set_daylight(lux)
        return self

    def simulation_off(self) -> Self:
        """Simuleringsläget slås av i livevyn. Låtsaspersonerna försvinner."""
        self.simulation.set_enabled(False)
        return self

    def person_enters(self, room: str | None = None, *, who: str = _PERSON) -> Self:
        """Personen går till mitten av rummet."""
        room_id = self._room_id(room)
        self._place(who, self.home.floor_of(room_id).id, self.home.room(room_id).center)
        return self

    def person_moves_to(
        self, *, x: float, y: float, floor: str | None = None, who: str = _PERSON
    ) -> Self:
        floor_id = floor if floor is not None else _only(f.id for f in self.home.floors)
        self._place(who, floor_id, Point(x, y))
        return self

    def source_lost(self) -> Self:
        """Låtsaskällan tappar kontakten, som en radar utan ström."""
        self.source.stop()
        return self

    def person_leaves(self, *, who: str = _PERSON) -> Self:
        """Personen lämnar hemmet och syns inte längre."""
        self.simulation.remove_person(self._persons.pop(who))
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
                raise RuntimeError(
                    f"Regeln bad om att väckas vid {wake_at} s, men när den väcktes bad den "
                    "om samma tid igen. Scenariot skulle fastna, så det avbryts."
                )
        self.clock.advance_to(until)
        self.controller.tick()
        return self

    # --- Förväntningar ---

    def expect_lamp_on(self, lamp: str | None = None) -> Self:
        return self._expect_lamp(lamp, on=True)

    def expect_lamp_off(self, lamp: str | None = None) -> Self:
        return self._expect_lamp(lamp, on=False)

    def expect_lamp_switches(self, *switches: bool, lamp: str | None = None) -> Self:
        """Lampan har tänts (True) och släckts (False) i exakt den här ordningen."""
        lamp_id = self._lamp_view(lamp).lamp_id
        actual = tuple(c.on for c in self.lamps.history if c.lamp_id == lamp_id)
        if actual != switches:
            raise AssertionError(f"{lamp_id} slogs om {actual}, väntade {switches}")
        return self

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

    def _place(self, who: str, floor_id: str, position: Point) -> None:
        """Flyttar personen, eller lägger till den om den inte redan finns."""
        person_id = self._persons.get(who)
        if person_id is None or person_id not in self.source.persons:
            added = self.simulation.add_person(floor_id, position)
            if added is None:
                raise RuntimeError(f"Simuleringen tog inte emot {who} på våningen {floor_id}")
            self._persons[who] = added
        else:
            self.simulation.move_person(person_id, floor_id, position)

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
