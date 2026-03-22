"""Upstox authentication — OAuth login flow + token management."""

import os
import json
from pathlib import Path
from datetime import date
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

from config.settings import UPSTOX_BASE_URL

UPSTOX_AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
UPSTOX_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"

# Persistent token file — survives page refreshes, tab idling, restarts
_TOKEN_FILE = Path(__file__).resolve().parent.parent.parent / ".upstox_token.json"


def _save_token_to_file(token: str) -> None:
    """Save token with today's date so we know when it expires."""
    try:
        _TOKEN_FILE.write_text(json.dumps({
            "token": token,
            "date": date.today().isoformat(),
        }))
        os.chmod(_TOKEN_FILE, 0o600)
    except OSError:
        pass


def _load_token_from_file() -> str | None:
    """Load token if it was saved today (Upstox tokens are daily)."""
    try:
        if not _TOKEN_FILE.exists():
            return None
        data = json.loads(_TOKEN_FILE.read_text())
        if data.get("date") == date.today().isoformat():
            return data.get("token")
    except Exception:
        pass
    return None


def get_login_url(api_key: str, redirect_uri: str) -> str:
    """Build the Upstox OAuth login URL."""
    params = {
        "response_type": "code",
        "client_id": api_key,
        "redirect_uri": redirect_uri,
    }
    return f"{UPSTOX_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_token(
    code: str, api_key: str, api_secret: str, redirect_uri: str
) -> str:
    """Exchange the authorization code for an access token.

    Returns the access token string. Raises ValueError on failure.
    """
    resp = requests.post(
        UPSTOX_TOKEN_URL,
        headers={
            "accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "code": code,
            "client_id": api_key,
            "client_secret": api_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise ValueError(f"Token exchange failed: {resp.text}")
    return resp.json()["access_token"]


def load_access_token(env_path: str = ".env") -> str:
    """Load the Upstox access token.

    Checks in order:
      1. Streamlit session state (set by OAuth flow)
      2. Persistent token file (survives refreshes, saved today)
      3. .env file
      4. Streamlit secrets

    Once found, saves to persistent file so you don't need to login again today.
    Raises ValueError if the token is missing or is the placeholder default.
    """
    # 1. Check Streamlit session state first (set by OAuth flow)
    try:
        import streamlit as st
        token = st.session_state.get("upstox_access_token", "")
        if token:
            _save_token_to_file(token)
            return token
    except Exception:
        pass

    # 2. Check persistent token file (saved today)
    token = _load_token_from_file()
    if token:
        # Also restore to session state so other pages pick it up
        try:
            import streamlit as st
            st.session_state["upstox_access_token"] = token
        except Exception:
            pass
        return token

    # 3. Check .env file
    load_dotenv(env_path)
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")

    # 4. Fallback: try Streamlit secrets (used on Streamlit Community Cloud)
    if not token or token == "your_daily_access_token_here":
        try:
            import streamlit as st
            token = st.secrets.get("UPSTOX_ACCESS_TOKEN", "")
        except Exception:
            pass

    if not token or token == "your_daily_access_token_here":
        raise ValueError(
            "UPSTOX_ACCESS_TOKEN not set. "
            "Login with Upstox or update your .env / Streamlit secrets."
        )

    # Save to persistent file so we don't ask again today
    _save_token_to_file(token)
    return token


def validate_token(access_token: str) -> bool:
    """Validate the token by calling a lightweight Upstox endpoint.

    Returns True if valid, False otherwise.
    """
    try:
        resp = requests.get(
            f"{UPSTOX_BASE_URL}/user/profile",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False


def get_auth_headers(access_token: str) -> dict:
    """Return the authorization headers for Upstox API calls."""
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
