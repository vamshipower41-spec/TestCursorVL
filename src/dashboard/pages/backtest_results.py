"""Backtest Results page — visualize backtesting outcomes."""

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
import plotly.express as px

from config.instruments import INSTRUMENTS
from src.backtest.data_store import HistoricalDataStore
from src.backtest.runner import BacktestRunner
from src.backtest.metrics import compute_signal_metrics, metrics_by_time_of_day, generate_summary


st.title("Backtest Results")

store = HistoricalDataStore()
runner = BacktestRunner(store)

available_instruments = store.list_instruments()

if not available_instruments:
    st.info(
        "No historical data found. Run `scripts/fetch_historical_chains.py` on expiry days "
        "to collect options chain snapshots for backtesting."
    )
    st.stop()

# Controls
instrument = st.sidebar.selectbox("Instrument", available_instruments)
expiries = store.list_available_expiries(instrument)

if not expiries:
    st.warning(f"No expiry data available for {instrument}.")
    st.stop()

run_all = st.sidebar.checkbox("Run all expiries", value=True)
if not run_all:
    selected_expiry = st.sidebar.selectbox("Select Expiry", expiries)
    expiries_to_run = [selected_expiry]
else:
    expiries_to_run = expiries

# Run backtest
if st.sidebar.button("Run Backtest"):
    with st.spinner("Running backtest..."):
        results = [runner.run_expiry_day(instrument, exp) for exp in expiries_to_run]
        st.session_state.backtest_results = results

if "backtest_results" not in st.session_state:
    st.info("Configure settings and click 'Run Backtest' to see results.")
    st.stop()

results = st.session_state.backtest_results

# Summary
summary = generate_summary(results)
cols = st.columns(4)
cols[0].metric("Expiry Days", summary["expiry_days_tested"])
cols[1].metric("Total Signals", summary["total_signals"])
cols[2].metric("Hit Rate", f"{summary['overall_hit_rate']:.0%}")
cols[3].metric("Avg Signals/Day", f"{summary['avg_signals_per_day']:.1f}")

# Per-signal metrics
st.subheader("Signal Type Metrics")
metrics_df = compute_signal_metrics(results)
if not metrics_df.empty:
    st.dataframe(
        metrics_df.style.format({
            "hit_rate": "{:.0%}",
            "avg_favorable_pct": "{:.2%}",
            "avg_adverse_pct": "{:.2%}",
            "profit_factor": "{:.2f}",
            "avg_time_to_hit_min": "{:.0f}",
        }),
        use_container_width=True,
        hide_index=True,
    )

    # Hit rate chart
    fig = px.bar(
        metrics_df, x="signal_type", y="hit_rate",
        color="hit_rate",
        color_continuous_scale="RdYlGn",
        title="Hit Rate by Signal Type",
        template="plotly_dark",
    )
    fig.update_layout(yaxis_tickformat=".0%")
    st.plotly_chart(fig, use_container_width=True)

# Time-of-day analysis
st.subheader("Hit Rate by Time of Day")
tod_df = metrics_by_time_of_day(results)
if not tod_df.empty:
    fig = px.bar(
        tod_df, x="time_bucket", y="hit_rate",
        color="signal_type",
        barmode="group",
        title="Signal Hit Rate by Session",
        template="plotly_dark",
    )
    fig.update_layout(yaxis_tickformat=".0%")
    st.plotly_chart(fig, use_container_width=True)
