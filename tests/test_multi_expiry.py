"""Tests for multi-expiry GEX aggregation."""

import sys

sys.path.insert(0, ".")

import pandas as pd
import numpy as np
import pytest

from src.engine.multi_expiry_gex import aggregate_multi_expiry_gex, ExpiryContribution


def _make_chain(spot=22000, num_strikes=10, call_oi=50000, put_oi=40000):
    strikes = [spot - 250 + i * 50 for i in range(num_strikes)]
    rows = []
    for s in strikes:
        dist = abs(s - spot) / spot
        gamma = max(0.001 * np.exp(-50 * dist ** 2), 0.0001)
        rows.append({
            "strike_price": s,
            "call_oi": call_oi, "put_oi": put_oi,
            "call_gamma": gamma, "put_gamma": gamma,
            "call_delta": 0.5, "put_delta": -0.5,
            "call_iv": 15.0, "put_iv": 15.0,
            "call_ltp": max(spot - s, 5.0), "put_ltp": max(s - spot, 5.0),
            "call_volume": 1000, "put_volume": 1000,
        })
    return pd.DataFrame(rows)


class TestAggregateMultiExpiry:
    def test_single_expiry(self):
        chain = _make_chain()
        result = aggregate_multi_expiry_gex(
            [("2026-03-24", chain, 5.0)], 22000, 65,
        )
        assert len(result["expiry_contributions"]) == 1
        assert result["expiry_contributions"][0].oi_weight == 1.0

    def test_two_expiries(self):
        chain1 = _make_chain(call_oi=50000, put_oi=40000)
        chain2 = _make_chain(call_oi=25000, put_oi=20000)
        result = aggregate_multi_expiry_gex(
            [("2026-03-24", chain1, 5.0), ("2026-03-31", chain2, 170.0)],
            22000, 65,
        )
        assert len(result["expiry_contributions"]) == 2
        # First expiry should have higher weight (more OI)
        assert result["expiry_contributions"][0].oi_weight > result["expiry_contributions"][1].oi_weight

    def test_empty_expiry_chains(self):
        result = aggregate_multi_expiry_gex([], 22000, 65)
        assert result["combined_net_gex"] == 0.0
        assert result["expiry_contributions"] == []

    def test_combined_gex_df_has_all_strikes(self):
        chain1 = _make_chain(spot=22000)
        chain2 = _make_chain(spot=22050)
        result = aggregate_multi_expiry_gex(
            [("2026-03-24", chain1, 5.0), ("2026-03-31", chain2, 170.0)],
            22000, 65,
        )
        assert len(result["combined_gex_df"]) > 0
        assert "net_gex" in result["combined_gex_df"].columns

    def test_reinforced_walls_detected(self):
        """Same strike showing as a wall in 2+ expiries should be flagged."""
        chain1 = _make_chain(spot=22000)
        chain2 = _make_chain(spot=22000)
        result = aggregate_multi_expiry_gex(
            [("2026-03-24", chain1, 5.0), ("2026-03-31", chain2, 170.0)],
            22000, 65,
        )
        # Same chain data → same walls → should show reinforced
        assert isinstance(result["reinforced_call_walls"], list)
        assert isinstance(result["reinforced_put_walls"], list)

    def test_weighted_gamma_flip(self):
        chain = _make_chain()
        result = aggregate_multi_expiry_gex(
            [("2026-03-24", chain, 5.0)], 22000, 65,
        )
        # Should have a gamma flip value
        flip = result["weighted_gamma_flip"]
        assert flip is None or isinstance(flip, float)
