"""Backtesting engine — replays historical expiry days and evaluates signals."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from config.instruments import get_instrument
from src.backtest.data_store import HistoricalDataStore
from src.data.models import GEXSignal
from src.engine.gex_calculator import build_gex_profile
from src.engine.greeks import validate_greeks, filter_active_strikes
from src.engine.signal_generator import generate_signals


@dataclass
class SignalOutcome:
    """Evaluation of a single signal's outcome."""

    signal: GEXSignal
    hit_target: bool
    time_to_hit_minutes: float | None = None
    max_favorable_move_pct: float = 0.0
    max_adverse_move_pct: float = 0.0


@dataclass
class BacktestResult:
    """Results of backtesting one expiry day."""

    instrument: str
    expiry_date: str
    signals: list[GEXSignal] = field(default_factory=list)
    outcomes: list[SignalOutcome] = field(default_factory=list)
    price_path: pd.DataFrame = field(default_factory=pd.DataFrame)
    num_snapshots: int = 0


class BacktestRunner:
    """Replay historical expiry days and evaluate signal quality."""

    def __init__(self, data_store: HistoricalDataStore):
        self.data_store = data_store

    def run_expiry_day(self, instrument: str, expiry_date: str) -> BacktestResult:
        """Replay one expiry day.

        1. Load all chain snapshots for the day
        2. Compute GEX profile at each timestamp
        3. Generate signals
        4. Evaluate signal outcomes against subsequent price action
        """
        inst = get_instrument(instrument)
        snapshots = self.data_store.load_expiry_day(instrument, expiry_date)

        if not snapshots:
            return BacktestResult(instrument=instrument, expiry_date=expiry_date)

        result = BacktestResult(
            instrument=instrument,
            expiry_date=expiry_date,
            num_snapshots=len(snapshots),
        )

        # Build price path
        price_records = [
            {"timestamp": ts, "spot_price": spot}
            for ts, _, spot in snapshots
        ]
        result.price_path = pd.DataFrame(price_records)

        prev_profile = None
        all_signals: list[tuple[int, GEXSignal]] = []  # (snapshot_index, signal)

        for i, (timestamp, chain_df, spot_price) in enumerate(snapshots):
            chain_df = validate_greeks(chain_df)
            chain_df = filter_active_strikes(chain_df, spot_price, num_strikes=40)

            profile = build_gex_profile(
                chain_df, spot_price, inst["contract_multiplier"],
                instrument, expiry_date, timestamp,
            )

            # Compute time to expiry (3:30 PM)
            expiry_dt = datetime.strptime(expiry_date, "%Y-%m-%d").replace(hour=15, minute=30)
            tte = max((expiry_dt - timestamp).total_seconds() / 3600.0, 0.0)

            signals = generate_signals(profile, prev_profile, tte)

            for sig in signals:
                all_signals.append((i, sig))
                result.signals.append(sig)

            prev_profile = profile

        # Evaluate outcomes
        for snap_idx, signal in all_signals:
            outcome = self._evaluate_signal(
                signal, snap_idx, snapshots, result.price_path
            )
            result.outcomes.append(outcome)

        return result

    def _evaluate_signal(
        self,
        signal: GEXSignal,
        snap_idx: int,
        snapshots: list,
        price_path: pd.DataFrame,
    ) -> SignalOutcome:
        """Evaluate a signal's outcome against subsequent price action."""
        subsequent_prices = price_path.iloc[snap_idx:]
        if subsequent_prices.empty:
            return SignalOutcome(signal=signal, hit_target=False)

        entry_price = subsequent_prices.iloc[0]["spot_price"]
        hit_target = False
        time_to_hit = None
        max_favorable = 0.0
        max_adverse = 0.0

        for _, row in subsequent_prices.iterrows():
            price = row["spot_price"]
            move_pct = (price - entry_price) / entry_price

            if signal.signal_type == "pin_risk":
                # Pin = price stays within 0.3% of predicted level
                dist = abs(price - signal.level) / signal.level
                if dist <= 0.003:
                    hit_target = True
                    if time_to_hit is None:
                        delta = row["timestamp"] - signal.timestamp
                        time_to_hit = delta.total_seconds() / 60.0

            elif signal.signal_type == "breakout":
                target_move = 0.01  # 1%
                if signal.direction == "bullish" and move_pct >= target_move:
                    hit_target = True
                elif signal.direction == "bearish" and move_pct <= -target_move:
                    hit_target = True
                if hit_target and time_to_hit is None:
                    delta = row["timestamp"] - signal.timestamp
                    time_to_hit = delta.total_seconds() / 60.0

            elif signal.signal_type == "gamma_flip":
                # Check if volatility changed as predicted
                if signal.direction == "bullish" and move_pct > 0.005:
                    hit_target = True
                elif signal.direction == "bearish" and move_pct < -0.005:
                    hit_target = True
                if hit_target and time_to_hit is None:
                    delta = row["timestamp"] - signal.timestamp
                    time_to_hit = delta.total_seconds() / 60.0

            # Track max favorable/adverse moves
            if signal.direction == "bullish":
                max_favorable = max(max_favorable, move_pct)
                max_adverse = min(max_adverse, move_pct)
            elif signal.direction == "bearish":
                max_favorable = max(max_favorable, -move_pct)
                max_adverse = min(max_adverse, -move_pct)
            else:
                max_favorable = max(max_favorable, abs(move_pct))

        return SignalOutcome(
            signal=signal,
            hit_target=hit_target,
            time_to_hit_minutes=time_to_hit,
            max_favorable_move_pct=max_favorable,
            max_adverse_move_pct=abs(max_adverse),
        )

    def run_all(self, instrument: str) -> list[BacktestResult]:
        """Run backtest across all available expiry days for an instrument."""
        expiries = self.data_store.list_available_expiries(instrument)
        return [self.run_expiry_day(instrument, exp) for exp in expiries]
