"""Multi-Expiry GEX Aggregation — aggregate gamma across 2-3 nearest expiries.

SpotGamma calculates GEX across the 4 nearest expirations. Single-expiry GEX
misses gamma walls and flip levels from next-week/monthly contracts that ALSO
affect dealer hedging.

Strategy:
  - Fetch chains for the 2-3 nearest expiries
  - Weight each expiry's GEX by its share of total OI (higher OI = more hedging impact)
  - Combine into a single unified GEX profile with levels from all expiries
  - Track which gamma walls are "reinforced" across multiple expiries (stronger walls)
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from dataclasses import dataclass


@dataclass
class ExpiryContribution:
    """How much a single expiry contributes to overall GEX."""
    expiry_date: str
    total_oi: int
    oi_weight: float        # 0-1, share of total OI across all expiries
    net_gex: float          # weighted net GEX contribution
    call_wall: float | None
    put_wall: float | None
    gamma_flip: float | None


def aggregate_multi_expiry_gex(
    expiry_chains: list[tuple[str, pd.DataFrame, float]],
    spot_price: float,
    contract_multiplier: int,
) -> dict:
    """Aggregate GEX profiles across multiple expiries.

    Args:
        expiry_chains: list of (expiry_date, chain_df, time_to_expiry_hours)
        spot_price: current spot price
        contract_multiplier: lot size

    Returns dict with:
        - combined_gex_df: DataFrame with aggregated GEX per strike
        - expiry_contributions: list of ExpiryContribution
        - reinforced_call_walls: strikes that appear as call walls in 2+ expiries
        - reinforced_put_walls: strikes that appear as put walls in 2+ expiries
        - weighted_gamma_flip: OI-weighted gamma flip level
        - combined_net_gex: total net GEX across all expiries
    """
    from src.engine.gex_calculator import compute_gex_profile, find_gamma_flip, compute_gamma_walls

    if not expiry_chains:
        return _empty_result()

    # Step 1: Compute individual GEX profiles and total OI per expiry
    expiry_profiles = []
    total_oi_all = 0

    for expiry_date, chain_df, tte in expiry_chains:
        if chain_df.empty:
            continue

        gex_df = compute_gex_profile(chain_df, spot_price, contract_multiplier)
        oi = int(chain_df[["call_oi", "put_oi"]].sum().sum()) if "call_oi" in chain_df.columns else 0
        total_oi_all += oi

        walls = compute_gamma_walls(gex_df, top_n=3)
        flip = find_gamma_flip(gex_df)

        expiry_profiles.append({
            "expiry_date": expiry_date,
            "gex_df": gex_df,
            "tte": tte,
            "total_oi": oi,
            "walls": walls,
            "flip": flip,
        })

    if not expiry_profiles or total_oi_all == 0:
        return _empty_result()

    # Step 2: Compute OI weights
    for ep in expiry_profiles:
        ep["oi_weight"] = ep["total_oi"] / total_oi_all

    # Step 3: Aggregate GEX across all expiries (weighted by OI share)
    # Collect all unique strikes
    all_strikes = set()
    for ep in expiry_profiles:
        all_strikes.update(ep["gex_df"]["strike_price"].values)

    all_strikes = sorted(all_strikes)
    combined = {"strike_price": all_strikes, "call_gex": [], "put_gex": [], "net_gex": []}

    for strike in all_strikes:
        total_call = 0.0
        total_put = 0.0
        for ep in expiry_profiles:
            row = ep["gex_df"][ep["gex_df"]["strike_price"] == strike]
            if not row.empty:
                w = ep["oi_weight"]
                total_call += float(row["call_gex"].iloc[0]) * w
                total_put += float(row["put_gex"].iloc[0]) * w
        combined["call_gex"].append(total_call)
        combined["put_gex"].append(total_put)
        combined["net_gex"].append(total_call + total_put)

    combined_df = pd.DataFrame(combined)

    # Step 4: Find reinforced walls (appear in 2+ expiries)
    call_wall_strikes: dict[float, int] = {}
    put_wall_strikes: dict[float, int] = {}

    for ep in expiry_profiles:
        for w in ep["walls"].get("call_walls", []):
            s = w["strike"]
            call_wall_strikes[s] = call_wall_strikes.get(s, 0) + 1
        for w in ep["walls"].get("put_walls", []):
            s = w["strike"]
            put_wall_strikes[s] = put_wall_strikes.get(s, 0) + 1

    reinforced_call = [s for s, c in call_wall_strikes.items() if c >= 2]
    reinforced_put = [s for s, c in put_wall_strikes.items() if c >= 2]

    # Step 5: Weighted gamma flip (OI-weighted average of per-expiry flips)
    flip_values = []
    flip_weights = []
    for ep in expiry_profiles:
        if ep["flip"] is not None:
            flip_values.append(ep["flip"])
            flip_weights.append(ep["oi_weight"])

    weighted_flip = None
    if flip_values:
        total_w = sum(flip_weights)
        if total_w > 0:
            weighted_flip = sum(v * w for v, w in zip(flip_values, flip_weights)) / total_w

    # Step 6: Build expiry contributions
    contributions = []
    for ep in expiry_profiles:
        net = float(ep["gex_df"]["net_gex"].sum()) * ep["oi_weight"]
        cw_list = ep["walls"].get("call_walls", [])
        pw_list = ep["walls"].get("put_walls", [])
        cw = cw_list[0]["strike"] if cw_list else None
        pw = pw_list[0]["strike"] if pw_list else None
        contributions.append(ExpiryContribution(
            expiry_date=ep["expiry_date"],
            total_oi=ep["total_oi"],
            oi_weight=round(ep["oi_weight"], 3),
            net_gex=net,
            call_wall=cw,
            put_wall=pw,
            gamma_flip=ep["flip"],
        ))

    combined_net = float(combined_df["net_gex"].sum())

    return {
        "combined_gex_df": combined_df,
        "expiry_contributions": contributions,
        "reinforced_call_walls": sorted(reinforced_call),
        "reinforced_put_walls": sorted(reinforced_put),
        "weighted_gamma_flip": weighted_flip,
        "combined_net_gex": combined_net,
    }


def _empty_result() -> dict:
    return {
        "combined_gex_df": pd.DataFrame(columns=["strike_price", "call_gex", "put_gex", "net_gex"]),
        "expiry_contributions": [],
        "reinforced_call_walls": [],
        "reinforced_put_walls": [],
        "weighted_gamma_flip": None,
        "combined_net_gex": 0.0,
    }
