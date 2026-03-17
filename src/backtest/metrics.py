"""Signal quality metrics for backtesting evaluation."""

from __future__ import annotations

import pandas as pd

from src.backtest.runner import BacktestResult


def compute_trade_details(results: list[BacktestResult]) -> pd.DataFrame:
    """Build a detailed trade log DataFrame for verification.

    Each row = one trade with strike, CE/PE, entry/exit LTP, P&L, etc.
    """
    records = []
    for result in results:
        for trade in result.trades:
            records.append({
                "Expiry": result.expiry_date,
                "Time": trade.entry_time.strftime("%H:%M") if trade.entry_time else "",
                "Signal": trade.signal_type,
                "Direction": trade.direction or "neutral",
                "Strength": trade.signal_strength,
                "Spot Entry": trade.spot_at_entry,
                "Spot Exit": trade.spot_at_exit,
                "Strike": trade.strike_price,
                "CE/PE": trade.option_type,
                "Entry LTP": trade.entry_ltp,
                "Exit LTP": trade.exit_ltp,
                "P&L (pts)": trade.pnl_points,
                "P&L %": trade.pnl_pct,
                "Predicted Level": trade.predicted_level,
                "Hit Target": trade.hit_target,
                "Reason": trade.reason,
            })

    if not records:
        return pd.DataFrame()

    return pd.DataFrame(records)


def compute_trade_summary(results: list[BacktestResult]) -> dict:
    """Aggregate trade-level P&L summary."""
    trades = [t for r in results for t in r.trades]
    if not trades:
        return {
            "total_trades": 0, "winners": 0, "losers": 0,
            "win_rate": 0.0, "total_pnl_pts": 0.0,
            "avg_win_pts": 0.0, "avg_loss_pts": 0.0,
            "best_trade_pts": 0.0, "worst_trade_pts": 0.0,
        }

    winners = [t for t in trades if t.pnl_points > 0]
    losers = [t for t in trades if t.pnl_points <= 0]

    return {
        "total_trades": len(trades),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": len(winners) / max(len(trades), 1),
        "total_pnl_pts": sum(t.pnl_points for t in trades),
        "avg_win_pts": sum(t.pnl_points for t in winners) / max(len(winners), 1),
        "avg_loss_pts": sum(t.pnl_points for t in losers) / max(len(losers), 1),
        "best_trade_pts": max((t.pnl_points for t in trades), default=0.0),
        "worst_trade_pts": min((t.pnl_points for t in trades), default=0.0),
    }


def compute_signal_metrics(results: list[BacktestResult]) -> pd.DataFrame:
    """Compute per-signal-type metrics across all backtest results.

    Returns DataFrame with columns:
        signal_type, total_count, hit_count, hit_rate,
        avg_time_to_hit_min, avg_favorable_pct, avg_adverse_pct, profit_factor
    """
    records = []
    for result in results:
        for outcome in result.outcomes:
            records.append({
                "expiry_date": result.expiry_date,
                "instrument": result.instrument,
                "signal_type": outcome.signal.signal_type,
                "direction": outcome.signal.direction,
                "strength": outcome.signal.strength,
                "hit_target": outcome.hit_target,
                "time_to_hit_min": outcome.time_to_hit_minutes,
                "favorable_pct": outcome.max_favorable_move_pct,
                "adverse_pct": outcome.max_adverse_move_pct,
            })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)

    metrics = df.groupby("signal_type").agg(
        total_count=("hit_target", "count"),
        hit_count=("hit_target", "sum"),
        avg_time_to_hit_min=("time_to_hit_min", "mean"),
        avg_favorable_pct=("favorable_pct", "mean"),
        avg_adverse_pct=("adverse_pct", "mean"),
    ).reset_index()

    metrics["hit_rate"] = metrics["hit_count"] / metrics["total_count"]
    metrics["profit_factor"] = (
        metrics["avg_favorable_pct"] / metrics["avg_adverse_pct"].clip(lower=0.0001)
    )

    return metrics


def metrics_by_time_of_day(results: list[BacktestResult]) -> pd.DataFrame:
    """Break down hit rates by time-of-day bucket (morning/midday/last hour)."""
    records = []
    for result in results:
        for outcome in result.outcomes:
            hour = outcome.signal.timestamp.hour
            if hour < 11:
                bucket = "morning"
            elif hour < 14:
                bucket = "midday"
            else:
                bucket = "last_hour"

            records.append({
                "signal_type": outcome.signal.signal_type,
                "time_bucket": bucket,
                "hit_target": outcome.hit_target,
            })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    return df.groupby(["signal_type", "time_bucket"]).agg(
        count=("hit_target", "count"),
        hits=("hit_target", "sum"),
    ).reset_index().assign(
        hit_rate=lambda x: x["hits"] / x["count"]
    )


def generate_summary(results: list[BacktestResult]) -> dict:
    """Generate a high-level summary of backtest results."""
    total_signals = sum(len(r.signals) for r in results)
    total_hits = sum(o.hit_target for r in results for o in r.outcomes)
    expiry_days = len(results)

    return {
        "expiry_days_tested": expiry_days,
        "total_signals": total_signals,
        "total_hits": total_hits,
        "overall_hit_rate": total_hits / max(total_signals, 1),
        "avg_signals_per_day": total_signals / max(expiry_days, 1),
    }
