"""Tests for charm/vanna flow computation with NaN handling."""

import sys

sys.path.insert(0, ".")

import pandas as pd
import numpy as np
import pytest

from src.engine.charm_vanna import (
    compute_charm_flow,
    compute_vanna_exposure,
    compute_oi_change,
)


def _make_chain(spot=22000, num_strikes=10):
    """Generate a synthetic options chain."""
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


class TestComputeCharmFlow:
    def test_returns_required_keys(self):
        chain = _make_chain()
        result = compute_charm_flow(chain, 22000, 3.0, 65)
        assert "net_charm_flow" in result
        assert "charm_intensity" in result
        assert "call_charm_exposure" in result
        assert "put_charm_exposure" in result

    def test_zero_time_to_expiry(self):
        chain = _make_chain()
        result = compute_charm_flow(chain, 22000, 0.0, 65)
        assert result["net_charm_flow"] == 0
        assert result["charm_intensity"] == 0

    def test_intensity_bounded_0_100(self):
        chain = _make_chain()
        result = compute_charm_flow(chain, 22000, 0.5, 65)
        assert 0 <= result["charm_intensity"] <= 100

    def test_nan_delta_handled(self):
        """BUG 2: NaN in delta columns should not crash computation."""
        chain = _make_chain()
        chain.loc[0, "call_delta"] = np.nan
        chain.loc[1, "put_delta"] = np.nan
        result = compute_charm_flow(chain, 22000, 3.0, 65)
        assert not np.isnan(result["net_charm_flow"])

    def test_nan_oi_handled(self):
        """BUG 2: NaN in OI columns should not crash computation."""
        chain = _make_chain()
        chain.loc[0, "call_oi"] = np.nan
        chain.loc[1, "put_oi"] = np.nan
        result = compute_charm_flow(chain, 22000, 3.0, 65)
        assert not np.isnan(result["net_charm_flow"])

    def test_last_two_hours_boost(self):
        """Charm intensity should be boosted in the last 2 hours."""
        chain = _make_chain()
        result_3h = compute_charm_flow(chain, 22000, 3.0, 65)
        result_1h = compute_charm_flow(chain, 22000, 1.0, 65)
        # 1h result should have higher intensity due to time factor + boost
        assert result_1h["charm_intensity"] >= result_3h["charm_intensity"]


class TestComputeVannaExposure:
    def test_no_previous_chain(self):
        chain = _make_chain()
        result = compute_vanna_exposure(chain, None, 22000, 65)
        assert result["net_vanna_flow"] == 0
        assert result["vanna_intensity"] == 0
        assert result["avg_iv_change"] == 0

    def test_returns_required_keys(self):
        chain = _make_chain()
        prev_chain = _make_chain()
        prev_chain["call_iv"] = 17.0  # IV was higher before (vol crush)
        prev_chain["put_iv"] = 18.0
        result = compute_vanna_exposure(chain, prev_chain, 22000, 65)
        assert "net_vanna_flow" in result
        assert "vanna_intensity" in result
        assert "avg_iv_change" in result

    def test_iv_drop_detected(self):
        chain = _make_chain()
        prev_chain = _make_chain()
        prev_chain["call_iv"] = 20.0  # Was higher
        prev_chain["put_iv"] = 21.0
        result = compute_vanna_exposure(chain, prev_chain, 22000, 65)
        assert result["avg_iv_change"] < 0  # IV dropped

    def test_nan_iv_change_handled(self):
        """BUG 2: NaN IV changes should be filled with 0."""
        chain = _make_chain()
        prev_chain = _make_chain()
        # Add extra strike in chain that doesn't exist in prev — inner merge excludes it
        extra = pd.DataFrame([{
            "strike_price": 22500,
            "call_oi": 1000, "put_oi": 1000,
            "call_gamma": 0.001, "put_gamma": 0.001,
            "call_delta": 0.3, "put_delta": -0.3,
            "call_iv": np.nan, "put_iv": np.nan,
            "call_ltp": 10.0, "put_ltp": 10.0,
            "call_volume": 100, "put_volume": 100,
        }])
        chain = pd.concat([chain, extra], ignore_index=True)
        result = compute_vanna_exposure(chain, prev_chain, 22000, 65)
        assert not np.isnan(result["avg_iv_change"])
        assert not np.isnan(result["net_vanna_flow"])

    def test_empty_merge_result(self):
        """No overlapping strikes should return zero values."""
        chain = _make_chain(spot=22000)
        prev_chain = _make_chain(spot=25000)  # Completely different strikes
        result = compute_vanna_exposure(chain, prev_chain, 22000, 65)
        assert result["net_vanna_flow"] == 0
        assert result["vanna_intensity"] == 0


class TestComputeOIChange:
    def test_no_previous_chain(self):
        chain = _make_chain()
        result = compute_oi_change(chain, None, 22000)
        assert result["net_oi_change"] == 0
        assert result["oi_surge_strikes"] == []
        assert result["oi_intensity"] == 0

    def test_oi_buildup_detected(self):
        chain = _make_chain()
        prev_chain = _make_chain()
        chain["call_oi"] = 80000  # OI increased
        chain["put_oi"] = 70000
        result = compute_oi_change(chain, prev_chain, 22000)
        assert result["net_oi_change"] > 0

    def test_oi_unwinding_detected(self):
        chain = _make_chain()
        prev_chain = _make_chain()
        chain["call_oi"] = 10000  # OI decreased
        chain["put_oi"] = 5000
        result = compute_oi_change(chain, prev_chain, 22000)
        assert result["net_oi_change"] < 0

    def test_nan_oi_change_handled(self):
        """BUG 2: NaN in OI after merge should not crash."""
        chain = _make_chain()
        prev_chain = _make_chain()
        chain.loc[0, "call_oi"] = np.nan
        prev_chain.loc[1, "put_oi"] = np.nan
        result = compute_oi_change(chain, prev_chain, 22000)
        # Should not crash and should return valid values
        assert isinstance(result["net_oi_change"], (int, np.integer))

    def test_surge_detection(self):
        chain = _make_chain()
        prev_chain = _make_chain()
        # Create a 50% OI surge at one strike
        chain.loc[5, "call_oi"] = 100000  # Was 50000
        result = compute_oi_change(chain, prev_chain, 22000)
        assert len(result["oi_surge_strikes"]) > 0
