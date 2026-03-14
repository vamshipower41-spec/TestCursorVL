"""Signal Timeline page — view and filter signal history."""

import sys

sys.path.insert(0, ".")

import streamlit as st
import pandas as pd

from src.dashboard.components.signal_timeline import render_signal_timeline


st.title("Signal Timeline")

if "signal_history" not in st.session_state or not st.session_state.signal_history:
    st.info("No signals yet. Start the Live GEX Monitor to generate signals.")
    st.stop()

signals = st.session_state.signal_history

# Filters
signal_types = sorted({s.signal_type for s in signals})
selected_types = st.sidebar.multiselect(
    "Filter Signal Types", signal_types, default=signal_types
)
min_strength = st.sidebar.slider("Minimum Strength", 0.0, 1.0, 0.0, 0.05)

filtered = [
    s for s in signals
    if s.signal_type in selected_types and s.strength >= min_strength
]

# Timeline chart
st.plotly_chart(render_signal_timeline(filtered), use_container_width=True)

# Detail table
st.subheader(f"Signal Log ({len(filtered)} signals)")

rows = []
for s in reversed(filtered):
    rows.append({
        "Time": s.timestamp.strftime("%H:%M:%S"),
        "Type": s.signal_type.replace("_", " ").title(),
        "Level": f"{s.level:,.2f}",
        "Strength": f"{s.strength:.0%}",
        "Direction": (s.direction or "neutral").title(),
        "Instrument": s.instrument,
    })

if rows:
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
