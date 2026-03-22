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

from src.data.models import GammaBlast, GEXProfile

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
# Prepare Alert — early warning when price approaches blast zone with momentum
# ---------------------------------------------------------------------------

class PrepareAlertTracker:
    """Detects when price approaches key gamma levels with consecutive momentum candles.

    Sends a Telegram "PREPARE" alert when:
    1. Price enters a zone near a key level (gamma wall, gamma flip, max gamma pin)
    2. There are 2-3 consecutive same-direction price moves (candles) confirming momentum
    3. Direction suggests whether to prepare for CALL or PUT

    This gives the trader a heads-up ~5 minutes BEFORE a full blast signal fires.

    Real-world observation: blasts don't happen at exact levels — they happen in
    zones (e.g., 24500-24560), and 2-3 consecutive candles confirm the move is real.
    """

    def __init__(
        self,
        zone_pct: float = 0.004,
        min_candles: int = 2,
        cooldown_minutes: int = 10,
        max_alerts_per_day: int = 8,
    ):
        self._zone_pct = zone_pct
        self._min_candles = min_candles
        self._cooldown_minutes = cooldown_minutes
        self._max_alerts_per_day = max_alerts_per_day
        self._price_readings: list[dict] = []
        self._alerts_today: int = 0
        self._last_alert_time: datetime | None = None
        self._last_alert_zone: str | None = None  # e.g., "call_wall_24500"
        self._last_reset_date: str | None = None

    def update(
        self,
        spot_price: float,
        profile: GEXProfile,
        instrument: str,
        timestamp: datetime | None = None,
    ) -> str | None:
        """Feed a new price reading. Returns Telegram alert message if prepare alert triggered.

        Args:
            spot_price: Current spot price
            profile: Current GEX profile with walls, gamma flip, etc.
            instrument: "NIFTY" or "SENSEX"
            timestamp: Current timestamp (defaults to now)

        Returns:
            Formatted Telegram message if alert triggered, None otherwise.
        """
        if timestamp is None:
            from src.utils.ist import now_ist
            timestamp = now_ist()

        # Reset daily counters
        today_str = timestamp.strftime("%Y-%m-%d")
        if self._last_reset_date != today_str:
            self._alerts_today = 0
            self._last_alert_time = None
            self._last_alert_zone = None
            self._last_reset_date = today_str

        # Daily cap
        if self._alerts_today >= self._max_alerts_per_day:
            return None

        # Record price reading
        self._price_readings.append({
            "price": spot_price,
            "time": timestamp,
        })
        if len(self._price_readings) > 30:
            self._price_readings = self._price_readings[-30:]

        # Need at least min_candles + 1 readings to detect consecutive moves
        if len(self._price_readings) < self._min_candles + 1:
            return None

        # Detect consecutive same-direction candles
        recent = self._price_readings[-(self._min_candles + 1):]
        moves = []
        for i in range(1, len(recent)):
            diff = recent[i]["price"] - recent[i - 1]["price"]
            if diff > 0:
                moves.append("green")
            elif diff < 0:
                moves.append("red")
            else:
                moves.append("flat")

        # Check if last N candles are all the same color
        candle_colors = moves[-self._min_candles:]
        if not candle_colors:
            return None

        all_green = all(c == "green" for c in candle_colors)
        all_red = all(c == "red" for c in candle_colors)

        if not all_green and not all_red:
            return None  # No consecutive momentum

        momentum_direction = "bullish" if all_green else "bearish"

        # Build key levels to check
        key_levels = self._get_key_levels(profile)

        # Check if price is in zone of any key level
        for level_name, level_price, level_type in key_levels:
            if level_price is None or level_price <= 0:
                continue

            distance_pct = abs(spot_price - level_price) / spot_price

            if distance_pct > self._zone_pct:
                continue  # Not in zone

            # Price is in zone of this level with consecutive candles
            zone_id = f"{level_name}_{level_price:.0f}"

            # Cooldown check (same zone)
            if self._last_alert_time is not None:
                elapsed = (timestamp - self._last_alert_time).total_seconds() / 60
                if elapsed < self._cooldown_minutes and self._last_alert_zone == zone_id:
                    continue  # Same zone, still in cooldown

            # Determine trade suggestion
            trade_action = self._suggest_trade(
                momentum_direction, level_type, spot_price, level_price,
            )

            if trade_action is None:
                continue  # No clear trade suggestion for this setup

            # Calculate total move across the consecutive candles
            start_price = recent[-(self._min_candles + 1)]["price"]
            total_move = spot_price - start_price
            total_move_pct = total_move / start_price

            # Fire alert
            self._alerts_today += 1
            self._last_alert_time = timestamp
            self._last_alert_zone = zone_id

            return self._format_prepare_alert(
                instrument=instrument,
                trade_action=trade_action,
                momentum_direction=momentum_direction,
                spot_price=spot_price,
                level_name=level_name,
                level_price=level_price,
                distance_pct=distance_pct,
                candle_count=len(candle_colors),
                total_move=total_move,
                total_move_pct=total_move_pct,
                timestamp=timestamp,
            )

        return None

    @staticmethod
    def _get_key_levels(profile: GEXProfile) -> list[tuple[str, float | None, str]]:
        """Extract key levels from GEX profile.

        Returns list of (name, price, type) where type is 'resistance' or 'support'.
        """
        levels = []

        if profile.call_wall is not None:
            levels.append(("Call Wall", profile.call_wall, "resistance"))
        if profile.put_wall is not None:
            levels.append(("Put Wall", profile.put_wall, "support"))
        if profile.gamma_flip_level is not None:
            levels.append(("Gamma Flip", profile.gamma_flip_level, "pivot"))
        if profile.max_gamma_strike is not None:
            levels.append(("Max Gamma Pin", profile.max_gamma_strike, "pin"))

        # Zero GEX levels are transition zones
        for i, zgl in enumerate(profile.zero_gex_levels or []):
            levels.append((f"Zero GEX #{i+1}", zgl, "pivot"))

        return levels

    @staticmethod
    def _suggest_trade(
        momentum_dir: str,
        level_type: str,
        spot: float,
        level_price: float,
    ) -> str | None:
        """Suggest CALL or PUT based on momentum + level type.

        Returns "CALL", "PUT", or None if no clear trade.
        """
        approaching_from_below = spot < level_price
        approaching_from_above = spot > level_price

        if level_type == "resistance":
            # Near resistance (call wall)
            if momentum_dir == "bullish" and approaching_from_below:
                # Approaching resistance with bullish momentum → might break above
                return "CALL"
            if momentum_dir == "bearish" and not approaching_from_below:
                # Rejected from resistance, falling → PUT
                return "PUT"

        elif level_type == "support":
            # Near support (put wall)
            if momentum_dir == "bearish" and approaching_from_above:
                # Approaching support with bearish momentum → might break below
                return "PUT"
            if momentum_dir == "bullish" and not approaching_from_above:
                # Bouncing from support → CALL
                return "CALL"

        elif level_type in ("pivot", "pin"):
            # Near gamma flip or max gamma pin — direction of momentum matters
            if momentum_dir == "bullish":
                return "CALL"
            elif momentum_dir == "bearish":
                return "PUT"

        return None

    @staticmethod
    def _format_prepare_alert(
        instrument: str,
        trade_action: str,
        momentum_direction: str,
        spot_price: float,
        level_name: str,
        level_price: float,
        distance_pct: float,
        candle_count: int,
        total_move: float,
        total_move_pct: float,
        timestamp: datetime,
    ) -> str:
        """Format the prepare alert message for Telegram."""
        if trade_action == "CALL":
            icon = "\u26a1\U0001F7E2"  # ⚡🟢
            action_text = "PREPARE — BUY CALL"
        else:
            icon = "\u26a1\U0001F534"  # ⚡🔴
            action_text = "PREPARE — BUY PUT"

        candle_emoji = "\U0001F7E9" * candle_count if momentum_direction == "bullish" else "\U0001F7E5" * candle_count

        # Zone description
        if spot_price > level_price:
            pos_text = f"{abs(spot_price - level_price):,.0f} pts ABOVE"
        else:
            pos_text = f"{abs(level_price - spot_price):,.0f} pts BELOW"

        msg = (
            f"{icon} <b>{action_text} — {instrument}</b>\n"
            f"\n"
            f"<b>Signal:</b> {candle_count} consecutive {'green' if momentum_direction == 'bullish' else 'red'} "
            f"candles near {level_name}\n"
            f"{candle_emoji} Move: {total_move:+,.0f} pts ({total_move_pct:+.2%})\n"
            f"\n"
            f"<b>Zone:</b>\n"
            f"  Spot  : {spot_price:,.2f}\n"
            f"  {level_name}: {level_price:,.0f} ({pos_text})\n"
            f"  Distance: {distance_pct:.2%}\n"
            f"\n"
            f"<i>Next 5 min trade setup building. Watch for blast confirmation.</i>\n"
            f"<i>{timestamp:%H:%M IST}</i>"
        )
        return msg


def send_prepare_alert(message: str) -> bool:
    """Send a prepare alert to Telegram."""
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
    best = stats.get("best_trade_pct", 0)
    worst = stats.get("worst_trade_pct", 0)
    profit_factor = stats.get("profit_factor", 0)
    win_streak = stats.get("max_consecutive_wins", 0)

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
