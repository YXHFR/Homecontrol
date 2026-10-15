"""Klockor. Kärnan frågar alltid en klocka om tiden i stället för att läsa systemtiden.

Tiden är sekunder som ett flyttal. Bara skillnader mellan två tidpunkter har
betydelse, så en klocka får börja var som helst.
"""

import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Nuvarande tid i sekunder."""
        ...


class SystemClock:
    """Riktig tid. Monoton, så den hoppar inte när datorns klocka ställs om."""

    def now(self) -> float:
        return time.monotonic()


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
