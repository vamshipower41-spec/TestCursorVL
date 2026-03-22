"""Action Center — One page, one answer.

Pulls data from all engines and tells you in plain English:
- What to do RIGHT NOW (wait / buy call / buy put)
- Why (the key reasons)
- Key numbers you need (entry, stop loss, target)

Designed for beginners who don't want to check 5 different pages.
"""

import sys
from pathlib import Path

try:
    _project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
except NameError:
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
    UPSTOX_BASE_URL,
)
from src.auth.upstox_auth import load_access_token
from src.data.options_chain import OptionsChainFetcher
from src.engine.gex_calculator import build_gex_profile
from src.engine.greeks import validate_greeks, filter_active_strikes
from src.engine.signal_generator import generate_signals
from src.engine.gamma_blast import detect_gamma_blast
from src.engine.blast_filters import compute_trend_bias, compute_pcr, compute_iv_skew
from src.engine.multi_expiry_gex import aggregate_multi_expiry_gex
from src.engine.oi_flow import classify_oi_flow
from src.engine.bs_greeks import compute_dealer_charm_flow
from config.settings import MULTI_EXPIRY_COUNT


# --- Setup ---
EXPIRY_DAYS = {"NIFTY": 1, "SENSEX": 3}


def _get_todays_expiry_instrument():
    today_weekday = today_ist().weekday()
    for name, weekday in EXPIRY_DAYS.items():
        if today_weekday == weekday:
            return name
    return None


def _is_expiry_day(instrument_name):
    return today_ist().weekday() == EXPIRY_DAYS.get(instrument_name, -1)


# --- Page Header ---
st.markdown("""
<div style="text-align:center; margin-bottom:4px;">
    <span style="font-size:2.2rem; font-weight:900;">Action Center</span><br>
    <span style="font-size:1rem; color:#888;">One page. One answer. What should I do right now?</span>
</div>
""", unsafe_allow_html=True)

# Auto-detect instrument
expiry_instrument = _get_todays_expiry_instrument()
instrument_name = st.radio(
    "Index",
    list(INSTRUMENTS.keys()),
    index=list(INSTRUMENTS.keys()).index(expiry_instrument) if expiry_instrument else 0,
    horizontal=True,
)

is_expiry = _is_expiry_day(instrument_name)
refresh_interval = EXPIRY_DAY_POLL_INTERVAL if is_expiry else DASHBOARD_REFRESH_INTERVAL
if HAS_AUTOREFRESH:
    st_autorefresh(interval=refresh_interval * 1000, key="action_refresh")

# --- Session State ---
for key, default in [
    ("action_prev_profile", None),
    ("action_prev_chain", None),
    ("action_fired_today", 0),
    ("action_last_time", None),
    ("action_price_history", []),
    ("action_vix_value", None),
    ("action_blast_history", []),
    ("action_last_date", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# Reset daily
if st.session_state.action_last_date != today_ist().isoformat():
    st.session_state.action_fired_today = 0
    st.session_state.action_last_time = None
    st.session_state.action_blast_history = []
    st.session_state.action_last_date = today_ist().isoformat()

# --- Fetch Data ---
try:
    token = load_access_token()
except ValueError:
    st.error("Not logged in. Please refresh the page to login.")
    st.stop()

inst = get_instrument(instrument_name)
fetcher = OptionsChainFetcher(token)

try:
    expiry_date = fetcher.get_nearest_expiry(inst["instrument_key"])
    chain_df, spot_price = fetcher.fetch_chain(inst["instrument_key"], expiry_date)
except Exception as e:
    st.error(f"Cannot fetch data: {e}")
    st.stop()

if chain_df.empty:
    st.warning("No data available right now.")
    st.stop()

# --- Process ---
chain_df_clean = validate_greeks(chain_df)
chain_df_filtered = filter_active_strikes(chain_df_clean, spot_price, num_strikes=40)
profile = build_gex_profile(
    chain_df_filtered, spot_price, inst["contract_multiplier"],
    instrument_name, expiry_date,
)

# Track prices
st.session_state.action_price_history.append(spot_price)
st.session_state.action_price_history = st.session_state.action_price_history[-20:]

# Fetch VIX
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
                st.session_state.action_vix_value = ltp
except Exception:
    pass

vix_val = st.session_state.action_vix_value
tte = compute_tte(expiry_date)
trend_data = compute_trend_bias(st.session_state.action_price_history)

# --- Multi-Expiry GEX ---
multi_expiry_info = None
try:
    expiry_chains_raw = fetcher.fetch_multi_expiry_chains(
        inst["instrument_key"], count=MULTI_EXPIRY_COUNT,
    )
    expiry_chains = []
    for exp, cdf, sp in expiry_chains_raw:
        if not cdf.empty:
            cdf = validate_greeks(cdf)
            cdf = filter_active_strikes(cdf, sp, num_strikes=40)
            exp_tte = compute_tte(exp)
            expiry_chains.append((exp, cdf, exp_tte))
    if expiry_chains:
        multi_expiry_info = aggregate_multi_expiry_gex(
            expiry_chains, spot_price, inst["contract_multiplier"],
        )
except Exception:
    pass

# --- OI Flow ---
oi_flow_info = None
prev_chain = st.session_state.action_prev_chain
if prev_chain is not None and not prev_chain.empty:
    try:
        oi_flow_info = classify_oi_flow(chain_df_filtered, prev_chain, spot_price)
    except Exception:
        pass

# --- PCR & IV Skew ---
pcr_info = None
iv_skew_info = None
try:
    pcr_info = compute_pcr(chain_df_filtered)
except Exception:
    pass
try:
    iv_skew_info = compute_iv_skew(chain_df_filtered, spot_price)
except Exception:
    pass

# --- Charm Flow ---
charm_info = None
try:
    charm_info = compute_dealer_charm_flow(
        chain_df_filtered, spot_price, tte, inst["contract_multiplier"],
    )
except Exception:
    pass

# Run blast detection
blast = detect_gamma_blast(
    profile=profile,
    prev_profile=st.session_state.action_prev_profile,
    chain_df=chain_df_filtered,
    prev_chain_df=st.session_state.action_prev_chain,
    time_to_expiry_hours=tte,
    fired_today=st.session_state.action_fired_today,
    last_blast_time=st.session_state.action_last_time,
    price_history=st.session_state.action_price_history,
    vix_value=vix_val,
    expiry_date=expiry_date,
)

# Update state
st.session_state.action_prev_profile = profile
st.session_state.action_prev_chain = chain_df_filtered.copy()

if blast is not None:
    blast_id = f"{blast.instrument}_{blast.timestamp.isoformat()}"
    existing_ids = {
        f"{b.instrument}_{b.timestamp.isoformat()}"
        for b in st.session_state.action_blast_history
    }
    if blast_id not in existing_ids:
        st.session_state.action_fired_today += 1
        st.session_state.action_last_time = blast.timestamp
        st.session_state.action_blast_history.append(blast)

# Generate signals
signals = generate_signals(profile, st.session_state.action_prev_profile, tte)

# --- Gather key levels ---
flip = profile.gamma_flip_level
call_wall = profile.call_wall
put_wall = profile.put_wall
magnet = profile.max_gamma_strike
net_gex = profile.net_gex_total
trend = trend_data.get("trend", "neutral")
trend_strength = trend_data.get("strength", 0)


# =====================================================
# THE BIG ANSWER — What should I do right now?
# =====================================================

if blast is not None:
    # ---- BLAST FIRED — TRADE NOW ----
    is_bull = blast.direction == "bullish"
    action_text = "BUY CALL (CE) NOW" if is_bull else "BUY PUT (PE) NOW"
    action_color = "#26a69a" if is_bull else "#ef5350"
    action_bg = "#0d2818" if is_bull else "#2d0a0a"
    dir_word = "UP" if is_bull else "DOWN"
    confidence = blast.composite_score

    risk = abs(blast.entry_level - blast.stop_loss)
    reward = abs(blast.target - blast.entry_level)
    rr = reward / risk if risk > 0 else 0

    st.markdown(f"""
    <div style="background:{action_bg}; border:3px solid {action_color}; border-radius:20px;
                padding:32px; text-align:center; margin:16px 0;">
        <div style="font-size:1rem; color:#888; text-transform:uppercase; letter-spacing:2px;">
            What To Do Right Now
        </div>
        <div style="font-size:2.8rem; font-weight:900; color:{action_color}; margin:8px 0;">
            {action_text}
        </div>
        <div style="font-size:1.2rem; color:#ccc; margin:8px 0;">
            Market is moving <b style="color:{action_color};">{dir_word}</b>
            &nbsp;&bull;&nbsp; Confidence: <b>{confidence:.0f}%</b>
        </div>
        <div style="margin-top:20px; display:flex; justify-content:center; gap:40px;">
            <div>
                <div style="color:#888; font-size:0.9rem;">Enter at</div>
                <div style="color:white; font-size:1.8rem; font-weight:900;">{blast.entry_level:,.2f}</div>
            </div>
            <div>
                <div style="color:#ef5350; font-size:0.9rem;">Stop Loss</div>
                <div style="color:#ef5350; font-size:1.8rem; font-weight:900;">{blast.stop_loss:,.2f}</div>
                <div style="color:#888; font-size:0.85rem;">{risk:,.0f} pts risk</div>
            </div>
            <div>
                <div style="color:#26a69a; font-size:0.9rem;">Target</div>
                <div style="color:#26a69a; font-size:1.8rem; font-weight:900;">{blast.target:,.2f}</div>
                <div style="color:#888; font-size:0.85rem;">{reward:,.0f} pts reward</div>
            </div>
        </div>
        <div style="color:#888; font-size:0.95rem; margin-top:16px;">
            Risk : Reward = 1 : {rr:.1f} &nbsp;&bull;&nbsp; {blast.timestamp:%H:%M:%S} IST
        </div>
    </div>
    """, unsafe_allow_html=True)

else:
    # ---- NO BLAST — Figure out what to tell the user ----

    # Determine the overall situation
    action_text = "WAIT"
    action_detail = ""
    action_color = "#ffc107"
    action_bg = "#2d2a0a"
    watch_level = None
    watch_label = ""

    if not is_expiry:
        day_name = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
        today_name = day_name.get(date.today().weekday(), "?")
        nifty_day = "Tuesday"
        sensex_day = "Thursday"
        action_text = "NO TRADE TODAY"
        action_detail = (
            f"Today is {today_name} — not an expiry day. "
            f"Best signals come on expiry days (NIFTY: {nifty_day}, SENSEX: {sensex_day}). "
            f"Relax and prepare for the next expiry."
        )
        action_color = "#888"
        action_bg = "#1a1a2e"

    elif net_gex > 0 and flip:
        # Positive gamma — market is stable
        dist_above_flip = spot_price - flip
        if dist_above_flip > 0:
            action_text = "WAIT — Market is Calm"
            if put_wall:
                watch_level = flip
                watch_label = "Gamma Flip"
                action_detail = (
                    f"Price ({spot_price:,.0f}) is {dist_above_flip:,.0f} pts ABOVE the Gamma Flip ({flip:,.0f}). "
                    f"Dealers are keeping the market stable. No big move expected right now. "
                    f"Wait for price to drop near {flip:,.0f} — that's when things could get interesting."
                )
            else:
                action_detail = (
                    f"Price is in the stable zone above Gamma Flip. "
                    f"Market is range-bound. Wait for a setup."
                )
        else:
            action_text = "WATCH CLOSELY"
            action_color = "#ff9800"
            action_detail = (
                f"Price ({spot_price:,.0f}) just crossed below Gamma Flip ({flip:,.0f})! "
                f"Market could become volatile soon. Stay alert for a Gamma Blast signal."
            )

    elif net_gex < 0 and flip:
        # Negative gamma — volatile
        dist_below_flip = flip - spot_price
        action_color = "#ff9800"
        action_bg = "#2d1a0a"

        if trend == "bearish" and trend_strength > 0.4:
            action_text = "ALERT — Bearish Momentum"
            action_detail = (
                f"Price ({spot_price:,.0f}) is {dist_below_flip:,.0f} pts BELOW Gamma Flip ({flip:,.0f}) "
                f"and trending DOWN with {trend_strength:.0%} strength. "
                f"Dealers are amplifying the fall. "
                f"Watch for a Gamma Blast PUT signal any moment."
            )
            if put_wall:
                watch_level = put_wall
                watch_label = "Floor (Put Wall)"
        elif trend == "bullish" and trend_strength > 0.4:
            action_text = "ALERT — Bullish Push in Volatile Zone"
            action_detail = (
                f"Price ({spot_price:,.0f}) is BELOW Gamma Flip ({flip:,.0f}) but pushing UP. "
                f"If it breaks back above {flip:,.0f}, expect a strong rally. "
                f"Watch for a Gamma Blast CALL signal."
            )
            watch_level = flip
            watch_label = "Gamma Flip"
        else:
            action_text = "WATCH — Volatile Zone"
            action_detail = (
                f"Price ({spot_price:,.0f}) is {dist_below_flip:,.0f} pts BELOW Gamma Flip ({flip:,.0f}). "
                f"Market is in the volatile zone where moves get amplified. "
                f"No clear direction yet. Wait for the blast signal."
            )
    else:
        action_text = "WAIT — Scanning"
        action_detail = (
            f"Analyzing {instrument_name} at {spot_price:,.0f}. "
            f"No clear setup right now. The system is watching for you."
        )

    st.markdown(f"""
    <div style="background:{action_bg}; border:3px solid {action_color}; border-radius:20px;
                padding:32px; text-align:center; margin:16px 0;">
        <div style="font-size:1rem; color:#888; text-transform:uppercase; letter-spacing:2px;">
            What To Do Right Now
        </div>
        <div style="font-size:2.5rem; font-weight:900; color:{action_color}; margin:12px 0;">
            {action_text}
        </div>
        <div style="font-size:1.1rem; color:#bbb; margin:8px 24px; line-height:1.7;">
            {action_detail}
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Show what level to watch
    if watch_level:
        dist = abs(spot_price - watch_level)
        st.markdown(f"""
        <div style="background:#1a1a2e; border:1px solid #444; border-radius:12px;
                    padding:16px; text-align:center; margin:8px 0;">
            <div style="color:#888; font-size:0.9rem;">Key Level to Watch</div>
            <div style="color:white; font-size:2rem; font-weight:900;">{watch_level:,.0f}</div>
            <div style="color:#888; font-size:0.95rem;">
                {watch_label} &nbsp;&bull;&nbsp; Currently {dist:,.0f} pts away
            </div>
        </div>
        """, unsafe_allow_html=True)


# =====================================================
# QUICK STATUS — 6 simple cards (2 rows of 3)
# =====================================================
st.markdown("---")

# Row 1: Trend, Zone, VIX
c1, c2, c3 = st.columns(3)

# Card 1: Market Direction
if trend == "bullish":
    c1.markdown(f"""
    <div style="background:#0d2818; border-radius:12px; padding:16px; text-align:center;">
        <div style="color:#26a69a; font-size:1.5rem; font-weight:900;">UP</div>
        <div style="color:#888; font-size:0.85rem;">Market Trend</div>
        <div style="color:#aaa; font-size:0.8rem;">{trend_strength:.0%} strong</div>
    </div>
    """, unsafe_allow_html=True)
elif trend == "bearish":
    c1.markdown(f"""
    <div style="background:#2d0a0a; border-radius:12px; padding:16px; text-align:center;">
        <div style="color:#ef5350; font-size:1.5rem; font-weight:900;">DOWN</div>
        <div style="color:#888; font-size:0.85rem;">Market Trend</div>
        <div style="color:#aaa; font-size:0.8rem;">{trend_strength:.0%} strong</div>
    </div>
    """, unsafe_allow_html=True)
else:
    c1.markdown(f"""
    <div style="background:#1a1a2e; border-radius:12px; padding:16px; text-align:center;">
        <div style="color:#ffc107; font-size:1.5rem; font-weight:900;">FLAT</div>
        <div style="color:#888; font-size:0.85rem;">Market Trend</div>
        <div style="color:#aaa; font-size:0.8rem;">No clear direction</div>
    </div>
    """, unsafe_allow_html=True)

# Card 2: Zone
zone_text = "STABLE" if net_gex > 0 else "VOLATILE"
zone_color = "#26a69a" if net_gex > 0 else "#ef5350"
zone_bg = "#0d2818" if net_gex > 0 else "#2d0a0a"
zone_desc = "Calm moves" if net_gex > 0 else "Wild moves!"
c2.markdown(f"""
<div style="background:{zone_bg}; border-radius:12px; padding:16px; text-align:center;">
    <div style="color:{zone_color}; font-size:1.5rem; font-weight:900;">{zone_text}</div>
    <div style="color:#888; font-size:0.85rem;">Market Zone</div>
    <div style="color:#aaa; font-size:0.8rem;">{zone_desc}</div>
</div>
""", unsafe_allow_html=True)

# Card 3: VIX
if vix_val:
    if vix_val < 14:
        vix_text, vix_color, vix_bg = "LOW", "#26a69a", "#0d2818"
    elif vix_val < 18:
        vix_text, vix_color, vix_bg = "NORMAL", "#4a90d9", "#0a1a2d"
    elif vix_val < 22:
        vix_text, vix_color, vix_bg = "HIGH", "#ff9800", "#2d1a0a"
    else:
        vix_text, vix_color, vix_bg = "EXTREME", "#ef5350", "#2d0a0a"
    c3.markdown(f"""
    <div style="background:{vix_bg}; border-radius:12px; padding:16px; text-align:center;">
        <div style="color:{vix_color}; font-size:1.5rem; font-weight:900;">{vix_text}</div>
        <div style="color:#888; font-size:0.85rem;">Fear Level (VIX)</div>
        <div style="color:#aaa; font-size:0.8rem;">{vix_val:.1f}</div>
    </div>
    """, unsafe_allow_html=True)
else:
    c3.markdown("""
    <div style="background:#1a1a2e; border-radius:12px; padding:16px; text-align:center;">
        <div style="color:#888; font-size:1.5rem; font-weight:900;">N/A</div>
        <div style="color:#888; font-size:0.85rem;">Fear Level (VIX)</div>
    </div>
    """, unsafe_allow_html=True)

# Row 2: OI Flow, PCR, Time to Expiry
c4, c5, c6 = st.columns(3)

# Card 4: OI Flow — Who's buying?
if oi_flow_info:
    _dom = oi_flow_info.get("dominant_flow", "neutral")
    _conf = oi_flow_info.get("flow_confidence", 0)
    if _dom == "bullish":
        oi_text, oi_color, oi_bg = "BUYING", "#26a69a", "#0d2818"
        oi_desc = "Big players buying calls"
    elif _dom == "bearish":
        oi_text, oi_color, oi_bg = "SELLING", "#ef5350", "#2d0a0a"
        oi_desc = "Big players buying puts"
    else:
        oi_text, oi_color, oi_bg = "MIXED", "#ffc107", "#2d2a0a"
        oi_desc = "No clear flow"
    c4.markdown(f"""
    <div style="background:{oi_bg}; border-radius:12px; padding:16px; text-align:center;">
        <div style="color:{oi_color}; font-size:1.5rem; font-weight:900;">{oi_text}</div>
        <div style="color:#888; font-size:0.85rem;">Money Flow (OI)</div>
        <div style="color:#aaa; font-size:0.8rem;">{oi_desc}</div>
    </div>
    """, unsafe_allow_html=True)
else:
    c4.markdown("""
    <div style="background:#1a1a2e; border-radius:12px; padding:16px; text-align:center;">
        <div style="color:#888; font-size:1.5rem; font-weight:900;">LOADING</div>
        <div style="color:#888; font-size:0.85rem;">Money Flow (OI)</div>
        <div style="color:#aaa; font-size:0.8rem;">Needs 2 refreshes</div>
    </div>
    """, unsafe_allow_html=True)

# Card 5: Put-Call Ratio
if pcr_info:
    _pcr = pcr_info.get("pcr", 0)
    _pcr_sig = pcr_info.get("pcr_signal", "neutral")
    if _pcr_sig == "bullish":
        pcr_text, pcr_color, pcr_bg = "BULLISH", "#26a69a", "#0d2818"
        pcr_desc = "Heavy put writing = support"
    elif _pcr_sig == "bearish":
        pcr_text, pcr_color, pcr_bg = "BEARISH", "#ef5350", "#2d0a0a"
        pcr_desc = "Heavy call writing = resistance"
    else:
        pcr_text, pcr_color, pcr_bg = "NEUTRAL", "#4a90d9", "#0a1a2d"
        pcr_desc = "Balanced positioning"
    c5.markdown(f"""
    <div style="background:{pcr_bg}; border-radius:12px; padding:16px; text-align:center;">
        <div style="color:{pcr_color}; font-size:1.5rem; font-weight:900;">{pcr_text}</div>
        <div style="color:#888; font-size:0.85rem;">Put-Call Ratio</div>
        <div style="color:#aaa; font-size:0.8rem;">PCR: {_pcr:.2f}</div>
    </div>
    """, unsafe_allow_html=True)
else:
    c5.markdown("""
    <div style="background:#1a1a2e; border-radius:12px; padding:16px; text-align:center;">
        <div style="color:#888; font-size:1.5rem; font-weight:900;">N/A</div>
        <div style="color:#888; font-size:0.85rem;">Put-Call Ratio</div>
    </div>
    """, unsafe_allow_html=True)

# Card 6: Time to Expiry
if is_expiry:
    if tte < 2:
        tte_text, tte_color, tte_bg = f"{tte:.1f}h", "#ef5350", "#2d0a0a"
        tte_desc = "Almost over!"
    elif tte < 4:
        tte_text, tte_color, tte_bg = f"{tte:.1f}h", "#ff9800", "#2d1a0a"
        tte_desc = "Prime time soon"
    else:
        tte_text, tte_color, tte_bg = f"{tte:.1f}h", "#4a90d9", "#0a1a2d"
        tte_desc = "Still early"
else:
    tte_text, tte_color, tte_bg = "N/A", "#888", "#1a1a2e"
    tte_desc = "Not expiry day"

c6.markdown(f"""
<div style="background:{tte_bg}; border-radius:12px; padding:16px; text-align:center;">
    <div style="color:{tte_color}; font-size:1.5rem; font-weight:900;">{tte_text}</div>
    <div style="color:#888; font-size:0.85rem;">Time to Expiry</div>
    <div style="color:#aaa; font-size:0.8rem;">{tte_desc}</div>
</div>
""", unsafe_allow_html=True)


# =====================================================
# MARKET INTELLIGENCE — All confirmations in plain English
# =====================================================
st.markdown("---")
st.markdown("### Market Intelligence")
st.caption("All engines combined — what each one is telling us right now")

_intel = []

# 1. OI Flow insight
if oi_flow_info:
    _dom = oi_flow_info.get("dominant_flow", "neutral")
    _conf = oi_flow_info.get("flow_confidence", 0)
    _net_bull = oi_flow_info.get("net_bought_calls", 0) + oi_flow_info.get("net_sold_puts", 0)
    _net_bear = oi_flow_info.get("net_bought_puts", 0) + oi_flow_info.get("net_sold_calls", 0)
    if _dom == "bullish":
        _intel.append(("&#128176;", "Money Flow", "BULLISH", "#26a69a",
                       f"Big players are net bullish — bought calls + sold puts = {_net_bull:,} "
                       f"vs bearish {_net_bear:,}. Confidence: {_conf:.0%}"))
    elif _dom == "bearish":
        _intel.append(("&#128176;", "Money Flow", "BEARISH", "#ef5350",
                       f"Big players are net bearish — bought puts + sold calls = {_net_bear:,} "
                       f"vs bullish {_net_bull:,}. Confidence: {_conf:.0%}"))
    else:
        _intel.append(("&#128176;", "Money Flow", "MIXED", "#ffc107",
                       f"No clear direction from options flow. Bull: {_net_bull:,}, Bear: {_net_bear:,}"))

# 2. PCR insight
if pcr_info:
    _pcr = pcr_info.get("pcr", 0)
    _pcr_sig = pcr_info.get("pcr_signal", "neutral")
    if _pcr_sig == "bullish":
        _intel.append(("&#9878;", "Put-Call Ratio", f"BULLISH ({_pcr:.2f})", "#26a69a",
                       f"PCR above 1.3 means heavy put writing — institutions are providing "
                       f"support below. They don't expect the market to fall much."))
    elif _pcr_sig == "bearish":
        _intel.append(("&#9878;", "Put-Call Ratio", f"BEARISH ({_pcr:.2f})", "#ef5350",
                       f"PCR below 0.7 means heavy call writing — institutions see resistance "
                       f"above. They don't expect the market to rise much."))
    else:
        _intel.append(("&#9878;", "Put-Call Ratio", f"NEUTRAL ({_pcr:.2f})", "#4a90d9",
                       f"Put-Call Ratio is balanced. No strong bias from options writers."))

# 3. IV Skew insight
if iv_skew_info:
    _skew = iv_skew_info.get("iv_skew", 0)
    _skew_sig = iv_skew_info.get("skew_signal", "neutral")
    if _skew_sig == "bearish":
        _intel.append(("&#128200;", "IV Skew", "BEARISH", "#ef5350",
                       f"Put premiums are higher than calls (skew: {_skew:.2f}). "
                       f"Traders are paying more for downside protection — they're worried about a fall."))
    elif _skew_sig == "bullish":
        _intel.append(("&#128200;", "IV Skew", "BULLISH", "#26a69a",
                       f"Call premiums are higher than puts (skew: {_skew:.2f}). "
                       f"Traders are paying more for upside — they expect a rally."))
    else:
        _intel.append(("&#128200;", "IV Skew", "NEUTRAL", "#4a90d9",
                       f"IV skew is flat ({_skew:.2f}). No unusual directional bets from options traders."))

# 4. Charm Flow insight
if charm_info:
    _charm_flow = charm_info.get("net_charm_flow", 0)
    _charm_intensity = charm_info.get("charm_intensity", 0)
    if _charm_flow > 0 and _charm_intensity > 20:
        _intel.append(("&#9203;", "Time Decay Flow", "BULLISH", "#26a69a",
                       f"As options expire, dealers need to BUY the index to stay hedged. "
                       f"This creates upward pressure. Intensity: {_charm_intensity:.0f}/100"))
    elif _charm_flow < 0 and _charm_intensity > 20:
        _intel.append(("&#9203;", "Time Decay Flow", "BEARISH", "#ef5350",
                       f"As options expire, dealers need to SELL the index to stay hedged. "
                       f"This creates downward pressure. Intensity: {_charm_intensity:.0f}/100"))
    else:
        _intel.append(("&#9203;", "Time Decay Flow", "NEUTRAL", "#888",
                       f"Time decay effect is weak right now. Intensity: {_charm_intensity:.0f}/100"))

# 5. Multi-Expiry insight
if multi_expiry_info:
    _wflip = multi_expiry_info.get("weighted_gamma_flip")
    _r_calls = multi_expiry_info.get("reinforced_call_walls", [])
    _r_puts = multi_expiry_info.get("reinforced_put_walls", [])
    _parts = []
    if _wflip and flip:
        _diff = abs(_wflip - flip)
        if _diff > 50:
            _parts.append(
                f"Multi-expiry flip ({_wflip:,.0f}) differs from single ({flip:,.0f}) by {_diff:,.0f} pts. "
                f"Trust the multi-expiry level more.")
        else:
            _parts.append(f"Both single and multi-expiry flip levels agree around {_wflip:,.0f}. Strong level!")
    if _r_calls:
        _parts.append(f"{len(_r_calls)} reinforced ceiling(s) — extra strong resistance.")
    if _r_puts:
        _parts.append(f"{len(_r_puts)} reinforced floor(s) — extra strong support.")
    if not _r_calls and not _r_puts:
        _parts.append("No reinforced walls found — single-expiry walls may be weaker.")

    _multi_label = "CONFIRMS" if (_r_calls or _r_puts) else "NO EXTRA"
    _multi_color = "#26a69a" if (_r_calls or _r_puts) else "#888"
    _intel.append(("&#128202;", "Multi-Expiry", _multi_label, _multi_color,
                   " ".join(_parts)))

# Render intel cards
for emoji, source, verdict, color, detail in _intel:
    st.markdown(f"""
    <div style="display:flex; align-items:flex-start; background:#1a1a2e;
                border-left:4px solid {color}; border-radius:0 8px 8px 0;
                padding:12px 16px; margin:6px 0;">
        <div style="min-width:160px;">
            <div style="color:white; font-weight:700; font-size:0.95rem;">{emoji} {source}</div>
            <div style="color:{color}; font-weight:900; font-size:0.85rem; margin-top:2px;">{verdict}</div>
        </div>
        <div style="color:#aaa; font-size:0.9rem; line-height:1.5;">{detail}</div>
    </div>
    """, unsafe_allow_html=True)

# --- Confirmation Score ---
_bull_count = sum(1 for _, _, v, _, _ in _intel if "BULLISH" in v or v == "BUYING")
_bear_count = sum(1 for _, _, v, _, _ in _intel if "BEARISH" in v or v == "SELLING")
_total = len(_intel)

if _total > 0:
    if _bull_count > _bear_count and _bull_count >= 3:
        _conf_text = f"{_bull_count} of {_total} signals are BULLISH"
        _conf_color = "#26a69a"
        _conf_verdict = "Strong Bullish Confirmation"
    elif _bear_count > _bull_count and _bear_count >= 3:
        _conf_text = f"{_bear_count} of {_total} signals are BEARISH"
        _conf_color = "#ef5350"
        _conf_verdict = "Strong Bearish Confirmation"
    elif _bull_count > _bear_count:
        _conf_text = f"{_bull_count} bullish vs {_bear_count} bearish out of {_total}"
        _conf_color = "#26a69a"
        _conf_verdict = "Slight Bullish Lean"
    elif _bear_count > _bull_count:
        _conf_text = f"{_bear_count} bearish vs {_bull_count} bullish out of {_total}"
        _conf_color = "#ef5350"
        _conf_verdict = "Slight Bearish Lean"
    else:
        _conf_text = f"Split signals — {_bull_count} bullish, {_bear_count} bearish"
        _conf_color = "#ffc107"
        _conf_verdict = "No Clear Consensus — Wait"

    st.markdown(f"""
    <div style="background:#111; border:2px solid {_conf_color}; border-radius:12px;
                padding:16px; text-align:center; margin:12px 0;">
        <div style="color:{_conf_color}; font-size:1.3rem; font-weight:900;">
            {_conf_verdict}
        </div>
        <div style="color:#888; font-size:0.95rem; margin-top:4px;">{_conf_text}</div>
    </div>
    """, unsafe_allow_html=True)


# =====================================================
# KEY LEVELS — Simple version
# =====================================================
st.markdown("---")
st.markdown("### Key Price Levels")

levels = []
if call_wall:
    dist = call_wall - spot_price
    _reinforced = ""
    if multi_expiry_info and call_wall in multi_expiry_info.get("reinforced_call_walls", []):
        _reinforced = " | REINFORCED (extra strong!)"
    levels.append(("Ceiling", call_wall, "#26a69a",
                   f"Price bounces DOWN from here | {dist:,.0f} pts above spot{_reinforced}"))
if flip:
    dist = spot_price - flip
    side = "above" if dist > 0 else "below"
    _multi_note = ""
    if multi_expiry_info:
        _wflip = multi_expiry_info.get("weighted_gamma_flip")
        if _wflip:
            _multi_note = f" | Multi-expiry flip: {_wflip:,.0f}"
    levels.append(("Flip Level", flip, "#ffc107",
                   f"Spot is {abs(dist):,.0f} pts {side} | Above = stable, Below = volatile{_multi_note}"))
if magnet:
    dist = abs(spot_price - magnet)
    levels.append(("Magnet", magnet, "#2196f3",
                   f"Price gets pulled here | {dist:,.0f} pts away"))
if put_wall:
    dist = spot_price - put_wall
    _reinforced = ""
    if multi_expiry_info and put_wall in multi_expiry_info.get("reinforced_put_walls", []):
        _reinforced = " | REINFORCED (extra strong!)"
    levels.append(("Floor", put_wall, "#ef5350",
                   f"Price bounces UP from here | {dist:,.0f} pts below spot{_reinforced}"))

for name, value, color, desc in levels:
    st.markdown(f"""
    <div style="display:flex; align-items:center; background:#1a1a2e;
                border-left:4px solid {color}; border-radius:0 8px 8px 0;
                padding:12px 16px; margin:6px 0;">
        <div style="min-width:100px;">
            <div style="color:{color}; font-weight:700; font-size:0.95rem;">{name}</div>
        </div>
        <div style="min-width:120px;">
            <div style="color:white; font-weight:900; font-size:1.2rem;">{value:,.0f}</div>
        </div>
        <div style="color:#888; font-size:0.9rem;">{desc}</div>
    </div>
    """, unsafe_allow_html=True)

# Spot price indicator
st.markdown(f"""
<div style="text-align:center; margin:8px 0; color:#888; font-size:0.9rem;">
    Current Spot: <b style="color:white; font-size:1.1rem;">{spot_price:,.2f}</b>
    &nbsp;&bull;&nbsp; Last update: {profile.timestamp:%H:%M:%S} IST
</div>
""", unsafe_allow_html=True)


# =====================================================
# TODAY'S SIGNALS (if any)
# =====================================================
if st.session_state.action_blast_history:
    st.markdown("---")
    st.markdown("### Today's Signals")
    for i, b in enumerate(reversed(st.session_state.action_blast_history), 1):
        is_bull = b.direction == "bullish"
        color = "#26a69a" if is_bull else "#ef5350"
        action = "BUY CALL (CE)" if is_bull else "BUY PUT (PE)"
        risk = abs(b.entry_level - b.stop_loss)
        reward = abs(b.target - b.entry_level)
        rr = reward / risk if risk > 0 else 0
        st.markdown(f"""
        <div style="background:#111; border:2px solid {color}; border-radius:12px;
                    padding:16px; margin:8px 0;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                    <span style="color:{color}; font-weight:900; font-size:1.2rem;">
                        #{i} {action}
                    </span>
                    <span style="color:#888; font-size:0.9rem; margin-left:12px;">
                        {b.timestamp:%H:%M:%S} IST &bull; Score: {b.composite_score:.0f}%
                    </span>
                </div>
            </div>
            <div style="display:flex; gap:24px; margin-top:8px; color:#aaa; font-size:0.95rem;">
                <span>Entry: <b style="color:white;">{b.entry_level:,.2f}</b></span>
                <span>SL: <b style="color:#ef5350;">{b.stop_loss:,.2f}</b></span>
                <span>Target: <b style="color:#26a69a;">{b.target:,.2f}</b></span>
                <span>R:R = 1:{rr:.1f}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)


# =====================================================
# FOOTER
# =====================================================
st.markdown("---")
with st.expander("How does this work?"):
    st.markdown("""
**This page combines everything into one simple view:**

1. **The big box at top** tells you exactly what to do — wait, watch, or trade
2. **4 status cards** show market direction, zone, fear level, and time left
3. **Key levels** show where price might bounce or break
4. **Signals** appear automatically when the system detects a high-confidence trade

**You don't need to check any other page.** This page runs all 6 detection models,
checks 10 quality filters, and tells you the result in plain English.

**What the system watches for you:**
- Is price above or below the Gamma Flip? (stable vs volatile)
- Is price near a wall? (might bounce or break)
- Are multiple models agreeing on a direction? (Gamma Blast)
- Is VIX high? (expect bigger moves)
- How close to expiry? (options decay faster = bigger dealer moves)

**When a Gamma Blast fires**, you'll see entry price, stop loss, and target
right here. Just follow the levels.
    """)

st.caption(f"{instrument_name} | Expiry: {expiry_date} | TTE: {tte:.1f}h | {profile.timestamp:%H:%M:%S} IST")
