"""Greeks Dashboard — Delta, Gamma, Vanna, Charm profile visualization.

Professional-grade options Greeks analysis with:
- Delta profile (call vs put) by strike
- Gamma curve with peak gamma highlighted
- Charm flow (time decay pressure)
- Vanna exposure (IV sensitivity)
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
from plotly.subplots import make_subplots

from src.utils.ist import now_ist, time_to_expiry_hours as compute_tte

try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except ImportError:
    HAS_AUTOREFRESH = False

from config.instruments import get_instrument, INSTRUMENTS
from config.settings import DASHBOARD_REFRESH_INTERVAL
from src.auth.upstox_auth import load_access_token
from src.data.options_chain import OptionsChainFetcher
from src.engine.greeks import validate_greeks, filter_active_strikes
from src.engine.bs_greeks import compute_chain_greeks, compute_dealer_charm_flow
from src.engine.gex_calculator import build_gex_profile


st.header("Greeks Dashboard")
st.caption("Options Greeks profiles — Delta, Gamma, Charm, Vanna by strike.")

if HAS_AUTOREFRESH:
    st_autorefresh(interval=DASHBOARD_REFRESH_INTERVAL * 1000, key="greeks_refresh")

instrument_name = st.selectbox("Instrument", list(INSTRUMENTS.keys()), key="greeks_inst")
inst = get_instrument(instrument_name)

try:
    token = load_access_token()
    fetcher = OptionsChainFetcher(token)
    expiry_date = fetcher.get_nearest_expiry(inst["instrument_key"])

    chain_df, spot_price = fetcher.fetch_chain(inst["instrument_key"], expiry_date)
    if chain_df.empty:
        st.warning("No chain data available.")
        st.stop()

    chain_df = validate_greeks(chain_df)
    chain_df = filter_active_strikes(chain_df, spot_price, num_strikes=30)

    tte = compute_tte(expiry_date)

    # Compute BS Greeks
    bs_chain = compute_chain_greeks(chain_df, spot_price, tte)

    # Compute charm flow
    charm_data = compute_dealer_charm_flow(chain_df, spot_price, tte, inst["contract_multiplier"])

    # Build GEX profile for gamma flip reference
    profile = build_gex_profile(chain_df, spot_price, inst["contract_multiplier"],
                                instrument_name, expiry_date)

    # --- Header metrics ---
    st.subheader(f"{instrument_name} | Spot: {spot_price:,.2f} | Expiry: {expiry_date} | TTE: {tte:.1f}h")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Gamma Flip", f"{profile.gamma_flip_level:,.0f}" if profile.gamma_flip_level else "N/A")
    c2.metric("Max Gamma", f"{profile.max_gamma_strike:,.0f}" if profile.max_gamma_strike else "N/A")
    c3.metric("Charm Intensity", f"{charm_data.get('intensity', 0):.0f}/100")
    c4.metric("Charm Direction", charm_data.get("direction", "N/A").upper())

    strikes = bs_chain["strike_price"].tolist()

    # --- 1. Delta Profile ---
    st.subheader("Delta Profile")
    fig_delta = go.Figure()

    if "bs_call_delta" in bs_chain.columns:
        fig_delta.add_trace(go.Scatter(
            x=strikes, y=bs_chain["bs_call_delta"], name="Call Delta",
            line=dict(color="#26a69a", width=2),
        ))
        fig_delta.add_trace(go.Scatter(
            x=strikes, y=bs_chain["bs_put_delta"], name="Put Delta",
            line=dict(color="#ef5350", width=2),
        ))
    else:
        fig_delta.add_trace(go.Scatter(
            x=strikes, y=chain_df["call_delta"], name="Call Delta",
            line=dict(color="#26a69a", width=2),
        ))
        fig_delta.add_trace(go.Scatter(
            x=strikes, y=chain_df["put_delta"], name="Put Delta",
            line=dict(color="#ef5350", width=2),
        ))

    fig_delta.add_vline(x=spot_price, line_dash="dash", line_color="yellow",
                        annotation_text=f"Spot {spot_price:,.0f}")
    fig_delta.add_hline(y=0, line_dash="dot", line_color="gray")
    fig_delta.update_layout(
        xaxis_title="Strike", yaxis_title="Delta",
        template="plotly_dark", height=350,
    )
    st.plotly_chart(fig_delta, use_container_width=True)

    # --- 2. Gamma Curve ---
    st.subheader("Gamma Curve")
    fig_gamma = go.Figure()

    gamma_col = "bs_gamma" if "bs_gamma" in bs_chain.columns else "call_gamma"
    gamma_vals = bs_chain[gamma_col].tolist() if gamma_col in bs_chain.columns else chain_df["call_gamma"].tolist()

    # Find peak gamma
    max_gamma_idx = gamma_vals.index(max(gamma_vals)) if len(gamma_vals) > 0 else 0
    peak_strike = strikes[max_gamma_idx] if strikes else 0

    fig_gamma.add_trace(go.Scatter(
        x=strikes, y=gamma_vals, name="Gamma",
        fill="tozeroy", fillcolor="rgba(38, 166, 154, 0.3)",
        line=dict(color="#26a69a", width=2),
    ))
    fig_gamma.add_vline(x=spot_price, line_dash="dash", line_color="yellow",
                        annotation_text=f"Spot")
    fig_gamma.add_vline(x=peak_strike, line_dash="dot", line_color="#ffc107",
                        annotation_text=f"Peak Gamma {peak_strike:,.0f}")

    fig_gamma.update_layout(
        xaxis_title="Strike", yaxis_title="Gamma",
        template="plotly_dark", height=350,
    )
    st.plotly_chart(fig_gamma, use_container_width=True)

    # --- 3. Charm (dDelta/dTime) ---
    if "bs_charm" in bs_chain.columns:
        st.subheader("Charm (Delta Decay)")
        fig_charm = go.Figure()

        charm_vals = bs_chain["bs_charm"].tolist()

        fig_charm.add_trace(go.Bar(
            x=strikes, y=charm_vals, name="Charm",
            marker_color=["#ef5350" if v < 0 else "#26a69a" for v in charm_vals],
        ))
        fig_charm.add_vline(x=spot_price, line_dash="dash", line_color="yellow",
                            annotation_text="Spot")
        fig_charm.update_layout(
            xaxis_title="Strike", yaxis_title="Charm (dDelta/dTime)",
            template="plotly_dark", height=350,
        )
        st.plotly_chart(fig_charm, use_container_width=True)

        st.caption("Charm shows how delta changes as time passes. "
                   "Negative charm = delta eroding (dealers must hedge). "
                   "Strongest near expiry at ATM strikes.")

    # --- 4. Vanna (dDelta/dIV) ---
    if "bs_vanna" in bs_chain.columns:
        st.subheader("Vanna (IV Sensitivity)")
        fig_vanna = go.Figure()

        vanna_vals = bs_chain["bs_vanna"].tolist()

        fig_vanna.add_trace(go.Bar(
            x=strikes, y=vanna_vals, name="Vanna",
            marker_color=["#ef5350" if v < 0 else "#26a69a" for v in vanna_vals],
        ))
        fig_vanna.add_vline(x=spot_price, line_dash="dash", line_color="yellow",
                            annotation_text="Spot")
        fig_vanna.update_layout(
            xaxis_title="Strike", yaxis_title="Vanna (dDelta/dIV)",
            template="plotly_dark", height=350,
        )
        st.plotly_chart(fig_vanna, use_container_width=True)

        st.caption("Vanna shows how delta changes when IV moves. "
                   "When IV drops (e.g., post-event), vanna drives dealer hedging flows.")

    # --- 5. IV Smile ---
    st.subheader("IV Smile")
    fig_iv = go.Figure()
    fig_iv.add_trace(go.Scatter(
        x=strikes, y=chain_df["call_iv"], name="Call IV",
        line=dict(color="#26a69a", width=2),
    ))
    fig_iv.add_trace(go.Scatter(
        x=strikes, y=chain_df["put_iv"], name="Put IV",
        line=dict(color="#ef5350", width=2),
    ))
    fig_iv.add_vline(x=spot_price, line_dash="dash", line_color="yellow",
                     annotation_text="Spot")
    fig_iv.update_layout(
        xaxis_title="Strike", yaxis_title="Implied Volatility (%)",
        template="plotly_dark", height=350,
    )
    st.plotly_chart(fig_iv, use_container_width=True)

    # --- Explanation ---
    with st.expander("Greeks Glossary"):
        st.markdown("""
| Greek | Formula | What it tells you |
|-------|---------|-------------------|
| **Delta** | dPrice/dSpot | Directional exposure (0 to 1 for calls, -1 to 0 for puts) |
| **Gamma** | dDelta/dSpot | Rate of delta change — highest ATM, drives dealer hedging |
| **Charm** | dDelta/dTime | Time decay of delta — intensifies near expiry |
| **Vanna** | dDelta/dIV | IV sensitivity of delta — drives flows during IV crush/spike |
| **Theta** | dPrice/dTime | Time decay of option premium |

All computed using exact **Black-Scholes** formulas with Indian market parameters
(risk-free rate: 6.5% T-Bill, dividend yield: 1.3%).
        """)

except Exception as e:
    st.error(f"Error loading data: {e}")
    st.caption("Make sure your Upstox token is valid (check Settings page).")
