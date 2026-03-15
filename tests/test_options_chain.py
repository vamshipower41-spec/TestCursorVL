"""Tests for OptionsChainFetcher (mocked API responses)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data.options_chain import OptionsChainFetcher


@pytest.fixture
def fetcher():
    """Create a fetcher with a dummy token."""
    with patch("src.data.options_chain.get_auth_headers", return_value={"Authorization": "Bearer test"}):
        return OptionsChainFetcher("test_token")


class TestGetExpiryDates:
    def test_returns_sorted_expiries(self, fetcher):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {"expiry": "2026-03-26"},
                {"expiry": "2026-03-19"},
                {"expiry": "2026-03-26"},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        fetcher.session.get = MagicMock(return_value=mock_resp)

        expiries = fetcher.get_expiry_dates("NSE_INDEX|Nifty 50")
        assert expiries == ["2026-03-19", "2026-03-26"]

    def test_empty_data(self, fetcher):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": []}
        mock_resp.raise_for_status = MagicMock()
        fetcher.session.get = MagicMock(return_value=mock_resp)

        assert fetcher.get_expiry_dates("NSE_INDEX|Nifty 50") == []


class TestFetchChain:
    def test_parses_chain_data(self, fetcher):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {
                    "strike_price": 22000,
                    "underlying_spot_price": 22150.5,
                    "call_options": {
                        "market_data": {"oi": 50000, "ltp": 250.0, "volume": 10000},
                        "option_greeks": {"gamma": 0.0015, "delta": 0.55, "iv": 15.2},
                    },
                    "put_options": {
                        "market_data": {"oi": 40000, "ltp": 100.0, "volume": 8000},
                        "option_greeks": {"gamma": 0.0012, "delta": -0.45, "iv": 16.1},
                    },
                },
                {
                    "strike_price": 22100,
                    "underlying_spot_price": 22150.5,
                    "call_options": {
                        "market_data": {"oi": 30000, "ltp": 180.0, "volume": 7000},
                        "option_greeks": {"gamma": 0.0018, "delta": 0.48, "iv": 14.5},
                    },
                    "put_options": {
                        "market_data": {"oi": 35000, "ltp": 130.0, "volume": 6000},
                        "option_greeks": {"gamma": 0.0014, "delta": -0.52, "iv": 15.8},
                    },
                },
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        fetcher.session.get = MagicMock(return_value=mock_resp)

        chain_df, spot = fetcher.fetch_chain("NSE_INDEX|Nifty 50", "2026-03-19")

        assert spot == 22150.5
        assert len(chain_df) == 2
        assert "strike_price" in chain_df.columns
        assert "call_oi" in chain_df.columns
        assert "put_gamma" in chain_df.columns
        assert chain_df.iloc[0]["call_oi"] == 50000
        assert chain_df.iloc[1]["put_gamma"] == 0.0014

    def test_empty_chain(self, fetcher):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": []}
        mock_resp.raise_for_status = MagicMock()
        fetcher.session.get = MagicMock(return_value=mock_resp)

        chain_df, spot = fetcher.fetch_chain("NSE_INDEX|Nifty 50", "2026-03-19")
        assert chain_df.empty
        assert spot == 0.0


class TestGetNearestExpiry:
    def test_returns_first_expiry(self, fetcher):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {"expiry": "2026-03-26"},
                {"expiry": "2026-03-19"},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        fetcher.session.get = MagicMock(return_value=mock_resp)

        assert fetcher.get_nearest_expiry("NSE_INDEX|Nifty 50") == "2026-03-19"

    def test_raises_on_no_expiries(self, fetcher):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": []}
        mock_resp.raise_for_status = MagicMock()
        fetcher.session.get = MagicMock(return_value=mock_resp)

        with pytest.raises(ValueError, match="No expiry dates found"):
            fetcher.get_nearest_expiry("NSE_INDEX|Nifty 50")
