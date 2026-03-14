#!/usr/bin/env python3
"""Fetch and store options chain snapshots for backtesting.

Run this script on expiry days during market hours to collect data.

Usage:
    python scripts/fetch_historical_chains.py --instrument NIFTY --interval 180
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

sys.path.insert(0, ".")

from config.instruments import get_instrument
from config.settings import CHAIN_POLL_INTERVAL
from src.auth.upstox_auth import load_access_token, validate_token
from src.backtest.data_store import HistoricalDataStore
from src.data.options_chain import OptionsChainFetcher


def main():
    parser = argparse.ArgumentParser(description="Collect options chain snapshots")
    parser.add_argument("--instrument", default="NIFTY", choices=["NIFTY", "SENSEX"])
    parser.add_argument("--interval", type=int, default=CHAIN_POLL_INTERVAL)
    parser.add_argument("--expiry", default=None, help="Expiry date YYYY-MM-DD")
    args = parser.parse_args()

    token = load_access_token()
    if not validate_token(token):
        print("ERROR: Invalid token.")
        sys.exit(1)

    inst = get_instrument(args.instrument)
    fetcher = OptionsChainFetcher(token)
    store = HistoricalDataStore()

    expiry_date = args.expiry or fetcher.get_nearest_expiry(inst["instrument_key"])
    print(f"Collecting {args.instrument} chain snapshots | Expiry: {expiry_date}")
    print(f"Interval: {args.interval}s | Saving to data/historical/")

    while True:
        now = datetime.now()
        # Only collect during market hours (9:15 - 15:30 IST)
        if now.hour < 9 or (now.hour == 9 and now.minute < 15):
            print(f"[{now:%H:%M:%S}] Waiting for market open...")
            time.sleep(60)
            continue
        if now.hour > 15 or (now.hour == 15 and now.minute > 30):
            print(f"[{now:%H:%M:%S}] Market closed. Done for today.")
            break

        try:
            chain_df, spot = fetcher.fetch_chain(inst["instrument_key"], expiry_date)
            if not chain_df.empty:
                path = store.save_snapshot(args.instrument, expiry_date, now, chain_df, spot)
                print(f"[{now:%H:%M:%S}] Saved: {path} | Spot: {spot:.2f} | Strikes: {len(chain_df)}")
            else:
                print(f"[{now:%H:%M:%S}] Empty chain, skipping.")
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"[{now:%H:%M:%S}] Error: {e}")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
