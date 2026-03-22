"""Gamma Blast Scalper — expiry-day scalping page.

Focused view for scalpers wanting 1-2 high-conviction gamma blast trades.
Shows: NIFTY (Tuesday) / SENSEX (Thursday) with blast detection status,
model breakdown, and trade levels.
"""

import sys
from pathlib import Path

# Resolve project root relative to this file (src/dashboard/pages/gamma_blast.py)
try:
    _project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
except NameError:
    # __file__ may not be defined when run via exec()
    _project_root = "."
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import streamlit as st
import pandas as pd
from datetime import date

from src.utils.ist import now_ist, today_ist, time_to_expiry_hours as compute_tte

try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except ImportError:
    HAS_AUTOREFRESH = False

from config.instruments import get_instrument, INSTRUMENTS
from config.settings import (
    BLAST_MAX_SIGNALS_PER_DAY,
    BLAST_MAX_SIGNALS_NORMAL_DAY,
    EXPIRY_DAY_POLL_INTERVAL,
    DASHBOARD_REFRESH_INTERVAL,
    TELEGRAM_ENABLED,
    TREND_ALERT_MIN_CONSECUTIVE,
    TREND_ALERT_MIN_MOVE_PCT,
    TREND_ALERT_COOLDOWN_MINUTES,
    PREPARE_ALERT_ENABLED,
    PREPARE_ALERT_ZONE_PCT,
    PREPARE_ALERT_MIN_WARMUP_SCORE,
    PREPARE_ALERT_COOLDOWN_MINUTES,
    PREPARE_ALERT_MAX_PER_DAY,
)
from config.settings import UPSTOX_BASE_URL
from src.auth.upstox_auth import load_access_token
from src.data.options_chain import OptionsChainFetcher
from src.engine.gex_calculator import build_gex_profile
from src.engine.greeks import validate_greeks, filter_active_strikes
from src.engine.signal_generator import generate_signals
from src.engine.gamma_blast import detect_gamma_blast, compute_blast_readiness
from src.dashboard.components.blast_card import (
    render_blast_alert,
    render_blast_components,
    render_no_blast_status,
)
from src.notifications.telegram import (
    send_blast_alert,
    DirectionalTracker,
    send_directional_alert,
    PrepareAlertTracker,
    send_prepare_alert,
    get_last_send_error,
    validate_credentials,
)
from src.backtest.data_store import HistoricalDataStore

st.title("Gamma Blast Scalper")

# Determine which instrument's expiry is today
EXPIRY_DAYS = {
    "NIFTY": 1,    # Tuesday = weekday 1
    "SENSEX": 3,   # Thursday = weekday 3
}


def _get_todays_expiry_instrument() -> str | None:
    """Return the instrument whose expiry is today, or None."""
    today_weekday = today_ist().weekday()
    for name, weekday in EXPIRY_DAYS.items():
        if today_weekday == weekday:
            return name
    return None


def _is_expiry_day(instrument_name: str) -> bool:
    """Check if today is expiry day for the given instrument."""
    today_weekday = today_ist().weekday()
    return today_weekday == EXPIRY_DAYS.get(instrument_name, -1)


# Auto-detect expiry instrument, allow manual override
expiry_instrument = _get_todays_expiry_instrument()

instrument_name = st.radio(
    "Index",
    list(INSTRUMENTS.keys()),
    index=list(INSTRUMENTS.keys()).index(expiry_instrument) if expiry_instrument else 0,
    horizontal=True,
)

is_expiry = _is_expiry_day(instrument_name)

# Faster refresh on expiry day
refresh_interval = EXPIRY_DAY_POLL_INTERVAL if is_expiry else DASHBOARD_REFRESH_INTERVAL
if HAS_AUTOREFRESH:
    st_autorefresh(interval=refresh_interval * 1000, key="blast_refresh")

# Show expiry day indicator
if is_expiry:
    st.markdown(
        f'<div style="background:#1b3a26; color:#26a69a; padding:8px 16px; '
        f'border-radius:8px; text-align:center; font-weight:700;">'
        f'EXPIRY DAY — {instrument_name} — Blast Detection ACTIVE</div>',
        unsafe_allow_html=True,
    )
else:
    day_name = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
                4: "Friday", 5: "Saturday", 6: "Sunday"}
    expiry_day_name = {v: k for k, v in EXPIRY_DAYS.items()}
    nifty_day = "Tuesday"
    sensex_day = "Thursday"
    st.info(
        f"Today is {day_name.get(date.today().weekday(), '?')}. "
        f"Gamma Blast is most relevant on expiry days "
        f"(NIFTY: {nifty_day}, SENSEX: {sensex_day})."
    )

# Load token
try:
    token = load_access_token()
except ValueError:
    st.error("Not logged in. Please refresh the page to login.")
    st.stop()

# Initialize blast session state
if "blast_history" not in st.session_state:
    st.session_state.blast_history = []
if "blast_fired_today" not in st.session_state:
    st.session_state.blast_fired_today = 0
if "blast_last_time" not in st.session_state:
    st.session_state.blast_last_time = None
if "blast_prev_profile" not in st.session_state:
    st.session_state.blast_prev_profile = None
if "blast_prev_chain" not in st.session_state:
    st.session_state.blast_prev_chain = None
if "blast_last_date" not in st.session_state:
    st.session_state.blast_last_date = None
if "blast_price_history" not in st.session_state:
    st.session_state.blast_price_history = []
if "blast_vix_value" not in st.session_state:
    st.session_state.blast_vix_value = None
if "directional_tracker" not in st.session_state:
    st.session_state.directional_tracker = DirectionalTracker(
        min_consecutive=TREND_ALERT_MIN_CONSECUTIVE,
        min_move_pct=TREND_ALERT_MIN_MOVE_PCT,
        cooldown_minutes=TREND_ALERT_COOLDOWN_MINUTES,
    )
if "blast_alert_sent_ids" not in st.session_state:
    st.session_state.blast_alert_sent_ids = set()
if "prepare_tracker" not in st.session_state:
    st.session_state.prepare_tracker = PrepareAlertTracker(
        zone_pct=PREPARE_ALERT_ZONE_PCT,
        min_warmup_score=PREPARE_ALERT_MIN_WARMUP_SCORE,
        cooldown_minutes=PREPARE_ALERT_COOLDOWN_MINUTES,
        max_alerts_per_day=PREPARE_ALERT_MAX_PER_DAY,
    )

# Validate Telegram credentials once at startup
if "telegram_validated" not in st.session_state:
    if TELEGRAM_ENABLED:
        tg_valid, tg_msg = validate_credentials()
        st.session_state.telegram_validated = tg_valid
        if not tg_valid:
            st.warning(f"Telegram alerts will NOT work: {tg_msg}")
    else:
        st.session_state.telegram_validated = False

# Reset daily counters if new day
if st.session_state.blast_last_date != today_ist().isoformat():
    st.session_state.blast_fired_today = 0
    st.session_state.blast_last_time = None
    st.session_state.blast_history = []
    st.session_state.blast_last_date = today_ist().isoformat()

# Fetch data
inst = get_instrument(instrument_name)
fetcher = OptionsChainFetcher(token)

try:
    expiry_date = fetcher.get_nearest_expiry(inst["instrument_key"])
    chain_df, spot_price = fetcher.fetch_chain(inst["instrument_key"], expiry_date)
except Exception as e:
    st.error(f"Failed to fetch data: {e}")
    st.stop()

if chain_df.empty:
    st.warning("No options chain data available.")
    st.stop()

# Fetch real-time spot price from market-quote API (faster than chain, works after hours)
# This ensures spot_price is never 0 even if the chain API returns stale data
try:
    import requests as _req
    _spot_resp = _req.get(
        f"{UPSTOX_BASE_URL}/market-quote/quotes",
        params={"instrument_key": inst["instrument_key"]},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=5,
    )
    if _spot_resp.status_code == 200:
        _spot_data = _spot_resp.json().get("data", {})
        for _v in _spot_data.values():
            _live_ltp = _v.get("last_price", 0) or _v.get("ltp", 0)
            if _live_ltp and float(_live_ltp) > 0:
                spot_price = float(_live_ltp)
except Exception:
    pass  # Fall back to chain-derived spot price

if spot_price <= 0:
    st.error("Could not determine spot price. Please check your API connection.")
    st.stop()

# Process
chain_df_clean = validate_greeks(chain_df)
chain_df_filtered = filter_active_strikes(chain_df_clean, spot_price, num_strikes=40)

profile = build_gex_profile(
    chain_df_filtered, spot_price, inst["contract_multiplier"],
    instrument_name, expiry_date,
)

# Auto-save chain snapshot on expiry days for backtesting
if is_expiry:
    try:
        _hist_store = HistoricalDataStore()
        _hist_store.save_snapshot(instrument_name, expiry_date, now_ist(), chain_df_filtered, spot_price)
    except Exception:
        pass  # Non-blocking — don't break dashboard if save fails

# Track price history for trend filter
st.session_state.blast_price_history.append(spot_price)
# Keep last 20 data points
st.session_state.blast_price_history = st.session_state.blast_price_history[-20:]

# Try to fetch India VIX (best-effort, non-blocking)
try:
    import requests as _vix_req
    vix_resp = _vix_req.get(
        f"{UPSTOX_BASE_URL}/market-quote/quotes",
        params={"instrument_key": "NSE_INDEX|India VIX"},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=5,
    )
    if vix_resp.status_code == 200:
        vix_data = vix_resp.json().get("data", {})
        for v in vix_data.values():
            # Upstox may use "last_price" or "ltp" depending on the response version
            ltp = v.get("last_price") or v.get("ltp") or 0
            if isinstance(ltp, (int, float)) and ltp > 0:
                st.session_state.blast_vix_value = float(ltp)
            elif isinstance(ltp, str):
                try:
                    parsed = float(ltp)
                    if parsed > 0:
                        st.session_state.blast_vix_value = parsed
                except ValueError:
                    pass
except Exception:
    pass  # VIX fetch failed — continue without it

vix_val = st.session_state.blast_vix_value

# Time to expiry
tte = compute_tte(expiry_date)

# Key metrics row
vix_display = f" | VIX: {vix_val:.1f}" if vix_val else ""
st.caption(f"Expiry: {expiry_date} | TTE: {tte:.1f}h{vix_display} | Last update: {profile.timestamp:%H:%M:%S} IST")

row1 = st.columns(4)
row1[0].metric("Spot", f"{spot_price:,.2f}")
row1[1].metric("Gamma Flip", f"{profile.gamma_flip_level:,.0f}" if profile.gamma_flip_level else "N/A")
regime = "POSITIVE" if profile.net_gex_total > 0 else "NEGATIVE"
regime_color = "normal" if profile.net_gex_total > 0 else "inverse"
row1[2].metric("GEX Regime", regime, delta=f"{profile.net_gex_total:,.0f}", delta_color=regime_color)
row1[3].metric("Max Gamma Pin", f"{profile.max_gamma_strike:,.0f}" if profile.max_gamma_strike else "N/A")

# Additional context row
row2 = st.columns(4)
row2[0].metric("Call Wall", f"{profile.call_wall:,.0f}" if profile.call_wall else "N/A")
row2[1].metric("Put Wall", f"{profile.put_wall:,.0f}" if profile.put_wall else "N/A")
if vix_val:
    vix_regime = "LOW" if vix_val < 14 else ("NORMAL" if vix_val < 18 else ("HIGH" if vix_val < 22 else "EXTREME"))
    row2[2].metric("India VIX", f"{vix_val:.1f}", delta=vix_regime,
                   delta_color="normal" if vix_val < 18 else "inverse")
else:
    row2[2].metric("India VIX", "N/A")

# Trend indicator
from src.engine.blast_filters import compute_trend_bias
trend_data = compute_trend_bias(st.session_state.blast_price_history)
trend_arrow = {"bullish": "UP", "bearish": "DOWN", "neutral": "FLAT"}.get(trend_data["trend"], "?")
row2[3].metric("Trend", trend_arrow, delta=f"{trend_data['strength']:.0%} strength",
               delta_color="normal" if trend_data["trend"] == "bullish" else (
                   "inverse" if trend_data["trend"] == "bearish" else "off"))

# Directional trend Telegram alert (sustained bullish/bearish only, not consolidation)
if TELEGRAM_ENABLED:
    dir_alert = st.session_state.directional_tracker.update(
        trend_data=trend_data,
        spot_price=spot_price,
        instrument=instrument_name,
    )
    if dir_alert is not None:
        if send_directional_alert(dir_alert):
            st.toast(f"Directional {trend_data['trend']} alert sent to Telegram!")
        else:
            err = get_last_send_error()
            st.warning(f"Directional alert failed: {err or 'Check Telegram credentials.'}")

# Prepare alert — early warning when price enters zone AND models are warming up
if TELEGRAM_ENABLED and PREPARE_ALERT_ENABLED:
    readiness = compute_blast_readiness(
        profile=profile,
        prev_profile=st.session_state.blast_prev_profile,
        chain_df=chain_df_filtered,
        prev_chain_df=st.session_state.blast_prev_chain,
        time_to_expiry_hours=tte,
        vix_value=vix_val,
    )
    prepare_msg = st.session_state.prepare_tracker.update(
        spot_price=spot_price,
        profile=profile,
        instrument=instrument_name,
        readiness=readiness,
    )
    if prepare_msg is not None:
        if send_prepare_alert(prepare_msg):
            st.toast("Prepare alert sent to Telegram!")
        else:
            err = get_last_send_error()
            st.warning(f"Prepare alert failed: {err or 'Check Telegram credentials.'}")

# =====================================================
# PLAIN ENGLISH SUMMARY — so you don't have to compare numbers
# =====================================================
st.markdown("---")
st.markdown("### What's Happening Right Now?")

_summary_points = []
_summary_color = "#26a69a"

# 1. Spot vs Gamma Flip (the most important comparison)
if profile.gamma_flip_level:
    _flip = profile.gamma_flip_level
    _diff = spot_price - _flip
    if _diff > 0:
        _summary_points.append(
            f'&#128994; <b>Spot ({spot_price:,.0f}) is ABOVE Gamma Flip ({_flip:,.0f}) by {_diff:,.0f} pts</b><br>'
            f'<span style="color:#aaa;">Market is in STABLE zone — dealers are cushioning moves. '
            f'Price tends to stay in a range. Good for selling options.</span>'
        )
    else:
        _summary_color = "#ef5350"
        _summary_points.append(
            f'&#128308; <b>Spot ({spot_price:,.0f}) is BELOW Gamma Flip ({_flip:,.0f}) by {abs(_diff):,.0f} pts</b><br>'
            f'<span style="color:#aaa;">Market is in VOLATILE zone — moves get amplified! '
            f'Dealers are adding fuel to the fire. Be careful, big swings possible.</span>'
        )
else:
    _summary_points.append(
        '&#128993; <b>Gamma Flip level not available</b><br>'
        '<span style="color:#aaa;">Cannot determine market regime right now.</span>'
    )

# 2. Where is price relative to walls?
if profile.call_wall and profile.put_wall:
    _cw = profile.call_wall
    _pw = profile.put_wall
    _range = _cw - _pw
    _dist_to_ceiling = _cw - spot_price
    _dist_to_floor = spot_price - _pw

    if _range > 0 and _dist_to_ceiling < _range * 0.15:
        _summary_points.append(
            f'&#9888; <b>Price is VERY CLOSE to Ceiling ({_cw:,.0f}) — only {_dist_to_ceiling:,.0f} pts away!</b><br>'
            f'<span style="color:#aaa;">Price may bounce down from here. '
            f'OR if it breaks above → fast move UP expected.</span>'
        )
    elif _range > 0 and _dist_to_floor < _range * 0.15:
        _summary_points.append(
            f'&#9888; <b>Price is VERY CLOSE to Floor ({_pw:,.0f}) — only {_dist_to_floor:,.0f} pts away!</b><br>'
            f'<span style="color:#aaa;">Price may bounce up from here. '
            f'OR if it breaks below → fast move DOWN expected.</span>'
        )
    else:
        _summary_points.append(
            f'&#128205; <b>Price is between Floor ({_pw:,.0f}) and Ceiling ({_cw:,.0f})</b><br>'
            f'<span style="color:#aaa;">{_dist_to_floor:,.0f} pts above floor, '
            f'{_dist_to_ceiling:,.0f} pts below ceiling. '
            f'Price is in the middle — no immediate wall pressure.</span>'
        )

# 3. VIX summary
if vix_val:
    if vix_val < 14:
        _summary_points.append(
            f'&#128154; <b>VIX is LOW ({vix_val:.1f}) — Market is calm</b><br>'
            f'<span style="color:#aaa;">Small moves expected. Less chance of a Gamma Blast.</span>'
        )
    elif vix_val < 18:
        _summary_points.append(
            f'&#128154; <b>VIX is NORMAL ({vix_val:.1f})</b><br>'
            f'<span style="color:#aaa;">Nothing unusual. Normal market conditions.</span>'
        )
    elif vix_val < 22:
        _summary_points.append(
            f'&#128992; <b>VIX is HIGH ({vix_val:.1f}) — Market is nervous</b><br>'
            f'<span style="color:#aaa;">Bigger swings expected. Gamma Blast signals more likely.</span>'
        )
    else:
        _summary_points.append(
            f'&#128308; <b>VIX is EXTREME ({vix_val:.1f}) — Market is very scared!</b><br>'
            f'<span style="color:#aaa;">Wild moves possible. Be very careful with positions.</span>'
        )

# 4. Expiry time
if is_expiry:
    if tte < 2:
        _summary_points.append(
            f'&#9200; <b>Only {tte:.1f} hours left to expiry!</b><br>'
            f'<span style="color:#aaa;">Last stretch — options are dying fast. '
            f'This is when Gamma Blasts are most likely. Stay alert!</span>'
        )
    elif tte < 4:
        _summary_points.append(
            f'&#9200; <b>{tte:.1f} hours to expiry — Charm zone approaching</b><br>'
            f'<span style="color:#aaa;">After 1:30 PM, options decay accelerates. '
            f'Watch for signals in the next few hours.</span>'
        )
    else:
        _summary_points.append(
            f'&#9200; <b>{tte:.1f} hours to expiry</b><br>'
            f'<span style="color:#aaa;">Still early in the day. Best signals usually come after lunch.</span>'
        )

# Render summary box
_summary_html = ""
for pt in _summary_points:
    _summary_html += (
        f'<div style="background:#1a1a2e; border-left:4px solid {_summary_color}; '
        f'border-radius:0 8px 8px 0; padding:12px 16px; margin:8px 0; line-height:1.6;">'
        f'{pt}</div>'
    )

st.markdown(_summary_html, unsafe_allow_html=True)

st.markdown("---")

# Run gamma blast detection with all filters
blast = detect_gamma_blast(
    profile=profile,
    prev_profile=st.session_state.blast_prev_profile,
    chain_df=chain_df_filtered,
    prev_chain_df=st.session_state.blast_prev_chain,
    time_to_expiry_hours=tte,
    fired_today=st.session_state.blast_fired_today,
    last_blast_time=st.session_state.blast_last_time,
    price_history=st.session_state.blast_price_history,
    vix_value=vix_val,
    expiry_date=expiry_date,
)

# Update state for next iteration
st.session_state.blast_prev_profile = profile
st.session_state.blast_prev_chain = chain_df_filtered.copy()

if blast is not None:
    # BLAST DETECTED — deduplicate against Streamlit re-renders
    blast_id = f"{blast.instrument}_{blast.timestamp.isoformat()}"
    existing_ids = {
        f"{b.instrument}_{b.timestamp.isoformat()}"
        for b in st.session_state.blast_history
    }
    if blast_id not in existing_ids:
        st.session_state.blast_fired_today += 1
        st.session_state.blast_last_time = blast.timestamp
        st.session_state.blast_history.append(blast)

    # Send Telegram alert (only once per blast, keyed by timestamp)
    blast_id = f"{blast.instrument}_{blast.timestamp.isoformat()}"
    if TELEGRAM_ENABLED and blast_id not in st.session_state.blast_alert_sent_ids:
        if send_blast_alert(blast):
            st.session_state.blast_alert_sent_ids.add(blast_id)
            st.toast("Telegram alert sent!")
        else:
            # Show error feedback instead of silently failing
            err = get_last_send_error()
            st.error(f"Telegram alert FAILED: {err or 'Unknown error. Check credentials.'}")

    render_blast_alert(blast)

    with st.expander("Model Breakdown", expanded=True):
        render_blast_components(blast)

    # Show filter details
    filters = blast.metadata.get("filters_applied", {})
    if filters:
        with st.expander("Quality Filters Applied"):
            raw = blast.metadata.get("raw_score", 0)
            final = filters.get("final_score", 0)
            st.markdown(f"**Raw Score:** {raw:.0f} → **Filtered Score:** {final:.0f}")

            trend_info = filters.get("trend", {})
            if trend_info:
                st.markdown(f"- **Trend:** {trend_info.get('trend', '?')} "
                           f"(strength {trend_info.get('strength', 0):.0%})")

            vix_info = filters.get("vix_regime", {})
            if vix_info:
                st.markdown(f"- **VIX Regime:** {vix_info.get('regime', '?')} "
                           f"(adj: -{vix_info.get('threshold_adjustment', 0)} pts)")

            vol_info = filters.get("volume", {})
            if vol_info:
                st.markdown(f"- **Volume:** {'Confirmed' if vol_info.get('confirmed') else 'Weak'} "
                           f"(score {vol_info.get('volume_score', 0):.0f}, "
                           f"dominant: {vol_info.get('dominant_side', '?')})")

            max_pain = filters.get("max_pain")
            if max_pain:
                st.markdown(f"- **Max Pain:** {max_pain:,.0f}")

            st.markdown(f"- **Monthly Expiry:** {'Yes' if filters.get('is_monthly') else 'No'}")
else:
    render_no_blast_status(
        instrument=instrument_name,
        is_expiry_day=is_expiry,
        time_to_expiry_hours=tte,
        fired_today=st.session_state.blast_fired_today,
        max_signals=BLAST_MAX_SIGNALS_PER_DAY if is_expiry else BLAST_MAX_SIGNALS_NORMAL_DAY,
    )

# Also generate standard GEX signals for context
signals = generate_signals(profile, st.session_state.blast_prev_profile, tte)
if signals:
    st.markdown("---")
    st.subheader("GEX Signals (Context)")
    for sig in signals:
        if sig.direction == "bullish":
            arrow = "BULLISH"
            css = "signal-bullish"
        elif sig.direction == "bearish":
            arrow = "BEARISH"
            css = "signal-bearish"
        else:
            arrow = "NEUTRAL"
            css = "signal-neutral"
        st.markdown(
            f'<div class="signal-card {css}">'
            f'<strong>{sig.signal_type.replace("_", " ").upper()}</strong> '
            f'@ {sig.level:,.2f} | Strength: {sig.strength:.0%} | {arrow}'
            f'</div>',
            unsafe_allow_html=True,
        )

# Blast history for today
if st.session_state.blast_history:
    st.markdown("---")
    _max_sig = BLAST_MAX_SIGNALS_PER_DAY if is_expiry else BLAST_MAX_SIGNALS_NORMAL_DAY
    st.subheader(f"Blast History ({len(st.session_state.blast_history)}/{_max_sig})")
    for i, b in enumerate(reversed(st.session_state.blast_history), 1):
        dir_icon = "UP" if b.direction == "bullish" else "DOWN"
        st.markdown(
            f"**#{i}** {dir_icon} {b.direction.upper()} @ {b.timestamp:%H:%M:%S} — "
            f"Score: {b.composite_score:.0f} | "
            f"Entry: {b.entry_level:,.2f} | SL: {b.stop_loss:,.2f} | "
            f"Target: {b.target:,.2f}"
        )

# Footer with model info
with st.expander("About Gamma Blast Models"):
    st.markdown("""
**6 Models + 10 Quality Filters + 3 Quality Gates for >85% Accuracy:**

**Models (weights adapt to VIX regime):**
1. **GEX Zero-Cross Cascade** — Spot crosses gamma flip, triggering dealer hedging cascade
2. **Gamma Wall Breach** — Price breaks call/put wall with velocity confirmation
3. **Charm Flow Accelerator** — Expiry-day delta decay creates directional dealer flow
4. **Negative Gamma Squeeze** — In negative gamma, dealer hedging amplifies moves
5. **Pin Break Blast** — Price breaks away from max gamma pin strike
6. **Vanna Squeeze** — IV crush + vanna exposure creates directional hedging flow

**10 Quality Filters:**
1. **Trend Filter** — EMA-based, penalizes counter-trend blasts by up to 25 pts
2. **VIX Regime** — Adapts weights & threshold (Low/Normal/High/Extreme vol)
3. **Volume Confirmation** — Requires ATM volume spike, 30% penalty if absent
4. **Smart Timing** — Morning signals penalized 40%, charm zone (1:30 PM+) boosted 15%
5. **Monthly vs Weekly** — Suppresses breakouts near max gamma on monthly expiry
6. **Sensex Liquidity** — Penalizes low-OI chains (BSE has 10x less liquidity)
7. **Max Pain Proximity** — Suppresses signals when pinned near max pain close to expiry
8. **PCR Confirmation** — Put-Call Ratio must align with blast direction
9. **IV Skew** — ATM put-call IV spread confirms institutional positioning
10. **Volume-Direction Alignment** — ATM volume dominant side must match blast direction

**3 Quality Gates:**
- **Model Confluence** — Non-linear boost when 3+ models fire together
- **Direction Conviction** — 15% margin required between bull/bear votes
- **R:R Gate** — Risk:Reward must be at least 1:1

**Rules:**
- Filtered composite score must reach **70+** to fire (65 safety net late on expiry)
- Maximum **4 signals** on expiry day, **2** on normal days
- **15-min cooldown** on expiry day, **30-min** on normal days
- Entry/SL/Target **dynamic by VIX** — wider in high vol, tighter in low vol
- All times in **IST**
    """)
