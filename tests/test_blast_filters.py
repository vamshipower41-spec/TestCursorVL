"""Tests for blast quality filters — PCR, max pain, IV skew, volume, trend."""

import sys

sys.path.insert(0, ".")

import pandas as pd
import numpy as np
import pytest

from src.engine.blast_filters import (
    compute_pcr,
    compute_max_pain,
    compute_iv_skew,
    compute_trend_bias,
    check_volume_confirmation,
    apply_trend_filter,
    apply_volume_filter,
    apply_pcr_filter,
    apply_iv_skew_filter,
    apply_max_pain_filter,
    check_volume_direction_alignment,
    classify_vix_regime,
    is_monthly_expiry,
    apply_liquidity_filter,
)


def _make_chain(spot=22000, num_strikes=10):
    """Generate a simple chain DataFrame."""
    strikes = [spot - 250 + i * 50 for i in range(num_strikes)]
    rows = []
    for s in strikes:
        rows.append({
            "strike_price": s,
            "call_oi": 50000, "put_oi": 40000,
            "call_gamma": 0.001, "put_gamma": 0.001,
            "call_delta": 0.5, "put_delta": -0.5,
            "call_iv": 15.0, "put_iv": 16.0,
            "call_ltp": max(spot - s, 1.0), "put_ltp": max(s - spot, 1.0),
            "call_volume": 5000, "put_volume": 3000,
        })
    return pd.DataFrame(rows)


class TestComputePCR:
    def test_normal_pcr(self):
        chain = _make_chain()
        result = compute_pcr(chain)
        assert "pcr" in result
        assert "pcr_signal" in result
        assert result["pcr"] > 0

    def test_zero_call_oi(self):
        chain = _make_chain()
        chain["call_oi"] = 0
        result = compute_pcr(chain)
        assert result["pcr"] == 0.0
        assert result["pcr_signal"] == "neutral"

    def test_bullish_pcr(self):
        chain = _make_chain()
        chain["put_oi"] = 100000  # Heavy put writing
        chain["call_oi"] = 10000
        result = compute_pcr(chain)
        assert result["pcr"] > 1.3
        assert result["pcr_signal"] == "bullish"

    def test_bearish_pcr(self):
        chain = _make_chain()
        chain["put_oi"] = 10000
        chain["call_oi"] = 100000  # Heavy call writing
        result = compute_pcr(chain)
        assert result["pcr"] < 0.7
        assert result["pcr_signal"] == "bearish"

    def test_missing_columns(self):
        """BUG 7: compute_pcr should not crash if OI columns missing."""
        chain = pd.DataFrame({"strike_price": [22000], "call_gamma": [0.001]})
        result = compute_pcr(chain)
        assert result["pcr"] == 0.0
        assert result["pcr_signal"] == "neutral"


class TestComputeMaxPain:
    def test_finds_max_pain(self):
        chain = _make_chain()
        result = compute_max_pain(chain, 22000)
        assert result is not None
        assert isinstance(result, float)

    def test_empty_chain(self):
        chain = pd.DataFrame(columns=["strike_price", "call_oi", "put_oi"])
        result = compute_max_pain(chain, 22000)
        assert result is None

    def test_missing_columns(self):
        """BUG 8: compute_max_pain should not crash if OI columns missing."""
        chain = pd.DataFrame({"strike_price": [22000]})
        result = compute_max_pain(chain, 22000)
        assert result is None

    def test_all_zero_oi(self):
        chain = _make_chain()
        chain["call_oi"] = 0
        chain["put_oi"] = 0
        result = compute_max_pain(chain, 22000)
        # Should return a strike (all have equal 0 pain)
        assert result is not None


class TestComputeIVSkew:
    def test_bearish_skew(self):
        chain = _make_chain()
        # Put IV >> Call IV = bearish fear
        chain["put_iv"] = 20.0
        chain["call_iv"] = 14.0
        result = compute_iv_skew(chain, 22000)
        assert result["skew_signal"] == "bearish"
        assert result["iv_skew"] > 2.0

    def test_bullish_skew(self):
        chain = _make_chain()
        chain["put_iv"] = 12.0
        chain["call_iv"] = 18.0
        result = compute_iv_skew(chain, 22000)
        assert result["skew_signal"] == "bullish"
        assert result["iv_skew"] < -2.0

    def test_neutral_skew(self):
        chain = _make_chain()
        chain["put_iv"] = 15.5
        chain["call_iv"] = 15.0
        result = compute_iv_skew(chain, 22000)
        assert result["skew_signal"] == "neutral"

    def test_empty_chain(self):
        chain = pd.DataFrame(columns=["strike_price", "call_iv", "put_iv"])
        result = compute_iv_skew(chain, 22000)
        assert result["iv_skew"] == 0.0
        assert result["skew_signal"] == "neutral"

    def test_zero_iv_filtered(self):
        chain = _make_chain()
        chain["call_iv"] = 0.0
        chain["put_iv"] = 0.0
        result = compute_iv_skew(chain, 22000)
        assert result["skew_signal"] == "neutral"


class TestComputeTrendBias:
    def test_short_history_neutral(self):
        result = compute_trend_bias([22000, 22010])
        assert result["trend"] == "neutral"

    def test_bullish_trend(self):
        prices = [22000 + i * 20 for i in range(10)]
        result = compute_trend_bias(prices)
        assert result["trend"] == "bullish"
        assert result["strength"] > 0

    def test_bearish_trend(self):
        prices = [23000 - i * 20 for i in range(10)]
        result = compute_trend_bias(prices)
        assert result["trend"] == "bearish"
        assert result["strength"] > 0

    def test_flat_prices_neutral(self):
        prices = [22000.0] * 10
        result = compute_trend_bias(prices)
        assert result["trend"] == "neutral"
        assert result["strength"] == 0.0


class TestCheckVolumeConfirmation:
    def test_balanced_volume(self):
        chain = _make_chain()
        chain["call_volume"] = 5000
        chain["put_volume"] = 5000
        result = check_volume_confirmation(chain, 22000)
        assert result["dominant_side"] in ["call", "put", "balanced"]

    def test_zero_volume(self):
        chain = _make_chain()
        chain["call_volume"] = 0
        chain["put_volume"] = 0
        result = check_volume_confirmation(chain, 22000)
        assert result["confirmed"] is False
        assert result["volume_score"] == 0

    def test_volume_surge_detection(self):
        chain = _make_chain()
        chain["call_volume"] = 10000
        chain["put_volume"] = 2000
        prev_chain = _make_chain()
        prev_chain["call_volume"] = 3000
        prev_chain["put_volume"] = 2000
        result = check_volume_confirmation(chain, 22000, prev_chain)
        assert result["dominant_side"] == "call"


class TestApplyFilters:
    def test_trend_filter_boost_with_trend(self):
        trend_data = {"trend": "bullish", "strength": 0.5}
        result = apply_trend_filter("bullish", 70.0, trend_data)
        assert result > 70.0

    def test_trend_filter_penalize_counter_trend(self):
        trend_data = {"trend": "bearish", "strength": 0.5}
        result = apply_trend_filter("bullish", 70.0, trend_data)
        assert result < 70.0

    def test_pcr_filter_confirms(self):
        pcr_data = {"pcr": 1.5, "pcr_signal": "bullish"}
        result = apply_pcr_filter(70.0, "bullish", pcr_data)
        assert result > 70.0

    def test_pcr_filter_contradicts(self):
        pcr_data = {"pcr": 0.5, "pcr_signal": "bearish"}
        result = apply_pcr_filter(70.0, "bullish", pcr_data)
        assert result < 70.0

    def test_iv_skew_filter_confirms(self):
        skew_data = {"iv_skew": 3.0, "skew_signal": "bearish"}
        result = apply_iv_skew_filter(70.0, "bearish", skew_data)
        assert result > 70.0

    def test_max_pain_filter_near_expiry(self):
        result = apply_max_pain_filter(70.0, 22000.0, 22010.0, 1.0)
        assert result < 70.0  # Near max pain near expiry = suppress

    def test_max_pain_filter_none(self):
        result = apply_max_pain_filter(70.0, 22000.0, None, 1.0)
        assert result == 70.0

    def test_volume_direction_alignment_match(self):
        vol_data = {"dominant_side": "call"}
        adj = check_volume_direction_alignment("bullish", vol_data)
        assert adj > 0

    def test_volume_direction_alignment_mismatch(self):
        vol_data = {"dominant_side": "put"}
        adj = check_volume_direction_alignment("bullish", vol_data)
        assert adj < 0


class TestVIXRegime:
    def test_low_vol(self):
        result = classify_vix_regime(12.0)
        assert result["regime"] == "low_vol"
        assert result["threshold_adjustment"] == 0

    def test_normal_vol(self):
        result = classify_vix_regime(16.0)
        assert result["regime"] == "normal_vol"
        assert result["weight_overrides"] is None

    def test_high_vol(self):
        result = classify_vix_regime(20.0)
        assert result["regime"] == "high_vol"
        assert result["threshold_adjustment"] == 5

    def test_extreme_vol(self):
        result = classify_vix_regime(25.0)
        assert result["regime"] == "extreme_vol"
        assert result["threshold_adjustment"] == 10


class TestMonthlyExpiry:
    def test_weekly_expiry(self):
        # 2026-03-10 is a Tuesday, and there's another Tuesday (17th) in March
        assert is_monthly_expiry("2026-03-10", "NIFTY") is False

    def test_monthly_expiry(self):
        # 2026-03-31 is a Tuesday, last Tuesday of March
        assert is_monthly_expiry("2026-03-31", "NIFTY") is True


class TestLiquidityFilter:
    def test_nifty_not_penalized(self):
        chain = _make_chain()
        result = apply_liquidity_filter(70.0, chain, "NIFTY")
        assert result == 70.0

    def test_sensex_low_oi_penalized(self):
        chain = _make_chain()
        chain["call_oi"] = 100
        chain["put_oi"] = 100
        result = apply_liquidity_filter(70.0, chain, "SENSEX")
        assert result < 70.0

    def test_sensex_adequate_oi_not_penalized(self):
        chain = _make_chain()
        chain["call_oi"] = 50000
        chain["put_oi"] = 50000
        result = apply_liquidity_filter(70.0, chain, "SENSEX")
        assert result == 70.0
