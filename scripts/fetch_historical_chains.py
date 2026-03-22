#!/usr/bin/env python3
"""Fetch and store options chain snapshots for backtesting.

Supports foreground and background (daemon) modes.

Usage:
    # Foreground (blocks terminal):
    python scripts/fetch_historical_chains.py --instrument NIFTY --interval 180

    # Background daemon (runs in background, writes logs to file):
    python scripts/fetch_historical_chains.py --instrument NIFTY --daemon

    # Both instruments simultaneously:
    python scripts/fetch_historical_chains.py --instrument NIFTY --daemon
    python scripts/fetch_historical_chains.py --instrument SENSEX --daemon
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, ".")

from src.utils.ist import now_ist
from config.instruments import get_instrument
from config.settings import CHAIN_POLL_INTERVAL, EXPIRY_DAY_POLL_INTERVAL
from src.auth.upstox_auth import load_access_token, validate_token
from src.backtest.data_store import HistoricalDataStore
from src.data.options_chain import OptionsChainFetcher


logger = logging.getLogger("data_collector")


def _is_market_hours(now: datetime) -> bool:
    """Check if current IST time is within market hours."""
    if now.hour < 9 or (now.hour == 9 and now.minute < 15):
        return False
    if now.hour > 15 or (now.hour == 15 and now.minute > 30):
        return False
    return True


def _is_expiry_day(instrument: str) -> bool:
    """Check if today is expiry day for this instrument."""
    weekday = now_ist().weekday()
    expiry_days = {"NIFTY": 1, "SENSEX": 3}  # Tuesday, Thursday
    return weekday == expiry_days.get(instrument, -1)


def collect_loop(
    instrument_name: str,
    interval: int,
    expiry_date: str | None,
    max_retries: int = 3,
) -> None:
    """Main collection loop with retry logic and adaptive polling.

    On expiry days, polls faster (1 min) to capture more data for backtesting.
    On normal days, polls at the standard interval.
    """
    token = load_access_token()
    if not validate_token(token):
        logger.error("Invalid access token. Update .env with today's token.")
        return

    inst = get_instrument(instrument_name)
    fetcher = OptionsChainFetcher(token)
    store = HistoricalDataStore()

    if expiry_date is None:
        expiry_date = fetcher.get_nearest_expiry(inst["instrument_key"])

    logger.info(
        "Collecting %s chain snapshots | Expiry: %s | Saving to data/historical/",
        instrument_name, expiry_date,
    )

    consecutive_errors = 0

    while True:
        now = now_ist()

        if not _is_market_hours(now):
            if now.hour > 15 or (now.hour == 15 and now.minute > 30):
                logger.info("[%s] Market closed (IST). Done for today.", now.strftime("%H:%M:%S"))
                break
            logger.info("[%s] Waiting for market open...", now.strftime("%H:%M:%S"))
            time.sleep(60)
            continue

        # Adaptive interval: faster on expiry days
        is_expiry = _is_expiry_day(instrument_name)
        active_interval = min(interval, EXPIRY_DAY_POLL_INTERVAL) if is_expiry else interval

        try:
            chain_df, spot = fetcher.fetch_chain(inst["instrument_key"], expiry_date)
            if not chain_df.empty:
                path = store.save_snapshot(instrument_name, expiry_date, now, chain_df, spot)
                logger.info(
                    "[%s] Saved: %s | Spot: %.2f | Strikes: %d",
                    now.strftime("%H:%M:%S"), path, spot, len(chain_df),
                )
                consecutive_errors = 0
            else:
                logger.warning("[%s] Empty chain, skipping.", now.strftime("%H:%M:%S"))

        except KeyboardInterrupt:
            logger.info("Stopped by user.")
            break
        except Exception as e:
            consecutive_errors += 1
            logger.error("[%s] Error: %s", now.strftime("%H:%M:%S"), e)
            if consecutive_errors >= max_retries:
                logger.error("Too many consecutive errors (%d). Backing off 60s.", consecutive_errors)
                time.sleep(60)
                consecutive_errors = 0
                continue

        time.sleep(active_interval)


def start_background_collector(
    instrument_name: str,
    interval: int = CHAIN_POLL_INTERVAL,
    expiry_date: str | None = None,
) -> threading.Thread:
    """Start data collection in a background daemon thread.

    Can be called from run_live.py or dashboard to auto-collect
    without needing a separate terminal.

    Returns the thread handle.
    """
    thread = threading.Thread(
        target=collect_loop,
        args=(instrument_name, interval, expiry_date),
        name=f"data_collector_{instrument_name}",
        daemon=True,
    )
    thread.start()
    logger.info("Background data collector started for %s", instrument_name)
    return thread


def main():
    parser = argparse.ArgumentParser(description="Collect options chain snapshots")
    parser.add_argument("--instrument", default="NIFTY", choices=["NIFTY", "SENSEX"])
    parser.add_argument("--interval", type=int, default=CHAIN_POLL_INTERVAL)
    parser.add_argument("--expiry", default=None, help="Expiry date YYYY-MM-DD")
    parser.add_argument(
        "--daemon", action="store_true",
        help="Run in background mode (logs to file instead of stdout)",
    )
    args = parser.parse_args()

    # Setup logging
    if args.daemon:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"data_collector_{args.instrument.lower()}.log"
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler(log_file),
            ],
        )
        print(f"Background collector started for {args.instrument}. Logs: {log_file}")
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[logging.StreamHandler()],
        )

    collect_loop(args.instrument, args.interval, args.expiry)


if __name__ == "__main__":
    main()
