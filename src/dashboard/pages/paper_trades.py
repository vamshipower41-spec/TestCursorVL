"""Paper Trade Tracker — Live P&L, win/loss streaks, signal accountability.

Shows real-time paper trade outcomes, running statistics, and
historical performance to validate blast signal accuracy.
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
import json
from datetime import date, timedelta

from config.settings import PAPER_TRADE_DIR


def _load_trades(log_dir: str, target_date: date | None = None) -> list[dict]:
    """Load paper trades from JSON lines files."""
    log_path = Path(log_dir)
    if not log_path.exists():
        return []

    trades = []
    if target_date:
        files = [log_path / f"trades_{target_date.isoformat()}.jsonl"]
    else:
        files = sorted(log_path.glob("trades_*.jsonl"))

    for f in files:
        if not f.exists():
            continue
        for line in f.read_text().strip().split("\n"):
            if line.strip():
                try:
                    trades.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return trades


def _compute_stats(trades: list[dict]) -> dict:
    """Compute statistics from trade records."""
    closed = [t for t in trades if t.get("_event") == "CLOSE"]
    if not closed:
        return {"total_trades": 0, "hit_rate": 0.0, "avg_pnl_pct": 0.0,
                "total_pnl_pct": 0.0, "best_trade_pnl": 0.0, "worst_trade_pnl": 0.0,
                "profit_factor": 0.0, "max_win_streak": 0, "max_loss_streak": 0,
                "targets_hit": 0, "stops_hit": 0, "expired": 0}

    pnls = [t.get("pnl_pct", 0) for t in closed]
    outcomes = [t.get("outcome", "") for t in closed]

    targets = sum(1 for o in outcomes if o == "target_hit")
    stops = sum(1 for o in outcomes if o == "sl_hit")
    expired = sum(1 for o in outcomes if o == "expired")
    total = len(closed)

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0

    # Win/loss streaks
    max_win = max_loss = cur_win = cur_loss = 0
    for p in pnls:
        if p > 0:
            cur_win += 1
            cur_loss = 0
            max_win = max(max_win, cur_win)
        elif p < 0:
            cur_loss += 1
            cur_win = 0
            max_loss = max(max_loss, cur_loss)
        else:
            cur_win = cur_loss = 0

    return {
        "total_trades": total,
        "hit_rate": targets / total if total > 0 else 0,
        "avg_pnl_pct": sum(pnls) / total if total else 0,
        "total_pnl_pct": sum(pnls),
        "best_trade_pnl": max(pnls) if pnls else 0,
        "worst_trade_pnl": min(pnls) if pnls else 0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0,
        "max_win_streak": max_win,
        "max_loss_streak": max_loss,
        "targets_hit": targets,
        "stops_hit": stops,
        "expired": expired,
    }


# --- Page ---
st.header("Paper Trade Tracker")
st.caption("Track every blast signal outcome — no hiding from the numbers.")

# Date selector
col1, col2 = st.columns([1, 2])
with col1:
    view_mode = st.radio("View", ["Today", "Last 7 Days", "All Time"], horizontal=True)

if view_mode == "Today":
    trades = _load_trades(PAPER_TRADE_DIR, date.today())
elif view_mode == "Last 7 Days":
    trades = []
    for i in range(7):
        d = date.today() - timedelta(days=i)
        trades.extend(_load_trades(PAPER_TRADE_DIR, d))
else:
    trades = _load_trades(PAPER_TRADE_DIR)

stats = _compute_stats(trades)

# --- Stats Cards ---
st.subheader("Performance")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Trades", stats["total_trades"])
c2.metric("Hit Rate", f"{stats['hit_rate']:.0%}" if stats["total_trades"] > 0 else "N/A")
c3.metric("Avg P&L", f"{stats['avg_pnl_pct']:+.2f}%" if stats["total_trades"] > 0 else "N/A")
c4.metric("Profit Factor", f"{stats['profit_factor']:.2f}" if stats["total_trades"] > 0 else "N/A")

c5, c6, c7, c8 = st.columns(4)
c5.metric("Targets Hit", stats["targets_hit"])
c6.metric("Stops Hit", stats["stops_hit"])
c7.metric("Win Streak", stats["max_win_streak"])
c8.metric("Loss Streak", stats["max_loss_streak"])

if stats["total_trades"] > 0:
    best_worst = st.columns(2)
    best_worst[0].metric("Best Trade", f"{stats['best_trade_pnl']:+.2f}%")
    best_worst[1].metric("Worst Trade", f"{stats['worst_trade_pnl']:+.2f}%")

# --- Active Trades ---
open_trades = [t for t in trades if t.get("_event") == "OPEN"
               and t.get("trade_id") not in {
                   ct.get("trade_id") for ct in trades if ct.get("_event") == "CLOSE"
               }]

if open_trades:
    st.subheader("Active Trades")
    for t in open_trades:
        dir_icon = "UP" if t.get("direction") == "bullish" else "DOWN"
        st.markdown(
            f'<div class="signal-card signal-{"bullish" if t.get("direction") == "bullish" else "bearish"}">'
            f'<b>{dir_icon} {t.get("instrument", "?")} — {t.get("direction", "?").upper()}</b><br>'
            f'Entry: {t.get("entry_price", 0):,.2f} | SL: {t.get("stop_loss", 0):,.2f} | '
            f'Target: {t.get("target", 0):,.2f}<br>'
            f'Score: {t.get("composite_score", 0):.0f}/100'
            f'</div>',
            unsafe_allow_html=True,
        )

# --- Trade Log ---
closed_trades = [t for t in trades if t.get("_event") == "CLOSE"]
if closed_trades:
    st.subheader("Trade Log")
    log_data = []
    for t in reversed(closed_trades):
        outcome = t.get("outcome", "")
        icon = {"target_hit": "TARGET", "sl_hit": "SL HIT", "expired": "EXPIRED"}.get(outcome, outcome)
        log_data.append({
            "Time": t.get("entry_time", "")[:16],
            "Instrument": t.get("instrument", ""),
            "Direction": t.get("direction", "").upper(),
            "Entry": f"{t.get('entry_price', 0):,.2f}",
            "Exit": f"{t.get('exit_price', 0):,.2f}",
            "Outcome": icon,
            "P&L": f"{t.get('pnl_pct', 0):+.2f}%",
            "Score": f"{t.get('composite_score', 0):.0f}",
            "Duration": f"{t.get('duration_minutes', 0):.0f}m",
        })
    st.dataframe(pd.DataFrame(log_data), use_container_width=True, hide_index=True)

    # --- P&L Chart ---
    st.subheader("Cumulative P&L")
    pnl_series = [t.get("pnl_pct", 0) for t in closed_trades]
    cumulative = []
    running = 0
    for p in pnl_series:
        running += p
        cumulative.append(running)
    chart_df = pd.DataFrame({"Trade #": range(1, len(cumulative) + 1), "Cumulative P&L %": cumulative})
    st.line_chart(chart_df, x="Trade #", y="Cumulative P&L %")
elif stats["total_trades"] == 0:
    st.info("No paper trades yet. Trades are logged automatically when blast signals fire during live monitoring.")
