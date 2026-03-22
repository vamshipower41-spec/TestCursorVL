"""Charm & Vanna flow calculator for expiry-day gamma dynamics.

Charm (delta decay): On expiry day, options lose delta rapidly. Dealers who are
delta-hedged must re-hedge as delta melts, creating directional flow.
  - OTM calls lose delta → dealers sell underlying (bearish flow)
  - OTM puts lose delta → dealers buy underlying (bullish flow)
  - Net charm flow = put charm flow - call charm flow

Vanna (delta sensitivity to IV): When IV drops on expiry day (vol crush),
vanna-driven hedging creates directional pressure.
  - Short calls with positive vanna: IV drop → delta drops → dealers buy
  - Short puts with negative vanna: IV drop → delta rises → dealers sell

These are the two dominant Greek flows on expiry day that drive gamma blasts.
"""

from __future__ import annotations

import pandas as pd
import numpy as np


def compute_charm_flow(
    chain_df: pd.DataFrame,
    spot_price: float,
    time_to_expiry_hours: float,
    contract_multiplier: int,
) -> dict:
    """Estimate net charm (delta decay) flow direction and magnitude.

    On expiry day, charm accelerates dramatically. OTM options lose delta
    fastest, forcing dealers to re-hedge aggressively.

    Returns dict with:
        - net_charm_flow: positive = bullish (put charm dominates), negative = bearish
        - charm_intensity: 0-100 score based on magnitude and time proximity
        - call_charm_exposure: total call-side charm pressure
        - put_charm_exposure: total put-side charm pressure
    """
    if time_to_expiry_hours <= 0:
        return {"net_charm_flow": 0, "charm_intensity": 0,
                "call_charm_exposure": 0, "put_charm_exposure": 0}

    # Charm approximation: delta * OI / time_remaining
    # Faster decay for ATM/near-ATM strikes
    df = chain_df.copy()
    df["moneyness"] = (df["strike_price"] - spot_price) / spot_price

    # Time decay factor: accelerates as expiry approaches
    # Charm ~ 1/sqrt(T) for ATM, even faster for near-ATM
    time_factor = 1.0 / max(np.sqrt(time_to_expiry_hours / 24.0), 0.01)

    # Ensure required columns have no NaN before computation
    for col in ["call_delta", "put_delta", "call_oi", "put_oi"]:
        if col in df.columns:
            df[col] = df[col].fillna(0)

    # Call charm: OTM calls (moneyness > 0) lose delta → dealers sell
    call_mask = df["moneyness"] > 0
    df.loc[call_mask, "call_charm"] = (
        df.loc[call_mask, "call_delta"].abs()
        * df.loc[call_mask, "call_oi"]
        * time_factor
        * np.exp(-3 * df.loc[call_mask, "moneyness"].abs())  # decay with distance
    )
    df["call_charm"] = df["call_charm"].fillna(0)

    # Put charm: OTM puts (moneyness < 0) lose delta → dealers buy
    put_mask = df["moneyness"] < 0
    df.loc[put_mask, "put_charm"] = (
        df.loc[put_mask, "put_delta"].abs()
        * df.loc[put_mask, "put_oi"]
        * time_factor
        * np.exp(-3 * df.loc[put_mask, "moneyness"].abs())
    )
    df["put_charm"] = df["put_charm"].fillna(0)

    call_charm_total = float(df["call_charm"].sum()) * contract_multiplier
    put_charm_total = float(df["put_charm"].sum()) * contract_multiplier

    # Net: put charm is bullish (dealers buy), call charm is bearish (dealers sell)
    net_charm = put_charm_total - call_charm_total

    # Intensity: normalize to 0-100
    max_exposure = max(call_charm_total, put_charm_total, 1.0)
    raw_intensity = abs(net_charm) / max_exposure * 100
    # Boost intensity in last 2 hours
    if time_to_expiry_hours < 2:
        raw_intensity *= 1.5
    charm_intensity = min(raw_intensity, 100.0)

    return {
        "net_charm_flow": net_charm,
        "charm_intensity": charm_intensity,
        "call_charm_exposure": call_charm_total,
        "put_charm_exposure": put_charm_total,
    }


def compute_vanna_exposure(
    chain_df: pd.DataFrame,
    prev_chain_df: pd.DataFrame | None,
    spot_price: float,
    contract_multiplier: int,
) -> dict:
    """Estimate vanna-driven hedging flow from IV changes.

    Vanna = d(delta)/d(IV). When IV drops (common on expiry), vanna-exposed
    dealers must re-hedge. For dealer-short positions:
      - Calls: IV drop → delta decreases → dealers buy back hedge (bullish)
      - Puts: IV drop → |delta| decreases → dealers sell hedge (bearish)

    Returns dict with:
        - net_vanna_flow: positive = bullish, negative = bearish
        - vanna_intensity: 0-100 score
        - avg_iv_change: average IV movement
    """
    if prev_chain_df is None:
        return {"net_vanna_flow": 0, "vanna_intensity": 0, "avg_iv_change": 0}

    df = chain_df.copy()
    prev = prev_chain_df.copy()

    # Merge on strike to get IV changes
    merged = df.merge(
        prev[["strike_price", "call_iv", "put_iv"]],
        on="strike_price",
        suffixes=("", "_prev"),
        how="inner",
    )

    if merged.empty:
        return {"net_vanna_flow": 0, "vanna_intensity": 0, "avg_iv_change": 0}

    merged["call_iv_change"] = merged["call_iv"] - merged["call_iv_prev"]
    merged["put_iv_change"] = merged["put_iv"] - merged["put_iv_prev"]

    # Drop rows with NaN IV changes (from missing/zero IVs in either snapshot)
    merged["call_iv_change"] = merged["call_iv_change"].fillna(0.0)
    merged["put_iv_change"] = merged["put_iv_change"].fillna(0.0)

    # Vanna effect: IV drop on calls → bullish (dealers buy)
    # Approximate vanna as delta * OI * iv_change
    merged["call_vanna_flow"] = (
        -merged["call_iv_change"]  # negative IV change = positive flow
        * merged["call_oi"].fillna(0)
        * merged["call_delta"].abs().fillna(0)
    )
    merged["put_vanna_flow"] = (
        merged["put_iv_change"]  # positive IV change on puts = dealers sell
        * merged["put_oi"].fillna(0)
        * merged["put_delta"].abs().fillna(0)
    )

    call_vanna = float(merged["call_vanna_flow"].sum()) * contract_multiplier
    put_vanna = float(merged["put_vanna_flow"].sum()) * contract_multiplier

    net_vanna = call_vanna - put_vanna
    avg_iv_change = float(
        (merged["call_iv_change"].mean() + merged["put_iv_change"].mean()) / 2
    )
    # Guard against NaN from empty mean
    if pd.isna(avg_iv_change):
        avg_iv_change = 0.0

    max_flow = max(abs(call_vanna), abs(put_vanna), 1.0)
    vanna_intensity = min(abs(net_vanna) / max_flow * 100, 100.0)

    return {
        "net_vanna_flow": net_vanna,
        "vanna_intensity": vanna_intensity,
        "avg_iv_change": avg_iv_change,
    }


def compute_oi_change(
    chain_df: pd.DataFrame,
    prev_chain_df: pd.DataFrame | None,
    spot_price: float,
) -> dict:
    """Detect OI buildup/unwinding near spot for gamma blast confirmation.

    Large OI additions near ATM on expiry day = more gamma fuel for a blast.
    OI unwinding = gamma dissipating, less explosive potential.

    Returns dict with:
        - net_oi_change: positive = OI buildup, negative = unwinding
        - oi_surge_strikes: list of strikes with significant OI changes
        - oi_intensity: 0-100 score
    """
    if prev_chain_df is None:
        return {"net_oi_change": 0, "oi_surge_strikes": [], "oi_intensity": 0}

    df = chain_df.copy()
    prev = prev_chain_df.copy()

    merged = df.merge(
        prev[["strike_price", "call_oi", "put_oi"]],
        on="strike_price",
        suffixes=("", "_prev"),
        how="inner",
    )

    if merged.empty:
        return {"net_oi_change": 0, "oi_surge_strikes": [], "oi_intensity": 0}

    merged["call_oi_change"] = (merged["call_oi"].fillna(0) - merged["call_oi_prev"].fillna(0))
    merged["put_oi_change"] = (merged["put_oi"].fillna(0) - merged["put_oi_prev"].fillna(0))
    merged["total_oi_change"] = merged["call_oi_change"] + merged["put_oi_change"]

    # Focus on near-ATM strikes (within 2% of spot)
    atm_mask = ((merged["strike_price"] - spot_price).abs() / spot_price) < 0.02
    atm_changes = merged[atm_mask]

    net_oi_change = int(atm_changes["total_oi_change"].sum()) if not atm_changes.empty else 0

    # Find surge strikes
    surge_strikes = []
    for _, row in merged.iterrows():
        prev_total = max(row.get("call_oi_prev", 0) + row.get("put_oi_prev", 0), 1)
        change_pct = abs(row["total_oi_change"]) / prev_total
        if change_pct > 0.15:  # 15% OI change
            surge_strikes.append({
                "strike": float(row["strike_price"]),
                "change": int(row["total_oi_change"]),
                "change_pct": float(change_pct),
            })

    # Intensity based on near-ATM OI change magnitude
    total_atm_oi = max(
        int(atm_changes[["call_oi", "put_oi"]].sum().sum()) if not atm_changes.empty else 1,
        1,
    )
    oi_intensity = min(abs(net_oi_change) / total_atm_oi * 200, 100.0)

    return {
        "net_oi_change": net_oi_change,
        "oi_surge_strikes": surge_strikes,
        "oi_intensity": oi_intensity,
    }
