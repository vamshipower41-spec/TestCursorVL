"""Settings page — token configuration and system status."""

import sys

sys.path.insert(0, ".")

import streamlit as st
from pathlib import Path

from src.auth.upstox_auth import load_access_token, validate_token
from config.instruments import INSTRUMENTS
from config.settings import CHAIN_POLL_INTERVAL, DASHBOARD_REFRESH_INTERVAL


st.title("Settings")

# Token status
st.subheader("Upstox Access Token")

try:
    token = load_access_token()
    is_valid = validate_token(token)
    if is_valid:
        st.success("Token is valid and active.")
        st.code(f"{token[:10]}...{token[-6:]}", language=None)
    else:
        st.error("Token is expired or invalid. Please update your .env file.")
except ValueError as e:
    st.error(str(e))
    is_valid = False

st.markdown(
    "**To update your token:** Edit the `.env` file in the project root and set "
    "`UPSTOX_ACCESS_TOKEN` to your new daily token from Upstox."
)

# Paste token directly
st.subheader("Quick Token Update")
new_token = st.text_input("Paste new access token:", type="password")
if st.button("Save Token") and new_token:
    env_path = Path(".env")
    env_path.write_text(f"UPSTOX_ACCESS_TOKEN={new_token}\n")
    st.success("Token saved to .env file. Refresh the page to activate.")
    st.rerun()

# Instrument info
st.subheader("Instrument Configuration")
for name, config in INSTRUMENTS.items():
    with st.expander(name):
        st.json(config)

# System settings
st.subheader("System Settings")
st.markdown(f"""
| Setting | Value |
|---------|-------|
| Chain Poll Interval | {CHAIN_POLL_INTERVAL}s |
| Dashboard Refresh | {DASHBOARD_REFRESH_INTERVAL}s |
| Nifty Lot Size | {INSTRUMENTS['NIFTY']['contract_multiplier']} |
| Sensex Lot Size | {INSTRUMENTS['SENSEX']['contract_multiplier']} |
""")
