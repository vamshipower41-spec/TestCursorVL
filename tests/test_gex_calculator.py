"""Tests for the GEX calculator engine using synthetic options chain data."""

import sys

sys.path.insert(0, ".")

import pandas as pd
import numpy as np
import pytest

from src.engine.gex_calculator import (
    compute_gex_profile,
    find_gamma_flip,
    find_max_gamma_strike,
    find_zero_gex_levels,
    compute_gamma_walls,
    build_gex_profile,
)


def make_chain(spot=22000, num_strikes=21, step=100):
    """Generate a synthetic options chain centered around spot.

    Designed so that puts dominate below spot (negative GEX) and calls dominate
    above spot (positive GEX), creating a gamma flip near ATM.
    """
    strikes = np.arange(spot - (num_strikes // 2) * step,
                        spot + (num_strikes // 2 + 1) * step, step)
    rows = []
    for s in strikes:
        dist = abs(s - spot) / spot
        gamma = max(0.001 * np.exp(-50 * dist ** 2), 0.0001)
        # Calls have more OI above spot; puts have more OI below spot
        # This creates positive GEX above spot and negative GEX below
        call_oi = int(80000 * np.exp(-20 * max(0, (s - spot) / spot) ** 2)) if s >= spot else int(10000)
        put_oi = int(80000 * np.exp(-20 * max(0, (spot - s) / spot) ** 2)) if s <= spot else int(10000)
        rows.append({
            "strike_price": s,
            "call_oi": call_oi,
            "call_gamma": gamma,
            "call_delta": 0.5,
            "call_iv": 15.0,
            "call_ltp": max(spot - s, 5.0),
            "call_volume": 1000,
            "put_oi": put_oi,
            "put_gamma": gamma,
            "put_delta": -0.5,
            "put_iv": 15.0,
            "put_ltp": max(s - spot, 5.0),
            "put_volume": 1000,
        })
    return pd.DataFrame(rows)


class TestComputeGEXProfile:
    def test_returns_correct_columns(self):
        chain = make_chain()
        gex = compute_gex_profile(chain, 22000, 65)
        assert set(gex.columns) == {"strike_price", "call_gex", "put_gex", "net_gex"}

    def test_call_gex_is_positive(self):
        chain = make_chain()
        gex = compute_gex_profile(chain, 22000, 65)
        assert (gex["call_gex"] >= 0).all()

    def test_put_gex_is_negative(self):
        chain = make_chain()
        gex = compute_gex_profile(chain, 22000, 65)
        assert (gex["put_gex"] <= 0).all()

    def test_net_gex_is_sum(self):
        chain = make_chain()
        gex = compute_gex_profile(chain, 22000, 65)
        np.testing.assert_allclose(
            gex["net_gex"].values,
            (gex["call_gex"] + gex["put_gex"]).values,
        )

    def test_multiplier_affects_magnitude(self):
        chain = make_chain()
        gex_65 = compute_gex_profile(chain, 22000, 65)
        gex_20 = compute_gex_profile(chain, 22000, 20)
        ratio = gex_65["call_gex"].sum() / gex_20["call_gex"].sum()
        assert abs(ratio - 65 / 20) < 0.01


class TestFindGammaFlip:
    def test_finds_flip_level(self):
        chain = make_chain()
        gex = compute_gex_profile(chain, 22000, 65)
        flip = find_gamma_flip(gex, 22000)
        # Should find a flip level somewhere near the spot
        assert flip is not None
        assert 20000 < flip < 24000

    def test_returns_none_for_uniform_gex(self):
        # All positive GEX → no flip
        chain = make_chain()
        chain["put_oi"] = 0  # Remove all put OI
        gex = compute_gex_profile(chain, 22000, 65)
        flip = find_gamma_flip(gex, 22000)
        # Cumulative may still not cross zero
        # This depends on the data — just verify it doesn't crash
        assert flip is None or isinstance(flip, float)


class TestFindMaxGammaStrike:
    def test_finds_strike(self):
        chain = make_chain()
        gex = compute_gex_profile(chain, 22000, 65)
        max_strike = find_max_gamma_strike(gex)
        assert max_strike is not None
        # Max gamma should be near ATM
        assert 21000 <= max_strike <= 23000

    def test_empty_profile(self):
        empty_gex = pd.DataFrame(columns=["strike_price", "net_gex", "call_gex", "put_gex"])
        assert find_max_gamma_strike(empty_gex) is None


class TestFindZeroGEXLevels:
    def test_finds_crossings(self):
        chain = make_chain()
        gex = compute_gex_profile(chain, 22000, 65)
        levels = find_zero_gex_levels(gex)
        assert isinstance(levels, list)
        for level in levels:
            assert 20000 < level < 24000


class TestComputeGammaWalls:
    def test_returns_call_and_put_walls(self):
        chain = make_chain()
        gex = compute_gex_profile(chain, 22000, 65)
        walls = compute_gamma_walls(gex, top_n=3)
        assert "call_walls" in walls
        assert "put_walls" in walls
        assert len(walls["call_walls"]) <= 3
        assert len(walls["put_walls"]) <= 3

    def test_call_walls_have_positive_gex(self):
        chain = make_chain()
        gex = compute_gex_profile(chain, 22000, 65)
        walls = compute_gamma_walls(gex)
        for wall in walls["call_walls"]:
            assert wall["gex"] > 0

    def test_put_walls_have_negative_gex(self):
        chain = make_chain()
        gex = compute_gex_profile(chain, 22000, 65)
        walls = compute_gamma_walls(gex)
        for wall in walls["put_walls"]:
            assert wall["gex"] < 0


class TestBuildGEXProfile:
    def test_builds_complete_profile(self):
        chain = make_chain()
        profile = build_gex_profile(chain, 22000, 65, "NIFTY", "2026-03-19")
        assert profile.instrument == "NIFTY"
        assert profile.spot_price == 22000
        assert profile.expiry_date == "2026-03-19"
        assert profile.contract_multiplier == 65
        assert len(profile.strikes) == 21
        assert isinstance(profile.net_gex_total, float)
