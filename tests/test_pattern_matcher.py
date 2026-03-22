"""Tests for historical pattern matching."""

import sys

sys.path.insert(0, ".")

import pytest
from datetime import datetime

from src.data.models import GEXProfile, StrikeGEX
from src.engine.pattern_matcher import (
    compute_pattern_features,
    match_historical_patterns,
    apply_pattern_adjustment,
    PatternResult,
)


def _make_profile(spot=22000, gamma_flip=21900, net_gex=100000):
    return GEXProfile(
        timestamp=datetime(2026, 3, 24, 14, 0),
        instrument="NIFTY",
        spot_price=spot,
        expiry_date="2026-03-24",
        contract_multiplier=65,
        strikes=[StrikeGEX(strike_price=22000, call_gex=100, put_gex=-50, net_gex=50)],
        gamma_flip_level=gamma_flip,
        max_gamma_strike=22000,
        zero_gex_levels=[],
        call_wall=22200,
        put_wall=21800,
        net_gex_total=net_gex,
    )


class TestComputePatternFeatures:
    def test_positive_gex_regime(self):
        profile = _make_profile(net_gex=100000)
        features = compute_pattern_features(profile, 15.0, 3.0, "bullish", "bullish")
        assert features["gex_regime"] == "positive"

    def test_negative_gex_regime(self):
        profile = _make_profile(net_gex=-100000)
        features = compute_pattern_features(profile, 15.0, 3.0, "bullish", "bullish")
        assert features["gex_regime"] == "negative"

    def test_vix_buckets(self):
        profile = _make_profile()
        assert compute_pattern_features(profile, 12.0, 3.0, "neutral", "bullish")["vix_bucket"] == "low"
        assert compute_pattern_features(profile, 16.0, 3.0, "neutral", "bullish")["vix_bucket"] == "normal"
        assert compute_pattern_features(profile, 20.0, 3.0, "neutral", "bullish")["vix_bucket"] == "high"
        assert compute_pattern_features(profile, 25.0, 3.0, "neutral", "bullish")["vix_bucket"] == "extreme"

    def test_time_buckets(self):
        profile = _make_profile()
        assert compute_pattern_features(profile, 15.0, 6.0, "neutral", "bullish")["time_bucket"] == "morning"
        assert compute_pattern_features(profile, 15.0, 4.0, "neutral", "bullish")["time_bucket"] == "midday"
        assert compute_pattern_features(profile, 15.0, 2.0, "neutral", "bullish")["time_bucket"] == "charm_zone"
        assert compute_pattern_features(profile, 15.0, 0.3, "neutral", "bullish")["time_bucket"] == "settlement"

    def test_flip_position(self):
        profile = _make_profile(spot=22000, gamma_flip=21900)
        features = compute_pattern_features(profile, 15.0, 3.0, "neutral", "bullish")
        assert features["flip_position"] == "above"

    def test_no_vix(self):
        profile = _make_profile()
        features = compute_pattern_features(profile, None, 3.0, "neutral", "bullish")
        assert features["vix_bucket"] == "unknown"


class TestMatchHistoricalPatterns:
    def test_no_data_returns_empty(self, tmp_path):
        result = match_historical_patterns(
            {"direction": "bullish"}, str(tmp_path / "nonexistent"),
        )
        assert result.total_matches == 0
        assert result.recommendation == "neutral"

    def test_empty_dir_returns_empty(self, tmp_path):
        result = match_historical_patterns({"direction": "bullish"}, str(tmp_path))
        assert result.total_matches == 0


class TestApplyPatternAdjustment:
    def test_low_confidence_no_change(self):
        pr = PatternResult(
            total_matches=2, hits=2, misses=0,
            conditional_hit_rate=1.0, avg_pnl_pct=0.5,
            confidence=0.1, matches=[], recommendation="boost",
        )
        assert apply_pattern_adjustment(70.0, pr) == 70.0

    def test_boost_with_high_confidence(self):
        pr = PatternResult(
            total_matches=30, hits=25, misses=5,
            conditional_hit_rate=0.83, avg_pnl_pct=0.5,
            confidence=0.8, matches=[], recommendation="boost",
        )
        assert apply_pattern_adjustment(70.0, pr) > 70.0

    def test_suppress_with_high_confidence(self):
        pr = PatternResult(
            total_matches=30, hits=5, misses=25,
            conditional_hit_rate=0.17, avg_pnl_pct=-0.3,
            confidence=0.8, matches=[], recommendation="suppress",
        )
        assert apply_pattern_adjustment(70.0, pr) < 70.0

    def test_neutral_no_change(self):
        pr = PatternResult(
            total_matches=20, hits=10, misses=10,
            conditional_hit_rate=0.5, avg_pnl_pct=0.0,
            confidence=0.7, matches=[], recommendation="neutral",
        )
        assert apply_pattern_adjustment(70.0, pr) == 70.0
