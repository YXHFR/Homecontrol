"""Ramverk för scenariotester: läsbara steg med styrd klocka i snabbspolad tid.

Ett scenario kör kärnans riktiga delar – låtsaskällan med sina virtuella
radarer, sammanvägningen, regeln och den virtuella lamputgången – men med en
klocka som bara går när scenariot säger till. Tre minuters väntan tar därför
ingen tid alls.

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
from homecontrol.fusion import HOLD_SECONDS, Occupancy
from homecontrol.home import Capability, Home, Point
from homecontrol.simulation import SimulatedLampOutput, SimulatedSource, Simulation
from homes import one_room_home

_PERSON = "personen"
RADAR_INTERVAL = 0.1
"""Radarn skickar det den ser tio gånger i sekunden."""


class Scenario:
    def __init__(self, home: Home | None = None, *, lamps_report: bool = True) -> None:
        """Utan `lamps_report` säger de virtuella lamporna aldrig hur de är, och läget är okänt."""
        self.home = home if home is not None else one_room_home()
        self.clock = ManualClock()
        self.lamps = SimulatedLampOutput()
        self.controller = Controller(self.home, self.clock, [self.lamps], wall_clock=self.clock.now)
        if lamps_report:
            self.lamps.report_new_lamps(self.home, self.controller.observe)
        # Samma simuleringsläge som i livevyn, men med en klocka som scenariot styr.
        self.source = SimulatedSource(self.home)
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

    def walk(self, *, seconds: float, **destinations: tuple[float, float]) -> Self:
        """Personerna går samtidigt i rak linje till sina mål, till exempel `Anna=(4, 2)`.

        Radarn ser dem tio gånger i sekunden på vägen. Framme står de kvar en
        sekund, så att radarn hinner se var de stannade.
        """
        floor_id = _only(floor.id for floor in self.home.floors)
        persons = self.source.persons
        starts = {who: persons[self._persons[who]].position for who in destinations}
        steps = max(1, round(seconds / RADAR_INTERVAL))
        for step in range(1, steps + 1):
            self.wait(seconds=seconds / steps)
            share = step / steps
            self.source.move_persons(
                {
                    self._persons[who]: (floor_id, _between(starts[who], Point(*end), share))
                    for who, end in destinations.items()
                }
            )
        for _ in range(round(1 / RADAR_INTERVAL)):
            self.wait(seconds=RADAR_INTERVAL)
            self.source.repeat_targets()
        return self

    def radar_loses(self, *, who: str = _PERSON) -> Self:
        """Radarn tappar personen, som sitter så still att den inte syns, men personen är kvar."""
        self.source.set_seen_by_radar(self._persons[who], False)
        return self

    def wifi_motion(self, room: str | None = None) -> Self:
        """Wi-Fi-mätningen i rummet känner rörelse."""
        return self._set_motion(room, moving=True)

    def wifi_motion_stops(self, room: str | None = None) -> Self:
        """Wi-Fi-mätningen i rummet slutar känna rörelse."""
        return self._set_motion(room, moving=False)

    def wait_for_hold(self) -> Self:
        """Väntar ut de 10 sekunder som ett tappat spår hålls kvar mitt i rummet."""
        return self.wait(seconds=HOLD_SECONDS)

    def source_lost(self) -> Self:
        """Låtsaskällan tappar kontakten, som en radar utan ström."""
        self.source.stop()
        return self

    def source_returns(self) -> Self:
        """Låtsaskällan får kontakt igen. Personerna den tappade är glömda."""
        self.source.start(self.controller.observe)
        return self

    def switch_lamp(self, on: bool, lamp: str | None = None) -> Self:
        """Någon klickar på lampan i livevyn och tänder eller släcker den för hand."""
        self.controller.set_lamp(self._lamp_view(lamp).lamp_id, on)
        return self

    def resume_automation(self, room: str | None = None) -> Self:
        """Någon ber om automatiken igen i livevyn, utan att vänta tills rummet är tomt."""
        self.controller.resume_automation(self._room_id(room))
        return self

    def person_leaves(self, *, who: str = _PERSON) -> Self:
        """Personen lämnar hemmet och syns inte längre."""
        self.simulation.remove_person(self._persons.pop(who))
        return self

    def wait(self, *, hours: float = 0, minutes: float = 0, seconds: float = 0) -> Self:
        """Spolar fram klockan och låter kärnan reagera vid varje tidpunkt den väntar på."""
        duration = (hours * 60 + minutes) * 60 + seconds
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

    def expect_room(self, status: str, room: str | None = None) -> Self:
        """Läget i rummet: "upptaget", "tomt" eller "okänt"."""
        room_id = self._room_id(room)
        view = next(r for r in self.controller.snapshot().rooms if r.room_id == room_id)
        return self._expect_status(room_id, view.status, status)

    def expect_zone(self, status: str, zone: str) -> Self:
        """Läget i zonen: "upptaget", "tomt" eller "okänt"."""
        view = next(z for z in self.controller.snapshot().zones if z.zone_id == zone)
        return self._expect_status(zone, view.status, status)

    def expect_manual(self, manual: bool = True, room: str | None = None) -> Self:
        """Om automatiken i rummet pausar efter manuell styrning."""
        room_id = self._room_id(room)
        view = next(r for r in self.controller.snapshot().rooms if r.room_id == room_id)
        if view.manual != manual:
            wanted = "i manuellt läge" if manual else "automatiskt"
            raise AssertionError(
                f"{room_id} skulle vara {wanted} efter {self.clock.now():g} s. "
                f"Förklaring: {view.explanation}"
            )
        return self

    def expect_event(self, text: str) -> Self:
        """Någon händelse i "Varför hände det?" innehåller texten."""
        events = [event.text for event in self.controller.snapshot().events]
        if not any(text in event for event in events):
            raise AssertionError(f"Ingen händelse innehåller {text!r}: {events}")
        return self

    def expect_tracks(self, count: int, *, held: int = 0) -> Self:
        """Så många spår finns, och så många av dem är tappade men kvarhållna."""
        tracks = self.controller.snapshot().tracks
        actual = (len(tracks), sum(track.held for track in tracks))
        if actual != (count, held):
            raise AssertionError(
                f"Väntade {count} spår varav {held} kvarhållna efter {self.clock.now():g} s, "
                f"men det är {actual[0]} varav {actual[1]}"
            )
        return self

    def expect_lamp_on(self, lamp: str | None = None) -> Self:
        return self._expect_lamp(lamp, on=True)

    def expect_lamp_off(self, lamp: str | None = None) -> Self:
        return self._expect_lamp(lamp, on=False)

    def expect_dimmed(self, percent: int, lamp: str | None = None) -> Self:
        """Lampan lyser med lägre ljusstyrka, som under varningsdimringen."""
        self._expect_lamp(lamp, on=True)
        lamp_id = self._lamp_view(lamp).lamp_id
        actual = self.lamps.brightness[lamp_id]
        if actual != percent:
            raise AssertionError(
                f"{lamp_id} lyser med {actual} % efter {self.clock.now():g} s, väntade {percent} %"
            )
        return self

    def expect_full_brightness(self, lamp: str | None = None) -> Self:
        return self.expect_dimmed(100, lamp)

    def expect_lamp_switches(self, *switches: bool, lamp: str | None = None) -> Self:
        """Lampan har tänts (True) och släckts (False) i exakt den här ordningen.

        En ändrad ljusstyrka på en lampa som redan lyser, som en dimring, räknas inte.
        """
        lamp_id = self._lamp_view(lamp).lamp_id
        commands = [c.on for c in self.lamps.history if c.lamp_id == lamp_id]
        actual = tuple(on for i, on in enumerate(commands) if i == 0 or commands[i - 1] != on)
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

    def _expect_status(self, name: str, actual: Occupancy, wanted: str) -> Self:
        if actual is not Occupancy(wanted):
            raise AssertionError(
                f"{name} skulle vara {wanted} efter {self.clock.now():g} s men är {actual.value}"
            )
        return self

    def _set_motion(self, room: str | None, *, moving: bool) -> Self:
        for node in self.home.room(self._room_id(room)).nodes:
            if Capability.CSI in node.capabilities:
                self.source.set_motion(node.id, moving)
        return self

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


def _between(start: Point, end: Point, share: float) -> Point:
    """Punkten en viss andel av vägen från `start` till `end`."""
    return Point(start.x + (end.x - start.x) * share, start.y + (end.y - start.y) * share)


def _only(ids: Iterable[str]) -> str:
    choices = list(ids)
    if len(choices) != 1:
        raise ValueError(f"Det finns {len(choices)} att välja mellan, ange vilken: {choices}")
    return choices[0]
