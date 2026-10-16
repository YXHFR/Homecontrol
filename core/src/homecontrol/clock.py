"""Klockor. Kärnan frågar alltid en klocka om tiden i stället för att läsa systemtiden.

Tiden är sekunder som ett flyttal. Bara skillnader mellan två tidpunkter har
betydelse, så en klocka får börja var som helst.
"""

import time
from collections.abc import Callable
from typing import Protocol

Listener = Callable[[], None]


class Clock(Protocol):
    def now(self) -> float:
        """Nuvarande tid i sekunder."""
        ...


class PacedClock(Clock, Protocol):
    """En klocka som går av sig själv, i en viss takt jämfört med riktig tid."""

    def real_seconds_until(self, when: float) -> float:
        """Hur många riktiga sekunder det dröjer tills klockan visar `when` (minst 0)."""
        ...

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Anropar `listener` när takten ändras. Returnerar en funktion som avslutar."""
        ...


class SystemClock:
    """Riktig tid. Monoton, så den hoppar inte när datorns klocka ställs om.

    Simuleringen kan låta tiden gå fortare med `set_speed`. Tiden fortsätter
    då från där den var, så inget hoppar, men en minut går på en sekund vid 60×.
    """

    def __init__(self, real_time: Callable[[], float] = time.monotonic) -> None:
        self._real_time = real_time
        self._speed = 1.0
        # Tiden räknas från den senaste ändringen av takten.
        self._real_start = real_time()
        self._start = self._real_start
        self._listeners: list[Listener] = []

    def now(self) -> float:
        return self._start + (self._real_time() - self._real_start) * self._speed

    @property
    def speed(self) -> float:
        return self._speed

    def set_speed(self, speed: float) -> None:
        if not speed > 0:
            raise ValueError(f"Tiden måste gå framåt (takt {speed})")
        if speed == self._speed:
            return
        self._start = self.now()
        self._real_start = self._real_time()
        self._speed = speed
        for listener in list(self._listeners):
            listener()

    def real_seconds_until(self, when: float) -> float:
        return max(0.0, (when - self.now()) / self._speed)

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)


class ManualClock:
    """Tid som bara går framåt när någon säger till. Används i scenariotester."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def now(self) -> float:
        return self._now

    def advance_to(self, when: float) -> None:
        if when < self._now:
            raise ValueError(f"Klockan kan inte gå bakåt ({when} < {self._now})")
        self._now = when
