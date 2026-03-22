"""Signal generation engine for expiry day GEX-based trading signals.

Generates five signal types:
1. Gamma Flip — spot crosses the gamma flip level
2. Pin Risk — spot near max gamma strike as expiry approaches
3. Breakout — spot breaches gamma wall in negative gamma zone
4. Vol Crush — net GEX regime shifts from negative to strongly positive
5. Zero GEX Instability — spot sits near a zero-GEX crossing
"""

from __future__ import annotations

from datetime import datetime

from src.data.models import GEXProfile, GEXSignal
from config.settings import (
    PIN_RISK_PROXIMITY_PCT,
    PIN_RISK_MAX_HOURS_TO_EXPIRY,
    BREAKOUT_MIN_MOVE_PCT,
    ZERO_GEX_PROXIMITY_PCT,
)


def generate_signals(
    gex_profile: GEXProfile,
    prev_gex_profile: GEXProfile | None = None,
    time_to_expiry_hours: float = 24.0,
) -> list[GEXSignal]:
    """Generate all applicable signals from the current GEX profile.

    Args:
        gex_profile: Current GEX profile with all computed levels
        prev_gex_profile: Previous GEX profile (for regime change detection)
        time_to_expiry_hours: Hours until expiry (3:30 PM IST)

    Returns:
        List of GEXSignal objects for all triggered signals
    """
    signals: list[GEXSignal] = []

    signals.extend(_check_gamma_flip(gex_profile, prev_gex_profile))
    signals.extend(_check_pin_risk(gex_profile, time_to_expiry_hours))
    signals.extend(_check_breakout(gex_profile))
    signals.extend(_check_vol_crush(gex_profile, prev_gex_profile))
    signals.extend(_check_zero_gex_instability(gex_profile))

    return signals


def _check_gamma_flip(
    current: GEXProfile,
    previous: GEXProfile | None,
) -> list[GEXSignal]:
    """Detect when spot price crosses the gamma flip level.

    If spot was above gamma flip (positive gamma) and now is below (negative gamma),
    that's bearish. Vice versa is bullish.
    """
    if previous is None or current.gamma_flip_level is None:
        return []
    if previous.gamma_flip_level is None:
        return []

    prev_above = previous.spot_price > previous.gamma_flip_level
    curr_above = current.spot_price > current.gamma_flip_level

    if prev_above == curr_above:
        return []  # No crossing

    direction = "bullish" if curr_above else "bearish"

    # Strength: proportional to how significant the GEX change is across the flip
    gex_change = abs(current.net_gex_total - previous.net_gex_total)
    max_gex = max(abs(current.net_gex_total), 1.0)
    strength = min(gex_change / max_gex, 1.0)

    return [
        GEXSignal(
            timestamp=current.timestamp,
            instrument=current.instrument,
            signal_type="gamma_flip",
            level=current.gamma_flip_level,
            strength=strength,
            direction=direction,
            metadata={
                "prev_spot": previous.spot_price,
                "curr_spot": current.spot_price,
                "regime": "positive_gamma" if curr_above else "negative_gamma",
            },
        )
    ]


def _check_pin_risk(
    profile: GEXProfile,
    time_to_expiry_hours: float,
) -> list[GEXSignal]:
    """Detect pin risk when spot is near max gamma strike close to expiry.

    Pin risk increases exponentially as expiry approaches.
    """
    if profile.max_gamma_strike is None:
        return []
    if time_to_expiry_hours > PIN_RISK_MAX_HOURS_TO_EXPIRY:
        return []
    if profile.spot_price <= 0:
        return []

    distance_pct = abs(profile.spot_price - profile.max_gamma_strike) / profile.spot_price

    if distance_pct > PIN_RISK_PROXIMITY_PCT:
        return []

    # Strength increases as: (1) spot gets closer to pin, (2) expiry approaches
    proximity_factor = 1.0 - (distance_pct / PIN_RISK_PROXIMITY_PCT)
    time_factor = 1.0 - (time_to_expiry_hours / PIN_RISK_MAX_HOURS_TO_EXPIRY)
    strength = min(proximity_factor * 0.5 + time_factor * 0.5, 1.0)

    return [
        GEXSignal(
            timestamp=profile.timestamp,
            instrument=profile.instrument,
            signal_type="pin_risk",
            level=profile.max_gamma_strike,
            strength=strength,
            direction=None,
            metadata={
                "distance_pct": distance_pct,
                "time_to_expiry_hours": time_to_expiry_hours,
                "spot": profile.spot_price,
            },
        )
    ]


def _check_breakout(profile: GEXProfile) -> list[GEXSignal]:
    """Detect breakout when spot breaches outermost gamma wall in negative gamma zone.

    Breakouts in negative gamma are self-reinforcing: dealer hedging amplifies the move.
    """
    if profile.net_gex_total >= 0:
        return []  # Only signal in negative gamma regime
    if profile.spot_price <= 0:
        return []

    signals = []

    # Check call wall breach (upside breakout)
    if profile.call_wall is not None and profile.call_wall > 0:
        move_pct = (profile.spot_price - profile.call_wall) / profile.call_wall
        if move_pct > BREAKOUT_MIN_MOVE_PCT:
            strength = min(move_pct / (BREAKOUT_MIN_MOVE_PCT * 3), 1.0)
            signals.append(
                GEXSignal(
                    timestamp=profile.timestamp,
                    instrument=profile.instrument,
                    signal_type="breakout",
                    level=profile.call_wall,
                    strength=strength,
                    direction="bullish",
                    metadata={
                        "wall_type": "call",
                        "move_past_wall_pct": move_pct,
                        "net_gex_total": profile.net_gex_total,
                    },
                )
            )

    # Check put wall breach (downside breakout)
    if profile.put_wall is not None and profile.put_wall > 0:
        move_pct = (profile.put_wall - profile.spot_price) / profile.put_wall
        if move_pct > BREAKOUT_MIN_MOVE_PCT:
            strength = min(move_pct / (BREAKOUT_MIN_MOVE_PCT * 3), 1.0)
            signals.append(
                GEXSignal(
                    timestamp=profile.timestamp,
                    instrument=profile.instrument,
                    signal_type="breakout",
                    level=profile.put_wall,
                    strength=strength,
                    direction="bearish",
                    metadata={
                        "wall_type": "put",
                        "move_past_wall_pct": move_pct,
                        "net_gex_total": profile.net_gex_total,
                    },
                )
            )

    return signals


def _check_vol_crush(
    current: GEXProfile,
    previous: GEXProfile | None,
) -> list[GEXSignal]:
    """Detect volatility crush when net GEX shifts from negative to strongly positive.

    A regime change from negative to positive gamma means dealer hedging flows
    will now dampen volatility instead of amplifying it.
    """
    if previous is None:
        return []

    was_negative = previous.net_gex_total < 0
    is_positive = current.net_gex_total > 0

    if not (was_negative and is_positive):
        return []

    magnitude = abs(current.net_gex_total - previous.net_gex_total)
    scale = max(abs(previous.net_gex_total), abs(current.net_gex_total), 1.0)
    strength = min(magnitude / scale, 1.0)

    return [
        GEXSignal(
            timestamp=current.timestamp,
            instrument=current.instrument,
            signal_type="vol_crush",
            level=current.spot_price,
            strength=strength,
            direction=None,
            metadata={
                "prev_net_gex": previous.net_gex_total,
                "curr_net_gex": current.net_gex_total,
                "regime_change": "negative_to_positive",
            },
        )
    ]


def _check_zero_gex_instability(profile: GEXProfile) -> list[GEXSignal]:
    """Detect when spot sits near a zero-GEX crossing point.

    These are transition zones where gamma regime is ambiguous and
    price can swing in either direction.
    """
    if not profile.zero_gex_levels:
        return []
    if profile.spot_price <= 0:
        return []

    signals = []
    for level in profile.zero_gex_levels:
        distance_pct = abs(profile.spot_price - level) / profile.spot_price
        if distance_pct <= ZERO_GEX_PROXIMITY_PCT:
            strength = 1.0 - (distance_pct / ZERO_GEX_PROXIMITY_PCT)
            signals.append(
                GEXSignal(
                    timestamp=profile.timestamp,
                    instrument=profile.instrument,
                    signal_type="zero_gex_instability",
                    level=level,
                    strength=strength,
                    direction=None,
                    metadata={
                        "distance_pct": distance_pct,
                        "spot": profile.spot_price,
                    },
                )
            )

    return signals
