#!/usr/bin/env python3
"""CLI entry point for live GEX monitoring with Telegram alerts.

Features:
- Multi-expiry GEX aggregation (2 nearest expiries)
- WebSocket real-time trigger engine (event-driven chain fetching)
- Paper trading with outcome Telegram alerts
- OI flow direction estimation
- Historical pattern matching

Usage:
    python scripts/run_live.py --instrument NIFTY
    python scripts/run_live.py --instrument SENSEX --interval 120
    python scripts/run_live.py --instrument NIFTY --no-telegram
    python scripts/run_live.py --instrument NIFTY --websocket
"""

from __future__ import annotations

import argparse
import sys
import time
import threading
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
    UPSTOX_BASE_URL,
    MULTI_EXPIRY_COUNT,
    PAPER_TRADE_TELEGRAM_ALERTS,
    WS_TRIGGER_PROXIMITY_PCT,
    WS_MIN_TRIGGER_INTERVAL,
    WS_MAX_TRIGGER_INTERVAL,
    PREPARE_ALERT_ENABLED,
    PREPARE_ALERT_ZONE_PCT,
    PREPARE_ALERT_MIN_CANDLES,
    PREPARE_ALERT_COOLDOWN_MINUTES,
    PREPARE_ALERT_MAX_PER_DAY,
)
from src.auth.upstox_auth import load_access_token, validate_token
from src.data.options_chain import OptionsChainFetcher
from src.engine.gex_calculator import build_gex_profile
from src.engine.greeks import validate_greeks, filter_active_strikes
from src.engine.signal_generator import generate_signals
from src.engine.gamma_blast import detect_gamma_blast
from src.engine.blast_filters import compute_trend_bias
from src.engine.multi_expiry_gex import aggregate_multi_expiry_gex
from src.notifications.telegram import (
    send_blast_alert,
    DirectionalTracker,
    send_directional_alert,
    PrepareAlertTracker,
    send_prepare_alert,
    send_paper_trade_alert,
    send_daily_summary,
)
from src.backtest.data_store import HistoricalDataStore
from src.backtest.paper_trader import PaperTrader
from src.engine.realtime_trigger import RealtimeTriggerEngine, CriticalLevels


def compute_time_to_expiry_hours(expiry_date: str) -> float:
    """Compute hours until expiry (3:30 PM IST)."""
    return ist_tte(expiry_date)


def _fetch_vix(token: str) -> float | None:
    """Fetch India VIX from Upstox API."""
    try:
        import requests
        vix_resp = requests.get(
            f"{UPSTOX_BASE_URL}/market-quote/quotes",
            params={"instrument_key": "NSE_INDEX|India VIX"},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=5,
        )
        if vix_resp.status_code == 200:
            vix_data = vix_resp.json().get("data", {})
            for v in vix_data.values():
                ltp = v.get("last_price", 0) or v.get("ltp", 0)
                if ltp > 0:
                    return ltp
    except Exception:
        pass
    return None


def _fetch_and_process(
    fetcher, inst, instrument_name, expiry_date, token,
    prev_profile, prev_chain, price_history, fired_today,
    last_blast_time, vix_value, hist_store, dir_tracker,
    paper_trader, enable_telegram, prepare_tracker=None,
):
    """Core processing: fetch chain, compute GEX, detect blasts, manage paper trades."""

    # --- Fetch primary chain ---
    chain_df, spot_price = fetcher.fetch_chain(inst["instrument_key"], expiry_date)
    if chain_df.empty:
        print(f"[{now_ist():%H:%M:%S}] No chain data received. Retrying...")
        return prev_profile, prev_chain, price_history, fired_today, last_blast_time, vix_value

    chain_df = validate_greeks(chain_df)
    chain_df = filter_active_strikes(chain_df, spot_price, num_strikes=40)

    # --- Auto-save snapshot ---
    try:
        hist_store.save_snapshot(instrument_name, expiry_date, now_ist(), chain_df, spot_price)
    except Exception:
        pass

    # --- Single-expiry GEX profile ---
    profile = build_gex_profile(
        chain_df, spot_price, inst["contract_multiplier"],
        instrument_name, expiry_date,
    )

    # --- Multi-expiry GEX (best effort) ---
    multi_expiry_info = None
    try:
        expiry_chains_raw = fetcher.fetch_multi_expiry_chains(
            inst["instrument_key"], count=MULTI_EXPIRY_COUNT,
        )
        if len(expiry_chains_raw) > 1:
            # Prepare (expiry, validated_chain, tte) tuples
            expiry_chains = []
            for exp, cdf, sp in expiry_chains_raw:
                cdf = validate_greeks(cdf)
                cdf = filter_active_strikes(cdf, sp, num_strikes=40)
                tte_exp = compute_time_to_expiry_hours(exp)
                expiry_chains.append((exp, cdf, tte_exp))

            multi_expiry_info = aggregate_multi_expiry_gex(
                expiry_chains, spot_price, inst["contract_multiplier"],
            )
    except Exception:
        pass  # Multi-expiry is best-effort

    # --- Fetch VIX ---
    new_vix = _fetch_vix(token)
    if new_vix is not None:
        vix_value = new_vix

    tte = compute_time_to_expiry_hours(expiry_date)
    signals = generate_signals(profile, prev_profile, tte)

    # Track price history
    price_history.append(spot_price)
    price_history = price_history[-20:]

    # --- Display ---
    print(f"\n[{profile.timestamp:%H:%M:%S}] {instrument_name} Spot: {spot_price:.2f}")
    print(f"  Gamma Flip : {profile.gamma_flip_level or 'N/A'}")
    print(f"  Max Gamma  : {profile.max_gamma_strike or 'N/A'}")
    print(f"  Call Wall  : {profile.call_wall or 'N/A'}")
    print(f"  Put Wall   : {profile.put_wall or 'N/A'}")
    print(f"  Net GEX    : {profile.net_gex_total:,.0f} ({'POSITIVE' if profile.net_gex_total > 0 else 'NEGATIVE'} gamma)")
    print(f"  Zero GEX   : {profile.zero_gex_levels or 'None'}")
    print(f"  Expiry in  : {tte:.1f} hours")
    if vix_value:
        print(f"  India VIX  : {vix_value:.2f}")

    # Multi-expiry info
    if multi_expiry_info:
        n_exp = len(multi_expiry_info.get("expiry_contributions", []))
        wflip = multi_expiry_info.get("weighted_gamma_flip")
        r_calls = len(multi_expiry_info.get("reinforced_call_walls", []))
        r_puts = len(multi_expiry_info.get("reinforced_put_walls", []))
        print(f"  Multi-Exp  : {n_exp} expiries | Weighted Flip: {wflip or 'N/A'}")
        if r_calls or r_puts:
            print(f"  Reinforced : {r_calls} call walls, {r_puts} put walls")

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
        vix_value=vix_value,
        expiry_date=expiry_date,
    )

    # --- Paper trader: update prices ---
    closed_trades = paper_trader.update_price(spot_price)
    for ct in closed_trades:
        result_icon = "\u2705" if ct.outcome == "target_hit" else "\u274c"
        print(f"  {result_icon} Paper trade {ct.trade_id}: {ct.outcome} | P&L: {ct.pnl_pct:+.2f}%")

        # Send paper trade outcome to Telegram
        if enable_telegram and PAPER_TRADE_TELEGRAM_ALERTS:
            try:
                send_paper_trade_alert(ct)
            except Exception:
                pass

    if blast is not None:
        fired_today += 1
        last_blast_time = blast.timestamp
        dir_arrow = "\u25b2" if blast.direction == "bullish" else "\u25bc"
        print(f"\n  {'='*50}")
        print(f"  {dir_arrow} GAMMA BLAST: {blast.direction.upper()}")
        print(f"  Score: {blast.composite_score:.0f}/100")
        print(f"  Entry: {blast.entry_level:,.2f} | SL: {blast.stop_loss:,.2f} | Target: {blast.target:,.2f}")

        # OI flow info
        oi_flow = blast.metadata.get("oi_flow", {})
        if oi_flow.get("dominant_flow") != "unavailable":
            print(f"  OI Flow: {oi_flow.get('dominant_flow', 'N/A')} (conf: {oi_flow.get('flow_confidence', 0):.0%})")

        # Pattern match info
        pm = blast.metadata.get("pattern_match", {})
        if pm.get("matches", 0) > 0:
            print(f"  Historical: {pm['hit_rate']:.0%} hit rate from {pm['matches']} similar setups")

        print(f"  {'='*50}")

        # Open paper trade
        paper_trade = paper_trader.open_trade(blast)
        print(f"  >> Paper trade opened: {paper_trade.trade_id}")

        if enable_telegram:
            if send_blast_alert(blast):
                print("  >> Telegram alert sent!")
            else:
                print("  >> Telegram send failed (check credentials)")

    # --- Prepare Alert (early warning near key levels with momentum) ---
    if enable_telegram and prepare_tracker is not None and PREPARE_ALERT_ENABLED:
        prepare_msg = prepare_tracker.update(
            spot_price=spot_price,
            profile=profile,
            instrument=instrument_name,
        )
        if prepare_msg is not None:
            if send_prepare_alert(prepare_msg):
                print(f"  >> Prepare alert sent to Telegram!")

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

    return profile, chain_df.copy(), price_history, fired_today, last_blast_time, vix_value


def run(instrument_name: str, interval: int, expiry_date: str | None,
        enable_telegram: bool = True, use_websocket: bool = False) -> None:
    """Main loop with blast detection, multi-expiry GEX, and Telegram alerts."""
    token = load_access_token()
    if not validate_token(token):
        print("ERROR: Access token is invalid or expired. Update .env with today's token.")
        sys.exit(1)

    inst = get_instrument(instrument_name)
    fetcher = OptionsChainFetcher(token)

    if expiry_date is None:
        expiry_date = fetcher.get_nearest_expiry(inst["instrument_key"])
    print(f"Monitoring {instrument_name} | Expiry: {expiry_date} | Interval: {interval}s")
    print(f"Multi-expiry: {MULTI_EXPIRY_COUNT} expiries | WebSocket: {'ON' if use_websocket else 'OFF'}")
    if enable_telegram:
        print("Telegram alerts: ENABLED (blasts + paper trade outcomes)")
    else:
        print("Telegram alerts: DISABLED")
    print("=" * 70)

    prev_profile = None
    prev_chain = None
    price_history: list[float] = []
    fired_today = 0
    last_blast_time: datetime | None = None
    vix_value: float | None = None

    hist_store = HistoricalDataStore()
    print("Auto-saving chain snapshots to data/historical/ for backtesting.")

    dir_tracker = DirectionalTracker(
        min_consecutive=TREND_ALERT_MIN_CONSECUTIVE,
        min_move_pct=TREND_ALERT_MIN_MOVE_PCT,
        cooldown_minutes=TREND_ALERT_COOLDOWN_MINUTES,
    )

    paper_trader = PaperTrader()
    print("Paper trading enabled — tracking all blast signal outcomes.")

    prepare_tracker = PrepareAlertTracker(
        zone_pct=PREPARE_ALERT_ZONE_PCT,
        min_candles=PREPARE_ALERT_MIN_CANDLES,
        cooldown_minutes=PREPARE_ALERT_COOLDOWN_MINUTES,
        max_alerts_per_day=PREPARE_ALERT_MAX_PER_DAY,
    )
    if enable_telegram and PREPARE_ALERT_ENABLED:
        print("Prepare alerts: ENABLED (early warning near S/R with momentum)")

    # --- WebSocket Trigger Mode ---
    if use_websocket:
        trigger_event = threading.Event()

        def on_trigger(reason, spot):
            print(f"  >> WS Trigger: {reason} @ {spot:.2f}")
            trigger_event.set()

        trigger_engine = RealtimeTriggerEngine(
            on_trigger=on_trigger,
            proximity_pct=WS_TRIGGER_PROXIMITY_PCT,
            min_interval=WS_MIN_TRIGGER_INTERVAL,
            max_interval=WS_MAX_TRIGGER_INTERVAL,
        )

        # Try to start WebSocket feed
        try:
            from src.data.websocket_feed import LiveMarketFeed
            ws_feed = LiveMarketFeed(token)
            ws_feed.subscribe([inst["instrument_key"]])
            ws_feed.on_tick = trigger_engine.on_tick
            ws_feed.run_in_background()
            print("WebSocket feed started — event-driven chain fetching active.")
        except Exception as e:
            print(f"WebSocket unavailable ({e}), falling back to polling.")
            use_websocket = False

    # --- Main Loop ---
    while True:
        try:
            if use_websocket:
                # Wait for trigger or max interval timeout
                triggered = trigger_event.wait(timeout=WS_MAX_TRIGGER_INTERVAL)
                trigger_event.clear()
                if not triggered:
                    print(f"  [Periodic fetch — {WS_MAX_TRIGGER_INTERVAL}s timeout]")

            result = _fetch_and_process(
                fetcher, inst, instrument_name, expiry_date, token,
                prev_profile, prev_chain, price_history, fired_today,
                last_blast_time, vix_value, hist_store, dir_tracker,
                paper_trader, enable_telegram, prepare_tracker,
            )
            prev_profile, prev_chain, price_history, fired_today, last_blast_time, vix_value = result

            # Update WebSocket trigger engine with new levels
            if use_websocket and prev_profile:
                trigger_engine.update_levels(CriticalLevels(
                    gamma_flip=prev_profile.gamma_flip_level,
                    call_wall=prev_profile.call_wall,
                    put_wall=prev_profile.put_wall,
                    zero_gex_levels=prev_profile.zero_gex_levels or [],
                    max_gamma=prev_profile.max_gamma_strike,
                ))

        except KeyboardInterrupt:
            # Expire open trades and send daily summary
            expired = paper_trader.expire_open_trades(
                spot_price=price_history[-1] if price_history else 0,
            )
            for et in expired:
                print(f"  Expired: {et.trade_id} | P&L: {et.pnl_pct:+.2f}%")

            stats = paper_trader.get_statistics()
            if stats["total_trades"] > 0:
                print(f"\n--- Daily Summary ---")
                print(f"  Trades: {stats['total_trades']} | Hit Rate: {stats['hit_rate']:.0%}")
                print(f"  Avg P&L: {stats.get('avg_pnl_pct', 0):+.2f}%")

                if enable_telegram and PAPER_TRADE_TELEGRAM_ALERTS:
                    try:
                        send_daily_summary(stats, instrument_name)
                        print("  >> Daily summary sent to Telegram.")
                    except Exception:
                        pass

            print("\nStopped.")
            break
        except Exception as e:
            print(f"[{now_ist():%H:%M:%S}] Error: {e}")

        if not use_websocket:
            time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Live GEX Signal Monitor")
    parser.add_argument("--instrument", default="NIFTY", choices=["NIFTY", "SENSEX"])
    parser.add_argument("--interval", type=int, default=CHAIN_POLL_INTERVAL,
                        help="Polling interval in seconds")
    parser.add_argument("--expiry", default=None, help="Expiry date (YYYY-MM-DD)")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Disable Telegram alerts")
    parser.add_argument("--websocket", action="store_true",
                        help="Use WebSocket for real-time price streaming (event-driven)")
    args = parser.parse_args()
    enable_tg = TELEGRAM_ENABLED and not args.no_telegram
    run(args.instrument, args.interval, args.expiry,
        enable_telegram=enable_tg, use_websocket=args.websocket)


if __name__ == "__main__":
    main()
