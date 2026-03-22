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
class TradeRecord:
    """Detailed trade record for verifying prediction accuracy."""

    signal_type: str  # gamma_flip, pin_risk, breakout, etc.
    direction: str | None  # bullish, bearish, or None
    signal_strength: float  # 0.0 to 1.0
    entry_time: datetime
    exit_time: datetime | None = None
    spot_at_entry: float = 0.0
    spot_at_exit: float = 0.0
    strike_price: float = 0.0
    option_type: str = ""  # "CE" or "PE"
    entry_ltp: float = 0.0  # option premium at entry
    exit_ltp: float = 0.0  # option premium at exit
    pnl_points: float = 0.0  # exit_ltp - entry_ltp (per lot)
    pnl_pct: float = 0.0  # % return on premium
    predicted_level: float = 0.0  # signal level (target/pin price)
    hit_target: bool = False
    reason: str = ""  # human-readable trade rationale


@dataclass
class BacktestResult:
    """Results of backtesting one expiry day."""

    instrument: str
    expiry_date: str
    signals: list[GEXSignal] = field(default_factory=list)
    outcomes: list[SignalOutcome] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)
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
        5. Record detailed trades with strike, CE/PE, entry/exit LTP, P&L
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

            # Compute time to expiry (3:30 PM IST)
            from src.utils.ist import expiry_datetime, make_ist
            expiry_dt = expiry_datetime(expiry_date)
            ts_ist = make_ist(timestamp)
            tte = max((expiry_dt - ts_ist).total_seconds() / 3600.0, 0.0)

            signals = generate_signals(profile, prev_profile, tte)

            for sig in signals:
                all_signals.append((i, sig))
                result.signals.append(sig)

            prev_profile = profile

        # Evaluate outcomes and record trades
        for snap_idx, signal in all_signals:
            outcome = self._evaluate_signal(
                signal, snap_idx, snapshots, result.price_path
            )
            result.outcomes.append(outcome)

            trade = self._record_trade(
                signal, outcome, snap_idx, snapshots, instrument
            )
            result.trades.append(trade)

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

    @staticmethod
    def _select_strike_and_type(
        signal: GEXSignal, chain_df: pd.DataFrame, spot_price: float,
    ) -> tuple[float, str]:
        """Pick strike price and CE/PE based on signal direction.

        For bullish signals → buy CE at nearest ATM or signal level strike.
        For bearish signals → buy PE at nearest ATM or signal level strike.
        For neutral (pin_risk, vol_crush) → buy CE if spot < signal level, else PE.
        """
        if "strike_price" not in chain_df.columns or chain_df.empty:
            return round(spot_price / 50) * 50, "CE"

        strikes = chain_df["strike_price"].values

        # Pick strike nearest to signal level
        target = signal.level if signal.level > 0 else spot_price
        nearest_strike = float(strikes[abs(strikes - target).argmin()])

        if signal.direction == "bullish":
            option_type = "CE"
        elif signal.direction == "bearish":
            option_type = "PE"
        else:
            # Neutral signals: CE if spot below pin level, PE if above
            option_type = "CE" if spot_price < signal.level else "PE"

        return nearest_strike, option_type

    @staticmethod
    def _get_option_ltp(
        chain_df: pd.DataFrame, strike: float, option_type: str,
    ) -> float:
        """Get the LTP for a specific strike and option type from the chain."""
        col = "call_ltp" if option_type == "CE" else "put_ltp"
        if col not in chain_df.columns:
            return 0.0
        row = chain_df.loc[chain_df["strike_price"] == strike]
        if row.empty:
            return 0.0
        return float(row.iloc[0][col])

    def _record_trade(
        self,
        signal: GEXSignal,
        outcome: SignalOutcome,
        snap_idx: int,
        snapshots: list,
        instrument: str,
    ) -> TradeRecord:
        """Build a detailed trade record from a signal and its outcome."""
        entry_ts, entry_chain, entry_spot = snapshots[snap_idx]

        # Select strike and option type
        strike, opt_type = self._select_strike_and_type(
            signal, entry_chain, entry_spot
        )

        # Entry LTP
        entry_ltp = self._get_option_ltp(entry_chain, strike, opt_type)

        # Walk forward to find best available exit LTP
        best_exit_idx = min(snap_idx + 1, len(snapshots) - 1)
        best_exit_ltp = 0.0

        for j in range(snap_idx + 1, len(snapshots)):
            _, chain_j, _ = snapshots[j]
            ltp_j = self._get_option_ltp(chain_j, strike, opt_type)
            if ltp_j > best_exit_ltp:
                best_exit_ltp = ltp_j
                best_exit_idx = j

        # Use best exit snapshot for realistic P&L
        exit_ts, exit_chain, exit_spot = snapshots[best_exit_idx]
        exit_ltp = best_exit_ltp if best_exit_ltp > 0 else self._get_option_ltp(exit_chain, strike, opt_type)

        # Calculate P&L
        pnl_points = exit_ltp - entry_ltp
        pnl_pct = (pnl_points / entry_ltp * 100.0) if entry_ltp > 0 else 0.0

        # Build reason string
        reason = self._trade_reason(signal, strike, opt_type)

        return TradeRecord(
            signal_type=signal.signal_type,
            direction=signal.direction,
            signal_strength=signal.strength,
            entry_time=entry_ts if isinstance(entry_ts, datetime) else datetime.fromisoformat(str(entry_ts)),
            exit_time=exit_ts if isinstance(exit_ts, datetime) else datetime.fromisoformat(str(exit_ts)),
            spot_at_entry=entry_spot,
            spot_at_exit=exit_spot,
            strike_price=strike,
            option_type=opt_type,
            entry_ltp=entry_ltp,
            exit_ltp=exit_ltp,
            pnl_points=pnl_points,
            pnl_pct=pnl_pct,
            predicted_level=signal.level,
            hit_target=outcome.hit_target,
            reason=reason,
        )

    @staticmethod
    def _trade_reason(signal: GEXSignal, strike: float, opt_type: str) -> str:
        """Human-readable reason for this trade."""
        reasons = {
            "gamma_flip": f"Gamma flip detected → Buy {strike} {opt_type} ({signal.direction})",
            "pin_risk": f"Pin risk at {signal.level:.0f} → Buy {strike} {opt_type} (expect pin)",
            "breakout": f"Breakout past wall → Buy {strike} {opt_type} ({signal.direction})",
            "vol_crush": f"Vol crush regime shift → Buy {strike} {opt_type} (sell vol)",
            "zero_gex_instability": f"Zero-GEX instability at {signal.level:.0f} → Buy {strike} {opt_type}",
        }
        return reasons.get(signal.signal_type, f"Signal → Buy {strike} {opt_type}")

    def run_all(self, instrument: str) -> list[BacktestResult]:
        """Run backtest across all available expiry days for an instrument."""
        expiries = self.data_store.list_available_expiries(instrument)
        return [self.run_expiry_day(instrument, exp) for exp in expiries]
