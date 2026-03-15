"""Upstox authentication — OAuth login flow + token management."""

import os
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

from config.settings import UPSTOX_BASE_URL

UPSTOX_AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
UPSTOX_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"


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
    """Load the Upstox access token from .env file or Streamlit secrets.

    Checks in order: session state → .env file → Streamlit secrets.
    Raises ValueError if the token is missing or is the placeholder default.
    """
    # Check Streamlit session state first (set by OAuth flow)
    try:
        import streamlit as st
        token = st.session_state.get("upstox_access_token", "")
        if token:
            return token
    except Exception:
        pass

    load_dotenv(env_path)
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")

    # Fallback: try Streamlit secrets (used on Streamlit Community Cloud)
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
