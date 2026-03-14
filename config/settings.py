"""Central configuration for the GEX Signal Prediction System."""

# Options chain polling interval (seconds)
CHAIN_POLL_INTERVAL = 180  # 3 minutes

# Dashboard auto-refresh interval (seconds)
DASHBOARD_REFRESH_INTERVAL = 30

# Signal thresholds
PIN_RISK_PROXIMITY_PCT = 0.005  # Spot within 0.5% of max gamma strike
PIN_RISK_MAX_HOURS_TO_EXPIRY = 4.0
BREAKOUT_MIN_MOVE_PCT = 0.01  # 1% move past wall for confirmation
ZERO_GEX_PROXIMITY_PCT = 0.003  # Spot within 0.3% of zero GEX level
VOL_CRUSH_WINDOW_MINUTES = 15

# GEX profile
GEX_TOP_N_WALLS = 5  # Number of top call/put walls to track

# Market hours (IST)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30

# Upstox API base URL
UPSTOX_BASE_URL = "https://api.upstox.com/v2"
