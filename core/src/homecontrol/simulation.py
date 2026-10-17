"""Simulering: en låtsaskälla, virtuella lampor och simuleringsläget i livevyn.

Låtsaskällan styrs från livevyn eller från scenariotester och lämnar sina
observationer genom samma ingång som riktiga källor. Varje rumskort med radar
i hemmet blir en virtuell radar, som ser låtsaspersonerna i sitt synfält och
skickar dem i sina egna koordinater, precis som en riktig radar. Den virtuella
radarn är av samma sort som nodens (`Node.radar`), så att ett rum med en
LD2461 kan provas med fem personer utan hårdvara. Kärnan kan
alltså inte se skillnad på en låtsasperson och en person som radarn ser, och
omräkningen och sammanvägningen prövas på samma sätt som med riktiga noder.

Den virtuella radarn ser genom alla väggar. Det gör en riktig radar också,
mer eller mindre, och sammanvägningen ska klara det.

Riktiga rumskort simuleras inte. Låtsaspersonerna syns bara för de virtuella
radarerna, och dagsljuset sätts bara i rum utan riktiga kort, så att
simuleringen aldrig blandas med det de riktiga korten mäter.

De virtuella lamporna kan lysa upp de simulerade ljussensorerna (se
`SimulatedLampOutput.light_by_room`), så att lampkalibreringen går att prova.
Ljuset avtar med avståndet från lampan till rummets ljussensor. Sensorerna
mäter var femte sekund (`run_light_sensors`), precis som rumskorten, och när
dagsljuset ändras.
"""

import asyncio
import contextlib
import logging
import math
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Set
from dataclasses import dataclass
from itertools import count
from typing import Literal, get_args

from homecontrol.clock import PacedClock, SpeedControl
from homecontrol.connectors import (
    LampCommand,
    LampStateReported,
    LightLevelObserved,
    MotionObserved,
    ObservationSink,
    RadarTarget,
    RadarTargetsObserved,
    SourceLost,
)
from homecontrol.home import Capability, Home, Node, Point, Room, Target
from homecontrol.lamplimit import LampLimit
from homecontrol.rules import FULL_BRIGHTNESS

logger = logging.getLogger(__name__)

Listener = Callable[[], None]

MAX_PERSONS = 10
"""Fler låtsaspersoner än så här behövs inte för att prova ett hem."""

Speed = Literal[1, 10, 60]
"""Tidsfarterna som går att välja. Vid 60× går en minut på en sekund."""
SPEEDS: tuple[Speed, ...] = get_args(Speed)

# Den virtuella radarn ser som en LD2450: 6 meter bort och 60° åt varje
# sida. Den följer så många personer som nodens radar (`Node.radar`): tre
# för LD2450 och fem för LD2461. Synfältet är LD2450:s även för LD2461,
# eftersom LD2461:s räckvidd inte är provad än.
RADAR_RANGE = 6.0
"""Så långt radarn ser, i meter."""
RADAR_HALF_ANGLE = 60.0
"""Så många grader åt varje sida om riktningen den tittar åt som radarn ser."""

MAX_HISTORY = 1000
"""Så många av de senaste kommandona minns de virtuella lamporna."""

DEFAULT_DAYLIGHT_LUX = 10.0
"""Dagsljuset när simuleringen startar: mörkt, så att lamporna tänds."""

LAMP_LUX_AT_ONE_METER = 80.0
"""Så mycket en virtuell lampa på full styrka lyser upp en sensor en meter bort, i lux.

Ungefär som en vanlig lampa på 800 lumen. Ljuset avtar med kvadraten på avståndet.
"""


class TooManyPersons(Exception):
    pass


class SimulatedSource:
    """En källkoppling med låtsaspersoner, låtsad Wi-Fi-rörelse och låtsat dagsljus.

    Varje person får ett id när den läggs till, så att den kan flyttas och tas
    bort. Id:t stannar i simuleringen; kärnan får bara se det radarna ser.
    """

    def __init__(
        self,
        home: Home,
        source_id: str = "simulering",
        real_nodes: Callable[[Home], Set[str]] = lambda _: frozenset(),
    ) -> None:
        """`real_nodes` ger de riktiga rumskorten i ett hem, som inte ska simuleras."""
        self._source_id = source_id
        self._home = home
        self._real_nodes = real_nodes
        self._sink: ObservationSink | None = None
        self._persons: dict[str, Target] = {}
        self._unseen: set[str] = set()
        """Personer som radarn har tappat, fast de är kvar."""
        self._motion: dict[str, bool] = {}
        """Om varje nods Wi-Fi-mätning känner rörelse."""
        self._next_id = count(1)
        self._daylight: float | None = None
        self._lamp_light: Callable[[], Mapping[str, float]] = dict
        """Ljuset från de virtuella lamporna i varje rum just nu, utöver dagsljuset."""

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def persons(self) -> Mapping[str, Target]:
        return dict(self._persons)

    @property
    def home(self) -> Home:
        return self._home

    @property
    def daylight(self) -> float | None:
        """Dagsljuset i alla rum, eller None om det inte är satt."""
        return self._daylight

    def start(self, sink: ObservationSink) -> None:
        self._sink = sink
        self._emit_targets()
        for node_id, moving in self._motion.items():
            self._sink(MotionObserved(self._source_id, node_id, moving))
        if self._daylight is not None:
            self.set_daylight(self._daylight)

    def stop(self) -> None:
        """Källan tystnar och glömmer sina personer, som en radar som stängs av."""
        self._persons.clear()
        self._unseen.clear()
        self._motion.clear()
        if self._sink is not None:
            self._sink(SourceLost(self._source_id))
        self._sink = None

    def add_person(self, floor_id: str, position: Point) -> str:
        """Lägger till en person och returnerar dess id."""
        if len(self._persons) >= MAX_PERSONS:
            raise TooManyPersons(f"Simuleringen har redan {MAX_PERSONS} personer")
        person_id = str(next(self._next_id))
        self._persons[person_id] = Target(floor_id, position)
        self._emit_targets()
        return person_id

    def move_person(self, person_id: str, floor_id: str, position: Point) -> None:
        """Flyttar en person. En person som inte finns (längre) lämnas i fred."""
        self.move_persons({person_id: (floor_id, position)})

    def move_persons(self, moves: Mapping[str, tuple[str, Point]]) -> None:
        """Flyttar flera personer på en gång, så att radarerna ser dem samtidigt."""
        moved = False
        for person_id, (floor_id, position) in moves.items():
            if person_id in self._persons:
                self._persons[person_id] = Target(floor_id, position)
                moved = True
        if moved:
            self._emit_targets()

    def remove_person(self, person_id: str) -> None:
        self._unseen.discard(person_id)
        if self._persons.pop(person_id, None) is not None:
            self._emit_targets()

    def set_seen_by_radar(self, person_id: str, seen: bool) -> None:
        """Radarn tappar personen (eller hittar den igen), fast den inte har gått någonstans.

        Så gör en riktig radar med någon som sitter eller ligger helt still.
        """
        if person_id in self._persons:
            if seen:
                self._unseen.discard(person_id)
            else:
                self._unseen.add(person_id)
            self._emit_targets()

    def repeat_targets(self) -> None:
        """Skickar det radarna ser igen, som en riktig radar gör tio gånger i sekunden."""
        self._emit_targets()

    def set_motion(self, node_id: str, moving: bool) -> None:
        """Låtsas att nodens Wi-Fi-mätning känner rörelse, eller slutar känna den."""
        self._motion[node_id] = moving
        if self._sink is not None:
            self._sink(MotionObserved(self._source_id, node_id, moving))

    def set_daylight(self, lux: float) -> None:
        """Samma ljusnivå i alla rum, som när solen går upp eller ner."""
        self._daylight = lux
        for room in self._simulated_rooms(self._home):
            self._measure(room.id)

    def use_lamp_light(self, lamp_light: Callable[[], Mapping[str, float]]) -> None:
        """`lamp_light` ger ljuset från de virtuella lamporna per rum, som sensorerna mäter."""
        self._lamp_light = lamp_light

    def measure_light(self) -> None:
        """Varje simulerad sensor mäter dagsljuset och lampornas ljus, som ett rumskort gör."""
        for room in self._simulated_rooms(self._home):
            self._measure(room.id)

    def use_home(self, home: Home) -> None:
        """Byter hem, till exempel när det har ritats om. Nya rum får dagsljuset."""
        before = {room.id for room in self._home.rooms}
        self._home = home
        nodes = {node.id for room in home.rooms for node in room.nodes}
        self._motion = {node: moving for node, moving in self._motion.items() if node in nodes}
        if self._daylight is not None:
            for room in self._simulated_rooms(home):
                if room.id not in before:
                    self._measure(room.id)
        self._emit_targets()

    def _simulated_rooms(self, home: Home) -> list[Room]:
        """Rummen utan riktiga rumskort, där simuleringen bestämmer dagsljuset."""
        real = self._real_nodes(home)
        return [room for room in home.rooms if not any(node.id in real for node in room.nodes)]

    def set_light_level(self, room_id: str, lux: float) -> None:
        """Ljusnivån i ett enda rum, som sensorn mäter den."""
        if self._sink is not None:
            self._sink(LightLevelObserved(self._source_id, room_id, lux))

    def _measure(self, room_id: str) -> None:
        """Den simulerade sensorn i rummet mäter dagsljuset och lampornas ljus."""
        if self._daylight is not None:
            self.set_light_level(room_id, self._daylight + self._lamp_light().get(room_id, 0.0))

    def _emit_targets(self) -> None:
        """Varje radar skickar personerna den ser, i sina egna koordinater.

        Radarn numrerar målen efter avståndet, så numren byter när två
        personer går förbi varandra, som de gör i en riktig radar.
        """
        if self._sink is None:
            return
        seen = [
            person for person_id, person in self._persons.items() if person_id not in self._unseen
        ]
        real = self._real_nodes(self._home)
        for floor in self._home.floors:
            on_floor = [person.position for person in seen if person.floor_id == floor.id]
            for node in (node for room in floor.rooms for node in room.nodes):
                if Capability.RADAR in node.capabilities and node.id not in real:
                    targets = _targets_seen_by(node, on_floor)
                    self._sink(RadarTargetsObserved(self._source_id, node.id, targets, node.radar))


def _targets_seen_by(node: Node, positions: Iterable[Point]) -> tuple[RadarTarget, ...]:
    """Det radarn på noden ser av personerna, närmast först och högst så många som den följer."""
    in_view = [
        target
        for target in (node.home_to_radar(position) for position in positions)
        if _in_view(target)
    ]
    in_view.sort(key=lambda target: math.hypot(target.x, target.y))
    return tuple(RadarTarget(t.x, t.y) for t in in_view[: node.radar.max_targets])


def _in_view(target: Point) -> bool:
    """Om en punkt i radarns koordinater ligger i dess synfält."""
    return (
        target.y > 0
        and math.hypot(target.x, target.y) <= RADAR_RANGE
        and abs(math.degrees(math.atan2(target.x, target.y))) <= RADAR_HALF_ANGLE
    )


class SimulatedLampOutput:
    """En lamputgång med virtuella lampor, som bara minns vad de senast fick för kommando.

    `real_lamps` ger lamporna i ett hem som en riktig lamputgång styr. De är
    inte virtuella, så den här utgången varken rapporterar eller styr dem.

    Simuleringen låter lampornas ljus nå de simulerade sensorerna (se `light_by_room`).

    Lamporna har samma styrgräns som riktiga lampor (se `LampLimit`), räknat
    med `now`, kärnans klocka. Ett kommando som gränsen stoppar skjuts upp
    och utförs så fort lampan får styras igen och någon frågar hur lamporna är.
    """

    def __init__(
        self,
        real_lamps: Callable[[Home], Set[str]] = lambda _: frozenset(),
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._real_lamps = real_lamps
        self._real: Set[str] = frozenset()
        self._states: dict[str, bool] = {}
        self._brightness: dict[str, int] = {}
        self._limit = LampLimit(now)
        self._held: dict[str, LampCommand] = {}
        """Kommandon som styrgränsen har skjutit upp, det senaste per lampa."""
        # De senaste kommandona, för scenariotesterna. Begränsat så att
        # förklaringarna (var någon har varit) inte samlas i minnet för alltid.
        self.history: deque[LampCommand] = deque(maxlen=MAX_HISTORY)

    @property
    def states(self) -> Mapping[str, bool]:
        """Om varje virtuell lampa är tänd. Uppskjutna kommandon som får utföras utförs först."""
        self._release_held()
        return self._states

    @property
    def brightness(self) -> Mapping[str, int]:
        """Ljusstyrkan i procent som varje virtuell lampa senast fick.

        Uppskjutna kommandon som får utföras utförs först.
        """
        self._release_held()
        return self._brightness

    def send(self, command: LampCommand) -> None:
        if command.lamp_id in self._real:
            return
        # Ett nytt kommando ersätter ett som har skjutits upp.
        self._held.pop(command.lamp_id, None)
        self._release_held()
        if self._limit.allow(command.lamp_id):
            self._apply(command)
        else:
            self._held[command.lamp_id] = command

    def _apply(self, command: LampCommand) -> None:
        self._states[command.lamp_id] = command.on
        self._brightness[command.lamp_id] = command.brightness
        self.history.append(command)

    def _release_held(self) -> None:
        """Utför de uppskjutna kommandona till lampor som får styras igen."""
        for lamp_id in list(self._held):
            if self._limit.allow(lamp_id):
                self._apply(self._held.pop(lamp_id))

    def light_by_room(self, home: Home) -> dict[str, float]:
        """Hur mycket de tända virtuella lamporna lyser upp varje rums ljussensor, i lux.

        Ljuset mäts vid rummets första rumskort med ljussensor, eller mitt i
        rummet om det inte har något. Lampor närmare än en meter räknas som en meter bort.
        """
        self._release_held()
        light = {}
        for room in home.rooms:
            sensor = next(
                (node.position for node in room.nodes if Capability.LUX in node.capabilities),
                room.center,
            )
            total = 0.0
            for lamp in room.lamps:
                if self._states.get(lamp.id) is not True:
                    continue
                distance = max(1.0, math.dist(lamp.position, sensor))
                share = self._brightness.get(lamp.id, FULL_BRIGHTNESS) / FULL_BRIGHTNESS
                total += LAMP_LUX_AT_ONE_METER * share / distance**2
            light[room.id] = total
        return light

    def report_new_lamps(self, home: Home, sink: ObservationSink) -> None:
        """Berättar att hemmets virtuella lampor som inte har styrts än är släckta.

        En virtuell lampa finns bara här, så utgången vet alltid hur den är,
        precis som en riktig lampa som rapporterar sitt läge. Anropas när
        utgången startar och när hemmet har ändrats.
        """
        # En lampa som har tagits bort glöms, så att den rapporteras igen om den läggs tillbaka.
        self._real = self._real_lamps(home)
        lamp_ids = {lamp.id for lamp in home.lamps if lamp.id not in self._real}
        self._states = {lamp_id: on for lamp_id, on in self._states.items() if lamp_id in lamp_ids}
        self._held = {
            lamp_id: command for lamp_id, command in self._held.items() if lamp_id in lamp_ids
        }
        for lamp in home.lamps:
            if lamp.id in lamp_ids and lamp.id not in self._states:
                self._states[lamp.id] = False
                sink(LampStateReported(lamp.id, on=False))


@dataclass(frozen=True)
class SimulationView:
    """Det som just nu går att visa om simuleringen."""

    enabled: bool
    speed: float
    speed_locked: bool
    """Tidsfarten är spärrad till 1×, eftersom riktiga sensorer eller lampor används."""
    daylight: float | None
    persons: Mapping[str, Target]


class Simulation:
    """Simuleringsläget: slår på och av låtsaskällan och styr dagsljus och tidsfart.

    När läget är avslaget tystnar låtsaskällan, precis som en radar som stängs
    av, och tiden går i vanlig takt. Kommandon till personerna och dagsljuset
    ignoreras då.

    Tidsfarten gäller hela kärnan, eftersom kärnan har en enda klocka. Det går
    bra så länge allt är simulerat; med riktiga lampor skulle de släckas 60
    gånger för tidigt. Så länge `real_hardware` säger att hemmet har riktiga
    sensorer eller lampor är tidsfarten därför spärrad till 1×.

    `floor_ids` är hemmets våningar. Personer på andra våningar avvisas. Utan
    `daylight` lämnas ljusnivån i rummen okänd tills någon ställer den.

    Med `lamps` lyser de virtuella lamporna upp de simulerade sensorerna när
    de mäter (`measure_light`).
    """

    def __init__(
        self,
        source: SimulatedSource,
        sink: ObservationSink,
        clock: SpeedControl,
        *,
        floor_ids: Iterable[str],
        enabled: bool = True,
        daylight: float | None = DEFAULT_DAYLIGHT_LUX,
        real_hardware: Callable[[Home], bool] = lambda _: False,
        lamps: SimulatedLampOutput | None = None,
    ) -> None:
        self._source = source
        if lamps is not None:
            source.use_lamp_light(lambda: lamps.light_by_room(source.home))
        self._real_hardware = real_hardware
        self._sink = sink
        self._clock = clock
        self._floor_ids = frozenset(floor_ids)
        self._enabled = False
        self._listeners: list[Listener] = []
        if daylight is not None:
            source.set_daylight(daylight)  # minns värdet tills källan startar
        self.set_enabled(enabled)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def view(self) -> SimulationView:
        return SimulationView(
            enabled=self._enabled,
            speed=self._clock.speed,
            speed_locked=self._speed_locked(),
            daylight=self._source.daylight,
            persons=self._source.persons,
        )

    def set_enabled(self, enabled: bool) -> None:
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if enabled:
            self._source.start(self._sink)
        else:
            self._source.stop()
            self._clock.set_speed(1)
        self._notify()

    def use_home(self, home: Home) -> None:
        """Byter till ett ändrat hem, så att nya rum, noder och våningar också simuleras.

        Har det första riktiga rumskortet eller den första riktiga lampan just
        placerats stängs simuleringsläget av, så att låtsaspersonerna inte styr
        riktiga lampor och de riktiga spåren hamnar i historiken. Slår någon på
        det igen går tiden ändå i vanlig takt.
        """
        was_real = self._speed_locked()
        self._floor_ids = frozenset(floor.id for floor in home.floors)
        self._source.use_home(home)
        if not self._speed_locked():
            return
        if not was_real and self._enabled:
            logger.info("Riktiga sensorer eller lampor är placerade; simuleringsläget stängs av")
            self.set_enabled(False)
        elif self._clock.speed != 1:
            self._clock.set_speed(1)
            self._notify()

    def add_person(self, floor_id: str, position: Point) -> str | None:
        """Lägger till en person och returnerar dess id, eller None om det inte gick."""
        if not self._enabled or not self._known_floor(floor_id):
            return None
        try:
            person_id = self._source.add_person(floor_id, position)
        except TooManyPersons:
            logger.info("Simuleringen har redan %d personer", MAX_PERSONS)
            return None
        self._notify()
        return person_id

    def move_person(self, person_id: str, floor_id: str, position: Point) -> None:
        if self._enabled and self._known_floor(floor_id):
            self._source.move_person(person_id, floor_id, position)
            self._notify()

    def remove_person(self, person_id: str) -> None:
        if self._enabled:
            self._source.remove_person(person_id)
            self._notify()

    def set_daylight(self, lux: float) -> None:
        if self._enabled:
            self._source.set_daylight(lux)
            self._notify()

    def measure_light(self) -> None:
        """De simulerade ljussensorerna mäter, med de virtuella lampornas ljus."""
        if self._enabled:
            self._source.measure_light()

    def set_speed(self, speed: Speed) -> None:
        if isinstance(speed, bool) or speed not in SPEEDS:
            raise ValueError(f"Tidsfarten ska vara en av {SPEEDS}, inte {speed}")
        if self._enabled and (speed == 1 or not self._speed_locked()):
            self._clock.set_speed(speed)
            self._notify()

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Anropar `listener` efter varje ändring. Returnerar en funktion som avslutar."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _speed_locked(self) -> bool:
        return self._real_hardware(self._source.home)

    def _known_floor(self, floor_id: str) -> bool:
        if floor_id in self._floor_ids:
            return True
        logger.warning("Person på okänd våning ignoreras")
        return False

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()


SENSOR_INTERVAL_SECONDS = 5.0
"""Så ofta de simulerade ljussensorerna mäter, i kärnans tid. Samma som rumskortens."""


async def run_light_sensors(simulation: Simulation, clock: PacedClock) -> None:
    """Låter de simulerade ljussensorerna mäta var femte sekund. Körs tills den avbryts.

    Med snabbare tidsfart mäter de lika mycket oftare, som allt annat i kärnan.
    Väntetiden räknas om när takten ändras.
    """
    changed = asyncio.Event()
    unsubscribe = clock.subscribe(changed.set)
    try:
        next_reading = clock.now() + SENSOR_INTERVAL_SECONDS
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(changed.wait(), clock.real_seconds_until(next_reading))
            changed.clear()
            if clock.now() < next_reading:
                continue
            next_reading = clock.now() + SENSOR_INTERVAL_SECONDS
            try:
                simulation.measure_light()
            except Exception:
                # Sensorerna måste fortsätta mäta, annars går ljuset inte att följa.
                logger.exception("Fel när de simulerade sensorerna mätte")
    finally:
        unsubscribe()
