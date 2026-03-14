"""Streamlit dashboard entry point.

Run with: streamlit run src/dashboard/app.py
"""

import streamlit as st

st.set_page_config(
    page_title="GEX Signal Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Navigation
pages = {
    "Live GEX": [
        st.Page("src/dashboard/pages/live_gex.py", title="Live GEX Monitor", icon="📈"),
    ],
    "Analysis": [
        st.Page("src/dashboard/pages/signals.py", title="Signal Timeline", icon="🔔"),
        st.Page("src/dashboard/pages/backtest_results.py", title="Backtest Results", icon="📊"),
    ],
    "Config": [
        st.Page("src/dashboard/pages/settings_page.py", title="Settings", icon="⚙️"),
    ],
}

pg = st.navigation(pages)
pg.run()
