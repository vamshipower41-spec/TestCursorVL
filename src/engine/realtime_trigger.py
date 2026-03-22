"""Real-Time Trigger Engine — WebSocket-driven smart chain fetching.

Instead of polling every 60-180 seconds (missing gamma flip crossings),
this engine:
  1. Streams spot price via WebSocket (sub-second updates)
  2. Maintains critical levels (gamma flip, walls, zero GEX)
  3. Triggers a chain fetch ONLY when price approaches a critical level
  4. Also triggers on periodic intervals as a fallback

This catches the exact moment of a gamma flip crossing, wall breach,
or zero-GEX penetration — the catalysts for gamma blasts.
"""

from __future__ import annotations

import time
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

try:
    from src.data.websocket_feed import MarketTick
except ImportError:
    from dataclasses import dataclass as _dc

    @_dc
    class MarketTick:  # type: ignore[no-redef]
        instrument_key: str
        ltp: float
        volume: int = 0
        oi: int = 0
        timestamp: str = ""
from src.utils.ist import now_ist


@dataclass
class CriticalLevels:
    """Levels that trigger a chain re-fetch when price approaches."""
    gamma_flip: float | None = None
    call_wall: float | None = None
    put_wall: float | None = None
    max_gamma: float | None = None
    zero_gex_levels: list[float] = field(default_factory=list)


# How close to a level (as % of spot) triggers a fetch
PROXIMITY_TRIGGER_PCT = 0.002  # 0.2%

# Minimum seconds between triggered fetches (prevent API hammering)
MIN_FETCH_INTERVAL_SECONDS = 30

# Maximum seconds between periodic fetches (fallback)
MAX_FETCH_INTERVAL_SECONDS = 120

# Velocity threshold: pts/second that triggers a fetch (fast move detection)
VELOCITY_TRIGGER_PTS_PER_SEC = 5.0


class RealtimeTriggerEngine:
    """Watches spot price stream and triggers chain fetches at optimal moments."""

    def __init__(
        self,
        on_trigger: Callable[[str, float], None],
        proximity_pct: float = PROXIMITY_TRIGGER_PCT,
        min_interval: int = MIN_FETCH_INTERVAL_SECONDS,
        max_interval: int = MAX_FETCH_INTERVAL_SECONDS,
    ):
        """
        Args:
            on_trigger: callback(reason, spot_price) invoked when a fetch should happen
            proximity_pct: how close to a level triggers a fetch (% of spot)
            min_interval: minimum seconds between fetches
            max_interval: maximum seconds between periodic fetches
        """
        self._on_trigger = on_trigger
        self._proximity_pct = proximity_pct
        self._min_interval = min_interval
        self._max_interval = max_interval

        self._levels = CriticalLevels()
        self._last_fetch_time: float = 0.0
        self._last_spot: float = 0.0
        self._last_spot_time: float = 0.0
        self._spot_history: list[tuple[float, float]] = []  # (timestamp, price)
        self._lock = threading.Lock()

        # Statistics
        self.triggers_fired: int = 0
        self.trigger_reasons: list[str] = []

    def update_levels(self, levels: CriticalLevels) -> None:
        """Update critical levels after a chain fetch + GEX computation."""
        with self._lock:
            self._levels = levels

    def on_tick(self, tick: MarketTick) -> None:
        """Process a real-time price tick. Called from WebSocket thread."""
        now = time.time()
        spot = tick.ltp

        if spot <= 0:
            return

        with self._lock:
            self._spot_history.append((now, spot))
            # Keep last 60 seconds of history
            cutoff = now - 60
            self._spot_history = [(t, p) for t, p in self._spot_history if t > cutoff]

            # Check if enough time has passed since last fetch
            time_since_fetch = now - self._last_fetch_time
            if time_since_fetch < self._min_interval:
                self._last_spot = spot
                self._last_spot_time = now
                return

            trigger_reason = self._check_triggers(spot, now)

            if trigger_reason:
                self._last_fetch_time = now
                self._last_spot = spot
                self._last_spot_time = now
                self.triggers_fired += 1
                self.trigger_reasons.append(trigger_reason)

        # Fire callback outside the lock
        if trigger_reason:
            self._on_trigger(trigger_reason, spot)

    def _check_triggers(self, spot: float, now: float) -> str | None:
        """Check if any trigger condition is met. Returns reason string or None."""
        levels = self._levels
        threshold = spot * self._proximity_pct

        # 1. Gamma flip proximity / crossing
        if levels.gamma_flip is not None:
            dist = abs(spot - levels.gamma_flip)
            if dist < threshold:
                # Check if we actually crossed
                if self._last_spot > 0:
                    crossed = (self._last_spot > levels.gamma_flip) != (spot > levels.gamma_flip)
                    if crossed:
                        return "gamma_flip_crossed"
                return "gamma_flip_proximity"

        # 2. Call wall proximity / breach
        if levels.call_wall is not None:
            dist = abs(spot - levels.call_wall)
            if dist < threshold:
                if self._last_spot > 0 and self._last_spot < levels.call_wall <= spot:
                    return "call_wall_breached"
                return "call_wall_proximity"

        # 3. Put wall proximity / breach
        if levels.put_wall is not None:
            dist = abs(spot - levels.put_wall)
            if dist < threshold:
                if self._last_spot > 0 and self._last_spot > levels.put_wall >= spot:
                    return "put_wall_breached"
                return "put_wall_proximity"

        # 4. Zero GEX level proximity
        for zg in levels.zero_gex_levels:
            dist = abs(spot - zg)
            if dist < threshold:
                return "zero_gex_proximity"

        # 5. Velocity spike (fast move detection)
        if len(self._spot_history) >= 5:
            recent = self._spot_history[-5:]
            dt = recent[-1][0] - recent[0][0]
            if dt > 0:
                velocity = abs(recent[-1][1] - recent[0][1]) / dt
                if velocity > VELOCITY_TRIGGER_PTS_PER_SEC:
                    return f"velocity_spike_{velocity:.1f}pts/s"

        # 6. Periodic fallback
        time_since_fetch = now - self._last_fetch_time
        if time_since_fetch >= self._max_interval:
            return "periodic"

        return None

    def get_stats(self) -> dict:
        """Return trigger statistics."""
        return {
            "total_triggers": self.triggers_fired,
            "reasons": dict(
                (r, self.trigger_reasons.count(r))
                for r in set(self.trigger_reasons)
            ),
        }
