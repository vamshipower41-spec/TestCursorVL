"""Fetch options chain data from Upstox v2 REST API."""

from __future__ import annotations

import requests
import pandas as pd

from config.settings import UPSTOX_BASE_URL
from src.auth.upstox_auth import get_auth_headers


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
        data = resp.json().get("data", [])

        if not data:
            return pd.DataFrame(), 0.0

        rows = []
        spot_price = 0.0

        for strike_data in data:
            row = {"strike_price": strike_data.get("strike_price", 0.0)}

            # Extract call side
            call = strike_data.get("call_options", {})
            call_market = call.get("market_data", {})
            call_greeks = call.get("option_greeks", {})
            row["call_oi"] = call_market.get("oi", 0)
            row["call_ltp"] = call_market.get("ltp", 0.0)
            row["call_volume"] = call_market.get("volume", 0)
            row["call_gamma"] = call_greeks.get("gamma", 0.0)
            row["call_delta"] = call_greeks.get("delta", 0.0)
            row["call_iv"] = call_greeks.get("iv", 0.0)

            # Extract put side
            put = strike_data.get("put_options", {})
            put_market = put.get("market_data", {})
            put_greeks = put.get("option_greeks", {})
            row["put_oi"] = put_market.get("oi", 0)
            row["put_ltp"] = put_market.get("ltp", 0.0)
            row["put_volume"] = put_market.get("volume", 0)
            row["put_gamma"] = put_greeks.get("gamma", 0.0)
            row["put_delta"] = put_greeks.get("delta", 0.0)
            row["put_iv"] = put_greeks.get("iv", 0.0)

            # Extract underlying spot price (same for all strikes)
            underlying = strike_data.get("underlying_spot_price", 0.0)
            if underlying:
                spot_price = underlying

            rows.append(row)

        chain_df = pd.DataFrame(rows)
        chain_df.sort_values("strike_price", inplace=True)
        chain_df.reset_index(drop=True, inplace=True)

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
            except Exception:
                continue
        return results
