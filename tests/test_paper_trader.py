"""Tests for paper trading signal logger."""

import sys

sys.path.insert(0, ".")

import pytest
from datetime import datetime

from src.backtest.paper_trader import PaperTrader, PaperTrade
from src.data.models import GammaBlast, BlastComponent


def _make_blast(direction="bullish", entry=22000, sl=21900, target=22200, score=75):
    return GammaBlast(
        timestamp=datetime(2026, 3, 24, 13, 30),
        instrument="NIFTY",
        composite_score=score,
        direction=direction,
        entry_level=entry,
        stop_loss=sl,
        target=target,
        time_to_expiry_hours=2.0,
        components=[
            BlastComponent(model_name="test", score=70, weight=0.25, detail="test"),
        ],
        metadata={"raw_score": 72, "filtered_score": 75, "vix_value": 15.0,
                  "confluence": {"firing_models": 3}},
    )


class TestPaperTrader:
    def test_open_trade(self, tmp_path):
        trader = PaperTrader(log_dir=str(tmp_path))
        blast = _make_blast()
        trade = trader.open_trade(blast)
        assert trade.outcome == "open"
        assert trade.entry_price == 22000
        assert trade.direction == "bullish"
        assert trade.trade_id in trader.active_trades

    def test_target_hit_bullish(self, tmp_path):
        trader = PaperTrader(log_dir=str(tmp_path))
        blast = _make_blast(direction="bullish", entry=22000, sl=21900, target=22200)
        trader.open_trade(blast)

        # Price reaches target
        closed = trader.update_price(22200)
        assert len(closed) == 1
        assert closed[0].outcome == "target_hit"
        assert closed[0].pnl_points > 0

    def test_sl_hit_bullish(self, tmp_path):
        trader = PaperTrader(log_dir=str(tmp_path))
        blast = _make_blast(direction="bullish", entry=22000, sl=21900, target=22200)
        trader.open_trade(blast)

        closed = trader.update_price(21900)
        assert len(closed) == 1
        assert closed[0].outcome == "sl_hit"
        assert closed[0].pnl_points < 0

    def test_target_hit_bearish(self, tmp_path):
        trader = PaperTrader(log_dir=str(tmp_path))
        blast = _make_blast(direction="bearish", entry=22000, sl=22100, target=21800)
        trader.open_trade(blast)

        closed = trader.update_price(21800)
        assert len(closed) == 1
        assert closed[0].outcome == "target_hit"
        assert closed[0].pnl_points > 0

    def test_no_close_between_sl_and_target(self, tmp_path):
        trader = PaperTrader(log_dir=str(tmp_path))
        blast = _make_blast(direction="bullish", entry=22000, sl=21900, target=22200)
        trader.open_trade(blast)

        closed = trader.update_price(22050)
        assert len(closed) == 0
        assert len(trader.active_trades) == 1

    def test_expire_open_trades(self, tmp_path):
        trader = PaperTrader(log_dir=str(tmp_path))
        blast = _make_blast()
        trader.open_trade(blast)

        expired = trader.expire_open_trades(spot_price=22050)
        assert len(expired) == 1
        assert expired[0].outcome == "expired"

    def test_statistics_empty(self, tmp_path):
        trader = PaperTrader(log_dir=str(tmp_path))
        stats = trader.get_statistics()
        assert stats["total_trades"] == 0
        assert stats["hit_rate"] == 0.0

    def test_max_favorable_tracked(self, tmp_path):
        trader = PaperTrader(log_dir=str(tmp_path))
        blast = _make_blast(direction="bullish", entry=22000, sl=21900, target=22200)
        trader.open_trade(blast)

        trader.update_price(22100)  # Favorable
        trader.update_price(22150)  # More favorable
        closed = trader.update_price(21900)  # Hit SL

        assert closed[0].max_favorable == 22150
