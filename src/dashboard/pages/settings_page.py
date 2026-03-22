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

from src.auth.upstox_auth import load_access_token, validate_token, _save_token_to_file
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
    # Also save to persistent file + session state
    _save_token_to_file(new_token)
    st.session_state["upstox_access_token"] = new_token
    st.success("Token saved! You won't need to login again today.")
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
st.markdown("- **Prepare Alerts** — early warning when models are warming up near key levels (CALL/PUT heads-up)")
st.markdown("- **Gamma Blast signals** — entry/SL/target with model breakdown")
st.markdown("- **Directional trend alerts** — sustained bullish or bearish moves")

if st.button("Send Test Alert"):
    msg = "\U0001F514 <b>Test Alert</b>\n\nTelegram alerts are working! You will receive:\n\n\u26a1 Prepare alerts (early warning)\n\U0001F4A5 Gamma Blast signals\n\U0001F4C8 Directional trend alerts"
    if send_telegram(msg):
        st.success("Test alert sent! Check your Telegram.")
    else:
        st.error("Failed to send. Check that TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set in Secrets.")

# Background worker status
st.subheader("Background Alert Worker")
from src.engine.alert_worker import _worker_started
if _worker_started:
    st.success("Background worker is RUNNING — alerts will be sent even if you close this tab.")
else:
    st.warning("Background worker not started. Go to any page to activate it.")

st.markdown("""
**How it works:**
- A background thread monitors NIFTY & SENSEX automatically
- Sends Telegram alerts every ~90 seconds during market hours (9:15 AM - 3:30 PM)
- Works even if you switch apps or lock your phone

**To keep alerts running all day (important!):**

Since this is on Streamlit Cloud, the app sleeps if no one visits for a while.
To prevent this, set up a **free ping service**:

1. Go to [UptimeRobot](https://uptimerobot.com) (free) or [cron-job.org](https://cron-job.org)
2. Add your app URL as a monitor
3. Set check interval to **5 minutes**
4. This keeps the app awake → background alerts run all day

Your app URL: the Streamlit Cloud URL (e.g., `https://your-app.streamlit.app`)
""")
