"""Signal quality metrics for backtesting evaluation."""

from __future__ import annotations

import pandas as pd

from src.backtest.runner import BacktestResult


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
