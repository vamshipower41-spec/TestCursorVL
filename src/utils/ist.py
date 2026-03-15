"""IST timezone utilities — single source of truth for all time operations.

All datetime operations in this project MUST use these helpers to ensure
consistent IST (India Standard Time, UTC+5:30) handling.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta, date

# IST = UTC + 5:30
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    """Return current datetime in IST (timezone-aware)."""
    return datetime.now(IST)


def today_ist() -> date:
    """Return today's date in IST."""
    return now_ist().date()


def make_ist(dt: datetime) -> datetime:
    """Attach IST timezone to a naive datetime, or convert aware datetime to IST."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def expiry_datetime(expiry_date_str: str) -> datetime:
    """Parse expiry date string and return 3:30 PM IST as timezone-aware datetime."""
    dt = datetime.strptime(expiry_date_str, "%Y-%m-%d")
    return dt.replace(hour=15, minute=30, tzinfo=IST)


def time_to_expiry_hours(expiry_date_str: str) -> float:
    """Compute hours from now (IST) until expiry (3:30 PM IST)."""
    expiry_dt = expiry_datetime(expiry_date_str)
    delta = expiry_dt - now_ist()
    return max(delta.total_seconds() / 3600.0, 0.0)


def is_market_open() -> bool:
    """Check if Indian market is currently open (9:15 AM - 3:30 PM IST)."""
    now = now_ist()
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


def market_minutes_elapsed() -> float:
    """Minutes elapsed since market open (9:15 AM IST). Negative if before open."""
    now = now_ist()
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    return (now - market_open).total_seconds() / 60.0


def is_charm_zone() -> bool:
    """Check if we're in the charm acceleration zone (post 1:30 PM IST).

    Charm (delta decay) accelerates dramatically in the last 2 hours of
    expiry day. The 1:30 PM threshold gives a buffer for early detection.
    """
    now = now_ist()
    charm_start = now.replace(hour=13, minute=30, second=0, microsecond=0)
    return now >= charm_start
