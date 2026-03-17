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
from src.backtest.metrics import (
    compute_signal_metrics, metrics_by_time_of_day, generate_summary,
    compute_trade_details, compute_trade_summary,
)


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

# Trade P&L Summary
trade_summary = compute_trade_summary(results)
if trade_summary["total_trades"] > 0:
    st.subheader("Trade P&L Summary")
    tcols = st.columns(5)
    tcols[0].metric("Total Trades", trade_summary["total_trades"])
    tcols[1].metric("Winners", trade_summary["winners"])
    tcols[2].metric("Win Rate", f"{trade_summary['win_rate']:.0%}")
    tcols[3].metric("Total P&L", f"{trade_summary['total_pnl_pts']:+.1f} pts")
    tcols[4].metric("Best Trade", f"{trade_summary['best_trade_pts']:+.1f} pts")

    tcols2 = st.columns(3)
    tcols2[0].metric("Avg Win", f"{trade_summary['avg_win_pts']:+.1f} pts")
    tcols2[1].metric("Avg Loss", f"{trade_summary['avg_loss_pts']:+.1f} pts")
    tcols2[2].metric("Worst Trade", f"{trade_summary['worst_trade_pts']:+.1f} pts")

# Detailed Trade Log
st.subheader("Trade Log — Strike, CE/PE, Entry/Exit, P&L")
trade_df = compute_trade_details(results)
if not trade_df.empty:
    # Color code P&L
    def highlight_pnl(val):
        if isinstance(val, (int, float)):
            if val > 0:
                return "color: #00cc44"
            elif val < 0:
                return "color: #ff4444"
        return ""

    st.dataframe(
        trade_df.style
        .map(highlight_pnl, subset=["P&L (pts)", "P&L %"])
        .format({
            "Strength": "{:.2f}",
            "Spot Entry": "{:.1f}",
            "Spot Exit": "{:.1f}",
            "Strike": "{:.0f}",
            "Entry LTP": "{:.2f}",
            "Exit LTP": "{:.2f}",
            "P&L (pts)": "{:+.2f}",
            "P&L %": "{:+.1f}%",
            "Predicted Level": "{:.0f}",
        }),
        use_container_width=True,
        hide_index=True,
        height=400,
    )

    # Filter by signal type
    st.subheader("Filter Trades by Signal Type")
    sig_filter = st.selectbox(
        "Signal Type", ["All"] + sorted(trade_df["Signal"].unique().tolist())
    )
    if sig_filter != "All":
        filtered = trade_df[trade_df["Signal"] == sig_filter]
    else:
        filtered = trade_df

    # Win/Loss breakdown chart
    if not filtered.empty:
        win_loss = filtered.copy()
        win_loss["Result"] = win_loss["P&L (pts)"].apply(
            lambda x: "Winner" if x > 0 else "Loser"
        )
        fig_wl = px.histogram(
            win_loss, x="P&L (pts)", color="Result",
            color_discrete_map={"Winner": "#00cc44", "Loser": "#ff4444"},
            title="Trade P&L Distribution",
            template="plotly_dark",
            nbins=20,
        )
        st.plotly_chart(fig_wl, use_container_width=True)
else:
    st.info("No trades recorded. Run backtest with historical chain data to see trade details.")

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
