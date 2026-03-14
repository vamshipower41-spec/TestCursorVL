"""Pydantic data models for the GEX Signal Prediction System."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OptionStrikeData(BaseModel):
    """Per-strike options data from the chain."""

    strike_price: float
    call_oi: int = 0
    call_gamma: float = 0.0
    call_delta: float = 0.0
    call_iv: float = 0.0
    call_ltp: float = 0.0
    call_volume: int = 0
    put_oi: int = 0
    put_gamma: float = 0.0
    put_delta: float = 0.0
    put_iv: float = 0.0
    put_ltp: float = 0.0
    put_volume: int = 0


class GEXProfile(BaseModel):
    """Computed Gamma Exposure profile for an instrument at a point in time."""

    timestamp: datetime
    instrument: str  # "NIFTY" or "SENSEX"
    spot_price: float
    expiry_date: str
    contract_multiplier: int
    strikes: list[StrikeGEX]
    gamma_flip_level: float | None = None
    max_gamma_strike: float | None = None
    zero_gex_levels: list[float] = []
    call_wall: float | None = None  # Highest call GEX strike (resistance)
    put_wall: float | None = None  # Highest put GEX strike (support)
    net_gex_total: float = 0.0  # Sum of all net GEX; positive = stabilizing


class StrikeGEX(BaseModel):
    """GEX values for a single strike."""

    strike_price: float
    call_gex: float = 0.0
    put_gex: float = 0.0
    net_gex: float = 0.0


class GEXSignal(BaseModel):
    """A trading signal generated from GEX analysis."""

    timestamp: datetime
    instrument: str
    signal_type: str  # gamma_flip, pin_risk, breakout, vol_crush, zero_gex_instability
    level: float  # Price level associated with signal
    strength: float  # 0.0 to 1.0 normalized confidence
    direction: str | None = None  # bullish, bearish, or None
    metadata: dict = {}


# Rebuild GEXProfile now that StrikeGEX is defined
GEXProfile.model_rebuild()
