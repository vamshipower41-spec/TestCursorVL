"""Historical options chain snapshot storage using Parquet files.

Stores chain snapshots partitioned by instrument/date/time:
    data/historical/NIFTY/2026-03-19/chain_0915.parquet
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pandas as pd


class HistoricalDataStore:
    """Manage Parquet-based historical options chain snapshots."""

    def __init__(self, base_path: str = "data/historical"):
        self.base_path = Path(base_path)

    def save_snapshot(
        self,
        instrument: str,
        expiry_date: str,
        timestamp: datetime,
        chain_df: pd.DataFrame,
        spot_price: float,
    ) -> Path:
        """Save a chain snapshot as a Parquet file.

        Args:
            instrument: "NIFTY" or "SENSEX"
            expiry_date: Expiry date string YYYY-MM-DD
            timestamp: Time of the snapshot
            chain_df: Full options chain DataFrame
            spot_price: Underlying spot price at snapshot time

        Returns:
            Path to the saved file
        """
        dir_path = self.base_path / instrument / expiry_date
        dir_path.mkdir(parents=True, exist_ok=True)

        filename = f"chain_{timestamp:%H%M}.parquet"
        file_path = dir_path / filename

        # Add metadata columns
        df = chain_df.copy()
        df["snapshot_timestamp"] = timestamp
        df["spot_price_at_snapshot"] = spot_price
        df["instrument"] = instrument
        df["expiry_date"] = expiry_date

        df.to_parquet(file_path, index=False)
        return file_path

    def load_expiry_day(
        self, instrument: str, expiry_date: str
    ) -> list[tuple[datetime, pd.DataFrame, float]]:
        """Load all snapshots for a given expiry day, sorted by time.

        Returns:
            List of (timestamp, chain_df, spot_price) tuples
        """
        dir_path = self.base_path / instrument / expiry_date
        if not dir_path.exists():
            return []

        snapshots = []
        for f in sorted(dir_path.glob("chain_*.parquet")):
            df = pd.read_parquet(f)
            ts = df["snapshot_timestamp"].iloc[0]
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            spot = df["spot_price_at_snapshot"].iloc[0]
            # Drop metadata columns for clean chain
            chain = df.drop(
                columns=["snapshot_timestamp", "spot_price_at_snapshot", "instrument", "expiry_date"],
                errors="ignore",
            )
            snapshots.append((ts, chain, spot))

        return snapshots

    def list_available_expiries(self, instrument: str) -> list[str]:
        """List all available expiry dates for an instrument."""
        inst_path = self.base_path / instrument
        if not inst_path.exists():
            return []
        return sorted(d.name for d in inst_path.iterdir() if d.is_dir())

    def list_instruments(self) -> list[str]:
        """List all instruments with stored data."""
        if not self.base_path.exists():
            return []
        return sorted(d.name for d in self.base_path.iterdir() if d.is_dir())
