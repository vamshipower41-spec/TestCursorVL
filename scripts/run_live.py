#!/usr/bin/env python3
"""CLI entry point for live GEX monitoring with Telegram alerts.

Usage:
    python scripts/run_live.py --instrument NIFTY
    python scripts/run_live.py --instrument SENSEX --interval 120
    python scripts/run_live.py --instrument NIFTY --no-telegram
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from src.utils.ist import now_ist, time_to_expiry_hours as ist_tte

sys.path.insert(0, ".")

from config.instruments import get_instrument
from config.settings import (
    CHAIN_POLL_INTERVAL,
    EXPIRY_DAY_POLL_INTERVAL,
    TELEGRAM_ENABLED,
    TREND_ALERT_MIN_CONSECUTIVE,
    TREND_ALERT_MIN_MOVE_PCT,
    TREND_ALERT_COOLDOWN_MINUTES,
)
from src.auth.upstox_auth import load_access_token, validate_token
from src.data.options_chain import OptionsChainFetcher
from src.engine.gex_calculator import build_gex_profile
from src.engine.greeks import validate_greeks, filter_active_strikes
from src.engine.signal_generator import generate_signals
from src.engine.gamma_blast import detect_gamma_blast
from src.engine.blast_filters import compute_trend_bias
from src.notifications.telegram import (
    send_blast_alert,
    DirectionalTracker,
    send_directional_alert,
)


def compute_time_to_expiry_hours(expiry_date: str) -> float:
    """Compute hours until expiry (3:30 PM IST)."""
    return ist_tte(expiry_date)


def run(instrument_name: str, interval: int, expiry_date: str | None,
        enable_telegram: bool = True) -> None:
    """Main polling loop with blast detection and Telegram alerts."""
    token = load_access_token()
    if not validate_token(token):
        print("ERROR: Access token is invalid or expired. Update .env with today's token.")
        sys.exit(1)

    inst = get_instrument(instrument_name)
    fetcher = OptionsChainFetcher(token)

    if expiry_date is None:
        expiry_date = fetcher.get_nearest_expiry(inst["instrument_key"])
    print(f"Monitoring {instrument_name} | Expiry: {expiry_date} | Interval: {interval}s")
    if enable_telegram:
        print("Telegram alerts: ENABLED")
    else:
        print("Telegram alerts: DISABLED")
    print("=" * 70)

    prev_profile = None
    prev_chain = None
    price_history: list[float] = []
    fired_today = 0
    last_blast_time: datetime | None = None

    # Directional trend tracker
    dir_tracker = DirectionalTracker(
        min_consecutive=TREND_ALERT_MIN_CONSECUTIVE,
        min_move_pct=TREND_ALERT_MIN_MOVE_PCT,
        cooldown_minutes=TREND_ALERT_COOLDOWN_MINUTES,
    )

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

            # Track price history
            price_history.append(spot_price)
            price_history = price_history[-20:]

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
                    arrow = {"bullish": "\u25b2", "bearish": "\u25bc"}.get(sig.direction, "\u25cf")
                    print(f"  {arrow} {sig.signal_type.upper()} @ {sig.level:.2f} "
                          f"(strength: {sig.strength:.2f}) {sig.direction or ''}")
            else:
                print("  No signals triggered.")

            # --- Gamma Blast Detection ---
            blast = detect_gamma_blast(
                profile=profile,
                prev_profile=prev_profile,
                chain_df=chain_df,
                prev_chain_df=prev_chain,
                time_to_expiry_hours=tte,
                fired_today=fired_today,
                last_blast_time=last_blast_time,
                price_history=price_history,
                vix_value=None,
                expiry_date=expiry_date,
            )

            if blast is not None:
                fired_today += 1
                last_blast_time = blast.timestamp
                dir_arrow = "\u25b2" if blast.direction == "bullish" else "\u25bc"
                print(f"\n  {'='*50}")
                print(f"  {dir_arrow} GAMMA BLAST: {blast.direction.upper()}")
                print(f"  Score: {blast.composite_score:.0f}/100")
                print(f"  Entry: {blast.entry_level:,.2f} | SL: {blast.stop_loss:,.2f} | Target: {blast.target:,.2f}")
                print(f"  {'='*50}")

                if enable_telegram:
                    if send_blast_alert(blast):
                        print("  >> Telegram alert sent!")
                    else:
                        print("  >> Telegram send failed (check credentials)")

            # --- Directional Trend Alert ---
            if enable_telegram:
                trend_data = compute_trend_bias(price_history)
                dir_alert = dir_tracker.update(
                    trend_data=trend_data,
                    spot_price=spot_price,
                    instrument=instrument_name,
                )
                if dir_alert is not None:
                    if send_directional_alert(dir_alert):
                        print(f"  >> Directional {trend_data['trend']} alert sent to Telegram!")

            prev_profile = profile
            prev_chain = chain_df.copy()

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
    parser.add_argument("--no-telegram", action="store_true",
                        help="Disable Telegram alerts")
    args = parser.parse_args()
    enable_tg = TELEGRAM_ENABLED and not args.no_telegram
    run(args.instrument, args.interval, args.expiry, enable_telegram=enable_tg)


if __name__ == "__main__":
    main()
