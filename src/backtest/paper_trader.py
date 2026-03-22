"""Paper Trading & Signal Logger — track every blast signal's real outcome.

Without validated track records, you're flying blind. This module:
  1. Logs every blast signal with entry/SL/target
  2. Tracks spot price after signal to determine if target or SL was hit
  3. Computes running hit rate, avg R:R, win/loss streaks
  4. Persists all data to disk (JSON lines) for analysis

After 50+ signals across 10+ expiry days, you have real statistics.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from pathlib import Path

from src.data.models import GammaBlast
from src.utils.ist import now_ist


@dataclass
class PaperTrade:
    """A single paper trade from a blast signal."""
    trade_id: str
    instrument: str
    direction: str          # "bullish" or "bearish"
    entry_price: float
    stop_loss: float
    target: float
    composite_score: float
    entry_time: str         # ISO format
    expiry_date: str

    # Outcome tracking (filled in later)
    outcome: str = "open"   # "open", "target_hit", "sl_hit", "expired", "partial"
    exit_price: float = 0.0
    exit_time: str = ""
    max_favorable: float = 0.0  # best price in trade direction
    max_adverse: float = 0.0    # worst price against trade
    pnl_points: float = 0.0
    pnl_pct: float = 0.0
    duration_minutes: float = 0.0
    price_path: list[float] = field(default_factory=list)

    # Signal metadata
    raw_score: float = 0.0
    filtered_score: float = 0.0
    vix_at_entry: float = 0.0
    models_firing: int = 0


class PaperTrader:
    """Paper trading engine that tracks blast signal outcomes."""

    def __init__(self, log_dir: str = "data/paper_trades"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.active_trades: dict[str, PaperTrade] = {}
        self._io_lock = threading.Lock()
        self._load_active_trades()

    def open_trade(self, blast: GammaBlast) -> PaperTrade:
        """Open a paper trade from a blast signal."""
        trade_id = f"{blast.instrument}_{blast.timestamp.strftime('%Y%m%d_%H%M%S')}"

        trade = PaperTrade(
            trade_id=trade_id,
            instrument=blast.instrument,
            direction=blast.direction,
            entry_price=blast.entry_level,
            stop_loss=blast.stop_loss,
            target=blast.target,
            composite_score=blast.composite_score,
            entry_time=blast.timestamp.isoformat(),
            expiry_date=blast.metadata.get("expiry_date", ""),
            raw_score=blast.metadata.get("raw_score", 0),
            filtered_score=blast.metadata.get("filtered_score", 0),
            vix_at_entry=blast.metadata.get("vix_value", 0) or 0,
            models_firing=blast.metadata.get("confluence", {}).get("firing_models", 0),
        )

        self.active_trades[trade_id] = trade
        self._append_log(trade, "OPEN")
        return trade

    def update_price(self, spot_price: float, timestamp: datetime | None = None) -> list[PaperTrade]:
        """Update all active trades with a new spot price.

        Returns list of trades that just closed (hit target or SL).
        """
        ts = timestamp or now_ist()
        closed = []

        for trade_id, trade in list(self.active_trades.items()):
            trade.price_path.append(spot_price)

            # Track max favorable / adverse
            if trade.direction == "bullish":
                trade.max_favorable = max(trade.max_favorable, spot_price)
                trade.max_adverse = min(trade.max_adverse or spot_price, spot_price)

                if spot_price >= trade.target:
                    self._close_trade(trade, "target_hit", spot_price, ts)
                    closed.append(trade)
                elif spot_price <= trade.stop_loss:
                    self._close_trade(trade, "sl_hit", spot_price, ts)
                    closed.append(trade)
            else:  # bearish
                trade.max_favorable = min(trade.max_favorable or spot_price, spot_price)
                trade.max_adverse = max(trade.max_adverse, spot_price)

                if spot_price <= trade.target:
                    self._close_trade(trade, "target_hit", spot_price, ts)
                    closed.append(trade)
                elif spot_price >= trade.stop_loss:
                    self._close_trade(trade, "sl_hit", spot_price, ts)
                    closed.append(trade)

        # Remove closed trades from active
        for trade in closed:
            self.active_trades.pop(trade.trade_id, None)

        return closed

    def expire_open_trades(self, timestamp: datetime | None = None, spot_price: float = 0) -> list[PaperTrade]:
        """Close all remaining open trades at expiry (3:30 PM IST)."""
        ts = timestamp or now_ist()
        expired = []
        for trade_id, trade in list(self.active_trades.items()):
            exit_price = spot_price or (trade.price_path[-1] if trade.price_path else trade.entry_price)
            self._close_trade(trade, "expired", exit_price, ts)
            expired.append(trade)
            self.active_trades.pop(trade_id, None)
        return expired

    def get_statistics(self) -> dict:
        """Compute aggregate statistics from all logged trades."""
        trades = self._load_all_closed_trades()

        if not trades:
            return {
                "total_trades": 0, "wins": 0, "losses": 0,
                "hit_rate": 0.0, "avg_pnl_pct": 0.0,
                "avg_winner_pct": 0.0, "avg_loser_pct": 0.0,
                "best_trade_pct": 0.0, "worst_trade_pct": 0.0,
                "avg_duration_min": 0.0, "profit_factor": 0.0,
                "max_consecutive_wins": 0, "max_consecutive_losses": 0,
            }

        wins = [t for t in trades if t["outcome"] == "target_hit"]
        losses = [t for t in trades if t["outcome"] == "sl_hit"]
        all_pnl = [t["pnl_pct"] for t in trades if t["outcome"] in ("target_hit", "sl_hit", "expired")]

        total_wins_pnl = sum(t["pnl_pct"] for t in wins) if wins else 0
        total_losses_pnl = abs(sum(t["pnl_pct"] for t in losses)) if losses else 0

        # Consecutive streaks
        outcomes = [t["outcome"] for t in trades if t["outcome"] in ("target_hit", "sl_hit")]
        max_wins = max_losses = curr_wins = curr_losses = 0
        for o in outcomes:
            if o == "target_hit":
                curr_wins += 1
                curr_losses = 0
                max_wins = max(max_wins, curr_wins)
            else:
                curr_losses += 1
                curr_wins = 0
                max_losses = max(max_losses, curr_losses)

        return {
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "hit_rate": len(wins) / max(len(wins) + len(losses), 1),
            "avg_pnl_pct": sum(all_pnl) / max(len(all_pnl), 1),
            "avg_winner_pct": total_wins_pnl / max(len(wins), 1),
            "avg_loser_pct": -total_losses_pnl / max(len(losses), 1),
            "best_trade_pct": max(all_pnl) if all_pnl else 0,
            "worst_trade_pct": min(all_pnl) if all_pnl else 0,
            "avg_duration_min": sum(t.get("duration_minutes", 0) for t in trades) / max(len(trades), 1),
            "profit_factor": total_wins_pnl / max(total_losses_pnl, 0.001),
            "max_consecutive_wins": max_wins,
            "max_consecutive_losses": max_losses,
        }

    def _close_trade(self, trade: PaperTrade, outcome: str, exit_price: float, ts: datetime):
        trade.outcome = outcome
        trade.exit_price = exit_price
        trade.exit_time = ts.isoformat()

        entry_ts = datetime.fromisoformat(trade.entry_time)
        # Handle naive/aware mismatch
        if ts.tzinfo is not None and entry_ts.tzinfo is None:
            entry_ts = entry_ts.replace(tzinfo=ts.tzinfo)
        elif ts.tzinfo is None and entry_ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        trade.duration_minutes = (ts - entry_ts).total_seconds() / 60.0

        if trade.direction == "bullish":
            trade.pnl_points = exit_price - trade.entry_price
        else:
            trade.pnl_points = trade.entry_price - exit_price

        trade.pnl_pct = (trade.pnl_points / trade.entry_price * 100) if trade.entry_price != 0 else 0.0

        self._append_log(trade, "CLOSE")

    def _append_log(self, trade: PaperTrade, event: str):
        """Append trade event to daily log file (JSON lines format)."""
        date_str = trade.entry_time[:10]
        log_file = self.log_dir / f"trades_{date_str}.jsonl"

        record = asdict(trade)
        record["_event"] = event
        record["_logged_at"] = now_ist().isoformat()
        # Don't persist full price path in log (too large)
        record["price_path_len"] = len(record.pop("price_path", []))

        with self._io_lock, open(log_file, "a") as f:
            f.write(json.dumps(record) + "\n")

    def _load_active_trades(self):
        """Reload any trades from today that are still open."""
        today = now_ist().strftime("%Y-%m-%d")
        log_file = self.log_dir / f"trades_{today}.jsonl"

        if not log_file.exists():
            return

        opens = {}
        closes = set()

        for line in log_file.read_text().strip().split("\n"):
            if not line:
                continue
            record = json.loads(line)
            tid = record["trade_id"]
            if record["_event"] == "OPEN":
                opens[tid] = record
            elif record["_event"] == "CLOSE":
                closes.add(tid)

        for tid, rec in opens.items():
            if tid not in closes:
                self.active_trades[tid] = PaperTrade(
                    trade_id=rec["trade_id"],
                    instrument=rec["instrument"],
                    direction=rec["direction"],
                    entry_price=rec["entry_price"],
                    stop_loss=rec["stop_loss"],
                    target=rec["target"],
                    composite_score=rec["composite_score"],
                    entry_time=rec["entry_time"],
                    expiry_date=rec.get("expiry_date", ""),
                    raw_score=rec.get("raw_score", 0),
                    filtered_score=rec.get("filtered_score", 0),
                    vix_at_entry=rec.get("vix_at_entry", 0),
                    models_firing=rec.get("models_firing", 0),
                )

    def _load_all_closed_trades(self) -> list[dict]:
        """Load all closed trades across all log files."""
        trades = []
        for log_file in sorted(self.log_dir.glob("trades_*.jsonl")):
            for line in log_file.read_text().strip().split("\n"):
                if not line:
                    continue
                record = json.loads(line)
                if record.get("_event") == "CLOSE":
                    trades.append(record)
        return trades
