"""Streamlit dashboard entry point.

Run with: streamlit run src/dashboard/app.py
"""

import sys
from pathlib import Path as _Path

# Resolve project root from this file: src/dashboard/app.py -> project root
_project_root = str(_Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import streamlit as st

from src.auth.upstox_auth import (
    get_login_url,
    exchange_code_for_token,
    validate_token,
    _load_token_from_file,
    _save_token_to_file,
)

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

# --- OAuth Login Gate ---
def _get_redirect_uri() -> str:
    """Determine the app's redirect URI based on environment."""
    # On Streamlit Cloud, use the app's public URL
    try:
        return st.secrets["REDIRECT_URI"]
    except Exception:
        return "http://localhost:8501"


def _handle_oauth():
    """Handle the Upstox OAuth flow. Returns True if authenticated."""
    # 1. Already authenticated this session
    if st.session_state.get("upstox_access_token"):
        return True

    # 2. Check persistent token file (saved from earlier today)
    saved_token = _load_token_from_file()
    if saved_token:
        st.session_state["upstox_access_token"] = saved_token
        return True

    # 3. Check for auth code in URL (redirect from Upstox)
    query_params = st.query_params
    auth_code = query_params.get("code")

    if auth_code:
        try:
            api_key = st.secrets["UPSTOX_API_KEY"]
            api_secret = st.secrets["UPSTOX_API_SECRET"]
            redirect_uri = _get_redirect_uri()
            token = exchange_code_for_token(
                auth_code, api_key, api_secret, redirect_uri
            )
            st.session_state["upstox_access_token"] = token
            _save_token_to_file(token)
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Login failed: {e}")
            st.query_params.clear()
            return False

    return False


if not _handle_oauth():
    # Show login page
    st.title("GEX Signal Dashboard")
    st.markdown("---")
    st.subheader("Login with Upstox to continue")
    st.caption("Your credentials go directly to Upstox — this app never sees your password.")

    try:
        api_key = st.secrets["UPSTOX_API_KEY"]
        redirect_uri = _get_redirect_uri()
        login_url = get_login_url(api_key, redirect_uri)
        st.link_button("Login with Upstox", login_url, type="primary")
    except Exception:
        st.error("UPSTOX_API_KEY not configured. Add it in Streamlit Secrets.")

    st.stop()

# --- Start background alert worker (runs even when you switch tabs) ---
from src.engine.alert_worker import start_alert_worker

_token = st.session_state.get("upstox_access_token", "")
if _token:
    start_alert_worker(_token)

# Navigation — resolve page paths relative to this file
from pathlib import Path

_pages_dir = Path(__file__).parent / "pages"

pages = {
    "Trading": [
        st.Page(str(_pages_dir / "action_center.py"), title="Action Center", icon="🎯", default=True),
        st.Page(str(_pages_dir / "market_radar.py"), title="Market Radar", icon="📡"),
        st.Page(str(_pages_dir / "paper_trades.py"), title="Paper Trades", icon="📋"),
    ],
    "Advanced": [
        st.Page(str(_pages_dir / "gamma_blast.py"), title="Gamma Blast (Pro)", icon="💥"),
        st.Page(str(_pages_dir / "live_gex.py"), title="Live GEX Monitor", icon="📈"),
        st.Page(str(_pages_dir / "multi_expiry.py"), title="Multi-Expiry GEX", icon="📊"),
    ],
    "Analysis": [
        st.Page(str(_pages_dir / "signals.py"), title="Signal Timeline", icon="🔔"),
        st.Page(str(_pages_dir / "oi_flow.py"), title="OI Flow Analysis", icon="🔄"),
        st.Page(str(_pages_dir / "greeks_dashboard.py"), title="Greeks Dashboard", icon="📐"),
        st.Page(str(_pages_dir / "backtest_results.py"), title="Backtest Results", icon="📊"),
    ],
    "Config": [
        st.Page(str(_pages_dir / "settings_page.py"), title="Settings", icon="⚙️"),
    ],
}

pg = st.navigation(pages)
pg.run()
