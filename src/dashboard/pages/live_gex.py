"""Live GEX Monitor — main dashboard page with auto-refresh."""

import sys

sys.path.insert(0, ".")

import streamlit as st
import pandas as pd
from datetime import datetime

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

# Sidebar controls
instrument_name = st.sidebar.selectbox(
    "Select Index", list(INSTRUMENTS.keys()), index=0
)

# Load token
try:
    token = load_access_token()
    token_valid = validate_token(token)
except ValueError:
    token_valid = False
    token = None

if not token_valid:
    st.error("Upstox access token is invalid or not configured. Go to Settings page.")
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
expiry_dt = datetime.strptime(expiry_date, "%Y-%m-%d").replace(hour=15, minute=30)
tte = max((expiry_dt - datetime.now()).total_seconds() / 3600.0, 0.0)

# Generate signals
signals = generate_signals(profile, st.session_state.prev_profile, tte)
st.session_state.prev_profile = profile

# Track history
st.session_state.signal_history.extend(signals)
st.session_state.price_history.append({
    "timestamp": profile.timestamp,
    "ltp": spot_price,
})

# Row 1: Key metrics
cols = st.columns(6)
cols[0].metric("Spot Price", f"{spot_price:,.2f}")
cols[1].metric("Gamma Flip", f"{profile.gamma_flip_level:,.0f}" if profile.gamma_flip_level else "N/A")
cols[2].metric("Max Gamma", f"{profile.max_gamma_strike:,.0f}" if profile.max_gamma_strike else "N/A")
cols[3].metric("Call Wall", f"{profile.call_wall:,.0f}" if profile.call_wall else "N/A")
cols[4].metric("Put Wall", f"{profile.put_wall:,.0f}" if profile.put_wall else "N/A")
regime = "POSITIVE" if profile.net_gex_total > 0 else "NEGATIVE"
regime_delta = "normal" if profile.net_gex_total > 0 else "inverse"
cols[5].metric("GEX Regime", regime, delta=f"{profile.net_gex_total:,.0f}", delta_color=regime_delta)

st.caption(f"Expiry: {expiry_date} | Time to Expiry: {tte:.1f}h | Last Update: {profile.timestamp:%H:%M:%S}")

# Row 2: Charts
chart_left, chart_right = st.columns([3, 2])

with chart_left:
    st.plotly_chart(render_gex_profile(profile), use_container_width=True)

with chart_right:
    price_df = pd.DataFrame(st.session_state.price_history)
    if not price_df.empty:
        st.plotly_chart(
            render_price_with_walls(price_df, profile),
            use_container_width=True,
        )
    else:
        st.info("Price chart will appear after data accumulates.")

# Row 3: Current signals
if signals:
    st.subheader(f"Active Signals ({len(signals)})")
    for sig in signals:
        arrow = {"bullish": ":green[▲ BULLISH]", "bearish": ":red[▼ BEARISH]"}.get(
            sig.direction, ":orange[● NEUTRAL]"
        )
        st.markdown(
            f"**{sig.signal_type.replace('_', ' ').upper()}** @ {sig.level:,.2f} | "
            f"Strength: {sig.strength:.0%} | {arrow}"
        )
else:
    st.info("No signals triggered in this update.")

# Row 4: Signal timeline
if st.session_state.signal_history:
    st.plotly_chart(
        render_signal_timeline(st.session_state.signal_history),
        use_container_width=True,
    )
