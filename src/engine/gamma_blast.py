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
from src.engine.blast_filters import apply_all_filters, classify_vix_regime
from config.settings import (
    BLAST_MIN_SCORE,
    BLAST_MAX_SIGNALS_PER_DAY,
    BLAST_MAX_SIGNALS_NORMAL_DAY,
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
    price_history: list[float] | None = None,
    vix_value: float | None = None,
    expiry_date: str = "",
) -> GammaBlast | None:
    """Run all 6 models, apply 10 quality filters, produce blast if score is high enough.

    Args:
        profile: Current GEX profile
        prev_profile: Previous GEX profile (for regime change detection)
        chain_df: Current options chain DataFrame
        prev_chain_df: Previous chain DataFrame (for OI/IV changes)
        time_to_expiry_hours: Hours until 3:30 PM IST
        fired_today: Number of blast signals already fired today
        last_blast_time: Timestamp of last blast signal
        price_history: Recent spot prices for trend detection
        vix_value: Current India VIX value (None if unavailable)
        expiry_date: Expiry date string (YYYY-MM-DD)

    Returns:
        GammaBlast if filtered composite score >= threshold, else None
    """
    # Determine if this is an expiry day (TTE < 7h = within market hours of expiry)
    is_expiry_day = time_to_expiry_hours <= 7.0
    max_signals = BLAST_MAX_SIGNALS_PER_DAY if is_expiry_day else BLAST_MAX_SIGNALS_NORMAL_DAY

    # Guard: max signals per day (adaptive: 4 expiry, 2 normal)
    if fired_today >= max_signals:
        return None

    # Guard: cooldown period (shorter on expiry: 15 min vs 30 min)
    cooldown = BLAST_COOLDOWN_MINUTES if not is_expiry_day else max(BLAST_COOLDOWN_MINUTES // 2, 15)
    if last_blast_time is not None:
        elapsed = (profile.timestamp - last_blast_time).total_seconds() / 60
        if elapsed < cooldown:
            return None

    # Adaptive weights: adjust based on VIX regime
    active_weights = dict(MODEL_WEIGHTS)
    if vix_value is not None and vix_value > 0:
        vix_regime = classify_vix_regime(vix_value)
        if vix_regime["weight_overrides"]:
            active_weights = vix_regime["weight_overrides"]

    # Run all 6 models
    components = []
    direction_votes = {"bullish": 0.0, "bearish": 0.0}

    # 1. GEX Zero-Cross Cascade
    score, direction = _score_gex_zero_cross(profile, prev_profile)
    w = active_weights["gex_zero_cross"]
    components.append(BlastComponent(
        model_name="gex_zero_cross",
        score=score,
        weight=w,
        detail=f"Gamma flip {'crossed' if score > 50 else 'not crossed'}; "
               f"regime={'neg' if profile.net_gex_total < 0 else 'pos'}",
    ))
    if direction:
        direction_votes[direction] += score * w

    # 2. Gamma Wall Breach
    score, direction = _score_gamma_wall_breach(profile, prev_profile)
    w = active_weights["gamma_wall_breach"]
    components.append(BlastComponent(
        model_name="gamma_wall_breach",
        score=score,
        weight=w,
        detail=f"Wall breach score {score:.0f}",
    ))
    if direction:
        direction_votes[direction] += score * w

    # 3. Charm Flow Accelerator
    charm_data = compute_charm_flow(
        chain_df, profile.spot_price, time_to_expiry_hours,
        profile.contract_multiplier,
    )
    score, direction = _score_charm_flow(charm_data, time_to_expiry_hours)
    w = active_weights["charm_flow"]
    components.append(BlastComponent(
        model_name="charm_flow",
        score=score,
        weight=w,
        detail=f"Charm intensity {charm_data['charm_intensity']:.0f}; "
               f"net flow {'bullish' if charm_data['net_charm_flow'] > 0 else 'bearish'}",
    ))
    if direction:
        direction_votes[direction] += score * w

    # 4. Negative Gamma Squeeze
    score, direction = _score_negative_gamma_squeeze(profile, prev_profile)
    w = active_weights["negative_gamma_squeeze"]
    components.append(BlastComponent(
        model_name="negative_gamma_squeeze",
        score=score,
        weight=w,
        detail=f"Net GEX {profile.net_gex_total:,.0f}; "
               f"regime={'NEGATIVE' if profile.net_gex_total < 0 else 'POSITIVE'}",
    ))
    if direction:
        direction_votes[direction] += score * w

    # 5. Pin Break Blast
    score, direction = _score_pin_break(profile, prev_profile)
    w = active_weights["pin_break"]
    components.append(BlastComponent(
        model_name="pin_break",
        score=score,
        weight=w,
        detail=f"Distance from pin: "
               f"{abs(profile.spot_price - (profile.max_gamma_strike or profile.spot_price)) / profile.spot_price:.2%}",
    ))
    if direction:
        direction_votes[direction] += score * w

    # 6. Vanna Squeeze
    vanna_data = compute_vanna_exposure(
        chain_df, prev_chain_df, profile.spot_price, profile.contract_multiplier,
    )
    score, direction = _score_vanna_squeeze(vanna_data)
    w = active_weights["vanna_squeeze"]
    components.append(BlastComponent(
        model_name="vanna_squeeze",
        score=score,
        weight=w,
        detail=f"Vanna intensity {vanna_data['vanna_intensity']:.0f}; "
               f"avg IV change {vanna_data['avg_iv_change']:.2f}",
    ))
    if direction:
        direction_votes[direction] += score * w

    # Compute OI change for confirmation (enrichment, not a scored model)
    oi_data = compute_oi_change(chain_df, prev_chain_df, profile.spot_price)

    # Compute raw composite score
    composite = sum(c.score * c.weight for c in components)

    # OI buildup near ATM boosts confidence; unwinding penalizes
    if oi_data["oi_intensity"] > 30 and oi_data["net_oi_change"] > 0:
        composite = min(composite + oi_data["oi_intensity"] * 0.05, 100.0)
    elif oi_data["oi_intensity"] > 30 and oi_data["net_oi_change"] < 0:
        composite = max(composite - oi_data["oi_intensity"] * 0.05, 0.0)

    # --- Model Confluence Boost (AI Architect) ---
    # Gamma dynamics are NON-LINEAR: when multiple models fire together,
    # the real effect is multiplicative, not additive.
    # e.g., negative gamma squeeze + wall breach = explosive cascade.
    firing_models = [c for c in components if c.score >= 40]
    if len(firing_models) >= 3:
        # 3+ models agree → strong confluence, boost up to +8 pts
        confluence_boost = min((len(firing_models) - 2) * 4.0, 8.0)
        composite = min(composite + confluence_boost, 100.0)
    # Special interaction: negative gamma + wall breach is multiplicative
    neg_gamma_score = next((c.score for c in components if c.model_name == "negative_gamma_squeeze"), 0)
    wall_breach_score = next((c.score for c in components if c.model_name == "gamma_wall_breach"), 0)
    if neg_gamma_score >= 50 and wall_breach_score >= 50:
        # Both firing strongly → dealer hedging amplifies through the wall
        interaction_boost = min((neg_gamma_score + wall_breach_score) * 0.05, 7.0)
        composite = min(composite + interaction_boost, 100.0)

    # --- Direction Conviction Margin (ML Engineer) ---
    # Require meaningful gap between bull/bear votes to avoid ambiguous signals
    if direction_votes["bullish"] == 0 and direction_votes["bearish"] == 0:
        return None

    bull_total = direction_votes["bullish"]
    bear_total = direction_votes["bearish"]
    vote_total = bull_total + bear_total
    vote_margin = abs(bull_total - bear_total) / max(vote_total, 1.0)

    if vote_margin < 0.15:
        # Less than 15% margin — direction is ambiguous, suppress
        return None

    blast_direction = "bullish" if bull_total > bear_total else "bearish"

    # Apply 10 quality filters (trend, VIX, volume, timing, expiry type,
    # liquidity, max pain, PCR, IV skew, volume-direction) — separates 5/10 from 9/10
    filtered_score, filter_details = apply_all_filters(
        raw_score=composite,
        blast_direction=blast_direction,
        profile=profile,
        chain_df=chain_df,
        prev_chain_df=prev_chain_df,
        time_to_expiry_hours=time_to_expiry_hours,
        price_history=price_history or [],
        vix_value=vix_value,
        expiry_date=expiry_date,
    )

    # Apply threshold against filtered score
    # Safety net: on expiry day with zero signals late in the day, relax threshold
    # slightly so we don't end up with zero blasts on a valid expiry day.
    effective_threshold = BLAST_MIN_SCORE
    if is_expiry_day and fired_today == 0 and time_to_expiry_hours < 2.0:
        # Late in the day with no signal — lower threshold by 5 pts
        effective_threshold = BLAST_MIN_SCORE - 5

    if filtered_score < effective_threshold:
        return None

    # Compute entry/SL/target (dynamic by VIX regime)
    entry, sl, target = _compute_levels(profile, blast_direction, vix_value)

    # --- R:R Quality Gate (AI Strategist) ---
    # Suppress signals with poor risk-reward ratio
    risk = abs(entry - sl)
    reward = abs(target - entry)
    if risk > 0:
        rr_ratio = reward / risk
        if rr_ratio < 1.0:
            # R:R below 1:1 — not worth the trade, suppress
            return None

    return GammaBlast(
        timestamp=profile.timestamp,
        instrument=profile.instrument,
        composite_score=round(filtered_score, 1),
        direction=blast_direction,
        entry_level=entry,
        stop_loss=sl,
        target=target,
        time_to_expiry_hours=time_to_expiry_hours,
        components=components,
        metadata={
            "raw_score": round(composite, 1),
            "filtered_score": round(filtered_score, 1),
            "filters_applied": filter_details,
            "direction_votes": direction_votes,
            "vix_value": vix_value,
            "adaptive_weights": active_weights,
            "charm_data": {
                "net_charm_flow": charm_data["net_charm_flow"],
                "charm_intensity": charm_data["charm_intensity"],
            },
            "vanna_data": {
                "net_vanna_flow": vanna_data["net_vanna_flow"],
                "vanna_intensity": vanna_data["vanna_intensity"],
            },
            "oi_data": {
                "net_oi_change": oi_data["net_oi_change"],
                "oi_intensity": oi_data["oi_intensity"],
                "surge_strikes": oi_data["oi_surge_strikes"][:3],
            },
            "confluence": {
                "firing_models": len(firing_models),
                "neg_gamma_wall_interaction": neg_gamma_score >= 50 and wall_breach_score >= 50,
            },
            "direction_margin": round(vote_margin, 3),
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
    profile: GEXProfile, direction: str, vix_value: float | None = None,
) -> tuple[float, float, float]:
    """Compute entry, stop-loss, and target levels for a gamma blast trade.

    Uses gamma walls and profile levels for intelligent placement.
    SL/Target percentages scale dynamically with VIX:
    - Low VIX (<14):  tighter SL (0.3%), tighter target (0.6%) — small moves
    - Normal VIX:     standard SL (0.5%), standard target (0.8%)
    - High VIX (>18): wider SL (0.7%), wider target (1.2%) — bigger swings
    - Extreme (>22):  widest SL (0.9%), widest target (1.5%)
    """
    spot = profile.spot_price
    entry = spot

    # Dynamic SL/Target percentages based on VIX regime
    if vix_value is not None and vix_value > 0:
        if vix_value < 14:
            sl_pct, tgt_pct = 0.003, 0.006
        elif vix_value < 18:
            sl_pct, tgt_pct = 0.005, 0.008
        elif vix_value < 22:
            sl_pct, tgt_pct = 0.007, 0.012
        else:
            sl_pct, tgt_pct = 0.009, 0.015
    else:
        sl_pct, tgt_pct = 0.005, 0.008  # default (normal vol)

    if direction == "bullish":
        # Stop loss: put wall or dynamic % below entry
        if profile.put_wall and profile.put_wall < spot:
            sl = profile.put_wall
        else:
            sl = spot * (1.0 - sl_pct)

        # Target: call wall or dynamic % above entry
        if profile.call_wall and profile.call_wall > spot:
            target = profile.call_wall
        else:
            target = spot * (1.0 + tgt_pct)

    else:  # bearish
        # Stop loss: call wall or dynamic % above entry
        if profile.call_wall and profile.call_wall > spot:
            sl = profile.call_wall
        else:
            sl = spot * (1.0 + sl_pct)

        # Target: put wall or dynamic % below entry
        if profile.put_wall and profile.put_wall < spot:
            target = profile.put_wall
        else:
            target = spot * (1.0 - tgt_pct)

    return round(entry, 2), round(sl, 2), round(target, 2)
