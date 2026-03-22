"""Market Radar — Simple, beginner-friendly dashboard.

Shows where market is going and where Gamma Blast happens,
all in plain English with visual indicators anyone can understand.
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
    EXPIRY_DAY_POLL_INTERVAL,
    DASHBOARD_REFRESH_INTERVAL,
    TELEGRAM_ENABLED,
    TREND_ALERT_MIN_CONSECUTIVE,
    TREND_ALERT_MIN_MOVE_PCT,
    TREND_ALERT_COOLDOWN_MINUTES,
    UPSTOX_BASE_URL,
)
from src.auth.upstox_auth import load_access_token
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
from src.backtest.data_store import HistoricalDataStore

# --- Page Header ---
st.markdown("""
<div style="text-align:center; margin-bottom:8px;">
    <span style="font-size:2rem; font-weight:900;">Market Radar</span>
</div>
""", unsafe_allow_html=True)

# Expiry day detection
EXPIRY_DAYS = {"NIFTY": 1, "SENSEX": 3}


def _get_todays_expiry_instrument():
    today_weekday = today_ist().weekday()
    for name, weekday in EXPIRY_DAYS.items():
        if today_weekday == weekday:
            return name
    return None


def _is_expiry_day(instrument_name):
    return today_ist().weekday() == EXPIRY_DAYS.get(instrument_name, -1)


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
    st_autorefresh(interval=refresh_interval * 1000, key="radar_refresh")

# Load token
try:
    token = load_access_token()
except ValueError:
    st.error("Not logged in. Please refresh the page to login.")
    st.stop()

# Session state
for key, default in [
    ("radar_blast_history", []),
    ("radar_fired_today", 0),
    ("radar_last_time", None),
    ("radar_prev_profile", None),
    ("radar_prev_chain", None),
    ("radar_last_date", None),
    ("radar_price_history", []),
    ("radar_vix_value", None),
    ("radar_alert_sent_ids", set()),
]:
    if key not in st.session_state:
        st.session_state[key] = default

if "directional_tracker" not in st.session_state:
    st.session_state.directional_tracker = DirectionalTracker(
        min_consecutive=TREND_ALERT_MIN_CONSECUTIVE,
        min_move_pct=TREND_ALERT_MIN_MOVE_PCT,
        cooldown_minutes=TREND_ALERT_COOLDOWN_MINUTES,
    )

# Reset daily
if st.session_state.radar_last_date != today_ist().isoformat():
    st.session_state.radar_fired_today = 0
    st.session_state.radar_last_time = None
    st.session_state.radar_blast_history = []
    st.session_state.radar_last_date = today_ist().isoformat()

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

# Process
chain_df_clean = validate_greeks(chain_df)
chain_df_filtered = filter_active_strikes(chain_df_clean, spot_price, num_strikes=40)
profile = build_gex_profile(
    chain_df_filtered, spot_price, inst["contract_multiplier"],
    instrument_name, expiry_date,
)

# Save snapshot on expiry day
if is_expiry:
    try:
        HistoricalDataStore().save_snapshot(
            instrument_name, expiry_date, now_ist(), chain_df_filtered, spot_price
        )
    except Exception:
        pass

# Track prices
st.session_state.radar_price_history.append(spot_price)
st.session_state.radar_price_history = st.session_state.radar_price_history[-20:]

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
                st.session_state.radar_vix_value = ltp
except Exception:
    pass

vix_val = st.session_state.radar_vix_value
tte = compute_tte(expiry_date)
trend_data = compute_trend_bias(st.session_state.radar_price_history)

# Directional Telegram alert
if TELEGRAM_ENABLED:
    dir_alert = st.session_state.directional_tracker.update(
        trend_data=trend_data, spot_price=spot_price, instrument=instrument_name,
    )
    if dir_alert is not None:
        send_directional_alert(dir_alert)

# =====================================================
# SECTION 1: BIG DIRECTION INDICATOR
# =====================================================
trend = trend_data.get("trend", "neutral")
strength = trend_data.get("strength", 0)

if trend == "bullish":
    dir_icon = "&#9650;"  # ▲
    dir_text = "Market Moving UP"
    dir_color = "#26a69a"
    dir_bg = "#0d2818"
elif trend == "bearish":
    dir_icon = "&#9660;"  # ▼
    dir_text = "Market Moving DOWN"
    dir_color = "#ef5350"
    dir_bg = "#2d0a0a"
else:
    dir_icon = "&#9654;&#9664;"  # ►◄
    dir_text = "Market Moving SIDEWAYS"
    dir_color = "#ffc107"
    dir_bg = "#2d2a0a"

st.markdown(f"""
<div style="background:{dir_bg}; border:2px solid {dir_color}; border-radius:16px;
            padding:20px; text-align:center; margin:8px 0 16px 0;">
    <div style="font-size:3rem; color:{dir_color}; line-height:1;">{dir_icon}</div>
    <div style="font-size:1.6rem; font-weight:900; color:{dir_color}; margin-top:4px;">
        {dir_text}
    </div>
    <div style="font-size:1rem; color:#aaa; margin-top:4px;">
        {instrument_name} at <b style="color:white;">{spot_price:,.2f}</b>
        &nbsp;&bull;&nbsp; Confidence: {strength:.0%}
    </div>
</div>
""", unsafe_allow_html=True)

# =====================================================
# SECTION 2: SIMPLE MARKET MAP — Where is price?
# =====================================================
st.markdown("### Where is the Market?")

ceiling = profile.call_wall or (spot_price + 500)
floor_level = profile.put_wall or (spot_price - 500)
magnet = profile.max_gamma_strike
flip = profile.gamma_flip_level

# Calculate position percentage (0=floor, 100=ceiling)
price_range = ceiling - floor_level
if price_range <= 0:
    price_range = 1000  # fallback to avoid division by zero

position_pct = max(0, min(100, ((spot_price - floor_level) / price_range) * 100))
spot_top_px = 40 + (100 - position_pct) * 1.2

# Determine zone description
if profile.net_gex_total > 0:
    zone_label = "STABLE ZONE — Dealers are cushioning moves"
    zone_color = "#26a69a"
else:
    zone_label = "VOLATILE ZONE — Moves get amplified!"
    zone_color = "#ef5350"

# Build magnet HTML
magnet_html = ""
if magnet and price_range > 0:
    magnet_pct = max(0, min(100, ((magnet - floor_level) / price_range) * 100))
    magnet_top = 40 + (100 - magnet_pct) * 1.2
    magnet_html = (
        f'<div style="position:absolute; top:{magnet_top:.0f}px; right:0; transform:translateY(-50%);">'
        f'<div style="color:#2196f3; font-size:0.85rem; font-weight:600;">'
        f'Magnet: {magnet:,.0f}</div></div>'
    )

# Build flip HTML
flip_html = ""
if flip and price_range > 0:
    flip_pct = max(0, min(100, ((flip - floor_level) / price_range) * 100))
    flip_top = 40 + (100 - flip_pct) * 1.2
    flip_html = (
        f'<div style="position:absolute; top:{flip_top:.0f}px; left:0; transform:translateY(-50%);">'
        f'<div style="color:#ffc107; font-size:0.85rem; font-weight:600;">'
        f'Flip: {flip:,.0f}</div></div>'
    )

# Simple key levels as metrics (always works, no complex HTML positioning)
col1, col2, col3, col4 = st.columns(4)
col1.metric("Ceiling", f"{ceiling:,.0f}", help="Resistance — price bounces down here")
col2.metric("Floor", f"{floor_level:,.0f}", help="Support — price bounces up here")
col3.metric("Magnet", f"{magnet:,.0f}" if magnet else "N/A", help="Price gets pulled here near expiry")
col4.metric("Flip Level", f"{flip:,.0f}" if flip else "N/A", help="Above=stable, Below=volatile")

# Zone + position indicator
st.markdown(f"""
<div style="background:#1a1a2e; border:1px solid #333; border-radius:12px; padding:20px; margin:8px 0;">
    <div style="text-align:center; margin-bottom:16px;">
        <span style="font-size:1.1rem; color:{zone_color}; font-weight:700;">{zone_label}</span>
    </div>
    <div style="position:relative; height:220px; margin:0 40px;">
        <div style="position:absolute; top:0; left:0; right:0; text-align:center;">
            <div style="background:#1b3a26; color:#26a69a; padding:6px 12px; border-radius:6px;
                        display:inline-block; font-weight:700; font-size:0.95rem;">
                CEILING: {ceiling:,.0f}
            </div>
            <div style="color:#888; font-size:0.8rem; margin-top:2px;">Price unlikely to go above this easily</div>
        </div>
        <div style="position:absolute; top:40px; bottom:40px; left:50%; width:8px;
                    background:linear-gradient(to bottom, #26a69a, #ffc107, #ef5350);
                    border-radius:4px; transform:translateX(-50%);"></div>
        <div style="position:absolute; top:{spot_top_px:.0f}px; left:50%;
                    transform:translate(-50%, -50%); z-index:10;">
            <div style="background:white; color:#000; padding:6px 14px; border-radius:20px;
                        font-weight:900; font-size:1.1rem; white-space:nowrap;
                        box-shadow: 0 0 12px rgba(255,255,255,0.4);">
                {spot_price:,.2f}
            </div>
        </div>
        {magnet_html}
        {flip_html}
        <div style="position:absolute; bottom:0; left:0; right:0; text-align:center;">
            <div style="background:#3a1b1b; color:#ef5350; padding:6px 12px; border-radius:6px;
                        display:inline-block; font-weight:700; font-size:0.95rem;">
                FLOOR: {floor_level:,.0f}
            </div>
            <div style="color:#888; font-size:0.8rem; margin-top:2px;">Price unlikely to fall below this easily</div>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)


# =====================================================
# SECTION 3: PLAIN ENGLISH EXPLANATION
# =====================================================
st.markdown("### What Does This Mean?")

explanations = []

# Regime explanation
if profile.net_gex_total > 0:
    explanations.append(
        ("&#128994;", "Market is in a STABLE zone",
         f"When price is above the Gamma Flip level ({f'{flip:,.0f}' if flip else 'N/A'}), "
         f"big institutions (dealers) act like shock absorbers — they SELL when market goes up "
         f"and BUY when it goes down. This keeps the market calm and range-bound.")
    )
else:
    explanations.append(
        ("&#128308;", "Market is in a VOLATILE zone",
         f"When price is below the Gamma Flip level ({f'{flip:,.0f}' if flip else 'N/A'}), "
         f"dealers do the OPPOSITE — they BUY when market goes up and SELL when it goes down. "
         f"This AMPLIFIES moves. Expect bigger swings!")
    )

# Magnet explanation
if magnet:
    dist_from_magnet = abs(spot_price - magnet)
    if dist_from_magnet < price_range * 0.1:
        explanations.append(
            ("&#128178;", f"Price is STUCK near the Magnet ({magnet:,.0f})",
             "The market is pinned near the level with highest options activity. "
             "This is like a magnetic pull — price keeps coming back here. "
             "Hard to break away unless something big happens.")
        )
    else:
        magnet_dir = "above" if spot_price > magnet else "below"
        explanations.append(
            ("&#128178;", f"Price is {dist_from_magnet:,.0f} pts {magnet_dir} the Magnet ({magnet:,.0f})",
             "The Magnet is where most options activity is concentrated. "
             "Price tends to drift toward this level, especially near expiry.")
        )

# VIX explanation
if vix_val:
    if vix_val < 14:
        explanations.append(
            ("&#128154;", f"VIX is LOW ({vix_val:.1f})",
             "The fear meter is low. Market is relaxed. "
             "Expect small, predictable moves. Good for option sellers.")
        )
    elif vix_val < 18:
        explanations.append(
            ("&#128154;", f"VIX is NORMAL ({vix_val:.1f})",
             "The fear meter is at normal levels. Nothing unusual.")
        )
    elif vix_val < 22:
        explanations.append(
            ("&#128992;", f"VIX is HIGH ({vix_val:.1f})",
             "The fear meter is elevated. Market participants are nervous. "
             "Expect bigger swings than usual. Be careful with positions.")
        )
    else:
        explanations.append(
            ("&#128308;", f"VIX is EXTREME ({vix_val:.1f})",
             "The fear meter is very high! Market is very nervous. "
             "Expect LARGE, unpredictable swings. Risky time to trade. "
             "Option premiums are expensive.")
        )

# Near wall explanation
if ceiling and abs(spot_price - ceiling) < price_range * 0.15:
    explanations.append(
        ("&#9888;", "Price is NEAR the Ceiling!",
         f"Getting close to the resistance level at {ceiling:,.0f}. "
         f"This is where call sellers have big positions. Price may bounce down from here, "
         f"OR if it breaks above — expect a fast move up!")
    )
elif floor_level and abs(spot_price - floor_level) < price_range * 0.15:
    explanations.append(
        ("&#9888;", "Price is NEAR the Floor!",
         f"Getting close to the support level at {floor_level:,.0f}. "
         f"This is where put sellers have big positions. Price may bounce up from here, "
         f"OR if it breaks below — expect a fast move down!")
    )

for emoji, title, desc in explanations:
    st.markdown(f"""
    <div style="background:#1a1a2e; border-left:4px solid #555; border-radius:0 8px 8px 0;
                padding:12px 16px; margin:8px 0;">
        <div style="font-size:1rem; font-weight:700; color:white;">
            {emoji} {title}
        </div>
        <div style="font-size:0.9rem; color:#bbb; margin-top:4px; line-height:1.5;">
            {desc}
        </div>
    </div>
    """, unsafe_allow_html=True)


# =====================================================
# SECTION 4: GAMMA BLAST — Traffic Light
# =====================================================
st.markdown("---")
st.markdown("### Gamma Blast Signal")

# Run blast detection
blast = detect_gamma_blast(
    profile=profile,
    prev_profile=st.session_state.radar_prev_profile,
    chain_df=chain_df_filtered,
    prev_chain_df=st.session_state.radar_prev_chain,
    time_to_expiry_hours=tte,
    fired_today=st.session_state.radar_fired_today,
    last_blast_time=st.session_state.radar_last_time,
    price_history=st.session_state.radar_price_history,
    vix_value=vix_val,
    expiry_date=expiry_date,
)

st.session_state.radar_prev_profile = profile
st.session_state.radar_prev_chain = chain_df_filtered.copy()

if blast is not None:
    # BLAST DETECTED — deduplicate against re-renders
    blast_id = f"{blast.instrument}_{blast.timestamp.isoformat()}"
    existing_ids = {
        f"{b.instrument}_{b.timestamp.isoformat()}"
        for b in st.session_state.radar_blast_history
    }
    if blast_id not in existing_ids:
        st.session_state.radar_fired_today += 1
        st.session_state.radar_last_time = blast.timestamp
        st.session_state.radar_blast_history.append(blast)

    # Telegram alert
    blast_id = f"{blast.instrument}_{blast.timestamp.isoformat()}"
    if TELEGRAM_ENABLED and blast_id not in st.session_state.radar_alert_sent_ids:
        if send_blast_alert(blast):
            st.session_state.radar_alert_sent_ids.add(blast_id)
            st.toast("Telegram alert sent!")

    is_bull = blast.direction == "bullish"
    color = "#26a69a" if is_bull else "#ef5350"
    bg = "#0d2818" if is_bull else "#2d0a0a"
    action = "BUY CALL (CE)" if is_bull else "BUY PUT (PE)"
    dir_word = "UP" if is_bull else "DOWN"

    # Calculate risk/reward
    risk = abs(blast.entry_level - blast.stop_loss)
    reward = abs(blast.target - blast.entry_level)
    rr_ratio = reward / risk if risk > 0 else 0

    st.markdown(f"""
    <div style="background:{bg}; border:3px solid {color}; border-radius:16px;
                padding:24px; text-align:center; margin:8px 0;
                animation: pulse 1.5s ease-in-out infinite;">
        <div style="font-size:2.5rem; font-weight:900; color:{color};">
            GAMMA BLAST! — Market going {dir_word}
        </div>
        <div style="font-size:1.5rem; color:{color}; margin:8px 0; font-weight:700;">
            {action}
        </div>
        <div style="font-size:1rem; color:#ccc; margin-top:8px;">
            Confidence: {blast.composite_score:.0f}% &nbsp;&bull;&nbsp;
            {blast.instrument} &nbsp;&bull;&nbsp;
            {blast.timestamp:%H:%M:%S} IST
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Simple trade card
    st.markdown(f"""
    <div style="background:#111; border:1px solid #444; border-radius:12px;
                padding:20px; margin:12px 0;">
        <div style="font-size:1.1rem; font-weight:700; color:white; text-align:center;
                    margin-bottom:16px;">
            Trade Setup
        </div>
        <div style="display:flex; justify-content:space-around; text-align:center;">
            <div>
                <div style="color:#888; font-size:0.85rem;">Enter at</div>
                <div style="color:white; font-size:1.3rem; font-weight:700;">{blast.entry_level:,.2f}</div>
            </div>
            <div>
                <div style="color:#ef5350; font-size:0.85rem;">Stop Loss</div>
                <div style="color:#ef5350; font-size:1.3rem; font-weight:700;">{blast.stop_loss:,.2f}</div>
                <div style="color:#888; font-size:0.8rem;">{risk:,.0f} pts risk</div>
            </div>
            <div>
                <div style="color:#26a69a; font-size:0.85rem;">Target</div>
                <div style="color:#26a69a; font-size:1.3rem; font-weight:700;">{blast.target:,.2f}</div>
                <div style="color:#888; font-size:0.8rem;">{reward:,.0f} pts reward</div>
            </div>
        </div>
        <div style="text-align:center; margin-top:12px; color:#888; font-size:0.9rem;">
            Risk : Reward = 1 : {rr_ratio:.1f}
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Simple explanation of WHY
    top_models = sorted(blast.components, key=lambda c: c.score * c.weight, reverse=True)[:3]
    reasons = []
    for comp in top_models:
        if comp.score > 30:
            reasons.append(comp.detail)

    if reasons:
        st.markdown("**Why this signal fired:**")
        for r in reasons:
            st.markdown(f"- {r}")

else:
    # No blast — show status
    if not is_expiry:
        day_name = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
                    4: "Friday", 5: "Saturday", 6: "Sunday"}
        today_name = day_name.get(date.today().weekday(), "?")

        st.markdown(f"""
        <div style="background:#1a1a2e; border:1px solid #444; border-radius:12px;
                    padding:24px; text-align:center; margin:8px 0;">
            <div style="font-size:3rem; color:#888;">&#128308;&#128993;&#128994;</div>
            <div style="font-size:1.2rem; color:#888; margin-top:8px; font-weight:600;">
                Today is {today_name} — Not Expiry Day
            </div>
            <div style="font-size:0.95rem; color:#666; margin-top:8px; line-height:1.6;">
                Gamma Blast signals only fire on expiry days because that's when<br>
                options decay fastest and dealers have to hedge aggressively.<br><br>
                <b style="color:#aaa;">NIFTY expiry: Tuesday</b> &nbsp;&bull;&nbsp;
                <b style="color:#aaa;">SENSEX expiry: Thursday</b>
            </div>
            <div style="font-size:0.85rem; color:#555; margin-top:12px;">
                You can still see where the market is positioned above &#9650;
            </div>
        </div>
        """, unsafe_allow_html=True)
    elif st.session_state.radar_fired_today >= BLAST_MAX_SIGNALS_PER_DAY:
        st.markdown(f"""
        <div style="background:#1a2e1a; border:1px solid #26a69a; border-radius:12px;
                    padding:24px; text-align:center; margin:8px 0;">
            <div style="font-size:1.5rem; color:#26a69a; font-weight:700;">
                Done for Today!
            </div>
            <div style="font-size:0.95rem; color:#888; margin-top:8px;">
                {st.session_state.radar_fired_today} signals fired today (max {BLAST_MAX_SIGNALS_PER_DAY}).
                <br>No more signals will be generated.
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        # Scanning...
        pulse_color = "#ffc107" if tte < 3 else "#4a90d9"
        urgency = "Last few hours! Charm decay is accelerating!" if tte < 3 else f"{tte:.1f} hours to expiry"

        st.markdown(f"""
        <div style="background:#1a1a2e; border:2px solid {pulse_color}; border-radius:12px;
                    padding:24px; text-align:center; margin:8px 0;">
            <div style="font-size:2rem; color:{pulse_color};">
                &#128269; Scanning...
            </div>
            <div style="font-size:1rem; color:#aaa; margin-top:8px;">
                Looking for a high-conviction Gamma Blast on {instrument_name}
            </div>
            <div style="font-size:0.9rem; color:#888; margin-top:4px;">
                {urgency} &nbsp;&bull;&nbsp;
                Signals today: {st.session_state.radar_fired_today}/{BLAST_MAX_SIGNALS_PER_DAY}
            </div>
            <div style="font-size:0.85rem; color:#555; margin-top:8px;">
                A signal fires when multiple models confirm a strong move is likely.<br>
                You'll get a Telegram alert when it happens.
            </div>
        </div>
        """, unsafe_allow_html=True)


# =====================================================
# SECTION 5: BLAST HISTORY (if any today)
# =====================================================
if st.session_state.radar_blast_history:
    st.markdown("---")
    st.markdown("### Today's Signals")
    for i, b in enumerate(reversed(st.session_state.radar_blast_history), 1):
        is_bull = b.direction == "bullish"
        color = "#26a69a" if is_bull else "#ef5350"
        action = "BUY CE" if is_bull else "BUY PE"
        st.markdown(f"""
        <div style="background:#111; border-left:4px solid {color}; border-radius:0 8px 8px 0;
                    padding:12px 16px; margin:8px 0;">
            <div style="color:{color}; font-weight:700;">
                #{i} {action} @ {b.timestamp:%H:%M:%S}
                — Score: {b.composite_score:.0f}%
            </div>
            <div style="color:#aaa; font-size:0.9rem; margin-top:4px;">
                Entry: {b.entry_level:,.2f} &nbsp;|&nbsp;
                SL: {b.stop_loss:,.2f} &nbsp;|&nbsp;
                Target: {b.target:,.2f}
            </div>
        </div>
        """, unsafe_allow_html=True)


# =====================================================
# SECTION 6: QUICK GLOSSARY (expandable)
# =====================================================
with st.expander("What do these terms mean?"):
    st.markdown("""
**Ceiling (Call Wall):** The price level where lots of CALL options are sold.
Acts like a glass ceiling — price bounces down from here. If it breaks above, expect a fast move up.

**Floor (Put Wall):** The price level where lots of PUT options are sold.
Acts like a safety net — price bounces up from here. If it breaks below, expect a fast move down.

**Magnet (Max Gamma):** The price with the most options activity.
Price gets pulled toward this level like a magnet, especially near expiry.

**Gamma Flip:** The dividing line between stable and volatile zones.
- **Above = Stable:** Dealers cushion moves (sell highs, buy dips)
- **Below = Volatile:** Dealers amplify moves (buy highs, sell dips)

**VIX (Fear Meter):** Measures how scared the market is.
- Below 14 = Calm (small moves)
- 14-18 = Normal
- 18-22 = Nervous (bigger swings)
- Above 22 = Extreme fear (wild swings)

**Gamma Blast:** When 6 different models all agree that a big, fast move is about to happen.
This is the main trade signal — it only fires 1-2 times per expiry day.

**CE (Call):** Buy this when you think market will go UP.
**PE (Put):** Buy this when you think market will go DOWN.
    """)

# Footer
st.caption(f"Last update: {profile.timestamp:%H:%M:%S} IST | Expiry: {expiry_date} | TTE: {tte:.1f}h")
