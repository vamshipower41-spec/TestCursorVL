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


_last_send_error: str = ""  # Stores last error for dashboard feedback


def get_last_send_error() -> str:
    """Return the last Telegram send error message (for dashboard display)."""
    return _last_send_error


def validate_credentials() -> tuple[bool, str]:
    """Check if Telegram credentials are configured and valid.

    Returns (is_valid, message).
    """
    creds = _get_credentials()
    if creds is None:
        return False, "TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID not set in env or Streamlit secrets."
    token, chat_id = creds
    if not token.strip():
        return False, "TELEGRAM_BOT_TOKEN is empty."
    if not chat_id.strip():
        return False, "TELEGRAM_CHAT_ID is empty."
    return True, "Credentials configured."


def send_telegram(message: str, max_retries: int = 2) -> bool:
    """Send a message via Telegram Bot API with retry logic.

    Retries up to max_retries times on network failure.
    Returns True if sent successfully, False otherwise.
    """
    global _last_send_error

    creds = _get_credentials()
    if creds is None:
        _last_send_error = "Telegram credentials not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing)."
        logger.warning(_last_send_error)
        return False

    token, chat_id = creds

    for attempt in range(1, max_retries + 2):  # 1 initial + max_retries
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
                _last_send_error = ""
                return True
            _last_send_error = f"Telegram API error {resp.status_code}: {resp.text[:200]}"
            logger.error(_last_send_error)
            # Don't retry on auth errors (401, 403) or bad request (400)
            if resp.status_code in (400, 401, 403):
                return False
        except requests.exceptions.Timeout:
            _last_send_error = f"Telegram send timed out (attempt {attempt}/{max_retries + 1})"
            logger.warning(_last_send_error)
        except requests.exceptions.ConnectionError as exc:
            _last_send_error = f"Telegram connection error: {exc} (attempt {attempt}/{max_retries + 1})"
            logger.warning(_last_send_error)
        except Exception as exc:
            _last_send_error = f"Telegram send failed: {exc}"
            logger.error(_last_send_error)
            return False  # Unknown error, don't retry

        # Wait before retry (exponential backoff: 2s, 4s)
        if attempt <= max_retries:
            import time
            time.sleep(2 ** attempt)

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


# ---------------------------------------------------------------------------
# Paper trade outcome alerts
# ---------------------------------------------------------------------------

def format_paper_trade_outcome(trade) -> str:
    """Format a closed paper trade into a Telegram-friendly message."""
    if trade.outcome == "target_hit":
        icon = "\u2705"  # ✅
        label = "TARGET HIT"
    elif trade.outcome == "sl_hit":
        icon = "\u274c"  # ❌
        label = "STOP LOSS HIT"
    else:
        icon = "\u23f0"  # ⏰
        label = "EXPIRED"

    dir_arrow = "\U0001F7E2" if trade.direction == "bullish" else "\U0001F534"
    pnl_sign = "+" if trade.pnl_points >= 0 else ""

    msg = (
        f"{icon} <b>Paper Trade — {label}</b>\n"
        f"\n"
        f"{dir_arrow} {trade.instrument} {trade.direction.upper()}\n"
        f"Entry : {trade.entry_price:,.2f}\n"
        f"Exit  : {trade.exit_price:,.2f}\n"
        f"P&L   : <b>{pnl_sign}{trade.pnl_points:,.0f} pts ({pnl_sign}{trade.pnl_pct:.2f}%)</b>\n"
        f"Duration: {trade.duration_minutes:.0f} min\n"
        f"Score : {trade.composite_score:.0f}/100\n"
    )

    if trade.max_favorable:
        fav_pts = abs(trade.max_favorable - trade.entry_price)
        msg += f"Max Favorable: {fav_pts:,.0f} pts\n"

    return msg


def send_paper_trade_alert(trade) -> bool:
    """Send a paper trade outcome alert to Telegram."""
    msg = format_paper_trade_outcome(trade)
    return send_telegram(msg)


def format_daily_summary(stats: dict, instrument: str) -> str:
    """Format daily paper trading summary for Telegram."""
    total = stats.get("total_trades", 0)
    if total == 0:
        return f"\U0001F4CA <b>{instrument} — Daily Summary</b>\n\nNo trades today."

    hit_rate = stats.get("hit_rate", 0)
    avg_pnl = stats.get("avg_pnl_pct", 0)
    best = stats.get("best_trade_pnl", 0)
    worst = stats.get("worst_trade_pnl", 0)
    profit_factor = stats.get("profit_factor", 0)
    win_streak = stats.get("max_win_streak", 0)

    perf_icon = "\U0001F4C8" if avg_pnl > 0 else "\U0001F4C9"  # 📈 / 📉

    msg = (
        f"{perf_icon} <b>{instrument} — Daily Summary</b>\n"
        f"\n"
        f"Trades : {total}\n"
        f"Hit Rate: <b>{hit_rate:.0%}</b>\n"
        f"Avg P&L : {avg_pnl:+.2f}%\n"
        f"Best    : {best:+.2f}%\n"
        f"Worst   : {worst:+.2f}%\n"
        f"Profit Factor: {profit_factor:.2f}\n"
        f"Win Streak: {win_streak}\n"
    )
    return msg


def send_daily_summary(stats: dict, instrument: str) -> bool:
    """Send daily paper trading summary to Telegram."""
    msg = format_daily_summary(stats, instrument)
    return send_telegram(msg)
