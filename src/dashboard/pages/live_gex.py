"""Live GEX Monitor — main dashboard page with auto-refresh.

Optimized for mobile/tablet with stacked layout and touch-friendly elements.
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

from src.utils.ist import now_ist, time_to_expiry_hours as compute_tte

try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except ImportError:
    HAS_AUTOREFRESH = False

from config.instruments import get_instrument, INSTRUMENTS
from config.settings import DASHBOARD_REFRESH_INTERVAL
from src.auth.upstox_auth import load_access_token, validate_token
from src.data.options_chain import OptionsChainFetcher
from src.engine.gex_calculator import build_gex_profile
from src.engine.greeks import validate_greeks, filter_active_strikes
from src.engine.signal_generator import generate_signals
from src.dashboard.components.gex_profile_chart import render_gex_profile
from src.dashboard.components.gamma_walls import render_price_with_walls
from src.dashboard.components.signal_timeline import render_signal_timeline


st.title("Live GEX Monitor")

# Auto-refresh
if HAS_AUTOREFRESH:
    st_autorefresh(interval=DASHBOARD_REFRESH_INTERVAL * 1000, key="gex_refresh")

# Instrument selector — top of page as radio (mobile-friendly, no sidebar needed)
instrument_name = st.radio(
    "Index", list(INSTRUMENTS.keys()), index=0, horizontal=True
)

# Load token (from OAuth session or .env fallback)
try:
    token = load_access_token()
except ValueError:
    st.error("Not logged in. Please refresh the page to login.")
    st.stop()

# Initialize session state
if "prev_profile" not in st.session_state:
    st.session_state.prev_profile = None
if "signal_history" not in st.session_state:
    st.session_state.signal_history = []
if "price_history" not in st.session_state:
    st.session_state.price_history = []

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
chain_df = validate_greeks(chain_df)
chain_df = filter_active_strikes(chain_df, spot_price, num_strikes=40)

profile = build_gex_profile(
    chain_df, spot_price, inst["contract_multiplier"],
    instrument_name, expiry_date,
)

# Compute time to expiry
tte = compute_tte(expiry_date)

# Generate signals
signals = generate_signals(profile, st.session_state.prev_profile, tte)
st.session_state.prev_profile = profile

# Track history
st.session_state.signal_history.extend(signals)
st.session_state.price_history.append({
    "timestamp": profile.timestamp,
    "ltp": spot_price,
})

# Metrics — 3 columns x 2 rows (fits mobile)
st.caption(f"Expiry: {expiry_date} | TTE: {tte:.1f}h | {profile.timestamp:%H:%M:%S}")

row1 = st.columns(3)
row1[0].metric("Spot", f"{spot_price:,.2f}")
row1[1].metric("Gamma Flip", f"{profile.gamma_flip_level:,.0f}" if profile.gamma_flip_level else "N/A")
row1[2].metric("Max Gamma", f"{profile.max_gamma_strike:,.0f}" if profile.max_gamma_strike else "N/A")

row2 = st.columns(3)
row2[0].metric("Call Wall", f"{profile.call_wall:,.0f}" if profile.call_wall else "N/A")
row2[1].metric("Put Wall", f"{profile.put_wall:,.0f}" if profile.put_wall else "N/A")
regime = "POSITIVE" if profile.net_gex_total > 0 else "NEGATIVE"
regime_delta = "normal" if profile.net_gex_total > 0 else "inverse"
row2[2].metric("GEX Regime", regime, delta=f"{profile.net_gex_total:,.0f}", delta_color=regime_delta)

# Signals — styled cards (touch-friendly)
if signals:
    st.subheader(f"Signals ({len(signals)})")
    for sig in signals:
        if sig.direction == "bullish":
            css_class = "signal-bullish"
            arrow = "▲ BULLISH"
        elif sig.direction == "bearish":
            css_class = "signal-bearish"
            arrow = "▼ BEARISH"
        else:
            css_class = "signal-neutral"
            arrow = "● NEUTRAL"

        st.markdown(
            f'<div class="signal-card {css_class}">'
            f'<strong>{sig.signal_type.replace("_", " ").upper()}</strong> '
            f'@ {sig.level:,.2f} &nbsp;|&nbsp; '
            f'Strength: {sig.strength:.0%} &nbsp;|&nbsp; {arrow}'
            f'</div>',
            unsafe_allow_html=True,
        )

# Charts — stacked vertically (full width, scrollable on mobile)
st.plotly_chart(
    render_gex_profile(profile),
    use_container_width=True,
    config={"displayModeBar": False},
)

price_df = pd.DataFrame(st.session_state.price_history)
if not price_df.empty:
    st.plotly_chart(
        render_price_with_walls(price_df, profile),
        use_container_width=True,
        config={"displayModeBar": False},
    )

# Signal timeline
if st.session_state.signal_history:
    st.plotly_chart(
        render_signal_timeline(st.session_state.signal_history),
        use_container_width=True,
        config={"displayModeBar": False},
    )
