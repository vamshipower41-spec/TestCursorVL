"""Tests for Greeks validation and active strikes filtering."""

import sys

sys.path.insert(0, ".")

import pandas as pd
import numpy as np
import pytest

from src.engine.greeks import validate_greeks, filter_active_strikes


def _make_chain_with_nans():
    """Create a chain DataFrame with NaN values in various columns."""
    return pd.DataFrame([
        {
            "strike_price": 22000,
            "call_gamma": np.nan, "put_gamma": -0.001,
            "call_delta": np.nan, "put_delta": -0.5,
            "call_iv": np.nan, "put_iv": 15.0,
            "call_volume": np.nan, "put_volume": 1000,
            "call_oi": np.nan, "put_oi": 50000,
            "call_ltp": np.nan, "put_ltp": 100.0,
        },
        {
            "strike_price": 22100,
            "call_gamma": 0.002, "put_gamma": np.nan,
            "call_delta": 0.5, "put_delta": np.nan,
            "call_iv": 14.0, "put_iv": np.nan,
            "call_volume": 5000, "put_volume": np.nan,
            "call_oi": 30000, "put_oi": np.nan,
            "call_ltp": 200.0, "put_ltp": np.nan,
        },
    ])


class TestValidateGreeks:
    def test_fills_nan_gamma_with_zero(self):
        df = _make_chain_with_nans()
        result = validate_greeks(df)
        assert result["call_gamma"].iloc[0] == 0.0
        assert result["put_gamma"].iloc[1] == 0.0

    def test_clips_negative_gamma_to_zero(self):
        df = _make_chain_with_nans()
        result = validate_greeks(df)
        # put_gamma was -0.001, should be clipped to 0
        assert result["put_gamma"].iloc[0] == 0.0

    def test_fills_nan_delta_with_zero(self):
        df = _make_chain_with_nans()
        result = validate_greeks(df)
        assert result["call_delta"].iloc[0] == 0.0
        assert result["put_delta"].iloc[1] == 0.0

    def test_fills_nan_iv_with_zero(self):
        df = _make_chain_with_nans()
        result = validate_greeks(df)
        assert result["call_iv"].iloc[0] == 0.0
        assert result["put_iv"].iloc[1] == 0.0

    def test_clips_negative_iv_to_zero(self):
        df = pd.DataFrame([{
            "strike_price": 22000,
            "call_gamma": 0.001, "put_gamma": 0.001,
            "call_delta": 0.5, "put_delta": -0.5,
            "call_iv": -5.0, "put_iv": -3.0,
            "call_volume": 100, "put_volume": 100,
            "call_oi": 1000, "put_oi": 1000,
            "call_ltp": 10.0, "put_ltp": 10.0,
        }])
        result = validate_greeks(df)
        assert result["call_iv"].iloc[0] == 0.0
        assert result["put_iv"].iloc[0] == 0.0

    def test_fills_nan_volume_with_zero_int(self):
        df = _make_chain_with_nans()
        result = validate_greeks(df)
        assert result["call_volume"].iloc[0] == 0
        assert result["put_volume"].iloc[1] == 0
        assert result["call_volume"].dtype in [np.int64, np.int32, int]

    def test_fills_nan_oi_with_zero_int(self):
        df = _make_chain_with_nans()
        result = validate_greeks(df)
        assert result["call_oi"].iloc[0] == 0
        assert result["put_oi"].iloc[1] == 0
        assert result["call_oi"].dtype in [np.int64, np.int32, int]

    def test_fills_nan_ltp_with_zero_float(self):
        df = _make_chain_with_nans()
        result = validate_greeks(df)
        assert result["call_ltp"].iloc[0] == 0.0
        assert result["put_ltp"].iloc[1] == 0.0

    def test_does_not_modify_original(self):
        df = _make_chain_with_nans()
        original_nan_count = df.isna().sum().sum()
        validate_greeks(df)
        assert df.isna().sum().sum() == original_nan_count

    def test_handles_missing_optional_columns(self):
        """If volume/OI/LTP columns are missing entirely, should not crash."""
        df = pd.DataFrame([{
            "strike_price": 22000,
            "call_gamma": 0.001, "put_gamma": 0.001,
            "call_delta": 0.5, "put_delta": -0.5,
            "call_iv": 15.0, "put_iv": 15.0,
        }])
        result = validate_greeks(df)
        assert len(result) == 1


class TestFilterActiveStrikes:
    def test_filters_to_n_nearest(self):
        strikes = list(range(21000, 23100, 100))
        df = pd.DataFrame({"strike_price": strikes})
        for col in ["call_gamma", "put_gamma", "call_delta", "put_delta",
                     "call_iv", "put_iv"]:
            df[col] = 0.001
        result = filter_active_strikes(df, spot_price=22000, num_strikes=10)
        assert len(result) == 10

    def test_sorted_by_strike(self):
        strikes = list(range(21000, 23100, 100))
        df = pd.DataFrame({"strike_price": strikes})
        for col in ["call_gamma", "put_gamma", "call_delta", "put_delta",
                     "call_iv", "put_iv"]:
            df[col] = 0.001
        result = filter_active_strikes(df, spot_price=22000, num_strikes=5)
        assert list(result["strike_price"]) == sorted(result["strike_price"])

    def test_distance_column_removed(self):
        df = pd.DataFrame({"strike_price": [21900, 22000, 22100]})
        for col in ["call_gamma", "put_gamma", "call_delta", "put_delta",
                     "call_iv", "put_iv"]:
            df[col] = 0.001
        result = filter_active_strikes(df, spot_price=22000, num_strikes=3)
        assert "distance" not in result.columns
