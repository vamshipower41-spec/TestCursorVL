"""Upstox authentication — loads daily access token from .env and validates it."""

import os

import requests
from dotenv import load_dotenv

from config.settings import UPSTOX_BASE_URL


def load_access_token(env_path: str = ".env") -> str:
    """Load the Upstox access token from .env file.

    Raises ValueError if the token is missing or is the placeholder default.
    """
    load_dotenv(env_path)
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    if not token or token == "your_daily_access_token_here":
        raise ValueError(
            "UPSTOX_ACCESS_TOKEN not set. "
            "Update your .env file with today's access token."
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
