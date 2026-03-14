"""Streamlit dashboard entry point.

Run with: streamlit run src/dashboard/app.py
"""

import streamlit as st

st.set_page_config(
    page_title="GEX Signal Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",  # Collapsed by default for mobile
)

# Mobile-responsive CSS
st.markdown("""
<style>
/* Larger metric values for mobile readability */
[data-testid="stMetricValue"] {
    font-size: 1.3rem !important;
    font-weight: 700 !important;
}
[data-testid="stMetricLabel"] {
    font-size: 0.85rem !important;
}
[data-testid="stMetricDelta"] {
    font-size: 0.8rem !important;
}

/* Better spacing for touch targets */
.stButton > button {
    min-height: 48px !important;
    font-size: 1rem !important;
}
.stRadio > div {
    gap: 0.5rem !important;
}
.stRadio label {
    padding: 8px 16px !important;
    min-height: 44px !important;
    display: flex !important;
    align-items: center !important;
}

/* Signal cards styling */
.signal-card {
    padding: 12px 16px;
    border-radius: 8px;
    margin-bottom: 8px;
    font-size: 1rem;
    border-left: 4px solid;
}
.signal-bullish { background: #1b3a26; border-color: #26a69a; }
.signal-bearish { background: #3a1b1b; border-color: #ef5350; }
.signal-neutral { background: #3a2e1b; border-color: #ffc107; }

/* Mobile: stack columns vertically */
@media (max-width: 768px) {
    [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
    }
    [data-testid="stHorizontalBlock"] > div {
        flex: 1 1 30% !important;
        min-width: 100px !important;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.1rem !important;
    }
}

/* iPad: moderate adjustments */
@media (min-width: 769px) and (max-width: 1024px) {
    [data-testid="stHorizontalBlock"] > div {
        flex: 1 1 30% !important;
    }
}
</style>
""", unsafe_allow_html=True)

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
