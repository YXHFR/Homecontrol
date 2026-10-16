"""Styrgränsen: hur ofta varje lampa får styras.

Alla i hemnätet och på Tailscale kan styra lamporna. Gränsen hindrar att en
trasig eller elak klient får en lampa att blinka. Lamputgångarna (se
zigbee.py och de virtuella lamporna i simulation.py) frågar `LampLimit`
innan ett kommando går ut.
"""

import logging
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

LIMIT_BURST = 5
"""Så många kommandon i rad som en lampa tar emot innan gränsen slår till."""
LIMIT_PER_SECOND = 1.0
"""Så många kommandon per sekund som en lampa sedan tar emot i längden."""


class LampLimit:
    """Hur ofta varje lampa får styras.

    Varje lampa har en hink med `burst` kommandon som fylls på med
    `per_second` kommandon i sekunden. Automatikens kommandon, kalibreringen
    och klick i livevyn kommer mycket glesare än så och märker aldrig gränsen.

    Kärnan skickar inte om ett beslut som inte har ändrats, så en utgång tappar
    aldrig ett stoppat kommando. Den skjuter upp det senaste kommandot till varje
    lampa och skickar det när `allow` säger ja. Att en lampa stoppas loggas en
    gång, och igen först när hinken har hunnit bli full.

    `now` är tiden i sekunder.
    """

    def __init__(
        self,
        now: Callable[[], float] = time.monotonic,
        *,
        burst: int = LIMIT_BURST,
        per_second: float = LIMIT_PER_SECOND,
    ) -> None:
        self._now = now
        self._burst = burst
        self._per_second = per_second
        self._buckets: dict[str, tuple[float, float]] = {}
        """Hur många kommandon som finns kvar i varje lampas hink, och när det räknades."""
        self._stopped: set[str] = set()
        """Lamporna som har stoppats sedan hinken senast var full, så att det loggas en gång."""

    def allow(self, lamp_id: str) -> bool:
        """Om ett kommando till lampan får gå ut nu. I så fall räknas det av."""
        now = self._now()
        tokens, counted_at = self._buckets.get(lamp_id, (self._burst, now))
        tokens = min(self._burst, tokens + (now - counted_at) * self._per_second)
        if tokens >= self._burst:
            self._stopped.discard(lamp_id)
        if tokens < 1:
            self._buckets[lamp_id] = (tokens, now)
            if lamp_id not in self._stopped:
                self._stopped.add(lamp_id)
                logger.warning(
                    "Kommandon till %s skjuts upp eftersom lampan styrs för ofta", lamp_id
                )
            return False
        self._buckets[lamp_id] = (tokens - 1, now)
        return True
