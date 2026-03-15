"""Tests for BacktestRunner and data store."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from src.backtest.data_store import HistoricalDataStore
from src.backtest.runner import BacktestRunner, BacktestResult, SignalOutcome
from src.backtest.metrics import compute_signal_metrics, generate_summary


def _make_chain_df(spot: float, num_strikes: int = 10) -> pd.DataFrame:
    """Generate a synthetic options chain around a spot price."""
    strikes = [spot - 250 + i * 50 for i in range(num_strikes)]
    rows = []
    for s in strikes:
        dist = abs(s - spot) / spot
        # More OI at distant strikes, more gamma near ATM
        gamma = max(0.002 - dist * 0.01, 0.0001)
        oi_call = int(50000 * (1 + dist * 5)) if s >= spot else int(20000 * (1 + dist * 2))
        oi_put = int(20000 * (1 + dist * 2)) if s >= spot else int(50000 * (1 + dist * 5))
        rows.append({
            "strike_price": s,
            "call_oi": oi_call, "call_gamma": gamma, "call_delta": 0.5,
            "call_iv": 15.0, "call_ltp": max(spot - s, 1.0), "call_volume": 5000,
            "put_oi": oi_put, "put_gamma": gamma, "put_delta": -0.5,
            "put_iv": 15.0, "put_ltp": max(s - spot, 1.0), "put_volume": 5000,
        })
    return pd.DataFrame(rows)


class TestHistoricalDataStore:
    def test_save_and_load_round_trip(self, tmp_path):
        store = HistoricalDataStore(str(tmp_path))
        chain = _make_chain_df(22000.0)
        ts = datetime(2026, 3, 19, 10, 30)

        path = store.save_snapshot("NIFTY", "2026-03-19", ts, chain, 22000.0)
        assert path.exists()

        loaded = store.load_expiry_day("NIFTY", "2026-03-19")
        assert len(loaded) == 1
        loaded_ts, loaded_chain, loaded_spot = loaded[0]
        assert loaded_spot == 22000.0
        assert len(loaded_chain) == 10
        assert "strike_price" in loaded_chain.columns

    def test_list_instruments_and_expiries(self, tmp_path):
        store = HistoricalDataStore(str(tmp_path))
        chain = _make_chain_df(22000.0)

        store.save_snapshot("NIFTY", "2026-03-19", datetime(2026, 3, 19, 10, 0), chain, 22000.0)
        store.save_snapshot("NIFTY", "2026-03-26", datetime(2026, 3, 26, 10, 0), chain, 22100.0)
        store.save_snapshot("SENSEX", "2026-03-20", datetime(2026, 3, 20, 10, 0), chain, 73000.0)

        assert store.list_instruments() == ["NIFTY", "SENSEX"]
        assert store.list_available_expiries("NIFTY") == ["2026-03-19", "2026-03-26"]

    def test_empty_store(self, tmp_path):
        store = HistoricalDataStore(str(tmp_path / "nonexistent"))
        assert store.list_instruments() == []
        assert store.load_expiry_day("NIFTY", "2026-03-19") == []


class TestBacktestRunner:
    def test_run_expiry_day_with_snapshots(self, tmp_path):
        store = HistoricalDataStore(str(tmp_path))

        # Save multiple snapshots with price moving up
        for hour, spot in [(9, 22000.0), (10, 22050.0), (11, 22100.0), (12, 22080.0)]:
            ts = datetime(2026, 3, 20, hour, 15)
            chain = _make_chain_df(spot, num_strikes=20)
            store.save_snapshot("NIFTY", "2026-03-20", ts, chain, spot)

        runner = BacktestRunner(store)
        result = runner.run_expiry_day("NIFTY", "2026-03-20")

        assert isinstance(result, BacktestResult)
        assert result.instrument == "NIFTY"
        assert result.expiry_date == "2026-03-20"
        assert result.num_snapshots == 4
        assert len(result.price_path) == 4

    def test_run_empty_day(self, tmp_path):
        store = HistoricalDataStore(str(tmp_path))
        runner = BacktestRunner(store)
        result = runner.run_expiry_day("NIFTY", "2026-03-20")

        assert result.num_snapshots == 0
        assert result.signals == []


class TestSignalMetrics:
    def test_compute_metrics_empty(self):
        df = compute_signal_metrics([])
        assert df.empty

    def test_generate_summary_empty(self):
        summary = generate_summary([])
        assert summary["total_signals"] == 0
        assert summary["overall_hit_rate"] == 0.0

    def test_compute_metrics_with_outcomes(self):
        from src.data.models import GEXSignal

        outcomes = []
        signals = []
        for i, (hit, sig_type) in enumerate([
            (True, "breakout"),
            (False, "breakout"),
            (True, "pin_risk"),
            (True, "pin_risk"),
        ]):
            sig = GEXSignal(
                signal_type=sig_type,
                level=22000.0 + i * 50,
                strength=0.7,
                direction="bullish",
                instrument="NIFTY",
                timestamp=datetime(2026, 3, 20, 10, i),
            )
            signals.append(sig)
            outcomes.append(SignalOutcome(
                signal=sig,
                hit_target=hit,
                max_favorable_move_pct=0.01 if hit else 0.002,
                max_adverse_move_pct=0.003,
            ))

        result = BacktestResult(
            instrument="NIFTY",
            expiry_date="2026-03-20",
            signals=signals,
            outcomes=outcomes,
            num_snapshots=4,
        )

        metrics_df = compute_signal_metrics([result])
        assert len(metrics_df) == 2  # breakout and pin_risk

        breakout = metrics_df[metrics_df["signal_type"] == "breakout"].iloc[0]
        assert breakout["total_count"] == 2
        assert breakout["hit_rate"] == 0.5

        pin_risk = metrics_df[metrics_df["signal_type"] == "pin_risk"].iloc[0]
        assert pin_risk["total_count"] == 2
        assert pin_risk["hit_rate"] == 1.0

        summary = generate_summary([result])
        assert summary["total_signals"] == 4
        assert summary["overall_hit_rate"] == 0.75
