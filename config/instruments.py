"""Instrument configuration for Nifty 50 and Sensex."""

INSTRUMENTS = {
    "NIFTY": {
        "instrument_key": "NSE_INDEX|Nifty 50",
        "option_prefix": "NSE_FO",
        "contract_multiplier": 65,
        "tick_size": 0.05,
        "exchange": "NSE",
        "weekly_expiry_day": "Tuesday",  # or previous trading day if holiday
    },
    "SENSEX": {
        "instrument_key": "BSE_INDEX|SENSEX",
        "option_prefix": "BSE_FO",
        "contract_multiplier": 20,
        "tick_size": 0.05,
        "exchange": "BSE",
        "weekly_expiry_day": "Thursday",
    },
}


def get_instrument(name: str) -> dict:
    """Get instrument config by name (case-insensitive)."""
    key = name.upper()
    if key not in INSTRUMENTS:
        raise ValueError(f"Unknown instrument: {name}. Available: {list(INSTRUMENTS.keys())}")
    return INSTRUMENTS[key]
