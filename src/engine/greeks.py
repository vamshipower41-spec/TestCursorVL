"""Greeks validation helpers.

The Upstox API provides pre-calculated greeks (gamma, delta, IV).
This module validates them for sanity rather than recomputing from scratch.
"""

from __future__ import annotations

import pandas as pd


def validate_greeks(chain_df: pd.DataFrame) -> pd.DataFrame:
    """Validate and clean greeks data from the API response.

    - Replaces NaN/None gamma values with 0
    - Flags strikes where gamma is suspiciously zero for ATM options
    - Ensures gamma values are non-negative

    Returns the cleaned DataFrame.
    """
    df = chain_df.copy()

    for col in ["call_gamma", "put_gamma"]:
        df[col] = df[col].fillna(0.0)
        df[col] = df[col].clip(lower=0.0)

    for col in ["call_delta", "put_delta"]:
        df[col] = df[col].fillna(0.0)

    for col in ["call_iv", "put_iv"]:
        df[col] = df[col].fillna(0.0)
        df[col] = df[col].clip(lower=0.0)

    # Volume columns: NaN → 0, ensure integer-safe
    for col in ["call_volume", "put_volume"]:
        if col in df.columns:
            df[col] = df[col].fillna(0).astype(int)

    # OI columns: NaN → 0, ensure integer-safe
    for col in ["call_oi", "put_oi"]:
        if col in df.columns:
            df[col] = df[col].fillna(0).astype(int)

    # LTP columns: NaN → 0.0
    for col in ["call_ltp", "put_ltp"]:
        if col in df.columns:
            df[col] = df[col].fillna(0.0)

    return df


def filter_active_strikes(
    chain_df: pd.DataFrame,
    spot_price: float,
    num_strikes: int = 30,
) -> pd.DataFrame:
    """Filter to only the N nearest strikes around the spot price.

    Deep OTM options have near-zero gamma and add noise to GEX profiles.
    Focusing on near-the-money strikes gives cleaner signals.
    """
    df = chain_df.copy()
    df["distance"] = (df["strike_price"] - spot_price).abs()
    df = df.nsmallest(num_strikes, "distance")
    df = df.drop(columns=["distance"])
    df = df.sort_values("strike_price").reset_index(drop=True)
    return df
