"""Multi-Expiry GEX — Aggregated gamma exposure across 2-3 expiries.

Shows:
- Combined GEX profile from nearest expiries
- Per-expiry contribution weights
- Reinforced walls (same wall in 2+ expiries = stronger)
- Weighted gamma flip level
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
import plotly.graph_objects as go

from src.utils.ist import now_ist, time_to_expiry_hours as compute_tte

try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except ImportError:
    HAS_AUTOREFRESH = False

from config.instruments import get_instrument, INSTRUMENTS
from config.settings import DASHBOARD_REFRESH_INTERVAL, MULTI_EXPIRY_COUNT
from src.auth.upstox_auth import load_access_token
from src.data.options_chain import OptionsChainFetcher
from src.engine.greeks import validate_greeks, filter_active_strikes
from src.engine.multi_expiry_gex import aggregate_multi_expiry_gex
from src.engine.gex_calculator import build_gex_profile


st.header("Multi-Expiry GEX")
st.caption("Aggregated gamma exposure across multiple expirations — finds reinforced walls.")

if HAS_AUTOREFRESH:
    st_autorefresh(interval=DASHBOARD_REFRESH_INTERVAL * 1000, key="multi_exp_refresh")

instrument_name = st.selectbox("Instrument", list(INSTRUMENTS.keys()), key="multi_exp_inst")
inst = get_instrument(instrument_name)

try:
    token = load_access_token()
    fetcher = OptionsChainFetcher(token)

    # Fetch multiple expiries
    all_expiries = fetcher.get_expiry_dates(inst["instrument_key"])
    num_expiries = min(MULTI_EXPIRY_COUNT, len(all_expiries))

    if num_expiries == 0:
        st.warning("No expiry dates found.")
        st.stop()

    expiry_chains = []
    spot_price = 0.0

    for exp in all_expiries[:num_expiries]:
        chain_df, sp = fetcher.fetch_chain(inst["instrument_key"], exp)
        if not chain_df.empty:
            chain_df = validate_greeks(chain_df)
            chain_df = filter_active_strikes(chain_df, sp, num_strikes=40)
            tte = compute_tte(exp)
            expiry_chains.append((exp, chain_df, tte))
            if sp > 0:
                spot_price = sp

    if not expiry_chains:
        st.warning("No chain data available.")
        st.stop()

    # Aggregate
    result = aggregate_multi_expiry_gex(
        expiry_chains, spot_price, inst["contract_multiplier"],
    )

    # Also build single-expiry profile for comparison
    nearest_exp, nearest_chain, nearest_tte = expiry_chains[0]
    single_profile = build_gex_profile(
        nearest_chain, spot_price, inst["contract_multiplier"],
        instrument_name, nearest_exp,
    )

    # --- Header ---
    st.subheader(f"{instrument_name} | Spot: {spot_price:,.2f} | Expiries: {num_expiries}")

    # --- Expiry Contributions ---
    st.subheader("Expiry Contributions")
    for contrib in result["expiry_contributions"]:
        c1, c2, c3 = st.columns(3)
        c1.metric(f"Expiry: {contrib.expiry}", f"TTE: {contrib.tte_hours:.1f}h")
        c2.metric("OI Weight", f"{contrib.oi_weight:.0%}")
        c3.metric("Net GEX", f"{contrib.net_gex:,.0f}")

    # --- Key Levels Comparison ---
    st.subheader("Key Levels")
    kc1, kc2 = st.columns(2)

    with kc1:
        st.markdown("**Single Expiry**")
        st.metric("Gamma Flip", f"{single_profile.gamma_flip_level:,.0f}" if single_profile.gamma_flip_level else "N/A")
        st.metric("Call Wall", f"{single_profile.call_wall:,.0f}" if single_profile.call_wall else "N/A")
        st.metric("Put Wall", f"{single_profile.put_wall:,.0f}" if single_profile.put_wall else "N/A")
        st.metric("Net GEX", f"{single_profile.net_gex_total:,.0f}")

    with kc2:
        st.markdown("**Multi-Expiry (Combined)**")
        wflip = result.get("weighted_gamma_flip")
        st.metric("Weighted Gamma Flip", f"{wflip:,.0f}" if wflip else "N/A")
        st.metric("Combined Net GEX", f"{result['combined_net_gex']:,.0f}")

        # Reinforced walls
        r_calls = result.get("reinforced_call_walls", [])
        r_puts = result.get("reinforced_put_walls", [])
        st.metric("Reinforced Call Walls", f"{len(r_calls)} strikes" if r_calls else "None")
        st.metric("Reinforced Put Walls", f"{len(r_puts)} strikes" if r_puts else "None")

    # --- Reinforced Walls Detail ---
    if r_calls or r_puts:
        st.subheader("Reinforced Walls (Appear in 2+ Expiries)")
        st.caption("These levels are stronger because multiple expirations have large gamma there.")

        if r_calls:
            st.markdown(f"**Call Walls**: {', '.join(f'{w:,.0f}' for w in r_calls[:5])}")
        if r_puts:
            st.markdown(f"**Put Walls**: {', '.join(f'{w:,.0f}' for w in r_puts[:5])}")

    # --- Combined GEX Chart ---
    combined_df = result.get("combined_gex_df")
    if combined_df is not None and not combined_df.empty and "net_gex" in combined_df.columns:
        st.subheader("Combined GEX Profile")

        fig = go.Figure()
        strikes = combined_df["strike_price"].tolist()
        net_gex = combined_df["net_gex"].tolist()

        colors = ["#26a69a" if g > 0 else "#ef5350" for g in net_gex]

        fig.add_trace(go.Bar(
            x=strikes, y=net_gex, name="Net GEX",
            marker_color=colors,
        ))

        fig.add_vline(x=spot_price, line_dash="dash", line_color="yellow",
                      annotation_text=f"Spot {spot_price:,.0f}")

        if wflip:
            fig.add_vline(x=wflip, line_dash="dot", line_color="#ffc107",
                          annotation_text=f"Flip {wflip:,.0f}")

        for rw in r_calls[:3]:
            fig.add_vline(x=rw, line_dash="dot", line_color="#26a69a", opacity=0.5)
        for rw in r_puts[:3]:
            fig.add_vline(x=rw, line_dash="dot", line_color="#ef5350", opacity=0.5)

        fig.update_layout(
            title="Multi-Expiry Combined GEX (Green = Positive, Red = Negative)",
            xaxis_title="Strike", yaxis_title="Net GEX",
            template="plotly_dark", height=400,
        )
        st.plotly_chart(fig, use_container_width=True)

    # --- Explanation ---
    with st.expander("Why Multi-Expiry Matters"):
        st.markdown("""
**Single-expiry GEX misses gamma from next week's options.**

Professional desks (SpotGamma, SqueezeMetrics) always aggregate 2-3 expiries:
- **Near-expiry** gamma dominates intraday (charm acceleration, pin risk)
- **Next-expiry** gamma shows where structural support/resistance lives
- **Reinforced walls** — same strike as a wall in multiple expiries = much stronger level

The **weighted gamma flip** combines all expiries' flip levels, weighted by OI share.
When single-expiry flip says 22000 but multi-expiry says 22050, the multi-expiry level
is more reliable because it captures the full dealer positioning.
        """)

except Exception as e:
    st.error(f"Error loading data: {e}")
    st.caption("Make sure your Upstox token is valid (check Settings page).")
