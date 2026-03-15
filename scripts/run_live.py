#!/usr/bin/env python3
"""CLI entry point for live GEX monitoring.

Usage:
    python scripts/run_live.py --instrument NIFTY
    python scripts/run_live.py --instrument SENSEX --interval 120
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from src.utils.ist import now_ist, time_to_expiry_hours as ist_tte

sys.path.insert(0, ".")

from config.instruments import get_instrument
from config.settings import CHAIN_POLL_INTERVAL
from src.auth.upstox_auth import load_access_token, validate_token
from src.data.options_chain import OptionsChainFetcher
from src.engine.gex_calculator import build_gex_profile
from src.engine.greeks import validate_greeks, filter_active_strikes
from src.engine.signal_generator import generate_signals


def compute_time_to_expiry_hours(expiry_date: str) -> float:
    """Compute hours until expiry (3:30 PM IST)."""
    return ist_tte(expiry_date)


def run(instrument_name: str, interval: int, expiry_date: str | None) -> None:
    """Main polling loop."""
    token = load_access_token()
    if not validate_token(token):
        print("ERROR: Access token is invalid or expired. Update .env with today's token.")
        sys.exit(1)

    inst = get_instrument(instrument_name)
    fetcher = OptionsChainFetcher(token)

    if expiry_date is None:
        expiry_date = fetcher.get_nearest_expiry(inst["instrument_key"])
    print(f"Monitoring {instrument_name} | Expiry: {expiry_date} | Interval: {interval}s")
    print("=" * 70)

    prev_profile = None

    while True:
        try:
            chain_df, spot_price = fetcher.fetch_chain(inst["instrument_key"], expiry_date)
            if chain_df.empty:
                print(f"[{datetime.now():%H:%M:%S}] No chain data received. Retrying...")
                time.sleep(interval)
                continue

            chain_df = validate_greeks(chain_df)
            chain_df = filter_active_strikes(chain_df, spot_price, num_strikes=40)

            profile = build_gex_profile(
                chain_df, spot_price, inst["contract_multiplier"],
                instrument_name, expiry_date,
            )

            tte = compute_time_to_expiry_hours(expiry_date)
            signals = generate_signals(profile, prev_profile, tte)

            # Display
            print(f"\n[{profile.timestamp:%H:%M:%S}] {instrument_name} Spot: {spot_price:.2f}")
            print(f"  Gamma Flip : {profile.gamma_flip_level or 'N/A'}")
            print(f"  Max Gamma  : {profile.max_gamma_strike or 'N/A'}")
            print(f"  Call Wall  : {profile.call_wall or 'N/A'}")
            print(f"  Put Wall   : {profile.put_wall or 'N/A'}")
            print(f"  Net GEX    : {profile.net_gex_total:,.0f} ({'POSITIVE' if profile.net_gex_total > 0 else 'NEGATIVE'} gamma)")
            print(f"  Zero GEX   : {profile.zero_gex_levels or 'None'}")
            print(f"  Expiry in  : {tte:.1f} hours")

            if signals:
                print(f"  --- SIGNALS ({len(signals)}) ---")
                for sig in signals:
                    arrow = {"bullish": "▲", "bearish": "▼"}.get(sig.direction, "●")
                    print(f"  {arrow} {sig.signal_type.upper()} @ {sig.level:.2f} "
                          f"(strength: {sig.strength:.2f}) {sig.direction or ''}")
            else:
                print("  No signals triggered.")

            prev_profile = profile

        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"[{datetime.now():%H:%M:%S}] Error: {e}")

        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Live GEX Signal Monitor")
    parser.add_argument("--instrument", default="NIFTY", choices=["NIFTY", "SENSEX"])
    parser.add_argument("--interval", type=int, default=CHAIN_POLL_INTERVAL,
                        help="Polling interval in seconds")
    parser.add_argument("--expiry", default=None, help="Expiry date (YYYY-MM-DD)")
    args = parser.parse_args()
    run(args.instrument, args.interval, args.expiry)


if __name__ == "__main__":
    main()
