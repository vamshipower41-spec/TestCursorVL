"""Tests for Black-Scholes Greeks computation."""

import sys

sys.path.insert(0, ".")

import math
import numpy as np
import pandas as pd
import pytest

from src.engine.bs_greeks import (
    compute_bs_greeks,
    compute_chain_greeks,
    compute_dealer_charm_flow,
    compute_dealer_vanna_flow,
    StrikeGreeks,
)


class TestComputeBSGreeks:
    def test_atm_call_delta_near_half(self):
        """ATM call delta should be ~0.5 (slightly above due to drift)."""
        g = compute_bs_greeks(S=22000, K=22000, sigma=0.15, tau_years=1/365)
        assert 0.4 < g.delta_call < 0.6

    def test_atm_put_delta_near_neg_half(self):
        g = compute_bs_greeks(S=22000, K=22000, sigma=0.15, tau_years=1/365)
        assert -0.6 < g.delta_put < -0.4

    def test_deep_itm_call_delta_near_one(self):
        g = compute_bs_greeks(S=22000, K=20000, sigma=0.15, tau_years=30/365)
        assert g.delta_call > 0.9

    def test_deep_otm_call_delta_near_zero(self):
        g = compute_bs_greeks(S=22000, K=25000, sigma=0.15, tau_years=30/365)
        assert g.delta_call < 0.1

    def test_gamma_positive(self):
        g = compute_bs_greeks(S=22000, K=22000, sigma=0.15, tau_years=1/365)
        assert g.gamma > 0

    def test_gamma_highest_atm(self):
        """Gamma should be highest ATM."""
        g_atm = compute_bs_greeks(S=22000, K=22000, sigma=0.15, tau_years=5/365)
        g_otm = compute_bs_greeks(S=22000, K=23000, sigma=0.15, tau_years=5/365)
        assert g_atm.gamma > g_otm.gamma

    def test_gamma_increases_near_expiry(self):
        """ATM gamma increases as expiry approaches."""
        g_far = compute_bs_greeks(S=22000, K=22000, sigma=0.15, tau_years=30/365)
        g_near = compute_bs_greeks(S=22000, K=22000, sigma=0.15, tau_years=1/365)
        assert g_near.gamma > g_far.gamma

    def test_vanna_nonzero(self):
        g = compute_bs_greeks(S=22000, K=22000, sigma=0.15, tau_years=5/365)
        assert g.vanna != 0

    def test_charm_nonzero_before_expiry(self):
        g = compute_bs_greeks(S=22000, K=22000, sigma=0.15, tau_years=3/365)
        assert g.charm_call != 0
        assert g.charm_put != 0

    def test_theta_negative_for_calls(self):
        """Time decay should be negative (options lose value)."""
        g = compute_bs_greeks(S=22000, K=22000, sigma=0.15, tau_years=30/365)
        assert g.theta_call < 0

    def test_expired_option(self):
        """Expired options should return step-function delta."""
        g = compute_bs_greeks(S=22000, K=21000, sigma=0.15, tau_years=0)
        assert g.delta_call == 1.0  # ITM call
        assert g.gamma == 0.0

    def test_zero_iv_handled(self):
        g = compute_bs_greeks(S=22000, K=22000, sigma=0.0, tau_years=5/365)
        assert isinstance(g.delta_call, float)

    def test_put_call_delta_parity(self):
        """Call delta - Put delta should approximately equal exp(-q*T)."""
        g = compute_bs_greeks(S=22000, K=22000, sigma=0.15, tau_years=30/365, q=0)
        assert abs((g.delta_call - g.delta_put) - 1.0) < 0.01


class TestComputeChainGreeks:
    def _make_chain(self):
        return pd.DataFrame([
            {"strike_price": 21800, "call_iv": 16.0, "put_iv": 17.0,
             "call_oi": 50000, "put_oi": 40000, "call_delta": 0.6, "put_delta": -0.4,
             "call_gamma": 0.001, "put_gamma": 0.001},
            {"strike_price": 22000, "call_iv": 15.0, "put_iv": 15.5,
             "call_oi": 60000, "put_oi": 55000, "call_delta": 0.5, "put_delta": -0.5,
             "call_gamma": 0.0015, "put_gamma": 0.0015},
            {"strike_price": 22200, "call_iv": 14.0, "put_iv": 15.0,
             "call_oi": 45000, "put_oi": 50000, "call_delta": 0.4, "put_delta": -0.6,
             "call_gamma": 0.001, "put_gamma": 0.001},
        ])

    def test_adds_bs_columns(self):
        chain = self._make_chain()
        result = compute_chain_greeks(chain, 22000, 3.0)
        assert "bs_gamma" in result.columns
        assert "bs_vanna" in result.columns
        assert "bs_charm_call" in result.columns

    def test_bs_gamma_all_positive(self):
        chain = self._make_chain()
        result = compute_chain_greeks(chain, 22000, 3.0)
        assert (result["bs_gamma"] >= 0).all()

    def test_zero_iv_row_handled(self):
        chain = self._make_chain()
        chain.loc[0, "call_iv"] = 0
        chain.loc[0, "put_iv"] = 0
        result = compute_chain_greeks(chain, 22000, 3.0)
        assert result["bs_gamma"].iloc[0] == 0.0


class TestDealerCharmFlow:
    def _make_chain(self):
        return pd.DataFrame([
            {"strike_price": 21800, "call_iv": 16.0, "put_iv": 17.0,
             "call_oi": 50000, "put_oi": 40000, "call_delta": 0.6, "put_delta": -0.4,
             "call_gamma": 0.001, "put_gamma": 0.001},
            {"strike_price": 22000, "call_iv": 15.0, "put_iv": 15.5,
             "call_oi": 60000, "put_oi": 55000, "call_delta": 0.5, "put_delta": -0.5,
             "call_gamma": 0.0015, "put_gamma": 0.0015},
        ])

    def test_returns_required_keys(self):
        chain = self._make_chain()
        result = compute_dealer_charm_flow(chain, 22000, 3.0, 65)
        assert "net_charm_flow" in result
        assert "charm_intensity" in result

    def test_intensity_bounded(self):
        chain = self._make_chain()
        result = compute_dealer_charm_flow(chain, 22000, 1.0, 65)
        assert 0 <= result["charm_intensity"] <= 100


class TestDealerVannaFlow:
    def _make_chain(self):
        return pd.DataFrame([
            {"strike_price": 22000, "call_iv": 15.0, "put_iv": 15.5,
             "call_oi": 60000, "put_oi": 55000, "call_delta": 0.5, "put_delta": -0.5,
             "call_gamma": 0.0015, "put_gamma": 0.0015},
        ])

    def test_no_previous_chain(self):
        chain = self._make_chain()
        result = compute_dealer_vanna_flow(chain, None, 22000, 3.0, 65)
        assert result["net_vanna_flow"] == 0

    def test_iv_drop_detected(self):
        chain = self._make_chain()
        prev = self._make_chain()
        prev["call_iv"] = 20.0
        prev["put_iv"] = 20.5
        result = compute_dealer_vanna_flow(chain, prev, 22000, 3.0, 65)
        assert result["avg_iv_change"] < 0
