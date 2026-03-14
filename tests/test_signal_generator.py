"""Tests for the signal generator with known scenarios."""

import sys

sys.path.insert(0, ".")

import pytest
from datetime import datetime

from src.data.models import GEXProfile, StrikeGEX, GEXSignal
from src.engine.signal_generator import generate_signals


def make_profile(
    spot=22000,
    gamma_flip=21900,
    max_gamma=22000,
    call_wall=22200,
    put_wall=21800,
    net_gex_total=1000000,
    zero_gex_levels=None,
    timestamp=None,
):
    """Create a GEXProfile for testing."""
    return GEXProfile(
        timestamp=timestamp or datetime(2026, 3, 19, 12, 0, 0),
        instrument="NIFTY",
        spot_price=spot,
        expiry_date="2026-03-19",
        contract_multiplier=65,
        strikes=[
            StrikeGEX(strike_price=21800, call_gex=100, put_gex=-500, net_gex=-400),
            StrikeGEX(strike_price=21900, call_gex=300, put_gex=-300, net_gex=0),
            StrikeGEX(strike_price=22000, call_gex=800, put_gex=-200, net_gex=600),
            StrikeGEX(strike_price=22100, call_gex=400, put_gex=-100, net_gex=300),
            StrikeGEX(strike_price=22200, call_gex=500, put_gex=-50, net_gex=450),
        ],
        gamma_flip_level=gamma_flip,
        max_gamma_strike=max_gamma,
        zero_gex_levels=zero_gex_levels or [],
        call_wall=call_wall,
        put_wall=put_wall,
        net_gex_total=net_gex_total,
    )


class TestGammaFlipSignal:
    def test_no_signal_without_previous(self):
        profile = make_profile()
        signals = generate_signals(profile, None, 6.0)
        flip_signals = [s for s in signals if s.signal_type == "gamma_flip"]
        assert len(flip_signals) == 0

    def test_bullish_flip(self):
        # Spot moves from below gamma flip to above
        prev = make_profile(spot=21850, gamma_flip=21900)
        curr = make_profile(spot=21950, gamma_flip=21900)
        signals = generate_signals(curr, prev, 6.0)
        flip_signals = [s for s in signals if s.signal_type == "gamma_flip"]
        assert len(flip_signals) == 1
        assert flip_signals[0].direction == "bullish"

    def test_bearish_flip(self):
        prev = make_profile(spot=21950, gamma_flip=21900)
        curr = make_profile(spot=21850, gamma_flip=21900)
        signals = generate_signals(curr, prev, 6.0)
        flip_signals = [s for s in signals if s.signal_type == "gamma_flip"]
        assert len(flip_signals) == 1
        assert flip_signals[0].direction == "bearish"

    def test_no_flip_when_same_side(self):
        prev = make_profile(spot=22050, gamma_flip=21900)
        curr = make_profile(spot=22100, gamma_flip=21900)
        signals = generate_signals(curr, prev, 6.0)
        flip_signals = [s for s in signals if s.signal_type == "gamma_flip"]
        assert len(flip_signals) == 0


class TestPinRiskSignal:
    def test_pin_risk_near_expiry(self):
        # Spot very close to max gamma, 1 hour to expiry
        profile = make_profile(spot=22005, max_gamma=22000)
        signals = generate_signals(profile, None, 1.0)
        pin_signals = [s for s in signals if s.signal_type == "pin_risk"]
        assert len(pin_signals) == 1
        assert pin_signals[0].strength > 0.5

    def test_no_pin_risk_far_from_expiry(self):
        profile = make_profile(spot=22005, max_gamma=22000)
        signals = generate_signals(profile, None, 6.0)  # > 4 hours
        pin_signals = [s for s in signals if s.signal_type == "pin_risk"]
        assert len(pin_signals) == 0

    def test_no_pin_risk_when_far_from_strike(self):
        profile = make_profile(spot=22500, max_gamma=22000)  # Too far
        signals = generate_signals(profile, None, 1.0)
        pin_signals = [s for s in signals if s.signal_type == "pin_risk"]
        assert len(pin_signals) == 0


class TestBreakoutSignal:
    def test_bullish_breakout(self):
        # Spot above call wall in negative gamma regime
        profile = make_profile(
            spot=22500, call_wall=22200, net_gex_total=-500000
        )
        signals = generate_signals(profile, None, 3.0)
        bo_signals = [s for s in signals if s.signal_type == "breakout"]
        assert any(s.direction == "bullish" for s in bo_signals)

    def test_no_breakout_in_positive_gamma(self):
        profile = make_profile(
            spot=22500, call_wall=22200, net_gex_total=1000000
        )
        signals = generate_signals(profile, None, 3.0)
        bo_signals = [s for s in signals if s.signal_type == "breakout"]
        assert len(bo_signals) == 0


class TestVolCrushSignal:
    def test_vol_crush_on_regime_change(self):
        prev = make_profile(net_gex_total=-500000)
        curr = make_profile(net_gex_total=800000)
        signals = generate_signals(curr, prev, 3.0)
        vc_signals = [s for s in signals if s.signal_type == "vol_crush"]
        assert len(vc_signals) == 1

    def test_no_vol_crush_same_regime(self):
        prev = make_profile(net_gex_total=300000)
        curr = make_profile(net_gex_total=800000)
        signals = generate_signals(curr, prev, 3.0)
        vc_signals = [s for s in signals if s.signal_type == "vol_crush"]
        assert len(vc_signals) == 0


class TestZeroGEXInstability:
    def test_detects_instability_near_zero_gex(self):
        profile = make_profile(spot=22000, zero_gex_levels=[22005])
        signals = generate_signals(profile, None, 3.0)
        zg_signals = [s for s in signals if s.signal_type == "zero_gex_instability"]
        assert len(zg_signals) == 1

    def test_no_instability_when_far(self):
        profile = make_profile(spot=22000, zero_gex_levels=[22500])
        signals = generate_signals(profile, None, 3.0)
        zg_signals = [s for s in signals if s.signal_type == "zero_gex_instability"]
        assert len(zg_signals) == 0
