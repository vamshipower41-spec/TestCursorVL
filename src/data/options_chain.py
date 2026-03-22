"""Fetch options chain data from Upstox v2 REST API."""

from __future__ import annotations

import logging
import requests
import pandas as pd

from config.settings import UPSTOX_BASE_URL
from src.auth.upstox_auth import get_auth_headers

logger = logging.getLogger(__name__)


class OptionsChainFetcher:
    """Fetches and parses options chain data from Upstox."""

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.session = requests.Session()
        self.session.headers.update(get_auth_headers(access_token))

    def get_expiry_dates(self, instrument_key: str) -> list[str]:
        """Fetch available expiry dates for an instrument.

        Uses the option contract endpoint to discover expiries.
        """
        resp = self.session.get(
            f"{UPSTOX_BASE_URL}/option/contract",
            params={"instrument_key": instrument_key},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        expiries = sorted({item["expiry"] for item in data if "expiry" in item})
        return expiries

    def fetch_chain(self, instrument_key: str, expiry_date: str) -> tuple[pd.DataFrame, float]:
        """Fetch the full options chain for a given instrument and expiry.

        Returns:
            (chain_df, spot_price) where chain_df has columns:
                strike_price, call_oi, call_gamma, call_delta, call_iv, call_ltp, call_volume,
                put_oi, put_gamma, put_delta, put_iv, put_ltp, put_volume
        """
        resp = self.session.get(
            f"{UPSTOX_BASE_URL}/option/chain",
            params={
                "instrument_key": instrument_key,
                "expiry_date": expiry_date,
            },
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data", [])

        if not data:
            logger.warning("Options chain API returned empty data for %s expiry %s",
                           instrument_key, expiry_date)
            return pd.DataFrame(), 0.0

        rows = []
        spot_price = 0.0

        for strike_data in data:
            strike = strike_data.get("strike_price", 0.0)
            if not strike or strike <= 0:
                continue  # Skip entries with missing/invalid strike price

            row = {"strike_price": float(strike)}

            # Extract call side — safely traverse nested dicts
            call = strike_data.get("call_options") or {}
            call_market = call.get("market_data") or {}
            call_greeks = call.get("option_greeks") or {}
            row["call_oi"] = int(call_market.get("oi") or 0)
            row["call_ltp"] = float(call_market.get("ltp") or call_market.get("last_price") or 0.0)
            row["call_volume"] = int(call_market.get("volume") or 0)
            row["call_gamma"] = float(call_greeks.get("gamma") or 0.0)
            row["call_delta"] = float(call_greeks.get("delta") or 0.0)
            row["call_iv"] = float(call_greeks.get("iv") or call_greeks.get("vega") and 0.0 or 0.0)

            # Extract put side — safely traverse nested dicts
            put = strike_data.get("put_options") or {}
            put_market = put.get("market_data") or {}
            put_greeks = put.get("option_greeks") or {}
            row["put_oi"] = int(put_market.get("oi") or 0)
            row["put_ltp"] = float(put_market.get("ltp") or put_market.get("last_price") or 0.0)
            row["put_volume"] = int(put_market.get("volume") or 0)
            row["put_gamma"] = float(put_greeks.get("gamma") or 0.0)
            row["put_delta"] = float(put_greeks.get("delta") or 0.0)
            row["put_iv"] = float(put_greeks.get("iv") or 0.0)

            # Extract underlying spot price (same for all strikes)
            # Upstox uses "underlying_spot_price" — try alternatives too
            underlying = (
                strike_data.get("underlying_spot_price")
                or strike_data.get("underlying_price")
                or strike_data.get("spot_price")
                or 0.0
            )
            if underlying and float(underlying) > 0:
                spot_price = float(underlying)

            rows.append(row)

        if not rows:
            logger.warning("No valid strikes found in chain data")
            return pd.DataFrame(), 0.0

        chain_df = pd.DataFrame(rows)
        chain_df.sort_values("strike_price", inplace=True)
        chain_df.reset_index(drop=True, inplace=True)

        if spot_price <= 0 and not chain_df.empty:
            # Fallback 1: estimate spot from ATM (where call_ltp ≈ put_ltp)
            if "call_ltp" in chain_df.columns and "put_ltp" in chain_df.columns:
                chain_df["_ltp_diff"] = (chain_df["call_ltp"] - chain_df["put_ltp"]).abs()
                valid = chain_df[chain_df["_ltp_diff"] > 0]
                if not valid.empty:
                    atm_idx = valid["_ltp_diff"].idxmin()
                    spot_price = float(chain_df.loc[atm_idx, "strike_price"])
                chain_df.drop(columns=["_ltp_diff"], inplace=True)

            # Fallback 2: mid-point of strike range
            if spot_price <= 0:
                strikes = chain_df["strike_price"].values
                spot_price = float(strikes[len(strikes) // 2])

            logger.info("Spot price estimated from chain data: %.2f", spot_price)

        return chain_df, spot_price

    def get_nearest_expiry(self, instrument_key: str) -> str:
        """Get the nearest (current week) expiry date."""
        expiries = self.get_expiry_dates(instrument_key)
        if not expiries:
            raise ValueError(f"No expiry dates found for {instrument_key}")
        return expiries[0]

    def fetch_multi_expiry_chains(
        self, instrument_key: str, count: int = 2,
    ) -> list[tuple[str, pd.DataFrame, float]]:
        """Fetch chains for the nearest N expiries.

        Returns list of (expiry_date, chain_df, spot_price) tuples.
        Only includes expiries with non-empty chain data.
        """
        expiries = self.get_expiry_dates(instrument_key)
        results = []
        for exp in expiries[:count]:
            try:
                chain_df, spot = self.fetch_chain(instrument_key, exp)
                if not chain_df.empty:
                    results.append((exp, chain_df, spot))
            except Exception as e:
                logger.warning("Failed to fetch chain for expiry %s: %s", exp, e)
                continue
        return results
