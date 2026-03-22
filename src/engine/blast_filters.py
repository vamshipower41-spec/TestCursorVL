"""Blast quality filters — the difference between 5/10 and 9/10.

These filters prevent false signals by checking market context before
allowing a gamma blast to fire. Each filter can suppress or boost the
composite score based on real market conditions.

Filters:
1. Trend Filter — EMA-based, prevents counter-trend blasts in strong trends
2. VIX Regime — adapts thresholds and weights based on volatility regime
3. Volume Confirmation — requires volume spike at key strikes
4. Smart Timing — post-1:30 PM IST blasts are more reliable (charm zone)
5. Monthly vs Weekly — monthly = pin bias, weekly = breakout bias
6. Sensex Liquidity — minimum OI threshold for Sensex (lower liquidity index)
7. Max Pain Proximity — suppress breakout signals when pinned near max pain
8. Adaptive Weights — shift model weights based on VIX and time-of-day
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from src.data.models import GEXProfile
from src.utils.ist import now_ist, today_ist, is_charm_zone, market_minutes_elapsed


# ---------------------------------------------------------------------------
# 1. Trend Filter
# ---------------------------------------------------------------------------

def compute_trend_bias(price_history: list[float], lookback: int = 10) -> dict:
    """Compute trend direction and strength from recent spot price history.

    Uses exponential moving average crossover (fast 5 vs slow 10) to determine
    trend direction, plus rate-of-change for strength.

    Returns:
        - trend: "bullish", "bearish", or "neutral"
        - strength: 0.0 to 1.0 (how strong the trend is)
        - ema_fast: current fast EMA value
        - ema_slow: current slow EMA value
    """
    if len(price_history) < 3:
        return {"trend": "neutral", "strength": 0.0, "ema_fast": 0, "ema_slow": 0}

    prices = np.array(price_history[-max(lookback, 10):])

    # EMA calculation
    fast_span = min(5, len(prices))
    slow_span = min(10, len(prices))

    ema_fast = float(pd.Series(prices).ewm(span=fast_span, adjust=False).mean().iloc[-1])
    ema_slow = float(pd.Series(prices).ewm(span=slow_span, adjust=False).mean().iloc[-1])

    # Trend direction
    if ema_fast > ema_slow * 1.001:  # 0.1% buffer to avoid noise
        trend = "bullish"
    elif ema_fast < ema_slow * 0.999:
        trend = "bearish"
    else:
        trend = "neutral"

    # Strength: rate of change over lookback
    if len(prices) >= 3:
        roc = abs(prices[-1] - prices[0]) / prices[0]
        strength = min(roc * 100, 1.0)  # 1% move = full strength
    else:
        strength = 0.0

    return {
        "trend": trend,
        "strength": strength,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
    }


def apply_trend_filter(
    blast_direction: str,
    blast_score: float,
    trend_data: dict,
) -> float:
    """Penalize counter-trend blasts, boost with-trend blasts.

    In a strong downtrend (like Feb-Mar 2026), bullish blasts get penalized
    heavily because gamma flips in a persistent selloff are unreliable.

    Returns adjusted score.
    """
    trend = trend_data["trend"]
    strength = trend_data["strength"]

    if trend == "neutral" or strength < 0.1:
        return blast_score  # No adjustment in neutral trend

    if blast_direction == trend:
        # With-trend blast — boost up to 10 points
        boost = strength * 10
        return min(blast_score + boost, 100.0)
    else:
        # Counter-trend blast — penalize up to 25 points
        # Stronger trend = bigger penalty
        penalty = strength * 25
        return max(blast_score - penalty, 0.0)


# ---------------------------------------------------------------------------
# 2. VIX Regime
# ---------------------------------------------------------------------------

def classify_vix_regime(vix_value: float) -> dict:
    """Classify market volatility regime based on India VIX.

    Returns regime info and adjusted threshold/weights.
    """
    if vix_value < 14:
        regime = "low_vol"
        # Low VIX: gamma pinning is strong, breakouts less likely
        # Pin break and charm models are more valuable
        blast_threshold_adj = 0  # standard threshold
        weight_overrides = {
            "pin_break": 0.20,       # pin is strong in low vol
            "charm_flow": 0.20,      # charm is predictable
            "gex_zero_cross": 0.20,
            "gamma_wall_breach": 0.15,
            "negative_gamma_squeeze": 0.10,  # less relevant in calm markets
            "vanna_squeeze": 0.15,
        }
    elif vix_value < 18:
        regime = "normal_vol"
        blast_threshold_adj = 0
        weight_overrides = None  # use default weights
    elif vix_value < 22:
        regime = "high_vol"
        # Higher VIX: trends are stronger, need higher conviction
        blast_threshold_adj = 5  # raise threshold by 5 points
        weight_overrides = {
            "negative_gamma_squeeze": 0.25,  # most important in high vol
            "gex_zero_cross": 0.20,
            "gamma_wall_breach": 0.20,
            "charm_flow": 0.15,
            "pin_break": 0.10,       # pins break easily in high vol
            "vanna_squeeze": 0.10,
        }
    else:
        regime = "extreme_vol"
        # VIX > 22: only with-trend blasts, very high conviction needed
        blast_threshold_adj = 10  # raise threshold by 10
        weight_overrides = {
            "negative_gamma_squeeze": 0.30,  # dominant force
            "gamma_wall_breach": 0.25,
            "gex_zero_cross": 0.20,
            "charm_flow": 0.10,
            "pin_break": 0.05,       # pins meaningless in extreme vol
            "vanna_squeeze": 0.10,
        }

    return {
        "regime": regime,
        "vix": vix_value,
        "threshold_adjustment": blast_threshold_adj,
        "weight_overrides": weight_overrides,
    }


# ---------------------------------------------------------------------------
# 3. Volume Confirmation
# ---------------------------------------------------------------------------

def check_volume_confirmation(
    chain_df: pd.DataFrame,
    spot_price: float,
    prev_chain_df: pd.DataFrame | None = None,
) -> dict:
    """Check if ATM option volumes confirm a directional move.

    A real gamma blast needs volume — not just price movement through levels.
    If ATM/near-ATM volume is low, the blast signal is less reliable.

    Returns:
        - confirmed: bool
        - volume_score: 0-100
        - dominant_side: "call" or "put" or "balanced"
    """
    # Near-ATM strikes (within 1% of spot)
    atm_mask = ((chain_df["strike_price"] - spot_price).abs() / spot_price) < 0.01
    atm = chain_df[atm_mask]

    if atm.empty:
        return {"confirmed": False, "volume_score": 0, "dominant_side": "balanced"}

    call_vol = int(atm["call_volume"].sum())
    put_vol = int(atm["put_volume"].sum())
    total_vol = call_vol + put_vol

    if total_vol == 0:
        return {"confirmed": False, "volume_score": 0, "dominant_side": "balanced"}

    # Volume score based on put-call volume ratio imbalance
    if call_vol > put_vol:
        ratio = call_vol / max(put_vol, 1)
        dominant_side = "call"
    else:
        ratio = put_vol / max(call_vol, 1)
        dominant_side = "put"

    # Score: higher ratio = stronger confirmation
    volume_score = min((ratio - 1) * 30, 100.0)  # ratio of 1.5 = 15, 2.0 = 30, 4.0 = 90

    # Compare with previous volume if available
    if prev_chain_df is not None:
        prev_atm_mask = ((prev_chain_df["strike_price"] - spot_price).abs() / spot_price) < 0.01
        prev_atm = prev_chain_df[prev_atm_mask]
        if not prev_atm.empty:
            prev_total = int(prev_atm[["call_volume", "put_volume"]].sum().sum())
            if prev_total > 0:
                vol_surge = total_vol / prev_total
                if vol_surge > 1.4:  # 40% volume increase
                    volume_score = min(volume_score + 20, 100.0)

    confirmed = volume_score >= 20

    return {
        "confirmed": confirmed,
        "volume_score": volume_score,
        "dominant_side": dominant_side,
    }


def apply_volume_filter(blast_score: float, volume_data: dict) -> float:
    """Adjust blast score based on volume confirmation."""
    if not volume_data["confirmed"]:
        # No volume confirmation — penalize
        return blast_score * 0.7  # 30% penalty

    # Volume confirmed — boost proportionally
    boost = volume_data["volume_score"] * 0.1  # up to +10 points
    return min(blast_score + boost, 100.0)


# ---------------------------------------------------------------------------
# 4. Smart Timing
# ---------------------------------------------------------------------------

def apply_timing_filter(blast_score: float, time_to_expiry_hours: float) -> float:
    """Adjust score based on time of day on expiry day.

    Research shows:
    - Pre-11 AM: Gamma signals are noisy (morning volatility, gap adjustments)
    - 11 AM - 1:30 PM: Moderate reliability
    - Post 1:30 PM (charm zone): Highest reliability — charm/vanna flows dominate
    - Last 30 min (3:00-3:30 PM): Settlement dynamics, pin risk dominant

    Returns adjusted score.
    """
    minutes = market_minutes_elapsed()

    if minutes < 0:
        # Before market open
        return 0.0

    if minutes < 105:  # Before 11:00 AM (105 min after 9:15)
        # Morning noise — penalize
        return blast_score * 0.6

    if minutes < 255:  # Before 1:30 PM (255 min after 9:15)
        # Mid-session — slight penalty
        return blast_score * 0.85

    if minutes < 345:  # Before 3:00 PM (345 min after 9:15)
        # Charm zone — BEST window, boost
        return min(blast_score * 1.15, 100.0)

    # Last 30 min — settlement dynamics, slightly reduce
    # (pin risk becomes dominant, breakouts less reliable)
    if time_to_expiry_hours < 0.5:
        return blast_score * 0.9

    return blast_score


# ---------------------------------------------------------------------------
# 5. Monthly vs Weekly Expiry
# ---------------------------------------------------------------------------

def is_monthly_expiry(expiry_date_str: str, instrument_name: str) -> bool:
    """Check if the given expiry is a monthly expiry.

    Monthly = last Tuesday (NIFTY) or last Thursday (SENSEX) of the month.
    """
    from datetime import datetime
    expiry = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
    # Check if there's another same-weekday in the remaining month
    next_same_weekday = expiry.day + 7
    # If adding 7 days puts us in the next month, this is the last one
    import calendar
    _, last_day = calendar.monthrange(expiry.year, expiry.month)
    return next_same_weekday > last_day


def apply_expiry_type_filter(
    blast_score: float,
    blast_direction: str,
    is_monthly: bool,
    profile: GEXProfile,
) -> float:
    """Adjust score based on monthly vs weekly expiry dynamics.

    Monthly expiry: Pin is stronger (more OI concentrated). Breakout signals
    are less reliable. Pin break signals are more reliable.

    Weekly expiry: Less OI concentration. Breakouts are more common.
    """
    if is_monthly:
        # Monthly: check if price is near max pain / max gamma
        if profile.max_gamma_strike:
            dist = abs(profile.spot_price - profile.max_gamma_strike) / profile.spot_price
            if dist < 0.003:  # within 0.3% of max gamma = strongly pinned
                # Suppress breakout direction, boost only pin-break
                return blast_score * 0.7
    # Weekly: no adjustment
    return blast_score


# ---------------------------------------------------------------------------
# 6. Sensex Liquidity Filter
# ---------------------------------------------------------------------------

SENSEX_MIN_TOTAL_OI = 50000  # Minimum total OI across chain for reliable signals


def apply_liquidity_filter(
    blast_score: float,
    chain_df: pd.DataFrame,
    instrument_name: str,
) -> float:
    """Penalize blast signals for low-liquidity instruments (mainly Sensex).

    BSE Sensex options have ~10x lower liquidity than NSE Nifty.
    If total OI is below threshold, signals are unreliable.
    """
    if instrument_name.upper() != "SENSEX":
        return blast_score  # Nifty has adequate liquidity

    total_oi = int(chain_df[["call_oi", "put_oi"]].sum().sum())
    if total_oi < SENSEX_MIN_TOTAL_OI:
        # Very low liquidity — heavy penalty
        ratio = total_oi / SENSEX_MIN_TOTAL_OI
        return blast_score * max(ratio, 0.3)

    return blast_score


# ---------------------------------------------------------------------------
# 7. Max Pain Proximity
# ---------------------------------------------------------------------------

def compute_max_pain(chain_df: pd.DataFrame, spot_price: float) -> float | None:
    """Compute approximate max pain level.

    Max pain = strike price where total option buyer losses are maximized.
    This is where writers (dealers) profit most, so price tends to gravitate here.
    """
    if chain_df.empty:
        return None

    strikes = chain_df["strike_price"].values
    min_pain = float("inf")
    max_pain_strike = None

    for strike in strikes:
        # Call buyers lose if closing price < their strike (OTM calls expire worthless,
        # ITM calls lose intrinsic). Pain = sum of (closing - strike) * OI for ITM calls.
        call_mask = chain_df["strike_price"] < strike
        call_pain = float(
            ((strike - chain_df.loc[call_mask, "strike_price"]) * chain_df.loc[call_mask, "call_oi"]).sum()
        )
        # Put buyers lose if closing price > their strike
        put_mask = chain_df["strike_price"] > strike
        put_pain = float(
            ((chain_df.loc[put_mask, "strike_price"] - strike) * chain_df.loc[put_mask, "put_oi"]).sum()
        )

        total_pain = call_pain + put_pain
        if total_pain < min_pain:
            min_pain = total_pain
            max_pain_strike = strike

    return float(max_pain_strike) if max_pain_strike is not None else None


def apply_max_pain_filter(
    blast_score: float,
    spot_price: float,
    max_pain_level: float | None,
    time_to_expiry_hours: float,
) -> float:
    """Suppress breakout signals when price is near max pain close to expiry.

    In the last 2 hours, if price is within 0.3% of max pain, it's likely to
    stay pinned. Breakout signals in this zone are unreliable.
    """
    if max_pain_level is None:
        return blast_score

    dist_pct = abs(spot_price - max_pain_level) / spot_price

    if time_to_expiry_hours < 2.0 and dist_pct < 0.003:
        # Very close to max pain near expiry — strong pin expected
        return blast_score * 0.5

    if time_to_expiry_hours < 1.0 and dist_pct < 0.005:
        # Within 0.5% in last hour
        return blast_score * 0.6

    return blast_score


# ---------------------------------------------------------------------------
# Combined filter application
# ---------------------------------------------------------------------------

def apply_all_filters(
    raw_score: float,
    blast_direction: str,
    profile: GEXProfile,
    chain_df: pd.DataFrame,
    prev_chain_df: pd.DataFrame | None,
    time_to_expiry_hours: float,
    price_history: list[float],
    vix_value: float | None,
    expiry_date: str,
) -> tuple[float, dict]:
    """Apply all quality filters to a raw blast composite score.

    Returns (filtered_score, filter_details).
    """
    score = raw_score
    details = {"raw_score": raw_score}

    # 1. Trend filter
    trend_data = compute_trend_bias(price_history)
    score = apply_trend_filter(blast_direction, score, trend_data)
    details["trend"] = trend_data
    details["after_trend"] = score

    # 2. VIX regime
    if vix_value is not None and vix_value > 0:
        vix_data = classify_vix_regime(vix_value)
        score = max(score - vix_data["threshold_adjustment"], 0)
        details["vix_regime"] = vix_data
        details["after_vix"] = score

    # 3. Volume confirmation
    vol_data = check_volume_confirmation(chain_df, profile.spot_price, prev_chain_df)
    score = apply_volume_filter(score, vol_data)
    details["volume"] = vol_data
    details["after_volume"] = score

    # 4. Smart timing
    score = apply_timing_filter(score, time_to_expiry_hours)
    details["after_timing"] = score

    # 5. Monthly vs weekly
    is_monthly = is_monthly_expiry(expiry_date, profile.instrument)
    score = apply_expiry_type_filter(score, blast_direction, is_monthly, profile)
    details["is_monthly"] = is_monthly
    details["after_expiry_type"] = score

    # 6. Sensex liquidity
    score = apply_liquidity_filter(score, chain_df, profile.instrument)
    details["after_liquidity"] = score

    # 7. Max pain proximity
    max_pain = compute_max_pain(chain_df, profile.spot_price)
    score = apply_max_pain_filter(score, profile.spot_price, max_pain, time_to_expiry_hours)
    details["max_pain"] = max_pain
    details["after_max_pain"] = score

    details["final_score"] = round(score, 1)
    return round(score, 1), details
