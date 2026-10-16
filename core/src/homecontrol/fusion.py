"""Sammanvägningen: från radarmål och Wi-Fi-rörelse till spår och upptagna rum.

Varje radarmål räknas om från nodens koordinater till hemmets. Mål utanför
nodens rum (radarn ser genom väggar) och mål i ignorera-zoner kastas. Resten
knyts till anonyma spår: varje mål går till det närmaste spåret som personen
hinner ha gått till sedan sist, och spårets position jämnas ut. Ett mål som
inget spår når blir ett nytt spår, så ett spår hoppar aldrig orimligt långt.

Radarns egen numrering av målen används inte. Den byter när två personer
möts, och spåren ska följa personerna, inte numren.

När radarn tappar ett spår hålls det kvar en stund, beroende på var det tappades:

* i en entrézon: inte alls, personen har gått ut
* i en stillhetszon: så länge Wi-Fi känner rörelse i rummet, högst 30 minuter
* någon annanstans i rummet (och i en stillhetszon utan Wi-Fi-rörelse): 10 sekunder

Varje rum och zon är upptaget, tomt eller okänt. Ett rum är upptaget om ett
spår, sett eller kvarhållet, finns i det, eller om Wi-Fi-rörelsen väger
tillräckligt på egen hand (se `RoomSettings.wifi_weight`). Annars är det okänt
om någon av rummets noder hör till en källa som har tappat kontakten, och
annars tomt.

Modulen läser ingen klocka; tiden lämnas in i varje anrop. Den loggar aldrig
var någon är.
"""

import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from homecontrol.connectors import MotionObserved, RadarTarget, RadarTargetsObserved
from homecontrol.home import Capability, Home, Node, Point, Room, ZoneKind

logger = logging.getLogger(__name__)

HOLD_SECONDS = 10.0
"""Så länge ett tappat spår hålls kvar i rummet utanför zonerna."""
STILL_HOLD_MAX_SECONDS = 30 * 60.0
"""Så länge ett tappat spår i en stillhetszon som längst hålls kvar av Wi-Fi-rörelse."""

WALKING_SPEED = 3.0
"""Fortare än så här (meter per sekund) rör sig ingen inomhus."""
POSITION_NOISE = 0.8
"""Så långt (meter) radarn kan flytta en person som står still, mellan två mätningar."""
MAX_GAP_SECONDS = 0.5
"""Ett spår som inte har setts på så här länge förutsägs inte längre fram."""
SMOOTHING = 0.5
"""Hur mycket av skillnaden mellan förutsagd och uppmätt position som tas med."""
VELOCITY_SMOOTHING = 0.2
MAX_TARGETS = 16
"""Fler mål än så här i en observation är fel i källan, och resten kastas."""


_STILL = Point(0, 0)
"""Farten hos någon som står still."""


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

    @property
    def occupied(self) -> bool:
        return self.status is Occupancy.OCCUPIED

    @property
    def vacant_since(self) -> float | None:
        """När rummet blev tomt, eller None om det inte är tomt."""
        return self.since if self.status is Occupancy.VACANT else None


@dataclass(frozen=True)
class Track:
    """Ett anonymt spår av en person, som det visas utanför modulen."""

    floor_id: str
    position: Point
    held: bool
    """Radarn har tappat spåret, men det hålls kvar."""


@dataclass
class _Track:
    floor_id: str
    room: Room
    node_id: str
    """Noden som såg spåret senast. När den slutar se det är spåret tappat."""
    position: Point
    velocity: Point
    seen_at: float
    lost_at: float | None = None


@dataclass(frozen=True)
class _Placed:
    node: Node
    room: Room
    floor_id: str


class Fusion:
    def __init__(self, home: Home, now: float) -> None:
        self._tracks: list[_Track] = []
        self._source_of: dict[str, str] = {}
        """Källan som senast hördes från varje nod."""
        self._lost_nodes: set[str] = set()
        self._motion: dict[str, float] = {}
        """Wi-Fi-rörelsens poäng per nod, 0 när den inte känner någon rörelse."""
        self._motion_stopped: dict[str, float] = {}
        """När nodens Wi-Fi-rörelse senast upphörde."""
        self._warned: set[str] = set()
        """Noder som det redan har varnats för, så att loggen inte fylls tio gånger i sekunden."""
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
        self._heard(observation.source_id, observation.node_id)
        self._expire(now)  # så att ett spår som just släppts inte hittas igen
        targets = observation.targets
        if len(targets) > MAX_TARGETS:
            logger.warning(
                "Noden %s skickade %d mål, bara de första används", placed.node.id, len(targets)
            )
            targets = targets[:MAX_TARGETS]
        self._associate(placed, list(_detections(placed, targets)), now)
        self._refresh(now)

    def motion(self, observation: MotionObserved, now: float) -> None:
        placed = self._placed_with(observation.node_id, Capability.CSI)
        if placed is None:
            return
        score = observation.score
        if not math.isfinite(score) or not 0 <= score <= 1:
            logger.warning("Ogiltig rörelsepoäng från noden %s ignoreras", placed.node.id)
            return
        self._heard(observation.source_id, observation.node_id)
        node_id = observation.node_id
        if not observation.moving and self._motion.get(node_id, 0.0) > 0:
            self._motion_stopped[node_id] = now
        self._motion[node_id] = score if observation.moving else 0.0
        self._refresh(now)

    def source_lost(self, source_id: str, now: float) -> None:
        """Källans noder räknas som borta tills de hörs av igen. Deras spår glöms."""
        nodes = {node_id for node_id, source in self._source_of.items() if source == source_id}
        self._lost_nodes |= nodes
        for node_id in nodes:
            self._motion.pop(node_id, None)
        self._tracks = [track for track in self._tracks if track.node_id not in nodes]
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
        self._source_of = {node: source for node, source in self._source_of.items() if node in kept}
        self._lost_nodes &= kept
        self._motion = {node: score for node, score in self._motion.items() if node in kept}
        self._warned.clear()  # en nod som just placerats kan ha varnats för förut
        self._refresh(now)

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
            if node_id not in self._warned:
                self._warned.add(node_id)
                logger.warning(
                    "Mätningar från noden %s ignoreras: den är inte placerad med %s",
                    node_id,
                    capability,
                )
            return None
        return placed

    def _heard(self, source_id: str, node_id: str) -> None:
        self._source_of[node_id] = source_id
        self._lost_nodes.discard(node_id)

    def _associate(self, placed: _Placed, detections: Sequence[Point], now: float) -> None:
        """Knyter nodens mål till spår. Närmaste par först, och bara par som personen hinner."""
        candidates = [track for track in self._tracks if track.floor_id == placed.floor_id]
        pairs = sorted(
            (distance, i, j)
            for i, track in enumerate(candidates)
            for j, detection in enumerate(detections)
            if (distance := _distance(_predict(track, now), detection)) <= _reach(track, now)
        )
        matched_tracks: set[int] = set()
        matched_detections: set[int] = set()
        for _, i, j in pairs:
            if i not in matched_tracks and j not in matched_detections:
                matched_tracks.add(i)
                matched_detections.add(j)
                _follow(candidates[i], detections[j], placed, now)
        # Det noden såg förut men inte ser nu har den tappat.
        for i, track in enumerate(candidates):
            if i not in matched_tracks and track.node_id == placed.node.id:
                track.lost_at = track.lost_at if track.lost_at is not None else now
        for j, detection in enumerate(detections):
            if j not in matched_detections:
                track = _Track(placed.floor_id, placed.room, placed.node.id, detection, _STILL, now)
                self._tracks.append(track)

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
        kinds = {zone.kind for zone in track.room.zones if zone.contains(track.position)}
        if ZoneKind.ENTRANCE in kinds:
            return lost_at
        if ZoneKind.STILL in kinds and track.room.settings.wifi_weight > 0:
            longest = lost_at + STILL_HOLD_MAX_SECONDS
            if self._wifi_score(track.room) > 0:
                return longest
            # Rörelsen har upphört. Personen räknas som kvar till dess, inte bara 10 sekunder.
            stopped = max(
                (self._motion_stopped.get(node.id, -math.inf) for node in track.room.nodes),
                default=-math.inf,
            )
            return min(longest, max(lost_at + HOLD_SECONDS, stopped))
        return lost_at + HOLD_SECONDS

    def _wifi_score(self, room: Room) -> float:
        """Wi-Fi-rörelsen i rummet, viktad med rummets inställning."""
        scores = (self._motion.get(node.id, 0.0) for node in room.nodes)
        return room.settings.wifi_weight * max(scores, default=0.0)

    def _refresh(self, now: float) -> None:
        """Räknar fram läget i alla rum och zoner. Ett spår som tappas i en entrézon släpps nu."""
        self._expire(now)
        self._refresh_at(now)

    def _refresh_at(self, now: float) -> None:
        for room in self._home.rooms:
            tracks = [track for track in self._tracks if track.room.id == room.id]
            if tracks or self._wifi_score(room) >= 1:
                status = Occupancy.OCCUPIED
            elif any(node.id in self._lost_nodes for node in room.nodes):
                status = Occupancy.UNKNOWN
            else:
                status = Occupancy.VACANT
            self._rooms[room.id] = _keep_since(self._rooms.get(room.id), status, now)
            for zone in room.zones:
                if zone.kind is not ZoneKind.IGNORE and any(
                    zone.contains(track.position) for track in tracks
                ):
                    zone_status = Occupancy.OCCUPIED
                elif status is Occupancy.UNKNOWN:
                    zone_status = Occupancy.UNKNOWN
                else:
                    zone_status = Occupancy.VACANT
                self._zones[zone.id] = _keep_since(self._zones.get(zone.id), zone_status, now)
        # Rum och zoner som har ritats bort glöms.
        room_ids = {room.id for room in self._home.rooms}
        zone_ids = {zone.id for room in self._home.rooms for zone in room.zones}
        self._rooms = {key: value for key, value in self._rooms.items() if key in room_ids}
        self._zones = {key: value for key, value in self._zones.items() if key in zone_ids}


def _detections(placed: _Placed, targets: Iterable[RadarTarget]) -> Iterable[Point]:
    """Målen i hemmets koordinater, utom de som är utanför rummet eller i en ignorera-zon."""
    ignored = [zone for zone in placed.room.zones if zone.kind is ZoneKind.IGNORE]
    for target in targets:
        if not (math.isfinite(target.x) and math.isfinite(target.y)):
            continue
        point = placed.node.radar_to_home(target.x, target.y)
        if placed.room.contains(point) and not any(zone.contains(point) for zone in ignored):
            yield point


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
