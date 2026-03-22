"""Background alert worker — runs in a daemon thread inside Streamlit.

Solves the core problem: Streamlit only runs code on page refresh, so if
you're on your phone and switch apps / lock screen, no alerts get sent.

This worker spawns once on first page load and keeps monitoring in the
background, sending Telegram alerts even when you're not looking at the page.

Works on Streamlit Cloud — no PC needed. Just keep the app awake with a
free ping service (UptimeRobot / cron-job.org hitting your app URL every 5 min).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)

# Singleton lock — only one worker thread ever
_worker_lock = threading.Lock()
_worker_started = False


def start_alert_worker(token: str) -> bool:
    """Start the background alert worker if not already running.

    Safe to call multiple times — only the first call spawns the thread.

    Args:
        token: Upstox access token for API calls.

    Returns:
        True if worker was started (or already running), False on error.
    """
    global _worker_started

    with _worker_lock:
        if _worker_started:
            return True
        _worker_started = True

    t = threading.Thread(
        target=_alert_loop,
        args=(token,),
        daemon=True,  # Dies when Streamlit process exits
        name="alert-worker",
    )
    t.start()
    logger.info("Background alert worker started.")
    return True


def _alert_loop(token: str) -> None:
    """Main monitoring loop — runs until process exits."""
    from config.instruments import get_instrument, INSTRUMENTS
    from config.settings import (
        TELEGRAM_ENABLED,
        TREND_ALERT_MIN_CONSECUTIVE,
        TREND_ALERT_MIN_MOVE_PCT,
        TREND_ALERT_COOLDOWN_MINUTES,
        PREPARE_ALERT_ENABLED,
        PREPARE_ALERT_ZONE_PCT,
        PREPARE_ALERT_MIN_WARMUP_SCORE,
        PREPARE_ALERT_COOLDOWN_MINUTES,
        PREPARE_ALERT_MAX_PER_DAY,
        UPSTOX_BASE_URL,
    )
    from src.data.options_chain import OptionsChainFetcher
    from src.engine.gex_calculator import build_gex_profile
    from src.engine.greeks import validate_greeks, filter_active_strikes
    from src.engine.gamma_blast import detect_gamma_blast, compute_blast_readiness
    from src.engine.blast_filters import compute_trend_bias
    from src.notifications.telegram import (
        send_blast_alert,
        DirectionalTracker,
        send_directional_alert,
        PrepareAlertTracker,
        send_prepare_alert,
        send_telegram,
    )
    from src.utils.ist import now_ist, today_ist, is_market_open
    from src.utils.ist import time_to_expiry_hours as compute_tte

    if not TELEGRAM_ENABLED:
        logger.info("Telegram disabled — alert worker exiting.")
        return

    # Send startup confirmation
    send_telegram(
        "\u2705 <b>Alert Worker Started</b>\n\n"
        "Background monitoring active for NIFTY & SENSEX.\n"
        "You'll get Telegram alerts even if you close the app.\n\n"
        f"<i>{now_ist():%H:%M IST}</i>"
    )

    EXPIRY_DAYS = {"NIFTY": 1, "SENSEX": 3}  # Tuesday, Thursday

    # Per-instrument state
    state: dict[str, dict] = {}
    for name in INSTRUMENTS:
        state[name] = {
            "prev_profile": None,
            "prev_chain": None,
            "price_history": [],
            "fired_today": 0,
            "last_blast_time": None,
            "vix_value": None,
            "last_date": None,
            "dir_tracker": DirectionalTracker(
                min_consecutive=TREND_ALERT_MIN_CONSECUTIVE,
                min_move_pct=TREND_ALERT_MIN_MOVE_PCT,
                cooldown_minutes=TREND_ALERT_COOLDOWN_MINUTES,
            ),
            "prepare_tracker": PrepareAlertTracker(
                zone_pct=PREPARE_ALERT_ZONE_PCT,
                min_warmup_score=PREPARE_ALERT_MIN_WARMUP_SCORE,
                cooldown_minutes=PREPARE_ALERT_COOLDOWN_MINUTES,
                max_alerts_per_day=PREPARE_ALERT_MAX_PER_DAY,
            ),
            "blast_sent_ids": set(),
        }

    fetcher = OptionsChainFetcher(token)
    poll_interval = 90  # seconds between checks (balanced for Streamlit Cloud limits)

    while True:
        try:
            # Only run during market hours
            if not is_market_open():
                # Sleep longer outside market hours
                time.sleep(300)
                continue

            now = now_ist()
            today = today_ist()
            today_weekday = today.weekday()

            # Determine which instruments to monitor today
            # Always monitor the one whose expiry is today, plus light monitoring of the other
            instruments_to_check = []
            for name in INSTRUMENTS:
                is_expiry = today_weekday == EXPIRY_DAYS.get(name, -1)
                instruments_to_check.append((name, is_expiry))

            for instrument_name, is_expiry in instruments_to_check:
                s = state[instrument_name]

                # Reset daily counters
                today_str = today.isoformat()
                if s["last_date"] != today_str:
                    s["fired_today"] = 0
                    s["last_blast_time"] = None
                    s["blast_sent_ids"] = set()
                    s["last_date"] = today_str

                # Skip non-expiry instruments to save API calls (check less often)
                if not is_expiry and now.minute % 10 != 0:
                    continue

                inst = get_instrument(instrument_name)

                try:
                    expiry_date = fetcher.get_nearest_expiry(inst["instrument_key"])
                    chain_df, spot_price = fetcher.fetch_chain(
                        inst["instrument_key"], expiry_date,
                    )
                except Exception as e:
                    logger.warning(f"[{instrument_name}] Fetch error: {e}")
                    continue

                if chain_df.empty:
                    continue

                chain_df = validate_greeks(chain_df)
                chain_df = filter_active_strikes(chain_df, spot_price, num_strikes=40)

                profile = build_gex_profile(
                    chain_df, spot_price, inst["contract_multiplier"],
                    instrument_name, expiry_date,
                )

                # Track price history
                s["price_history"].append(spot_price)
                s["price_history"] = s["price_history"][-20:]

                # Fetch VIX (best effort)
                try:
                    import requests
                    vix_resp = requests.get(
                        f"{UPSTOX_BASE_URL}/market-quote/quotes",
                        params={"instrument_key": "NSE_INDEX|India VIX"},
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Accept": "application/json",
                        },
                        timeout=5,
                    )
                    if vix_resp.status_code == 200:
                        vix_data = vix_resp.json().get("data", {})
                        for v in vix_data.values():
                            ltp = v.get("last_price", 0) or v.get("ltp", 0)
                            if ltp > 0:
                                s["vix_value"] = ltp
                except Exception:
                    pass

                tte = compute_tte(expiry_date)

                # --- Prepare Alert ---
                if PREPARE_ALERT_ENABLED:
                    readiness = compute_blast_readiness(
                        profile=profile,
                        prev_profile=s["prev_profile"],
                        chain_df=chain_df,
                        prev_chain_df=s["prev_chain"],
                        time_to_expiry_hours=tte,
                        vix_value=s["vix_value"],
                    )
                    prepare_msg = s["prepare_tracker"].update(
                        spot_price=spot_price,
                        profile=profile,
                        instrument=instrument_name,
                        readiness=readiness,
                    )
                    if prepare_msg is not None:
                        send_prepare_alert(prepare_msg)

                # --- Gamma Blast Detection ---
                blast = detect_gamma_blast(
                    profile=profile,
                    prev_profile=s["prev_profile"],
                    chain_df=chain_df,
                    prev_chain_df=s["prev_chain"],
                    time_to_expiry_hours=tte,
                    fired_today=s["fired_today"],
                    last_blast_time=s["last_blast_time"],
                    price_history=s["price_history"],
                    vix_value=s["vix_value"],
                    expiry_date=expiry_date,
                )

                if blast is not None:
                    blast_id = f"{blast.instrument}_{blast.timestamp.isoformat()}"
                    if blast_id not in s["blast_sent_ids"]:
                        s["fired_today"] += 1
                        s["last_blast_time"] = blast.timestamp
                        s["blast_sent_ids"].add(blast_id)
                        send_blast_alert(blast)

                # --- Directional Trend Alert ---
                trend_data = compute_trend_bias(s["price_history"])
                dir_alert = s["dir_tracker"].update(
                    trend_data=trend_data,
                    spot_price=spot_price,
                    instrument=instrument_name,
                )
                if dir_alert is not None:
                    send_directional_alert(dir_alert)

                # Update state
                s["prev_profile"] = profile
                s["prev_chain"] = chain_df.copy()

        except Exception as e:
            logger.error(f"Alert worker error: {e}")

        time.sleep(poll_interval)
