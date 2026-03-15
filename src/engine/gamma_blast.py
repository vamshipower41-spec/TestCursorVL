"""Gamma Blast Detection Engine — 6 professional models for expiry-day scalping.

Designed for disciplined scalpers who want 1-2 high-conviction trades per expiry.
Only fires when composite score from multiple confirming models exceeds threshold.

Six Models:
1. GEX Zero-Cross Cascade — spot crosses gamma flip, triggering dealer hedging cascade
2. Gamma Wall Breach — price breaks through call/put wall with velocity
3. Charm Flow Accelerator — expiry-day charm decay creates directional dealer flow
4. Negative Gamma Squeeze — in negative gamma, dealer hedging amplifies moves
5. Pin Break Blast — price breaks away from max gamma pin strike
6. Vanna Squeeze — IV crush + vanna exposure creates directional hedging flow

Each model produces a 0-100 score. The composite weighted score determines
whether a blast signal fires.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from src.data.models import GEXProfile, GammaBlast, BlastComponent
from src.engine.charm_vanna import (
    compute_charm_flow,
    compute_vanna_exposure,
    compute_oi_change,
)
from config.settings import (
    BLAST_MIN_SCORE,
    BLAST_MAX_SIGNALS_PER_DAY,
    CHARM_ACCELERATION_HOURS,
    NEGATIVE_GAMMA_THRESHOLD,
    PIN_BREAK_MIN_MOVE_PCT,
    WALL_BREACH_VELOCITY_MIN,
    VANNA_IV_DROP_MIN,
    BLAST_COOLDOWN_MINUTES,
)


# Model weights — sum to 1.0
MODEL_WEIGHTS = {
    "gex_zero_cross": 0.25,      # Strongest single predictor
    "gamma_wall_breach": 0.20,   # Breakout confirmation
    "charm_flow": 0.15,          # Expiry-day specific
    "negative_gamma_squeeze": 0.15,  # Regime amplification
    "pin_break": 0.15,           # Pin unwind catalyst
    "vanna_squeeze": 0.10,       # IV-driven flow
}


def detect_gamma_blast(
    profile: GEXProfile,
    prev_profile: GEXProfile | None,
    chain_df: pd.DataFrame,
    prev_chain_df: pd.DataFrame | None,
    time_to_expiry_hours: float,
    fired_today: int = 0,
    last_blast_time: datetime | None = None,
) -> GammaBlast | None:
    """Run all 6 models and produce a blast signal if composite score is high enough.

    Args:
        profile: Current GEX profile
        prev_profile: Previous GEX profile (for regime change detection)
        chain_df: Current options chain DataFrame
        prev_chain_df: Previous chain DataFrame (for OI/IV changes)
        time_to_expiry_hours: Hours until 3:30 PM IST
        fired_today: Number of blast signals already fired today
        last_blast_time: Timestamp of last blast signal

    Returns:
        GammaBlast if composite score >= threshold, else None
    """
    # Guard: max signals per day
    if fired_today >= BLAST_MAX_SIGNALS_PER_DAY:
        return None

    # Guard: cooldown period
    if last_blast_time is not None:
        elapsed = (profile.timestamp - last_blast_time).total_seconds() / 60
        if elapsed < BLAST_COOLDOWN_MINUTES:
            return None

    # Guard: only on expiry day (TTE < 7 hours means within market hours of expiry)
    if time_to_expiry_hours > 7.0:
        return None

    # Run all 6 models
    components = []
    direction_votes = {"bullish": 0.0, "bearish": 0.0}

    # 1. GEX Zero-Cross Cascade
    score, direction = _score_gex_zero_cross(profile, prev_profile)
    components.append(BlastComponent(
        model_name="gex_zero_cross",
        score=score,
        weight=MODEL_WEIGHTS["gex_zero_cross"],
        detail=f"Gamma flip {'crossed' if score > 50 else 'not crossed'}; "
               f"regime={'neg' if profile.net_gex_total < 0 else 'pos'}",
    ))
    if direction:
        direction_votes[direction] += score * MODEL_WEIGHTS["gex_zero_cross"]

    # 2. Gamma Wall Breach
    score, direction = _score_gamma_wall_breach(profile, prev_profile)
    components.append(BlastComponent(
        model_name="gamma_wall_breach",
        score=score,
        weight=MODEL_WEIGHTS["gamma_wall_breach"],
        detail=f"Wall breach score {score:.0f}",
    ))
    if direction:
        direction_votes[direction] += score * MODEL_WEIGHTS["gamma_wall_breach"]

    # 3. Charm Flow Accelerator
    charm_data = compute_charm_flow(
        chain_df, profile.spot_price, time_to_expiry_hours,
        profile.contract_multiplier,
    )
    score, direction = _score_charm_flow(charm_data, time_to_expiry_hours)
    components.append(BlastComponent(
        model_name="charm_flow",
        score=score,
        weight=MODEL_WEIGHTS["charm_flow"],
        detail=f"Charm intensity {charm_data['charm_intensity']:.0f}; "
               f"net flow {'bullish' if charm_data['net_charm_flow'] > 0 else 'bearish'}",
    ))
    if direction:
        direction_votes[direction] += score * MODEL_WEIGHTS["charm_flow"]

    # 4. Negative Gamma Squeeze
    score, direction = _score_negative_gamma_squeeze(profile, prev_profile)
    components.append(BlastComponent(
        model_name="negative_gamma_squeeze",
        score=score,
        weight=MODEL_WEIGHTS["negative_gamma_squeeze"],
        detail=f"Net GEX {profile.net_gex_total:,.0f}; "
               f"regime={'NEGATIVE' if profile.net_gex_total < 0 else 'POSITIVE'}",
    ))
    if direction:
        direction_votes[direction] += score * MODEL_WEIGHTS["negative_gamma_squeeze"]

    # 5. Pin Break Blast
    score, direction = _score_pin_break(profile, prev_profile)
    components.append(BlastComponent(
        model_name="pin_break",
        score=score,
        weight=MODEL_WEIGHTS["pin_break"],
        detail=f"Distance from pin: "
               f"{abs(profile.spot_price - (profile.max_gamma_strike or profile.spot_price)) / profile.spot_price:.2%}",
    ))
    if direction:
        direction_votes[direction] += score * MODEL_WEIGHTS["pin_break"]

    # 6. Vanna Squeeze
    vanna_data = compute_vanna_exposure(
        chain_df, prev_chain_df, profile.spot_price, profile.contract_multiplier,
    )
    score, direction = _score_vanna_squeeze(vanna_data)
    components.append(BlastComponent(
        model_name="vanna_squeeze",
        score=score,
        weight=MODEL_WEIGHTS["vanna_squeeze"],
        detail=f"Vanna intensity {vanna_data['vanna_intensity']:.0f}; "
               f"avg IV change {vanna_data['avg_iv_change']:.2f}",
    ))
    if direction:
        direction_votes[direction] += score * MODEL_WEIGHTS["vanna_squeeze"]

    # Compute composite score
    composite = sum(c.score * c.weight for c in components)

    # Determine direction
    if direction_votes["bullish"] == 0 and direction_votes["bearish"] == 0:
        return None
    blast_direction = (
        "bullish" if direction_votes["bullish"] >= direction_votes["bearish"]
        else "bearish"
    )

    # Apply threshold
    if composite < BLAST_MIN_SCORE:
        return None

    # Compute entry/SL/target
    entry, sl, target = _compute_levels(profile, blast_direction)

    return GammaBlast(
        timestamp=profile.timestamp,
        instrument=profile.instrument,
        composite_score=round(composite, 1),
        direction=blast_direction,
        entry_level=entry,
        stop_loss=sl,
        target=target,
        time_to_expiry_hours=time_to_expiry_hours,
        components=components,
        metadata={
            "direction_votes": direction_votes,
            "charm_data": {
                "net_charm_flow": charm_data["net_charm_flow"],
                "charm_intensity": charm_data["charm_intensity"],
            },
            "vanna_data": {
                "net_vanna_flow": vanna_data["net_vanna_flow"],
                "vanna_intensity": vanna_data["vanna_intensity"],
            },
        },
    )


# ---------------------------------------------------------------------------
# Individual model scorers
# ---------------------------------------------------------------------------


def _score_gex_zero_cross(
    current: GEXProfile, previous: GEXProfile | None,
) -> tuple[float, str | None]:
    """Model 1: GEX Zero-Cross Cascade.

    When spot crosses the gamma flip level, dealers must aggressively re-hedge.
    In negative gamma: they sell into drops / buy into rallies (amplifying).
    The crossing itself is the catalyst for a gamma blast.
    """
    if previous is None or current.gamma_flip_level is None:
        return 0.0, None
    if previous.gamma_flip_level is None:
        return 0.0, None

    prev_above = previous.spot_price > previous.gamma_flip_level
    curr_above = current.spot_price > current.gamma_flip_level

    if prev_above == curr_above:
        # No crossing, but score proximity to flip
        if current.gamma_flip_level:
            dist = abs(current.spot_price - current.gamma_flip_level) / current.spot_price
            if dist < 0.002:  # within 0.2% — imminent crossing
                direction = "bullish" if current.spot_price < current.gamma_flip_level else "bearish"
                return 40.0, direction
        return 0.0, None

    # Crossing detected — high score
    direction = "bullish" if curr_above else "bearish"

    # Boost score if entering negative gamma (more explosive)
    base_score = 80.0
    if not curr_above:  # entering negative gamma
        base_score = 90.0

    # Scale by GEX magnitude change
    gex_shift = abs(current.net_gex_total - previous.net_gex_total)
    max_gex = max(abs(current.net_gex_total), abs(previous.net_gex_total), 1.0)
    magnitude_boost = min(gex_shift / max_gex * 20, 10.0)

    return min(base_score + magnitude_boost, 100.0), direction


def _score_gamma_wall_breach(
    current: GEXProfile, previous: GEXProfile | None,
) -> tuple[float, str | None]:
    """Model 2: Gamma Wall Breach.

    When price breaks through a major gamma wall, the concentrated OI at that
    strike triggers a cascade of dealer delta-hedging. Combined with negative
    gamma, this creates explosive directional moves.
    """
    if previous is None:
        return 0.0, None

    score = 0.0
    direction = None

    # Check call wall breach (upside)
    if current.call_wall is not None and previous.call_wall is not None:
        prev_below_wall = previous.spot_price < previous.call_wall
        curr_above_wall = current.spot_price > current.call_wall

        if prev_below_wall and curr_above_wall:
            # Price broke through call wall
            move_pct = (current.spot_price - current.call_wall) / current.spot_price
            # Velocity check: how fast did it move
            dt_minutes = max(
                (current.timestamp - previous.timestamp).total_seconds() / 60, 1.0,
            )
            velocity = abs(current.spot_price - previous.spot_price) / dt_minutes

            score = 70.0
            if velocity > WALL_BREACH_VELOCITY_MIN:
                score += min(velocity / WALL_BREACH_VELOCITY_MIN * 10, 20.0)
            if move_pct > 0.005:  # 0.5% past wall
                score += 10.0
            direction = "bullish"

    # Check put wall breach (downside)
    if current.put_wall is not None and previous.put_wall is not None:
        prev_above_wall = previous.spot_price > previous.put_wall
        curr_below_wall = current.spot_price < current.put_wall

        if prev_above_wall and curr_below_wall:
            move_pct = (current.put_wall - current.spot_price) / current.spot_price
            dt_minutes = max(
                (current.timestamp - previous.timestamp).total_seconds() / 60, 1.0,
            )
            velocity = abs(current.spot_price - previous.spot_price) / dt_minutes

            put_score = 70.0
            if velocity > WALL_BREACH_VELOCITY_MIN:
                put_score += min(velocity / WALL_BREACH_VELOCITY_MIN * 10, 20.0)
            if move_pct > 0.005:
                put_score += 10.0

            if put_score > score:
                score = put_score
                direction = "bearish"

    return min(score, 100.0), direction


def _score_charm_flow(
    charm_data: dict, time_to_expiry_hours: float,
) -> tuple[float, str | None]:
    """Model 3: Charm Flow Accelerator.

    On expiry day, charm (delta decay) accelerates dramatically in the last
    2-3 hours. OTM options lose delta rapidly, forcing dealers to re-hedge.
    This creates a predictable directional flow — the side with more OI
    dominance drives the net charm flow direction.
    """
    intensity = charm_data["charm_intensity"]
    net_flow = charm_data["net_charm_flow"]

    if intensity < 20:
        return 0.0, None

    # Charm is most relevant in the charm acceleration zone
    if time_to_expiry_hours > CHARM_ACCELERATION_HOURS:
        # Before acceleration zone, score is reduced
        score = intensity * 0.3
    else:
        # In acceleration zone, charm is highly predictive
        time_boost = 1.0 + (1.0 - time_to_expiry_hours / CHARM_ACCELERATION_HOURS)
        score = min(intensity * time_boost, 100.0)

    direction = "bullish" if net_flow > 0 else "bearish"
    return score, direction


def _score_negative_gamma_squeeze(
    current: GEXProfile, previous: GEXProfile | None,
) -> tuple[float, str | None]:
    """Model 4: Negative Gamma Squeeze.

    In negative gamma territory, dealer hedging AMPLIFIES moves:
    - Price drops → dealers sell (short delta) → more selling → cascade down
    - Price rises → dealers buy (long delta) → more buying → cascade up

    The deeper into negative gamma, the more explosive the potential move.
    Combined with a price trigger (OI shift, wall breach), this creates blasts.
    """
    if current.net_gex_total >= 0:
        return 0.0, None  # Only relevant in negative gamma

    # Normalize: how deep into negative gamma
    if previous is not None and previous.net_gex_total != 0:
        gex_ratio = current.net_gex_total / abs(previous.net_gex_total)
    else:
        gex_ratio = -1.0 if current.net_gex_total < 0 else 0

    # Score based on depth of negative gamma
    if gex_ratio > NEGATIVE_GAMMA_THRESHOLD:
        # Mildly negative — low score
        score = 30.0
    else:
        # Deeply negative — high score
        depth = abs(gex_ratio - NEGATIVE_GAMMA_THRESHOLD)
        score = min(60.0 + depth * 40, 100.0)

    # Direction: follow the recent price movement (amplification)
    if previous is not None:
        direction = "bullish" if current.spot_price > previous.spot_price else "bearish"
    else:
        direction = None

    return score, direction


def _score_pin_break(
    current: GEXProfile, previous: GEXProfile | None,
) -> tuple[float, str | None]:
    """Model 5: Pin Break Blast.

    Near expiry, price tends to pin at max gamma strike due to dealer hedging.
    When the pin BREAKS (price moves away from max gamma), all the hedging
    that was keeping price pinned unwinds at once → explosive move.
    """
    if current.max_gamma_strike is None:
        return 0.0, None
    if previous is None or previous.max_gamma_strike is None:
        return 0.0, None

    # Was price pinned before?
    prev_dist = abs(previous.spot_price - previous.max_gamma_strike) / previous.spot_price
    curr_dist = abs(current.spot_price - current.max_gamma_strike) / current.spot_price

    # Pin break = was close, now moving away
    if prev_dist > PIN_BREAK_MIN_MOVE_PCT:
        return 0.0, None  # Wasn't pinned

    if curr_dist < PIN_BREAK_MIN_MOVE_PCT:
        return 0.0, None  # Still pinned

    # Break detected
    break_magnitude = curr_dist / PIN_BREAK_MIN_MOVE_PCT
    score = min(50.0 + break_magnitude * 15, 100.0)

    direction = (
        "bullish" if current.spot_price > current.max_gamma_strike
        else "bearish"
    )

    return score, direction


def _score_vanna_squeeze(vanna_data: dict) -> tuple[float, str | None]:
    """Model 6: Vanna Squeeze.

    When IV drops on expiry day (vol crush), vanna-exposed dealers must
    re-hedge their delta. Large IV drops create one-sided hedging flow.

    Most powerful when:
    - IV is dropping sharply (vol crush)
    - Combined with negative gamma (amplification)
    """
    intensity = vanna_data["vanna_intensity"]
    avg_iv_change = vanna_data["avg_iv_change"]

    if abs(avg_iv_change) < VANNA_IV_DROP_MIN:
        # IV not moving enough to trigger vanna
        if intensity > 30:
            return intensity * 0.3, None
        return 0.0, None

    # IV dropping = vol crush = vanna squeeze in play
    score = min(intensity, 100.0)

    net_flow = vanna_data["net_vanna_flow"]
    direction = "bullish" if net_flow > 0 else "bearish"

    return score, direction


# ---------------------------------------------------------------------------
# Level computation (entry / SL / target)
# ---------------------------------------------------------------------------


def _compute_levels(
    profile: GEXProfile, direction: str,
) -> tuple[float, float, float]:
    """Compute entry, stop-loss, and target levels for a gamma blast trade.

    Uses gamma walls and profile levels for intelligent placement:
    - Entry: current spot
    - SL: nearest opposing gamma wall (natural support/resistance)
    - Target: next gamma wall in blast direction
    """
    spot = profile.spot_price
    entry = spot

    if direction == "bullish":
        # Stop loss: below put wall or 0.5% below entry
        if profile.put_wall and profile.put_wall < spot:
            sl = profile.put_wall
        else:
            sl = spot * 0.995

        # Target: call wall or 0.8% above entry
        if profile.call_wall and profile.call_wall > spot:
            target = profile.call_wall
        else:
            target = spot * 1.008

    else:  # bearish
        # Stop loss: above call wall or 0.5% above entry
        if profile.call_wall and profile.call_wall > spot:
            sl = profile.call_wall
        else:
            sl = spot * 1.005

        # Target: put wall or 0.8% below entry
        if profile.put_wall and profile.put_wall < spot:
            target = profile.put_wall
        else:
            target = spot * 0.992

    return round(entry, 2), round(sl, 2), round(target, 2)
