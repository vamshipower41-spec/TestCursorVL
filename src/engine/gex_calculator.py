"""Core Gamma Exposure (GEX) calculation engine.

Computes dealer gamma exposure from options chain data and identifies
key levels: gamma flip, max gamma strike, gamma walls, zero GEX levels.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from src.data.models import GEXProfile, StrikeGEX
from config.settings import GEX_TOP_N_WALLS
from src.utils.ist import now_ist


def compute_gex_profile(
    chain_df: pd.DataFrame,
    spot_price: float,
    contract_multiplier: int,
) -> pd.DataFrame:
    """Compute Gamma Exposure per strike from options chain data.

    Dealer positioning assumption: dealers are net short options.
    - Call GEX is positive (dealers short calls → long gamma effect stabilizes)
    - Put GEX is negative (dealers short puts → short gamma effect destabilizes)

    Args:
        chain_df: DataFrame with columns: strike_price, call_oi, call_gamma, put_oi, put_gamma
        spot_price: Current underlying spot price
        contract_multiplier: Lot size (65 for Nifty, 20 for Sensex)

    Returns:
        DataFrame with columns: strike_price, call_gex, put_gex, net_gex
    """
    gex = pd.DataFrame({"strike_price": chain_df["strike_price"].values})

    scale = contract_multiplier * spot_price ** 2 * 0.01

    gex["call_gex"] = chain_df["call_oi"].values * chain_df["call_gamma"].values * scale
    gex["put_gex"] = -chain_df["put_oi"].values * chain_df["put_gamma"].values * scale
    gex["net_gex"] = gex["call_gex"] + gex["put_gex"]

    return gex


def find_gamma_flip(gex_profile: pd.DataFrame, spot_price: float) -> float | None:
    """Find the price level where cumulative net GEX crosses zero.

    Above the gamma flip: positive gamma regime (stabilizing, mean-reverting).
    Below the gamma flip: negative gamma regime (destabilizing, trending).

    Uses linear interpolation between strikes for a precise level.
    """
    sorted_gex = gex_profile.sort_values("strike_price").reset_index(drop=True)
    cum_gex = sorted_gex["net_gex"].cumsum().values
    strikes = sorted_gex["strike_price"].values

    # Find sign changes in cumulative GEX
    for i in range(len(cum_gex) - 1):
        if cum_gex[i] * cum_gex[i + 1] < 0:
            # Linear interpolation
            ratio = abs(cum_gex[i]) / (abs(cum_gex[i]) + abs(cum_gex[i + 1]))
            flip_level = strikes[i] + ratio * (strikes[i + 1] - strikes[i])
            return float(flip_level)

    return None


def find_max_gamma_strike(gex_profile: pd.DataFrame) -> float | None:
    """Find the strike with the highest absolute net GEX.

    This strike acts as a magnet — price tends to pin here, especially near expiry.
    """
    if gex_profile.empty:
        return None
    idx = gex_profile["net_gex"].abs().idxmax()
    return float(gex_profile.loc[idx, "strike_price"])


def find_zero_gex_levels(gex_profile: pd.DataFrame) -> list[float]:
    """Find all strike levels where net GEX crosses zero (sign changes).

    These are transition zones between positive and negative gamma regimes.
    """
    sorted_gex = gex_profile.sort_values("strike_price").reset_index(drop=True)
    net_gex = sorted_gex["net_gex"].values
    strikes = sorted_gex["strike_price"].values

    levels = []
    for i in range(len(net_gex) - 1):
        if net_gex[i] * net_gex[i + 1] < 0:
            ratio = abs(net_gex[i]) / (abs(net_gex[i]) + abs(net_gex[i + 1]))
            level = strikes[i] + ratio * (strikes[i + 1] - strikes[i])
            levels.append(float(level))

    return levels


def compute_gamma_walls(
    gex_profile: pd.DataFrame, top_n: int = GEX_TOP_N_WALLS
) -> dict[str, list[dict]]:
    """Find the top call-side (resistance) and put-side (support) gamma walls.

    Call walls: strikes with highest positive net GEX (price repelled downward).
    Put walls: strikes with most negative net GEX (price repelled upward).

    Returns:
        {"call_walls": [{"strike": ..., "gex": ...}, ...],
         "put_walls": [{"strike": ..., "gex": ...}, ...]}
    """
    positive = gex_profile[gex_profile["net_gex"] > 0].nlargest(top_n, "net_gex")
    negative = gex_profile[gex_profile["net_gex"] < 0].nsmallest(top_n, "net_gex")

    call_walls = [
        {"strike": float(row.strike_price), "gex": float(row.net_gex)}
        for row in positive.itertuples()
    ]
    put_walls = [
        {"strike": float(row.strike_price), "gex": float(row.net_gex)}
        for row in negative.itertuples()
    ]

    return {"call_walls": call_walls, "put_walls": put_walls}


def build_gex_profile(
    chain_df: pd.DataFrame,
    spot_price: float,
    contract_multiplier: int,
    instrument: str,
    expiry_date: str,
    timestamp: datetime | None = None,
) -> GEXProfile:
    """Full pipeline: compute GEX and extract all key levels into a GEXProfile."""
    if timestamp is None:
        timestamp = now_ist()

    gex_df = compute_gex_profile(chain_df, spot_price, contract_multiplier)

    gamma_flip = find_gamma_flip(gex_df, spot_price)
    max_gamma = find_max_gamma_strike(gex_df)
    zero_levels = find_zero_gex_levels(gex_df)
    walls = compute_gamma_walls(gex_df)

    strikes = [
        StrikeGEX(
            strike_price=float(row.strike_price),
            call_gex=float(row.call_gex),
            put_gex=float(row.put_gex),
            net_gex=float(row.net_gex),
        )
        for row in gex_df.itertuples()
    ]

    call_wall = walls["call_walls"][0]["strike"] if walls["call_walls"] else None
    put_wall = walls["put_walls"][0]["strike"] if walls["put_walls"] else None

    return GEXProfile(
        timestamp=timestamp,
        instrument=instrument,
        spot_price=spot_price,
        expiry_date=expiry_date,
        contract_multiplier=contract_multiplier,
        strikes=strikes,
        gamma_flip_level=gamma_flip,
        max_gamma_strike=max_gamma,
        zero_gex_levels=zero_levels,
        call_wall=call_wall,
        put_wall=put_wall,
        net_gex_total=float(gex_df["net_gex"].sum()),
    )
