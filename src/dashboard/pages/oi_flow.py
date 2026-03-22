"""OI Flow Visualization — Bought vs Sold positioning heatmap.

Shows real-time OI flow classification per strike:
- Long Buildup (bought) vs Short Buildup (sold)
- Net dealer delta estimation
- Dominant flow direction with confidence
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
from config.settings import DASHBOARD_REFRESH_INTERVAL
from src.auth.upstox_auth import load_access_token
from src.data.options_chain import OptionsChainFetcher
from src.engine.greeks import validate_greeks, filter_active_strikes
from src.engine.oi_flow import classify_oi_flow, FlowType


st.header("OI Flow Analysis")
st.caption("Approximate bought vs sold positioning — who's driving the market.")

# Auto-refresh
if HAS_AUTOREFRESH:
    st_autorefresh(interval=DASHBOARD_REFRESH_INTERVAL * 1000, key="oi_flow_refresh")

# Instrument selector
instrument_name = st.selectbox("Instrument", list(INSTRUMENTS.keys()), key="oi_flow_inst")
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

    # Get previous chain from session state
    prev_key = f"oi_flow_prev_chain_{instrument_name}"
    prev_chain = st.session_state.get(prev_key)
    st.session_state[prev_key] = chain_df.copy()

    if prev_chain is None:
        st.info("Collecting first snapshot... OI flow analysis will appear on next refresh.")
        st.metric("Spot Price", f"{spot_price:,.2f}")
        st.stop()

    # Classify flow
    flow_result = classify_oi_flow(chain_df, prev_chain, spot_price)

    # --- Summary Cards ---
    st.subheader(f"{instrument_name} | Spot: {spot_price:,.2f} | Expiry: {expiry_date}")

    dominant = flow_result["dominant_flow"]
    confidence = flow_result["flow_confidence"]
    dom_color = {"bullish": "green", "bearish": "red", "neutral": "orange"}.get(dominant, "gray")

    c1, c2, c3 = st.columns(3)
    c1.metric("Dominant Flow", dominant.upper(), delta=f"{confidence:.0%} confidence")
    c2.metric("Net Dealer Delta", f"{flow_result['net_dealer_delta']:,.0f}")

    net_bullish = flow_result.get("net_bought_calls", 0) + flow_result.get("net_sold_puts", 0)
    net_bearish = flow_result.get("net_bought_puts", 0) + flow_result.get("net_sold_calls", 0)
    c3.metric("Bull/Bear OI", f"{net_bullish:,.0f} / {net_bearish:,.0f}")

    # --- OI Flow Breakdown ---
    c4, c5, c6, c7 = st.columns(4)
    c4.metric("Bought Calls", f"{flow_result.get('net_bought_calls', 0):,}")
    c5.metric("Sold Calls", f"{flow_result.get('net_sold_calls', 0):,}")
    c6.metric("Bought Puts", f"{flow_result.get('net_bought_puts', 0):,}")
    c7.metric("Sold Puts", f"{flow_result.get('net_sold_puts', 0):,}")

    # --- Strike-by-Strike Flow Chart ---
    strike_flows = flow_result.get("strike_flows", [])
    if strike_flows:
        st.subheader("Strike-Level OI Flow")

        flow_data = []
        for sf in strike_flows:
            call_label = sf.call_flow.value.replace("_", " ").title()
            put_label = sf.put_flow.value.replace("_", " ").title()

            # Color-code: bought = green, sold = red
            call_color = "green" if sf.call_flow in (FlowType.LONG_BUILDUP, FlowType.SHORT_COVERING) else "red" if sf.call_flow in (FlowType.SHORT_BUILDUP, FlowType.LONG_UNWINDING) else "gray"
            put_color = "green" if sf.put_flow in (FlowType.LONG_BUILDUP, FlowType.SHORT_COVERING) else "red" if sf.put_flow in (FlowType.SHORT_BUILDUP, FlowType.LONG_UNWINDING) else "gray"

            flow_data.append({
                "Strike": sf.strike,
                "Call Flow": call_label,
                "Call OI Chg": sf.call_oi_change,
                "Call Conf": f"{sf.call_confidence:.0%}",
                "Put Flow": put_label,
                "Put OI Chg": sf.put_oi_change,
                "Put Conf": f"{sf.put_confidence:.0%}",
            })

        flow_df = pd.DataFrame(flow_data)
        st.dataframe(flow_df, use_container_width=True, hide_index=True)

        # --- Visual: OI Change by Strike ---
        fig = go.Figure()
        strikes = [sf.strike for sf in strike_flows]
        call_changes = [sf.call_oi_change for sf in strike_flows]
        put_changes = [sf.put_oi_change for sf in strike_flows]

        call_colors = [
            "#26a69a" if sf.call_flow in (FlowType.LONG_BUILDUP, FlowType.SHORT_COVERING)
            else "#ef5350" if sf.call_flow in (FlowType.SHORT_BUILDUP, FlowType.LONG_UNWINDING)
            else "#888"
            for sf in strike_flows
        ]
        put_colors = [
            "#26a69a" if sf.put_flow in (FlowType.LONG_BUILDUP, FlowType.SHORT_COVERING)
            else "#ef5350" if sf.put_flow in (FlowType.SHORT_BUILDUP, FlowType.LONG_UNWINDING)
            else "#888"
            for sf in strike_flows
        ]

        fig.add_trace(go.Bar(
            x=strikes, y=call_changes, name="Call OI Change",
            marker_color=call_colors, opacity=0.8,
        ))
        fig.add_trace(go.Bar(
            x=strikes, y=[-p for p in put_changes], name="Put OI Change (inverted)",
            marker_color=put_colors, opacity=0.8,
        ))

        fig.add_vline(x=spot_price, line_dash="dash", line_color="white",
                      annotation_text=f"Spot {spot_price:,.0f}")

        fig.update_layout(
            title="OI Changes by Strike (Green = Bought, Red = Sold)",
            xaxis_title="Strike", yaxis_title="OI Change",
            barmode="group", template="plotly_dark",
            height=400,
        )
        st.plotly_chart(fig, use_container_width=True)

    # --- Explanation ---
    with st.expander("How OI Flow Works"):
        st.markdown("""
**OI Flow Direction** classifies whether options were bought or sold:

| OI Change | Price Change | Classification | Meaning |
|-----------|-------------|----------------|---------|
| OI Up | Price Up | **Long Buildup** | Bought (bullish for calls) |
| OI Up | Price Down | **Short Buildup** | Sold (bearish for calls) |
| OI Down | Price Up | **Short Covering** | Covering shorts |
| OI Down | Price Down | **Long Unwinding** | Exiting longs |

**Accuracy**: ~70-80% aggregate (compared to SpotGamma's ~95% with OPRA tick data).

**Green** = Demand-driven (bought) | **Red** = Supply-driven (sold)
        """)

except Exception as e:
    st.error(f"Error loading data: {e}")
    st.caption("Make sure your Upstox token is valid (check Settings page).")
