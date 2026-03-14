"""Historical candle data fetching from Upstox API."""

from __future__ import annotations

import requests
import pandas as pd

from config.settings import UPSTOX_BASE_URL
from src.auth.upstox_auth import get_auth_headers


class HistoricalDataFetcher:
    """Fetch historical candle data for instruments (including expired options)."""

    def __init__(self, access_token: str):
        self.session = requests.Session()
        self.session.headers.update(get_auth_headers(access_token))

    def fetch_candles(
        self,
        instrument_key: str,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> pd.DataFrame:
        """Fetch historical candle data.

        Args:
            instrument_key: Upstox instrument key
            interval: Candle interval (1minute, 5minute, 15minute, 30minute, day)
            from_date: Start date YYYY-MM-DD
            to_date: End date YYYY-MM-DD

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume, oi
        """
        resp = self.session.get(
            f"{UPSTOX_BASE_URL}/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}",
            timeout=15,
        )
        resp.raise_for_status()
        candles = resp.json().get("data", {}).get("candles", [])

        if not candles:
            return pd.DataFrame()

        df = pd.DataFrame(
            candles,
            columns=["timestamp", "open", "high", "low", "close", "volume", "oi"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.sort_values("timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    def fetch_expired_candles(
        self,
        instrument_key: str,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> pd.DataFrame:
        """Fetch candle data for expired option contracts."""
        resp = self.session.get(
            f"{UPSTOX_BASE_URL}/historical-candle/expired/{instrument_key}/{interval}/{to_date}/{from_date}",
            timeout=15,
        )
        resp.raise_for_status()
        candles = resp.json().get("data", {}).get("candles", [])

        if not candles:
            return pd.DataFrame()

        df = pd.DataFrame(
            candles,
            columns=["timestamp", "open", "high", "low", "close", "volume", "oi"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.sort_values("timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df
