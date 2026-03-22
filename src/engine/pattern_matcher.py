"""Historical Pattern Matcher — conditional probability from past expiry days.

Before firing a blast, check: "In past expiry days with similar conditions,
did the predicted move actually happen?"

Features compared:
  1. GEX regime (positive vs negative net GEX)
  2. Spot position relative to gamma flip (above/below, distance %)
  3. VIX level bucket (low/normal/high/extreme)
  4. Time of day bucket (morning/midday/charm zone/settlement)
  5. Trend direction (bullish/bearish/neutral)
  6. Call/Put wall distance from spot

Requires at least 10 past expiry days of data for meaningful probabilities.
Uses the paper_trader log files and historical snapshots for matching.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from src.data.models import GEXProfile


@dataclass
class PatternMatch:
    """A historical pattern that resembles the current setup."""
    date: str
    similarity_score: float  # 0-1
    outcome: str             # "target_hit", "sl_hit", "expired"
    pnl_pct: float
    direction: str
    composite_score: float


@dataclass
class PatternResult:
    """Aggregate result from pattern matching."""
    total_matches: int
    hits: int
    misses: int
    conditional_hit_rate: float  # probability of success given similar pattern
    avg_pnl_pct: float
    confidence: float          # how reliable is this probability (0-1)
    matches: list[PatternMatch]
    recommendation: str        # "boost", "suppress", "neutral"


def compute_pattern_features(
    profile: GEXProfile,
    vix_value: float | None,
    time_to_expiry_hours: float,
    trend: str,
    blast_direction: str,
) -> dict:
    """Extract pattern features from current market state."""
    spot = profile.spot_price

    # 1. GEX regime
    gex_regime = "positive" if profile.net_gex_total > 0 else "negative"

    # 2. Spot vs gamma flip
    if profile.gamma_flip_level:
        flip_dist_pct = (spot - profile.gamma_flip_level) / spot
        flip_position = "above" if flip_dist_pct > 0 else "below"
    else:
        flip_dist_pct = 0.0
        flip_position = "unknown"

    # 3. VIX bucket
    if vix_value is None or vix_value <= 0:
        vix_bucket = "unknown"
    elif vix_value < 14:
        vix_bucket = "low"
    elif vix_value < 18:
        vix_bucket = "normal"
    elif vix_value < 22:
        vix_bucket = "high"
    else:
        vix_bucket = "extreme"

    # 4. Time of day bucket
    if time_to_expiry_hours > 5:
        time_bucket = "morning"
    elif time_to_expiry_hours > 3:
        time_bucket = "midday"
    elif time_to_expiry_hours > 0.5:
        time_bucket = "charm_zone"
    else:
        time_bucket = "settlement"

    # 5. Wall distances
    call_wall_dist = 0.0
    if profile.call_wall:
        call_wall_dist = (profile.call_wall - spot) / spot

    put_wall_dist = 0.0
    if profile.put_wall:
        put_wall_dist = (spot - profile.put_wall) / spot

    return {
        "gex_regime": gex_regime,
        "flip_position": flip_position,
        "flip_dist_pct": round(flip_dist_pct, 4),
        "vix_bucket": vix_bucket,
        "time_bucket": time_bucket,
        "trend": trend,
        "direction": blast_direction,
        "call_wall_dist_pct": round(call_wall_dist, 4),
        "put_wall_dist_pct": round(put_wall_dist, 4),
    }


def match_historical_patterns(
    current_features: dict,
    trade_log_dir: str = "data/paper_trades",
    min_similarity: float = 0.5,
) -> PatternResult:
    """Find historical trades with similar pattern features.

    Compares current features against all closed paper trades.
    Returns conditional probability of success.
    """
    log_dir = Path(trade_log_dir)
    if not log_dir.exists():
        return _empty_pattern_result()

    # Load all closed trades with their features
    closed_trades = []
    for log_file in sorted(log_dir.glob("trades_*.jsonl")):
        for line in log_file.read_text().strip().split("\n"):
            if not line:
                continue
            record = json.loads(line)
            if record.get("_event") == "CLOSE" and record.get("outcome") in ("target_hit", "sl_hit"):
                closed_trades.append(record)

    if len(closed_trades) < 5:
        return _empty_pattern_result()

    # Score each historical trade's similarity to current pattern
    matches = []
    for trade in closed_trades:
        features = trade.get("_pattern_features", {})
        if not features:
            # Old trades without features — use basic matching
            sim = _basic_similarity(current_features, trade)
        else:
            sim = _feature_similarity(current_features, features)

        if sim >= min_similarity:
            matches.append(PatternMatch(
                date=trade.get("entry_time", "")[:10],
                similarity_score=sim,
                outcome=trade["outcome"],
                pnl_pct=trade.get("pnl_pct", 0),
                direction=trade.get("direction", ""),
                composite_score=trade.get("composite_score", 0),
            ))

    if not matches:
        return _empty_pattern_result()

    # Sort by similarity (best matches first)
    matches.sort(key=lambda m: m.similarity_score, reverse=True)

    hits = sum(1 for m in matches if m.outcome == "target_hit")
    misses = sum(1 for m in matches if m.outcome == "sl_hit")
    total = hits + misses

    hit_rate = hits / max(total, 1)
    avg_pnl = sum(m.pnl_pct for m in matches) / len(matches)

    # Confidence based on sample size
    # <10 matches: low confidence; 10-30: medium; >30: high
    confidence = min(total / 30.0, 1.0)

    # Recommendation
    if confidence < 0.3:
        recommendation = "neutral"  # Not enough data
    elif hit_rate > 0.65:
        recommendation = "boost"
    elif hit_rate < 0.35:
        recommendation = "suppress"
    else:
        recommendation = "neutral"

    return PatternResult(
        total_matches=total,
        hits=hits,
        misses=misses,
        conditional_hit_rate=round(hit_rate, 3),
        avg_pnl_pct=round(avg_pnl, 3),
        confidence=round(confidence, 3),
        matches=matches[:10],  # Top 10 most similar
        recommendation=recommendation,
    )


def apply_pattern_adjustment(
    blast_score: float,
    pattern_result: PatternResult,
) -> float:
    """Adjust blast score based on historical pattern matching.

    Only adjusts when confidence is sufficient (enough historical data).
    """
    if pattern_result.confidence < 0.3:
        return blast_score  # Not enough data to adjust

    if pattern_result.recommendation == "boost":
        # Historical patterns show this setup works well
        boost = pattern_result.confidence * 8.0  # Up to +8 points
        return min(blast_score + boost, 100.0)
    elif pattern_result.recommendation == "suppress":
        # Historical patterns show this setup fails often
        penalty = pattern_result.confidence * 15.0  # Up to -15 points
        return max(blast_score - penalty, 0.0)

    return blast_score


def _feature_similarity(current: dict, historical: dict) -> float:
    """Compute similarity between two feature sets (0-1)."""
    score = 0.0
    total_weight = 0.0

    # Binary matches (weighted)
    comparisons = [
        ("gex_regime", 2.0),
        ("flip_position", 1.5),
        ("vix_bucket", 1.5),
        ("time_bucket", 1.0),
        ("trend", 1.0),
        ("direction", 2.0),
    ]

    for key, weight in comparisons:
        total_weight += weight
        if current.get(key) == historical.get(key):
            score += weight

    # Continuous similarity (flip distance)
    total_weight += 1.0
    flip_diff = abs(current.get("flip_dist_pct", 0) - historical.get("flip_dist_pct", 0))
    score += max(0, 1.0 - flip_diff * 100)  # 1% diff = 0 similarity

    return score / total_weight


def _basic_similarity(current: dict, trade: dict) -> float:
    """Basic similarity when historical trade lacks features."""
    score = 0.0

    # Direction match
    if current.get("direction") == trade.get("direction"):
        score += 0.3

    # Same instrument implied by context
    score += 0.2  # base score for being a completed trade

    # Time bucket approximation from entry_time
    entry_time = trade.get("entry_time", "")
    if len(entry_time) > 11:
        hour = int(entry_time[11:13])
        if current.get("time_bucket") == "charm_zone" and hour >= 13:
            score += 0.2
        elif current.get("time_bucket") == "morning" and hour < 11:
            score += 0.2

    return score


def _empty_pattern_result() -> PatternResult:
    return PatternResult(
        total_matches=0, hits=0, misses=0,
        conditional_hit_rate=0.0, avg_pnl_pct=0.0,
        confidence=0.0, matches=[], recommendation="neutral",
    )
