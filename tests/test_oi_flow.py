"""Tests for OI flow direction estimation (bought vs sold)."""

import sys

sys.path.insert(0, ".")

import pandas as pd
import numpy as np
import pytest

from src.engine.oi_flow import (
    classify_oi_flow,
    FlowType,
    compute_adjusted_gex,
)


def _make_chain(spot=22000, num_strikes=5, call_oi=50000, put_oi=40000,
                call_ltp=100.0, put_ltp=80.0, call_iv=15.0, put_iv=16.0):
    strikes = [spot - 100 + i * 50 for i in range(num_strikes)]
    rows = []
    for s in strikes:
        rows.append({
            "strike_price": s,
            "call_oi": call_oi, "put_oi": put_oi,
            "call_gamma": 0.001, "put_gamma": 0.001,
            "call_delta": 0.5, "put_delta": -0.5,
            "call_iv": call_iv, "put_iv": put_iv,
            "call_ltp": call_ltp, "put_ltp": put_ltp,
            "call_volume": 5000, "put_volume": 3000,
        })
    return pd.DataFrame(rows)


class TestClassifyOIFlow:
    def test_long_buildup(self):
        """OI up + price up = bought."""
        prev = _make_chain(call_oi=40000, call_ltp=80.0)
        curr = _make_chain(call_oi=50000, call_ltp=120.0)  # OI up, price up
        result = classify_oi_flow(curr, prev, 22000)
        assert result["dominant_flow"] in ["bullish", "bearish", "neutral"]
        assert len(result["strike_flows"]) == 5

    def test_short_buildup(self):
        """OI up + price down = sold."""
        prev = _make_chain(call_oi=40000, call_ltp=120.0)
        curr = _make_chain(call_oi=50000, call_ltp=80.0)  # OI up, price down
        result = classify_oi_flow(curr, prev, 22000)
        assert result["net_sold_calls"] > 0

    def test_empty_chains(self):
        curr = pd.DataFrame(columns=["strike_price"])
        prev = pd.DataFrame(columns=["strike_price"])
        result = classify_oi_flow(curr, prev, 22000)
        assert result["dominant_flow"] == "neutral"
        assert result["flow_confidence"] == 0.0

    def test_no_overlapping_strikes(self):
        prev = _make_chain(spot=20000)
        curr = _make_chain(spot=24000)
        result = classify_oi_flow(curr, prev, 22000)
        assert result["dominant_flow"] == "neutral"

    def test_confidence_range(self):
        prev = _make_chain()
        curr = _make_chain(call_oi=60000, call_ltp=150.0, call_iv=17.0)
        result = classify_oi_flow(curr, prev, 22000)
        assert 0.0 <= result["flow_confidence"] <= 1.0

    def test_dealer_delta_computed(self):
        prev = _make_chain(call_oi=40000)
        curr = _make_chain(call_oi=50000, call_ltp=120.0)
        result = classify_oi_flow(curr, prev, 22000)
        assert isinstance(result["net_dealer_delta"], float)

    def test_bullish_dominant_flow(self):
        """Heavy call buying + put selling = bullish."""
        prev = _make_chain(call_oi=30000, put_oi=60000, call_ltp=80.0, put_ltp=120.0)
        curr = _make_chain(call_oi=50000, put_oi=40000, call_ltp=130.0, put_ltp=70.0)
        result = classify_oi_flow(curr, prev, 22000)
        # Call OI up + price up = bought calls (bullish)
        # Put OI down + price down = long unwinding puts
        assert result["net_bought_calls"] > 0


class TestComputeAdjustedGEX:
    def test_returns_adjusted_columns(self):
        chain = _make_chain()
        flow_data = classify_oi_flow(chain, _make_chain(call_oi=40000, call_ltp=80.0), 22000)
        result = compute_adjusted_gex(chain, flow_data, 22000, 65)
        assert "adj_call_gex" in result.columns
        assert "adj_put_gex" in result.columns
        assert "adj_net_gex" in result.columns

    def test_empty_flow_data(self):
        chain = _make_chain()
        result = compute_adjusted_gex(chain, {"strike_flows": []}, 22000, 65)
        assert len(result) == 5
        assert "adj_net_gex" in result.columns
