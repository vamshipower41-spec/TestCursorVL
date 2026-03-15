"""Telegram alert system for Gamma Blast signals and directional moves.

Sends formatted Telegram messages when:
1. A Gamma Blast signal fires (entry/SL/target with model breakdown)
2. Market shows sustained directional bias (bullish or bearish, not consolidation)

Setup:
    1. Message @BotFather on Telegram → /newbot → get your BOT_TOKEN
    2. Message your bot, then visit:
       https://api.telegram.org/bot<TOKEN>/getUpdates
       to find your CHAT_ID
    3. Set in .env or Streamlit Secrets:
       TELEGRAM_BOT_TOKEN=<your_token>
       TELEGRAM_CHAT_ID=<your_chat_id>
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

import requests

from src.data.models import GammaBlast

logger = logging.getLogger(__name__)

_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"


def _get_credentials() -> tuple[str, str] | None:
    """Load Telegram credentials from env or Streamlit secrets."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        # Try Streamlit secrets
        try:
            import streamlit as st
            token = token or st.secrets.get("TELEGRAM_BOT_TOKEN", "")
            chat_id = chat_id or st.secrets.get("TELEGRAM_CHAT_ID", "")
        except Exception:
            pass

    if token and chat_id:
        return token, chat_id
    return None


def send_telegram(message: str) -> bool:
    """Send a message via Telegram Bot API.

    Returns True if sent successfully, False otherwise.
    """
    creds = _get_credentials()
    if creds is None:
        logger.warning("Telegram credentials not configured — skipping alert.")
        return False

    token, chat_id = creds
    try:
        resp = requests.post(
            _SEND_URL.format(token=token),
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return True
        logger.error("Telegram API error %s: %s", resp.status_code, resp.text)
        return False
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Gamma Blast alert
# ---------------------------------------------------------------------------

def format_blast_alert(blast: GammaBlast) -> str:
    """Format a GammaBlast signal into a Telegram-friendly message."""
    arrow = "\U0001F7E2" if blast.direction == "bullish" else "\U0001F534"  # 🟢 / 🔴
    dir_text = blast.direction.upper()

    risk_pts = abs(blast.entry_level - blast.stop_loss)
    reward_pts = abs(blast.target - blast.entry_level)
    rr = reward_pts / risk_pts if risk_pts > 0 else 0

    # Model breakdown (top contributors)
    top_models = sorted(blast.components, key=lambda c: c.score * c.weight, reverse=True)[:3]
    model_lines = "\n".join(
        f"  • {c.model_name.replace('_', ' ').title()}: {c.score:.0f}/100 (wt {c.weight:.0%})"
        for c in top_models
    )

    raw = blast.metadata.get("raw_score", "?")
    filtered = blast.metadata.get("filtered_score", blast.composite_score)

    msg = (
        f"{arrow} <b>GAMMA BLAST — {blast.instrument} {dir_text}</b>\n"
        f"\n"
        f"Score: <b>{blast.composite_score:.0f}/100</b> (raw {raw}, filtered {filtered})\n"
        f"Expiry in: {blast.time_to_expiry_hours:.1f}h\n"
        f"\n"
        f"<b>Levels:</b>\n"
        f"  Entry : {blast.entry_level:,.2f}\n"
        f"  SL    : {blast.stop_loss:,.2f} ({risk_pts:,.0f} pts)\n"
        f"  Target: {blast.target:,.2f} ({reward_pts:,.0f} pts)\n"
        f"  R:R   : 1:{rr:.1f}\n"
        f"\n"
        f"<b>Top Models:</b>\n"
        f"{model_lines}\n"
        f"\n"
        f"<i>{blast.timestamp:%H:%M IST}</i>"
    )
    return msg


def send_blast_alert(blast: GammaBlast) -> bool:
    """Format and send a Gamma Blast alert to Telegram."""
    msg = format_blast_alert(blast)
    return send_telegram(msg)


# ---------------------------------------------------------------------------
# Directional trend alert (sustained bullish/bearish, no consolidation)
# ---------------------------------------------------------------------------

class DirectionalTracker:
    """Tracks sustained directional moves and fires alerts.

    Only alerts when there's a clear, sustained directional bias —
    NOT during consolidation or volatile chop.

    Logic:
    - Maintains a rolling window of trend readings
    - Requires N consecutive readings in the same direction
    - Requires minimum price movement (not just EMA crossover noise)
    - Cooldown prevents repeated alerts for the same move
    """

    def __init__(
        self,
        min_consecutive: int = 3,
        min_move_pct: float = 0.003,
        cooldown_minutes: int = 30,
    ):
        self._min_consecutive = min_consecutive
        self._min_move_pct = min_move_pct
        self._cooldown_minutes = cooldown_minutes
        self._history: list[dict] = []
        self._last_alert_time: datetime | None = None
        self._last_alert_direction: str | None = None

    def update(
        self,
        trend_data: dict,
        spot_price: float,
        instrument: str,
        timestamp: datetime | None = None,
    ) -> str | None:
        """Feed a new trend reading. Returns alert message if directional move detected.

        Args:
            trend_data: Output from compute_trend_bias() with keys:
                        trend ("bullish"/"bearish"/"neutral"), strength (0-1)
            spot_price: Current spot price
            instrument: "NIFTY" or "SENSEX"
            timestamp: Current timestamp (defaults to now)

        Returns:
            Formatted Telegram message string if alert triggered, None otherwise.
        """
        if timestamp is None:
            from src.utils.ist import now_ist
            timestamp = now_ist()

        trend = trend_data.get("trend", "neutral")
        strength = trend_data.get("strength", 0.0)

        self._history.append({
            "trend": trend,
            "strength": strength,
            "spot": spot_price,
            "time": timestamp,
        })

        # Keep only recent readings
        if len(self._history) > 20:
            self._history = self._history[-20:]

        # Skip neutral / weak signals
        if trend == "neutral" or strength < 0.2:
            return None

        # Check cooldown
        if self._last_alert_time is not None:
            elapsed = (timestamp - self._last_alert_time).total_seconds() / 60
            if elapsed < self._cooldown_minutes and self._last_alert_direction == trend:
                return None

        # Check for N consecutive same-direction readings
        recent = self._history[-self._min_consecutive:]
        if len(recent) < self._min_consecutive:
            return None

        directions = [r["trend"] for r in recent]
        if not all(d == trend for d in directions):
            return None  # Not all readings agree — could be choppy

        # Check minimum price movement across the window
        first_price = recent[0]["spot"]
        move_pct = (spot_price - first_price) / first_price
        if trend == "bullish" and move_pct < self._min_move_pct:
            return None  # Not enough upside movement
        if trend == "bearish" and move_pct > -self._min_move_pct:
            return None  # Not enough downside movement

        # Check it's not volatile chop (price should be moving steadily, not zigzagging)
        if len(self._history) >= 5:
            last5 = [r["spot"] for r in self._history[-5:]]
            reversals = sum(
                1 for i in range(1, len(last5) - 1)
                if (last5[i] > last5[i-1]) != (last5[i+1] > last5[i])
            )
            if reversals >= 3:
                return None  # Too choppy — volatile, not directional

        # Average strength across the window
        avg_strength = sum(r["strength"] for r in recent) / len(recent)

        # Fire alert
        self._last_alert_time = timestamp
        self._last_alert_direction = trend

        return self._format_directional_alert(
            instrument, trend, avg_strength, spot_price,
            first_price, move_pct, timestamp,
        )

    @staticmethod
    def _format_directional_alert(
        instrument: str,
        direction: str,
        strength: float,
        current_price: float,
        start_price: float,
        move_pct: float,
        timestamp: datetime,
    ) -> str:
        if direction == "bullish":
            arrow = "\U0001F4C8"  # 📈
            label = "BULLISH"
        else:
            arrow = "\U0001F4C9"  # 📉
            label = "BEARISH"

        strength_bar = "\u2588" * int(strength * 10) + "\u2591" * (10 - int(strength * 10))

        msg = (
            f"{arrow} <b>{instrument} — {label} MOVE</b>\n"
            f"\n"
            f"Direction: <b>{label}</b>\n"
            f"Strength : [{strength_bar}] {strength:.0%}\n"
            f"Spot     : {current_price:,.2f}\n"
            f"Move     : {move_pct:+.2%} (from {start_price:,.2f})\n"
            f"\n"
            f"<i>Sustained directional bias detected — not consolidation.</i>\n"
            f"<i>{timestamp:%H:%M IST}</i>"
        )
        return msg


def send_directional_alert(message: str) -> bool:
    """Send a directional trend alert to Telegram."""
    return send_telegram(message)
