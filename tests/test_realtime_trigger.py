"""Tests for the real-time trigger engine."""

import sys

sys.path.insert(0, ".")

import time
import pytest

from src.engine.realtime_trigger import (
    RealtimeTriggerEngine,
    CriticalLevels,
)

try:
    from src.data.websocket_feed import MarketTick
except ImportError:
    # upstox_client not installed — define MarketTick locally for testing
    from dataclasses import dataclass

    @dataclass
    class MarketTick:
        instrument_key: str
        ltp: float
        volume: int = 0
        oi: int = 0
        timestamp: str = ""


class TestRealtimeTriggerEngine:
    def _make_engine(self):
        triggers = []

        def on_trigger(reason, spot):
            triggers.append((reason, spot))

        engine = RealtimeTriggerEngine(
            on_trigger=on_trigger,
            min_interval=0,  # Disable cooldown for testing
            max_interval=999,  # Disable periodic for testing
        )
        return engine, triggers

    def test_gamma_flip_crossing(self):
        engine, triggers = self._make_engine()
        engine.update_levels(CriticalLevels(gamma_flip=22000))

        # Price below flip
        engine._last_spot = 21990
        engine._last_fetch_time = 0
        tick = MarketTick(instrument_key="test", ltp=22010)
        engine.on_tick(tick)

        assert len(triggers) == 1
        assert "gamma_flip_crossed" in triggers[0][0]

    def test_call_wall_breach(self):
        engine, triggers = self._make_engine()
        engine.update_levels(CriticalLevels(call_wall=22200))

        engine._last_spot = 22190
        engine._last_fetch_time = 0
        tick = MarketTick(instrument_key="test", ltp=22210)
        engine.on_tick(tick)

        assert len(triggers) == 1
        assert "call_wall_breached" in triggers[0][0]

    def test_put_wall_breach(self):
        engine, triggers = self._make_engine()
        engine.update_levels(CriticalLevels(put_wall=21800))

        engine._last_spot = 21810
        engine._last_fetch_time = 0
        tick = MarketTick(instrument_key="test", ltp=21790)
        engine.on_tick(tick)

        assert len(triggers) == 1
        assert "put_wall_breached" in triggers[0][0]

    def test_proximity_trigger(self):
        engine, triggers = self._make_engine()
        engine.update_levels(CriticalLevels(gamma_flip=22000))

        # Price very close to flip but not crossing
        engine._last_spot = 21960
        engine._last_fetch_time = 0
        tick = MarketTick(instrument_key="test", ltp=21998)  # Within 0.2%
        engine.on_tick(tick)

        assert len(triggers) == 1
        assert "proximity" in triggers[0][0]

    def test_no_trigger_when_far_from_levels(self):
        engine, triggers = self._make_engine()
        engine.update_levels(CriticalLevels(
            gamma_flip=22000, call_wall=22500, put_wall=21500,
        ))

        engine._last_spot = 22200
        engine._last_fetch_time = time.time()
        tick = MarketTick(instrument_key="test", ltp=22210)
        engine.on_tick(tick)

        assert len(triggers) == 0

    def test_zero_price_ignored(self):
        engine, triggers = self._make_engine()
        tick = MarketTick(instrument_key="test", ltp=0)
        engine.on_tick(tick)
        assert len(triggers) == 0

    def test_stats_tracking(self):
        engine, triggers = self._make_engine()
        engine.update_levels(CriticalLevels(gamma_flip=22000))

        engine._last_spot = 21990
        engine._last_fetch_time = 0
        tick = MarketTick(instrument_key="test", ltp=22010)
        engine.on_tick(tick)

        stats = engine.get_stats()
        assert stats["total_triggers"] == 1
        assert "gamma_flip_crossed" in stats["reasons"]

    def test_cooldown_respected(self):
        engine, triggers = self._make_engine()
        engine._min_interval = 60  # 60 second cooldown
        engine.update_levels(CriticalLevels(gamma_flip=22000))

        engine._last_spot = 21990
        engine._last_fetch_time = time.time()  # Just fetched
        tick = MarketTick(instrument_key="test", ltp=22010)
        engine.on_tick(tick)

        assert len(triggers) == 0  # Cooldown not elapsed
