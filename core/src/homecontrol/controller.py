"""Kärnans flöde: observationer in, sammanvägning, regel, lampkommandon ut.

`Controller` håller det senaste läget och är den enda platsen där det ändras.
Den gör inget av sig själv: någon lämnar observationer via `observe` och
anropar `tick` när klockan har nått `wake_at`. Det gör att samma kod kan
köras i realtid av servern och i snabbspolad tid av scenariotester.

Manuell styrning (ett klick på en lampa i livevyn) kommer in via `set_lamp`.
En lampa som ändras utanför kärnan, med fjärrkontroll eller app, räknas också
som manuell styrning (`LampStateReported.external`).

Hälsa: ett rum där en källa har tappat kontakten är okänt (se fusion.py), och
detsamma gäller ett rum där en lampa inte svarar. Automatiken lämnar då
rummet orört, och när lampan svarar igen tar den över.
Varje beslut som ändrar något sparas som en händelse med klockslag, för
listan "Varför hände det?" i livevyn. Händelserna finns bara i minnet.

Lampkalibreringen (se calibration.py) startas med `start_calibration` och
körs av kärnan med samma klocka som reglerna. Under tiden pausar automatiken
i rummet, och efteråt ställs lamporna tillbaka som de var.
"""

import logging
import math
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from homecontrol import rules
from homecontrol.calibration import CalibrationView, LampSetting, Phase, RoomCalibration
from homecontrol.clock import Clock
from homecontrol.connectors import (
    LampCommand,
    LampOutput,
    LampStateReported,
    LightLevelObserved,
    MotionObserved,
    Observation,
    RadarTargetsObserved,
    SourceLost,
)
from homecontrol.fusion import Fusion, Occupancy, Presence, Track
from homecontrol.home import Home, Room

logger = logging.getLogger(__name__)

Listener = Callable[[], None]
CalibratedListener = Callable[[Mapping[str, float]], None]

MAX_EVENTS = 30
"""Så många av de senaste händelserna sparas för "Varför hände det?"."""


@dataclass(frozen=True)
class RoomView:
    room_id: str
    status: Occupancy
    lux: float | None
    adjusted_lux: float | None
    """Ljusnivån med rummets egna lampors ljus avräknat; den som reglerna använder."""
    manual: bool
    """Någon har styrt en lampa i rummet för hand, och automatiken pausar."""
    explanation: str

    @property
    def occupied(self) -> bool:
        return self.status is Occupancy.OCCUPIED


@dataclass(frozen=True)
class ZoneView:
    zone_id: str
    status: Occupancy


@dataclass(frozen=True)
class LampView:
    lamp_id: str
    on: bool | None
    """None när lampans läge är okänt."""
    brightness: int
    explanation: str
    responding: bool = True
    """False när lamputgången säger att lampan inte svarar."""


@dataclass(frozen=True)
class Event:
    """Något som hände med lamporna, med förklaring: till exempel att de tändes eller dimrades."""

    number: int
    """Löpnummer från 1, så att den som sparar händelserna ser vilka som är nya."""
    at: float
    """Klockslaget som Unix-tid i sekunder."""
    room_id: str
    text: str


@dataclass(frozen=True)
class Snapshot:
    """Det som just nu går att visa om hemmet."""

    tracks: tuple[Track, ...]
    """Spåren av personerna, utan radarns numrering."""
    rooms: tuple[RoomView, ...]
    zones: tuple[ZoneView, ...]
    lamps: tuple[LampView, ...]
    events: tuple[Event, ...]
    """De senaste händelserna, äldst först."""
    calibration: CalibrationView | None = None
    """Lampkalibreringen som pågår, eller den senaste, om någon har körts sedan kärnan startade."""


class Controller:
    def __init__(
        self,
        home: Home,
        clock: Clock,
        outputs: Sequence[LampOutput],
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        """`wall_clock` ger klockslaget som Unix-tid, bara för att visa när något hände."""
        self._home = home
        self._clock = clock
        self._wall_clock = wall_clock
        self._outputs = tuple(outputs)
        # Ljusnivån per rum och källa. Det senast mätta värdet gäller, och en
        # källa som tappas tar sina värden med sig.
        self._lux: dict[str, dict[str, float]] = {}
        self._fusion = Fusion(home, clock.now())
        # Kärnan vet inte hur lamporna är förrän de rapporterar eller den själv styr dem.
        self._lamps: dict[str, rules.LampState] = {}
        # Lamporna som lamputgången senast sa inte svarar, och sedan när.
        self._not_responding: dict[str, float] = {}
        self._memory: dict[str, rules.Memory] = {}
        self._explanations: dict[str, str] = {}
        self._events: deque[Event] = deque(maxlen=MAX_EVENTS)
        self._event_count = 0
        self._wake_at: float | None = None
        self._listeners: list[Listener] = []
        self._room_ids = {room.id for room in home.rooms}
        self._on_calibrated: CalibratedListener | None = None
        self._calibration: RoomCalibration | None = None
        self._calibration_view: CalibrationView | None = None
        self._unsaved_results: dict[str, float] | None = None
        self._evaluate()

    @property
    def home(self) -> Home:
        return self._home

    @property
    def wake_at(self) -> float | None:
        """När `tick` behöver anropas nästa gång, eller None om inget väntar."""
        calibration = (
            None if self._calibration is None else self._calibration.wake_at(self._clock.now())
        )
        return min(
            (
                when
                for when in (self._wake_at, self._fusion.wake_at, calibration)
                if when is not None
            ),
            default=None,
        )

    def observe(self, observation: Observation) -> None:
        """Ingången för alla källkopplingar."""
        now = self._clock.now()
        match observation:
            case RadarTargetsObserved():
                self._fusion.radar(observation, now)
            case MotionObserved():
                self._fusion.motion(observation, now)
            case SourceLost(source_id=source_id):
                self._fusion.source_lost(observation, now)
                for by_source in self._lux.values():
                    by_source.pop(source_id, None)
            case LightLevelObserved(source_id=source_id, room_id=room_id, lux=lux):
                if room_id not in self._room_ids:
                    logger.warning("Ljusnivå för okänt rum %r ignoreras", room_id)
                    return
                if not math.isfinite(lux) or lux < 0:
                    logger.warning("Ogiltig ljusnivå %r ignoreras", lux)
                    return
                by_source = self._lux.setdefault(room_id, {})
                by_source.pop(source_id, None)  # sist i ordningen är senast mätt
                by_source[source_id] = lux
                if self._calibration is not None and self._calibration.room_id == room_id:
                    self._calibration.reading(source_id, lux, now)
            case LampStateReported(lamp_id=lamp_id, on=on, brightness=brightness):
                if not any(lamp.id == lamp_id for lamp in self._home.lamps):
                    logger.warning("Läge för okänd lampa %r ignoreras", lamp_id)
                    return
                if on is True and not 1 <= brightness <= rules.FULL_BRIGHTNESS:
                    logger.warning("Ogiltig ljusstyrka %r ignoreras", brightness)
                    return
                state = rules.LampState(on, brightness)
                self._lamp_responding(lamp_id, responding=on is not None, now=now)
                if observation.external and on is not None:
                    self._changed_outside(lamp_id, state)
                self._lamps[lamp_id] = state
                self._check_calibrated_lamp(lamp_id, observation)
        self._advance_calibration()
        self._evaluate()
        self._notify()

    def set_lamp(self, lamp_id: str, on: bool) -> None:
        """Manuell styrning: tänder eller släcker en lampa, till exempel efter ett klick i livevyn.

        Automatiken i lampans rum pausar sedan tills rummet har varit tomt i
        rummets släckfördröjning.
        """
        try:
            room = self._home.room_of(lamp_id)
        except KeyError:
            logger.warning("Manuell styrning av okänd lampa %r ignoreras", lamp_id)
            return
        lamp = next(lamp for lamp in room.lamps if lamp.id == lamp_id)
        if self._calibration is not None and self._calibration.room_id == room.id:
            # Den som styr en lampa för hand vill ha den så; den ställs inte tillbaka.
            self._end_calibration(
                self._calibration, f"{lamp.name} styrdes för hand", touched=lamp_id
            )
        command = rules.manual_command(room, lamp, on)
        self._memory[room.id] = rules.start_manual(self._memory.get(room.id, {}), self._clock.now())
        self._switch(command)
        self._log_event(room.id, command.reason)
        self._evaluate()
        self._notify()

    def on_calibrated(self, listener: CalibratedListener) -> None:
        """Lämnar lampornas uppmätta bidrag till `listener` när en lampkalibrering är klar.

        Den ska spara dem i hemmet (se `calibration.with_contributions`).
        Kärnan använder dem när hemmet har bytts med `replace_home`.
        """
        self._on_calibrated = listener

    def start_calibration(self, room_id: str) -> str | None:
        """Startar lampkalibreringen i ett rum. Returnerar None, eller varför det inte går."""
        room = next((room for room in self._home.rooms if room.id == room_id), None)
        if room is None:
            return "Rummet finns inte."
        if self._calibration is not None:
            return (
                f"Lampkalibreringen i {self._calibration.room.name} pågår redan. "
                "Vänta tills den är klar eller avbryt den."
            )
        if not room.lamps:
            return f"{room.name} har inga lampor att mäta."
        if room.settings.windowless:
            return (
                f"{room.name} saknar fönster och räknas alltid som mörkt, så lampornas "
                "ljus behöver inte mätas."
            )
        if self._room_lux(room.id) is None:
            return (
                f"Ingen ljussensor har mätt ljuset i {room.name}. Rummet behöver ett "
                "rumskort med ljussensor som svarar."
            )
        before = {}
        for lamp in room.lamps:
            state = self._lamps.get(lamp.id, rules.UNKNOWN_LAMP)
            if lamp.id in self._not_responding or state.on is None:
                return f"{lamp.name} svarar inte, så dess ljus går inte att mäta."
            before[lamp.id] = LampSetting(state.on, state.brightness)
        now = self._clock.now()
        # Ett rum med flera ljussensorer mäts med den som mätte senast.
        source_id = next(reversed(self._lux[room.id]))
        calibration = RoomCalibration(room, before, source_id, now)
        self._calibration = calibration
        self._calibration_view = calibration.view()
        self._memory[room.id] = rules.start_calibrating(self._memory.get(room.id, {}), now)
        logger.info("Lampkalibreringen i %s startar", room.id)
        self._log_event(room.id, f"Lampkalibreringen i {room.name} startade.")
        for command in calibration.commands():
            self._switch(command)
        self._evaluate()
        self._notify()
        return None

    def cancel_calibration(self) -> None:
        """Avbryter lampkalibreringen som pågår och ställer tillbaka lamporna."""
        if self._calibration is None:
            return
        self._end_calibration(self._calibration, "någon tryckte på Avbryt")
        self._evaluate()
        self._notify()

    def calibration_not_saved(self, reason: str) -> None:
        """Lampornas bidrag från den senaste kalibreringen gick inte att spara.

        `reason` säger varför, till exempel att databasen inte svarade, som en hel mening.
        """
        view = self._calibration_view
        if view is None or view.phase is not Phase.DONE:
            return
        room = next((room for room in self._home.rooms if room.id == view.room_id), None)
        name = room.name if room is not None else "rummet"
        self._calibration_view = replace(
            view,
            phase=Phase.FAILED,
            text=f"Lampkalibreringen i {name} blev klar men gick inte att spara. {reason}",
            results={},
        )
        self._notify()

    def _check_calibrated_lamp(self, lamp_id: str, report: LampStateReported) -> None:
        """Avbryter kalibreringen om en av rummets lampor slutar svara eller ändras utifrån."""
        calibration = self._calibration
        if calibration is None or lamp_id not in calibration.before:
            return
        lamp = next(lamp for lamp in calibration.room.lamps if lamp.id == lamp_id)
        if report.on is None:
            self._end_calibration(calibration, f"{lamp.name} slutade svara")
        elif report.external:
            # Den som har ändrat lampan vill ha den så; den ställs inte tillbaka.
            self._end_calibration(
                calibration, f"{lamp.name} ändrades med fjärrkontroll eller app", touched=lamp_id
            )

    def _advance_calibration(self) -> None:
        calibration = self._calibration
        if calibration is None:
            return
        lux = self._lux.get(calibration.room_id, {}).get(calibration.source_id)
        if calibration.update(lux, self._clock.now()):
            for command in calibration.commands():
                self._switch(command)
        if calibration.finished:
            self._end_calibration(calibration, failure=None)
        else:
            self._calibration_view = calibration.view()

    def _end_calibration(
        self, calibration: RoomCalibration, failure: str | None, *, touched: str | None = None
    ) -> None:
        """Avslutar kalibreringen och ställer tillbaka lamporna.

        `failure` säger varför den avbröts, om den gjorde det. Lampan `touched`
        har någon just styrt själv, så den ställs inte tillbaka.
        """
        self._calibration = None
        if failure is not None:
            calibration.fail(failure)
        view = calibration.view()
        self._calibration_view = view
        room_id = calibration.room_id
        known = {lamp.id for lamp in self._home.lamps} - set(self._not_responding) - {touched}
        for command in calibration.restore_commands():
            if command.lamp_id in known:
                self._switch(command)
        self._memory[room_id] = rules.stop_calibrating(self._memory.get(room_id, {}))
        logger.info("Lampkalibreringen i %s: %s", room_id, view.phase.value)
        self._log_event(room_id, view.text)
        if view.phase is Phase.DONE:
            self._unsaved_results = dict(view.results)

    def _lamp_responding(self, lamp_id: str, *, responding: bool, now: float) -> None:
        """Håller reda på om lampan svarar. Rummet är okänt så länge en av dess lampor inte gör det.

        Bara ändringen sparas som en händelse, eftersom utgången kan säga samma sak flera gånger.
        """
        if responding == (lamp_id not in self._not_responding):
            return
        room = self._home.room_of(lamp_id)
        lamp = next(lamp for lamp in room.lamps if lamp.id == lamp_id)
        if responding:
            del self._not_responding[lamp_id]
            text = f"{lamp.name} svarar igen."
            if not any(other.id in self._not_responding for other in room.lamps):
                text += f" Automatiken i {room.name} tar över igen."
        else:
            self._not_responding[lamp_id] = now
            text = (
                f"{lamp.name} svarar inte. Läget i {room.name} är okänt, och automatiken "
                "lämnar rummets lampor som de är tills lampan svarar igen."
            )
        logger.info("%s %s", lamp_id, "svarar igen" if responding else "svarar inte")
        self._log_event(room.id, text)

    def _changed_outside(self, lamp_id: str, state: rules.LampState) -> None:
        """En lampa ändrades med fjärrkontroll eller app: det räknas som manuell styrning."""
        room = self._home.room_of(lamp_id)
        lamp = next(lamp for lamp in room.lamps if lamp.id == lamp_id)
        # Säger när någon i hemmet rör en fjärrkontroll, så det loggas bara när man felsöker.
        logger.debug("%s ändrades utanför kärnan", lamp_id)
        self._memory[room.id] = rules.start_manual(self._memory.get(room.id, {}), self._clock.now())
        self._log_event(room.id, rules.external_change_text(room, lamp, state))

    def resume_automation(self, room_id: str) -> None:
        """Låter automatiken ta över i ett rum direkt, utan att vänta tills rummet varit tomt."""
        room = next((room for room in self._home.rooms if room.id == room_id), None)
        if room is None:
            logger.warning("Automatik för okänt rum %r ignoreras", room_id)
            return
        memory = self._memory.get(room_id, {})
        if not rules.is_manual(memory):
            return
        self._memory[room_id] = rules.stop_manual(memory)
        self._log_event(room_id, f"Automatiken i {room.name} slogs på igen för hand.")
        self._evaluate()
        self._notify()

    def replace_home(self, home: Home) -> None:
        """Byter till ett ändrat hem, till exempel när kartredigeraren har sparat.

        Det som fortfarande gäller behålls: spåren från noder som finns kvar,
        ljusnivån och det reglerna minns (som manuellt läge) i rum som finns
        kvar, hur länge rummen har varit tomma och lampornas läge.
        En tänd lampa som inte längre finns i hemmet släcks, så att den inte
        lyser kvar utan att något styr den. En lampkalibrering i ett rum som har
        ändrats avbryts.

        `home` ska vara ett nytt objekt; livevyn ser att hemmet har bytts genom
        att jämföra med det förra.
        """
        kept = {lamp.id for lamp in home.lamps}
        for lamp_id, state in self._lamps.items():
            if state.on is True and lamp_id not in kept:
                self._send(LampCommand(lamp_id, on=False, reason="Lampan togs bort ur hemmet."))
        self._home = home
        self._room_ids = {room.id for room in home.rooms}
        # En kalibrering vars rum eller lampor har ändrats mäter inte längre
        # rätt sak. Lamporna som finns kvar ställs tillbaka.
        calibration = self._calibration
        if calibration is not None:
            room = next((room for room in home.rooms if room.id == calibration.room_id), None)
            if (
                room is None
                or room.settings.windowless
                or [lamp.id for lamp in room.lamps] != list(calibration.before)
            ):
                self._end_calibration(calibration, "rummet ändrades i kartredigeraren")
        self._lux = {
            room_id: lux for room_id, lux in self._lux.items() if room_id in self._room_ids
        }
        self._memory = {
            room_id: memory for room_id, memory in self._memory.items() if room_id in self._room_ids
        }
        self._lamps = {lamp_id: state for lamp_id, state in self._lamps.items() if lamp_id in kept}
        self._not_responding = {
            lamp_id: since for lamp_id, since in self._not_responding.items() if lamp_id in kept
        }
        self._fusion.replace_home(home, self._clock.now())
        self._evaluate()
        self._notify()

    def tick(self) -> None:
        """Låter sammanvägningen och regeln titta igen om klockan har nått `wake_at`."""
        wake_at = self.wake_at
        if wake_at is not None and self._clock.now() >= wake_at:
            self._fusion.update(self._clock.now())
            self._advance_calibration()
            self._evaluate()
            self._notify()

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Anropar `listener` efter varje ändring. Returnerar en funktion som avslutar."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def snapshot(self) -> Snapshot:
        rooms = self._presence()
        zones = self._fusion.zones()
        return Snapshot(
            tracks=self._fusion.tracks(),
            rooms=tuple(self._room_view(room, rooms[room.id].status) for room in self._home.rooms),
            zones=tuple(
                ZoneView(zone.id, zones[zone.id].status)
                for room in self._home.rooms
                for zone in room.zones
            ),
            lamps=tuple(
                self._lamp_view(room, lamp.id) for room in self._home.rooms for lamp in room.lamps
            ),
            events=tuple(self._events),
            calibration=self._calibration_view,
        )

    def _room_view(self, room: Room, status: Occupancy) -> RoomView:
        lux = self._room_lux(room.id)
        return RoomView(
            room.id,
            status,
            lux=lux,
            adjusted_lux=rules.adjusted_lux(room, lux, self._lamps),
            manual=rules.is_manual(self._memory.get(room.id, {})),
            explanation=self._explanations[room.id],
        )

    def _lamp_view(self, room: Room, lamp_id: str) -> LampView:
        state = self._lamps.get(lamp_id, rules.UNKNOWN_LAMP)
        responding = lamp_id not in self._not_responding
        # Kärnan minns det den senast bad lampan om, men en lampa som inte
        # svarar kan vara hur som helst.
        on = state.on if responding else None
        # Lamporna i ett rum styrs som en grupp och har rummets förklaring.
        return LampView(
            lamp_id, on, state.brightness, self._explanations[room.id], responding=responding
        )

    def _room_lux(self, room_id: str) -> float | None:
        by_source = self._lux.get(room_id)
        # Källan som mätte senast ligger sist, eftersom den läggs in på nytt varje gång.
        return next(reversed(by_source.values()), None) if by_source else None

    def _presence(self) -> dict[str, Presence]:
        """Läget i varje rum: sammanvägningens, men okänt där en lampa inte svarar."""
        presence = dict(self._fusion.rooms())
        for room in self._home.rooms:
            silent = [
                since
                for lamp in room.lamps
                if (since := self._not_responding.get(lamp.id)) is not None
            ]
            if not silent:
                continue
            fused = presence[room.id]
            if fused.status is Occupancy.UNKNOWN:
                silent.append(fused.since)
            presence[room.id] = Presence(Occupancy.UNKNOWN, min(silent))
        return presence

    def _evaluate(self) -> None:
        presence = self._presence()
        room_states = {
            room.id: rules.RoomState(
                presence[room.id],
                self._room_lux(room.id),
                self._memory.get(room.id, {}),
                silent_lamps=tuple(
                    lamp.name for lamp in room.lamps if lamp.id in self._not_responding
                ),
            )
            for room in self._home.rooms
        }
        result = rules.evaluate(self._home, room_states, self._lamps, self._clock.now())
        for command in result.commands:
            self._switch(command)
        for event in result.events:
            self._log_event(event.room_id, event.text)
        self._memory = dict(result.memory)
        self._explanations = dict(result.explanations)
        self._wake_at = result.wake_at

    def _switch(self, command: LampCommand) -> None:
        # Tänd, släck och dimra säger ungefär när någon kom och gick, och
        # förklaringen säger var någon är, så de loggas bara när man felsöker.
        if not command.on:
            logger.debug("Släcker %s", command.lamp_id)
        elif command.brightness == rules.FULL_BRIGHTNESS:
            logger.debug("Tänder %s", command.lamp_id)
        else:
            logger.debug("Dimrar %s till %d %%", command.lamp_id, command.brightness)
        logger.debug("%s: %s", command.lamp_id, command.reason)
        self._lamps[command.lamp_id] = rules.LampState(command.on, command.brightness)
        self._send(command)

    def _log_event(self, room_id: str, text: str) -> None:
        self._event_count += 1
        self._events.append(Event(self._event_count, self._wall_clock(), room_id, text))

    def _send(self, command: LampCommand) -> None:
        for output in self._outputs:
            # En trasig utgång får inte hindra de andra eller stoppa kärnan.
            try:
                output.send(command)
            except Exception:
                logger.exception("Lamputgången kunde inte ta emot %s", command)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()
        # Bidragen lämnas sist, när kärnan är klar med sitt eget, eftersom den
        # som sparar dem byter hemmet med `replace_home`.
        results, self._unsaved_results = self._unsaved_results, None
        if results is not None and self._on_calibrated is not None:
            self._on_calibrated(results)
