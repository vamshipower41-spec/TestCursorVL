"""Real-time market data streaming via Upstox WebSocket (MarketDataStreamerV3).

Provides sub-second spot price updates for live expiry day tracking.
OI/gamma data is still polled via REST (updated at exchange intervals).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

import upstox_client
from upstox_client.rest import ApiException


@dataclass
class MarketTick:
    """A single market data tick."""
    instrument_key: str
    ltp: float
    volume: int = 0
    oi: int = 0
    timestamp: str = ""


class LiveMarketFeed:
    """WebSocket-based real-time market data feed using Upstox MarketDataStreamerV3."""

    def __init__(self, access_token: str, instrument_keys: list[str], mode: str = "full"):
        """
        Args:
            access_token: Valid Upstox daily access token
            instrument_keys: List of instrument keys to subscribe to
            mode: Feed mode - "full", "option_greeks", or "ltpc"
        """
        self.access_token = access_token
        self.instrument_keys = instrument_keys
        self.mode = mode
        self.streamer = None
        self._callbacks: list[Callable[[MarketTick], None]] = []
        self._running = False

    def on_tick(self, callback: Callable[[MarketTick], None]) -> None:
        """Register a callback to receive market ticks."""
        self._callbacks.append(callback)

    def connect(self) -> None:
        """Connect to the WebSocket feed and start streaming."""
        configuration = upstox_client.Configuration()
        configuration.access_token = self.access_token
        api_client = upstox_client.ApiClient(configuration)

        self.streamer = upstox_client.MarketDataStreamerV3(
            api_client,
            self.instrument_keys,
            self.mode,
        )

        self.streamer.auto_reconnect(enable=True, interval=10, retry_count=5)
        self.streamer.on("message", self._on_message)
        self.streamer.on("error", self._on_error)
        self.streamer.on("close", self._on_close)
        self.streamer.on("open", self._on_open)

        self._running = True
        self.streamer.connect()

    def _on_message(self, message: str) -> None:
        """Parse incoming message and dispatch to callbacks."""
        try:
            data = json.loads(message) if isinstance(message, str) else message
            feeds = data.get("feeds", {})

            for inst_key, feed_data in feeds.items():
                ff = feed_data.get("ff", feed_data.get("ltpc", {}))
                market = ff.get("marketFF", ff) if isinstance(ff, dict) else {}
                ltpc = market.get("ltpc", ff.get("ltpc", {}))

                tick = MarketTick(
                    instrument_key=inst_key,
                    ltp=ltpc.get("ltp", 0.0),
                    volume=market.get("v", 0),
                    oi=market.get("oi", 0),
                )

                for callback in self._callbacks:
                    callback(tick)

        except (json.JSONDecodeError, AttributeError, KeyError):
            pass  # Skip malformed messages

    def _on_error(self, error: Exception) -> None:
        """Handle WebSocket errors."""
        print(f"[WebSocket Error] {error}")

    def _on_close(self, *args) -> None:
        """Handle WebSocket close."""
        self._running = False
        print("[WebSocket] Connection closed.")

    def _on_open(self, *args) -> None:
        """Handle WebSocket open."""
        print(f"[WebSocket] Connected. Streaming {len(self.instrument_keys)} instruments.")

    def disconnect(self) -> None:
        """Disconnect the WebSocket feed."""
        self._running = False
        if self.streamer:
            self.streamer.disconnect()

    @property
    def is_connected(self) -> bool:
        return self._running

    def run_in_background(self) -> threading.Thread:
        """Run the WebSocket connection in a background thread."""
        thread = threading.Thread(target=self.connect, daemon=True)
        thread.start()
        return thread
