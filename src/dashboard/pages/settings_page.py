"""Settings page — token configuration and system status."""

import sys
from pathlib import Path as _Path

try:
    _project_root = str(_Path(__file__).resolve().parent.parent.parent.parent)
except NameError:
    _project_root = "."
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import streamlit as st
from pathlib import Path

from src.auth.upstox_auth import load_access_token, validate_token
from config.instruments import INSTRUMENTS
from config.settings import CHAIN_POLL_INTERVAL, DASHBOARD_REFRESH_INTERVAL, TELEGRAM_ENABLED
from src.notifications.telegram import send_telegram


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
    import os as _os
    env_path = Path(".env")
    # Preserve existing .env content, only update the token line
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    updated = False
    for i, line in enumerate(lines):
        if line.startswith("UPSTOX_ACCESS_TOKEN="):
            lines[i] = f"UPSTOX_ACCESS_TOKEN={new_token}"
            updated = True
            break
    if not updated:
        lines.append(f"UPSTOX_ACCESS_TOKEN={new_token}")
    env_path.write_text("\n".join(lines) + "\n")
    # Restrict file permissions to owner-only (rw-------)
    try:
        _os.chmod(env_path, 0o600)
    except OSError:
        pass  # Windows or restricted filesystem — skip silently
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
| Telegram Alerts | {'Enabled' if TELEGRAM_ENABLED else 'Disabled'} |
""")

# Telegram settings
st.subheader("Telegram Alerts")
st.markdown("""
**Setup instructions:**
1. Message **@BotFather** on Telegram → `/newbot` → get your **BOT_TOKEN**
2. Message your bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your **CHAT_ID**
3. Add to **Streamlit Secrets** (Settings → Secrets):
```toml
TELEGRAM_BOT_TOKEN = "your_bot_token"
TELEGRAM_CHAT_ID = "your_chat_id"
```
""")

st.markdown("**You will receive alerts for:**")
st.markdown("- Gamma Blast signals (entry/SL/target with model breakdown)")
st.markdown("- Sustained directional moves (bullish or bearish, NOT consolidation)")

if st.button("Send Test Alert"):
    msg = "\U0001F514 <b>Test Alert</b>\n\nTelegram alerts are working! You will receive:\n\n• Gamma Blast signals\n• Directional trend alerts"
    if send_telegram(msg):
        st.success("Test alert sent! Check your Telegram.")
    else:
        st.error("Failed to send. Check that TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set in Secrets.")
