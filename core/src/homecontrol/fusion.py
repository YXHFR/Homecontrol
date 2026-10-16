"""Sammanvägningen: från radarmål och Wi-Fi-rörelse till spår och upptagna rum.

Varje radarmål räknas om från nodens koordinater till hemmets. Mål utanför
nodens rum (radarn ser genom väggar) och mål i ignorera-zoner kastas. Resten
knyts till anonyma spår: varje mål går till det närmaste spåret i nodens rum
som personen hinner ha gått till sedan sist, och spårets position jämnas ut.
Ett spår i ett annat rum tas bara över genom en dörröppning, och bara när
radarn där har tappat det eller när målet är samma punkt som spåret (två
radarer ser samma person i dörren). Ett mål som inget spår når blir ett nytt
spår, så ett spår hoppar aldrig orimligt långt.

Radarns egen numrering av målen används inte. Den byter när två personer
möts, och spåren ska följa personerna, inte numren.

När radarn tappar ett spår hålls det kvar en stund, beroende på var det tappades:

* i en entrézon: inte alls, personen har gått ut
* i en stillhetszon: så länge Wi-Fi känner rörelse i rummet, högst 30 minuter
* någon annanstans i rummet (och i en stillhetszon utan Wi-Fi-rörelse): 10 sekunder

Ett spår som hålls kvar i en stillhetszon tas bara över av ett mål i samma
zon eller alldeles intill, så att den som går förbi soffan inte tar med sig
spåret av den som sitter i den.

Wi-Fi-rörelsen vägs med rummets inställning `RoomSettings.wifi_weight`:
vikten gånger rörelsepoängen måste nå `WIFI_HOLD` för att hålla kvar ett
spår i en stillhetszon, och 1 för att rummet ska räknas som upptaget av
Wi-Fi-rörelse ensam.

Varje rum och zon är upptaget, tomt eller okänt. Ett rum är upptaget om ett
spår, sett eller kvarhållet, finns i det, eller om Wi-Fi-rörelsen väger
tillräckligt på egen hand. Annars är det okänt om radarn på någon av
rummets noder har tappat kontakten, eller deras Wi-Fi-mätning när rummet
använder Wi-Fi (vikten är över 0), och annars tomt. Bortfallet gäller varje
del av en nod för sig, så ett kort vars radar tystnar blir okänt även om dess
Wi-Fi-mätning fortsätter att höras.

Modulen läser ingen klocka; tiden lämnas in i varje anrop. Den loggar aldrig
var någon är.
"""

import logging
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from homecontrol.connectors import MotionObserved, RadarTarget, RadarTargetsObserved, SourceLost
from homecontrol.home import Capability, Home, Node, Point, Room, Zone, ZoneKind

logger = logging.getLogger(__name__)

HOLD_SECONDS = 10.0
"""Så länge ett tappat spår hålls kvar i rummet utanför zonerna."""
STILL_HOLD_MAX_SECONDS = 30 * 60.0
"""Så länge ett tappat spår i en stillhetszon som längst hålls kvar av Wi-Fi-rörelse."""
WIFI_HOLD = 0.25
"""Så mycket Wi-Fi-rörelsen måste väga (vikt gånger poäng) för att hålla kvar ett spår."""

WALKING_SPEED = 3.0
"""Fortare än så här (meter per sekund) rör sig ingen inomhus."""
POSITION_NOISE = 0.8
"""Så långt (meter) radarn kan flytta en person som står still, mellan två mätningar."""
SAME_PERSON = 0.4
"""Två radarer som ser ett mål så här nära varandra (meter) i en dörröppning ser samma person."""
MAX_GAP_SECONDS = 0.5
"""Ett spår som inte har setts på så här länge förutsägs inte längre fram."""
SMOOTHING = 0.5
"""Hur mycket av skillnaden mellan förutsagd och uppmätt position som tas med."""
VELOCITY_SMOOTHING = 0.2
"""Hur mycket av den uppmätta fartändringen som tas med."""

MAX_TARGETS = 16
"""Fler mål än så här i en observation är fel i källan, och resten kastas."""
MAX_TARGET_DISTANCE = 20.0
"""Mål längre bort än så här (meter, åt något håll) är fel i källan och kastas."""
MAX_WARNINGS = 100
"""Så många olika problem med källorna varnas det för. Sedan blir det tyst."""

_STILL = Point(0, 0)
"""Farten hos någon som står still."""

_Part = tuple[str, Capability]
"""En del av en nod, till exempel radarn på ett visst kort."""
_PRESENCE_PARTS = (Capability.RADAR, Capability.CSI)
"""Delarna som närvaron bygger på. Ljusnivån sköts av regeln, inte här."""


class Occupancy(StrEnum):
    OCCUPIED = "upptaget"
    VACANT = "tomt"
    UNKNOWN = "okänt"
    """En källa i rummet har tappat kontakten, så ingen vet om det är tomt."""


@dataclass(frozen=True)
class Presence:
    """Läget i ett rum eller en zon och sedan när det har gällt."""

    status: Occupancy
    since: float


@dataclass(frozen=True)
class Track:
    """Ett anonymt spår av en person, som det visas utanför modulen."""

    floor_id: str
    position: Point
    held: bool
    """Radarn har tappat spåret, men det hålls kvar."""


@dataclass(eq=False)
class _Track:
    floor_id: str
    room: Room
    node_id: str
    """Noden som såg spåret senast. När den slutar se det är spåret tappat."""
    position: Point
    velocity: Point
    seen_at: float
    lost_at: float | None = None
    wifi_held_at: float = -math.inf
    """Senast Wi-Fi-rörelsen höll kvar spåret efter att radarn tappade det."""


@dataclass(frozen=True)
class _Placed:
    node: Node
    room: Room
    floor_id: str


class Fusion:
    """Håller spåren och läget i varje rum och zon, och ändrar dem när mätningar kommer.

    Varje anrop får tiden `now`, och tiden får aldrig gå bakåt mellan två anrop.
    """

    def __init__(self, home: Home, now: float) -> None:
        self._tracks: list[_Track] = []
        self._source_of: dict[_Part, str] = {}
        """Källan som senast hördes från varje del av en nod. Radarn och Wi-Fi kan ha olika."""
        self._lost: set[_Part] = set()
        """Delar som har tappat kontakten och inte hörts av sedan dess."""
        self._motion: dict[str, float] = {}
        """Wi-Fi-rörelsens poäng per nod, 0 när den inte känner någon rörelse."""
        self._warned: set[tuple[str, str]] = set()
        """Problem som det redan har varnats för, så att loggen inte fylls tio gånger i sekunden."""
        self._rooms: dict[str, Presence] = {}
        self._zones: dict[str, Presence] = {}
        self._use(home)
        self._refresh(now)

    def rooms(self) -> Mapping[str, Presence]:
        return dict(self._rooms)

    def zones(self) -> Mapping[str, Presence]:
        return dict(self._zones)

    def tracks(self) -> tuple[Track, ...]:
        return tuple(
            Track(track.floor_id, track.position, held=track.lost_at is not None)
            for track in self._tracks
        )

    @property
    def wake_at(self) -> float | None:
        """När nästa kvarhållna spår släpps, om inget annat händer innan dess."""
        return min((self._expires_at(track) for track in self._held()), default=None)

    def radar(self, observation: RadarTargetsObserved, now: float) -> None:
        placed = self._placed_with(observation.node_id, Capability.RADAR)
        if placed is None:
            return
        # Först släpps det vars tid redan har gått ut, så att det inte hittas igen.
        self._expire(now)
        self._heard(observation.source_id, (observation.node_id, Capability.RADAR))
        targets = observation.targets
        if len(targets) > MAX_TARGETS:
            self._warn_once(placed.node.id, "för många mål", "Noden %r skickar för många mål")
            targets = targets[:MAX_TARGETS]
        self._associate(placed, list(self._detections(placed, targets)), now)
        self._refresh(now)

    def motion(self, observation: MotionObserved, now: float) -> None:
        placed = self._placed_with(observation.node_id, Capability.CSI)
        if placed is None:
            return
        score = observation.score
        if not math.isfinite(score) or not 0 <= score <= 1:
            self._warn_once(
                placed.node.id, "rörelsepoäng", "Ogiltig rörelsepoäng från noden %r ignoreras"
            )
            return
        # Rörelsen gäller från nu. Det som skulle ha släppts innan dess släpps först.
        self._before_change(now)
        self._heard(observation.source_id, (observation.node_id, Capability.CSI))
        self._motion[observation.node_id] = score if observation.moving else 0.0
        self._refresh(now)

    def source_lost(self, observation: SourceLost, now: float) -> None:
        """Delarna som tystnat räknas som borta tills de hörs av igen.

        Spåren från en radar som tystnat glöms, och Wi-Fi-rörelsen från en
        Wi-Fi-mätning som tystnat.
        """
        self._before_change(now)
        if observation.node_id is None:
            parts = {
                part for part, source in self._source_of.items() if source == observation.source_id
            }
        else:
            parts = self._parts_of(observation.node_id, observation.capability)
            for part in parts:
                self._source_of[part] = observation.source_id
        self._lost |= parts
        radars = {node_id for node_id, capability in parts if capability is Capability.RADAR}
        for node_id, capability in parts:
            if capability is Capability.CSI:
                self._motion.pop(node_id, None)
        self._tracks = [track for track in self._tracks if track.node_id not in radars]
        self._refresh(now)

    def update(self, now: float) -> None:
        """Släpper kvarhållna spår vars tid har gått ut."""
        self._refresh(now)

    def replace_home(self, home: Home, now: float) -> None:
        """Byter till ett ändrat hem. Det som noder som finns kvar har mätt behålls.

        Spåren från en nod som har flyttats eller vridits glöms, eftersom de
        räknades om med den gamla placeringen. Det gör också spår som inte
        längre är i sitt rum. Noden ser dem snart igen om de finns kvar.
        """
        self._before_change(now)
        before = self._placed
        self._use(home)
        kept = self._placed.keys()
        tracks = []
        for track in self._tracks:
            placed = self._placed.get(track.node_id)
            if (
                placed is not None
                and placed.node == before[track.node_id].node
                and placed.room.contains(track.position)
            ):
                track.room = placed.room  # rummet kan till exempel ha fått nya zoner
                tracks.append(track)
        self._tracks = tracks
        self._source_of = {
            part: source for part, source in self._source_of.items() if part[0] in kept
        }
        self._lost = {part for part in self._lost if part[0] in kept}
        self._motion = {node: score for node, score in self._motion.items() if node in kept}
        self._warned.clear()  # en nod som just placerats kan ha varnats för förut
        self._refresh(now)

    def _before_change(self, now: float) -> None:
        """Gör klart det som gällde fram till nu, innan Wi-Fi-rörelsen eller hemmet ändras.

        Det som skulle ha släppts innan nu släpps. Spår som Wi-Fi-rörelsen håller
        kvar just nu märks med tiden, så att de hålls kvar åtminstone hit även
        om rörelsen upphör eller blir för svag.
        """
        self._expire(now)
        for track in self._held():
            if self._held_by_wifi(track):
                track.wifi_held_at = now

    def _use(self, home: Home) -> None:
        self._home = home
        self._placed = {
            node.id: _Placed(node, room, floor.id)
            for floor in home.floors
            for room in floor.rooms
            for node in room.nodes
        }

    def _placed_with(self, node_id: str, capability: Capability) -> _Placed | None:
        placed = self._placed.get(node_id)
        if placed is None or capability not in placed.node.capabilities:
            self._warn_once(
                node_id,
                f"inte placerad med {capability}",
                f"Mätningar från noden %r ignoreras: den är inte placerad med {_NAMES[capability]}",
            )
            return None
        return placed

    def _warn_once(self, node_id: str, problem: str, message: str) -> None:
        """Varnar en gång per nod och problem. Nod-id:t kommer från källan och kortas av."""
        key = (node_id[:64], problem)
        if key in self._warned or len(self._warned) > MAX_WARNINGS:
            return
        self._warned.add(key)
        if len(self._warned) > MAX_WARNINGS:
            logger.warning("Fler problem med källorna loggas inte förrän hemmet ändras")
            return
        logger.warning(message, key[0])

    def _parts_of(self, node_id: str, capability: Capability | None) -> set[_Part]:
        """Nodens delar som närvaron bygger på, eller bara `capability` om den är given."""
        placed = self._placed.get(node_id)
        if placed is None:
            return set()
        return {
            (node_id, part)
            for part in _PRESENCE_PARTS
            if part in placed.node.capabilities and capability in (None, part)
        }

    def _heard(self, source_id: str, part: _Part) -> None:
        self._source_of[part] = source_id
        self._lost.discard(part)

    def _lost_in(self, room: Room) -> bool:
        """Om radarn i rummet har tappat kontakten, eller Wi-Fi-mätningen som rummet använder."""
        uses_wifi = room.settings.wifi_weight > 0
        return any(
            (node.id, Capability.RADAR) in self._lost
            or (uses_wifi and (node.id, Capability.CSI) in self._lost)
            for node in room.nodes
        )

    def _detections(self, placed: _Placed, targets: Iterable[RadarTarget]) -> Iterable[Point]:
        """Målen i hemmets koordinater, utom de som är utanför rummet eller i en ignorera-zon."""
        ignored = [zone for zone in placed.room.zones if zone.kind is ZoneKind.IGNORE]
        for target in targets:
            if not all(
                math.isfinite(value) and abs(value) <= MAX_TARGET_DISTANCE
                for value in (target.x, target.y)
            ):
                self._warn_once(placed.node.id, "ogiltigt mål", "Ogiltigt mål från noden %r")
                continue
            point = placed.node.radar_to_home(target.x, target.y)
            if placed.room.contains(point) and not any(zone.contains(point) for zone in ignored):
                yield point

    def _associate(self, placed: _Placed, detections: Sequence[Point], now: float) -> None:
        """Knyter nodens mål till spår, närmaste par först och bara par som personen hinner.

        Först prövas spåren i nodens eget rum och spår som har tappats var som
        helst. Mål som blir över kan sedan ta över ett spår som syns i ett
        annat rum, om det är samma person i en dörröppning.
        """
        on_floor = [track for track in self._tracks if track.floor_id == placed.floor_id]
        own = [t for t in on_floor if t.room.id == placed.room.id or t.lost_at is not None]
        others = [t for t in on_floor if t not in own]
        matched = self._match(
            own,
            dict(enumerate(detections)),
            lambda track, detection: (
                _distance(_predict(track, now), detection) <= _reach(track, now)
                and _may_take_over(track, detection)
                and _passable(track, placed.room, detection)
            ),
            now,
        )
        left = {j: d for j, d in enumerate(detections) if j not in matched.values()}
        matched |= self._match(
            others,
            left,
            lambda track, detection: (
                _distance(track.position, detection) <= SAME_PERSON
                and _passable(track, placed.room, detection)
            ),
            now,
        )
        for track, j in matched.items():
            _follow(track, detections[j], placed, now)
        # Det noden såg förut men inte ser nu har den tappat.
        for track in on_floor:
            if track not in matched and track.node_id == placed.node.id and track.lost_at is None:
                track.lost_at = now
        for j, detection in enumerate(detections):
            if j not in matched.values():
                track = _Track(placed.floor_id, placed.room, placed.node.id, detection, _STILL, now)
                self._tracks.append(track)

    @staticmethod
    def _match(
        tracks: Sequence[_Track],
        detections: Mapping[int, Point],
        allowed: Callable[[_Track, Point], bool],
        now: float,
    ) -> dict[_Track, int]:
        """Vilket mål (med sitt nummer i observationen) varje spår får, närmaste par först."""
        pairs = sorted(
            (_distance(_predict(track, now), detection), i, j)
            for i, track in enumerate(tracks)
            for j, detection in detections.items()
            if allowed(track, detection)
        )
        matched: dict[_Track, int] = {}
        for _, i, j in pairs:
            if tracks[i] not in matched and j not in matched.values():
                matched[tracks[i]] = j
        return matched

    def _held(self) -> Iterable[_Track]:
        return (track for track in self._tracks if track.lost_at is not None)

    def _expire(self, now: float) -> None:
        """Släpper kvarhållna spår vars tid har gått ut, vart och ett vid sin egen tidpunkt.

        Då blir ett rum tomt från när spåret släpptes, även om ingen frågade förrän senare.
        """
        while (when := self.wake_at) is not None and when <= now:
            self._tracks = [
                track
                for track in self._tracks
                if track.lost_at is None or self._expires_at(track) > when
            ]
            self._refresh_at(when)

    def _expires_at(self, track: _Track) -> float:
        lost_at = track.lost_at
        if lost_at is None:
            return math.inf  # radarn ser spåret
        kinds = {zone.kind for zone in _zones_at(track)}
        if ZoneKind.ENTRANCE in kinds:
            return lost_at
        if ZoneKind.STILL in kinds:
            longest = lost_at + STILL_HOLD_MAX_SECONDS
            if self._held_by_wifi(track):
                return longest
            # Wi-Fi-rörelsen kan ha hållit kvar personen en stund efter att radarn
            # tappade den. Då räknas personen som kvar till dess, om det var mer än
            # de vanliga 10 sekunderna.
            return min(longest, max(lost_at + HOLD_SECONDS, track.wifi_held_at))
        return lost_at + HOLD_SECONDS

    def _held_by_wifi(self, track: _Track) -> bool:
        """Om Wi-Fi-rörelsen väger nog för att hålla kvar ett tappat spår i en stillhetszon."""
        in_still_zone = any(zone.kind is ZoneKind.STILL for zone in _zones_at(track))
        return in_still_zone and self._wifi_weighted(track.room) >= WIFI_HOLD

    def _wifi_weighted(self, room: Room) -> float:
        """Wi-Fi-rörelsen i rummet gånger rummets vikt."""
        scores = (self._motion.get(node.id, 0.0) for node in room.nodes)
        return room.settings.wifi_weight * max(scores, default=0.0)

    def _refresh(self, now: float) -> None:
        """Räknar fram läget i alla rum och zoner. Ett spår som tappas i en entrézon släpps nu."""
        self._expire(now)
        self._refresh_at(now)

    def _refresh_at(self, now: float) -> None:
        for room in self._home.rooms:
            tracks = [track for track in self._tracks if track.room.id == room.id]
            if tracks or self._wifi_weighted(room) >= 1:
                status = Occupancy.OCCUPIED
            elif self._lost_in(room):
                status = Occupancy.UNKNOWN
            else:
                status = Occupancy.VACANT
            self._rooms[room.id] = _keep_since(self._rooms.get(room.id), status, now)
            for zone in room.zones:
                zone_status = _zone_status(zone, tracks, status)
                self._zones[zone.id] = _keep_since(self._zones.get(zone.id), zone_status, now)
        # Rum och zoner som har ritats bort glöms.
        room_ids = {room.id for room in self._home.rooms}
        zone_ids = {zone.id for room in self._home.rooms for zone in room.zones}
        self._rooms = {key: value for key, value in self._rooms.items() if key in room_ids}
        self._zones = {key: value for key, value in self._zones.items() if key in zone_ids}


_NAMES = {Capability.RADAR: "radar", Capability.CSI: "Wi-Fi-mätning", Capability.LUX: "ljussensor"}


def _zone_status(zone: Zone, tracks: Sequence[_Track], room_status: Occupancy) -> Occupancy:
    """En zon är upptagen om ett spår finns i den, utom en ignorera-zon som aldrig är det."""
    if zone.kind is not ZoneKind.IGNORE and any(zone.contains(t.position) for t in tracks):
        return Occupancy.OCCUPIED
    return Occupancy.UNKNOWN if room_status is Occupancy.UNKNOWN else Occupancy.VACANT


def _zones_at(track: _Track) -> list[Zone]:
    return [zone for zone in track.room.zones if zone.contains(track.position)]


def _passable(track: _Track, room: Room, detection: Point) -> bool:
    """Om personen kan ha gått från spåret till målet utan att gå genom en vägg."""
    return track.room.id == room.id or not (
        track.room.blocks(track.position, detection) or room.blocks(track.position, detection)
    )


def _may_take_over(track: _Track, detection: Point) -> bool:
    """Ett kvarhållet spår i en stillhetszon tas bara över av ett mål i zonen eller intill.

    Annars kunde den som går förbi soffan ta med sig spåret av den som sitter i den.
    """
    if track.lost_at is None:
        return True
    still = [zone for zone in _zones_at(track) if zone.kind is ZoneKind.STILL]
    return (
        not still
        or any(zone.contains(detection) for zone in still)
        or _distance(track.position, detection) <= POSITION_NOISE
    )


def _predict(track: _Track, now: float) -> Point:
    """Var spåret borde vara nu, om personen har gått vidare åt samma håll."""
    if track.lost_at is not None:
        return track.position
    elapsed = min(now - track.seen_at, MAX_GAP_SECONDS)
    return Point(
        track.position.x + track.velocity.x * elapsed,
        track.position.y + track.velocity.y * elapsed,
    )


def _reach(track: _Track, now: float) -> float:
    """Hur långt från förutsägelsen personen kan ha kommit sedan spåret sågs."""
    return POSITION_NOISE + WALKING_SPEED * min(now - track.seen_at, MAX_GAP_SECONDS)


def _follow(track: _Track, detection: Point, placed: _Placed, now: float) -> None:
    """Flyttar spåret mot målet, med utjämning av både position och fart."""
    if track.lost_at is not None:
        # Radarn hittade personen igen. Var den har varit under tiden vet ingen.
        track.position, track.velocity = detection, _STILL
        track.wifi_held_at = -math.inf
    else:
        predicted = _predict(track, now)
        dx, dy = detection.x - predicted.x, detection.y - predicted.y
        track.position = Point(predicted.x + SMOOTHING * dx, predicted.y + SMOOTHING * dy)
        elapsed = now - track.seen_at
        if elapsed > 0:
            vx = track.velocity.x + VELOCITY_SMOOTHING * dx / elapsed
            vy = track.velocity.y + VELOCITY_SMOOTHING * dy / elapsed
            speed = math.hypot(vx, vy)
            if speed > WALKING_SPEED:
                vx, vy = vx * WALKING_SPEED / speed, vy * WALKING_SPEED / speed
            track.velocity = Point(vx, vy)
    if not placed.room.contains(track.position):
        # Utjämningen får inte lägga spåret utanför rummet där radarn såg personen.
        track.position = detection
    track.seen_at = now
    track.lost_at = None
    track.node_id = placed.node.id
    track.room = placed.room


def _distance(a: Point, b: Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _keep_since(before: Presence | None, status: Occupancy, now: float) -> Presence:
    if before is not None and before.status is status:
        return before
    return Presence(status, now)
