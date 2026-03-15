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
    EXPIRY_DAY_POLL_INTERVAL,
    DASHBOARD_REFRESH_INTERVAL,
)
from config.settings import UPSTOX_BASE_URL
from src.auth.upstox_auth import load_access_token
from src.data.options_chain import OptionsChainFetcher
from src.engine.gex_calculator import build_gex_profile
from src.engine.greeks import validate_greeks, filter_active_strikes
from src.engine.signal_generator import generate_signals
from src.engine.gamma_blast import detect_gamma_blast
from src.dashboard.components.blast_card import (
    render_blast_alert,
    render_blast_components,
    render_no_blast_status,
)

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

# Process
chain_df_clean = validate_greeks(chain_df)
chain_df_filtered = filter_active_strikes(chain_df_clean, spot_price, num_strikes=40)

profile = build_gex_profile(
    chain_df_filtered, spot_price, inst["contract_multiplier"],
    instrument_name, expiry_date,
)

# Track price history for trend filter
st.session_state.blast_price_history.append(spot_price)
# Keep last 20 data points
st.session_state.blast_price_history = st.session_state.blast_price_history[-20:]

# Try to fetch India VIX (best-effort, non-blocking)
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
                st.session_state.blast_vix_value = ltp
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
    # BLAST DETECTED
    st.session_state.blast_fired_today += 1
    st.session_state.blast_last_time = blast.timestamp
    st.session_state.blast_history.append(blast)

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
        max_signals=BLAST_MAX_SIGNALS_PER_DAY,
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
    st.subheader(f"Blast History ({len(st.session_state.blast_history)}/{BLAST_MAX_SIGNALS_PER_DAY})")
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
**6 Models + 7 Quality Filters for High-Conviction Scalping:**

**Models (weights adapt to VIX regime):**
1. **GEX Zero-Cross Cascade** — Spot crosses gamma flip, triggering dealer hedging cascade
2. **Gamma Wall Breach** — Price breaks call/put wall with velocity confirmation
3. **Charm Flow Accelerator** — Expiry-day delta decay creates directional dealer flow
4. **Negative Gamma Squeeze** — In negative gamma, dealer hedging amplifies moves
5. **Pin Break Blast** — Price breaks away from max gamma pin strike
6. **Vanna Squeeze** — IV crush + vanna exposure creates directional hedging flow

**7 Quality Filters (what makes it accurate):**
1. **Trend Filter** — EMA-based, penalizes counter-trend blasts by up to 25 pts
2. **VIX Regime** — Adapts weights & threshold (Low/Normal/High/Extreme vol)
3. **Volume Confirmation** — Requires ATM volume spike, 30% penalty if absent
4. **Smart Timing** — Morning signals penalized 40%, charm zone (1:30 PM+) boosted 15%
5. **Monthly vs Weekly** — Suppresses breakouts near max gamma on monthly expiry
6. **Sensex Liquidity** — Penalizes low-OI chains (BSE has 10x less liquidity)
7. **Max Pain Proximity** — Suppresses signals when pinned near max pain close to expiry

**Rules:**
- Filtered composite score must reach **70+** to fire
- Maximum **2 signals** per expiry day
- **30-minute cooldown** between signals
- Entry/SL/Target based on gamma walls
- All times in **IST**
    """)
