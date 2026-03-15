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

# --- Gamma Blast Scalping Settings ---

# Minimum composite score (0-100) to fire a blast signal
# Higher = fewer but more accurate signals (scalper wants 1-2 per expiry)
BLAST_MIN_SCORE = 70

# Maximum blast signals per expiry day (strict cap for disciplined scalping)
BLAST_MAX_SIGNALS_PER_DAY = 2

# Charm acceleration zone: hours before expiry when charm flow intensifies
CHARM_ACCELERATION_HOURS = 3.0

# Negative gamma threshold: net GEX ratio below which regime is strongly negative
NEGATIVE_GAMMA_THRESHOLD = -0.3  # normalized ratio

# OI surge detection: minimum % increase in OI at a strike to flag buildup
OI_SURGE_PCT = 0.15  # 15% OI change between snapshots

# Pin break: minimum move (%) away from max gamma strike to confirm pin break
PIN_BREAK_MIN_MOVE_PCT = 0.003  # 0.3% move away from pin

# Gamma wall breach velocity: how fast price must move through wall (pts per minute)
WALL_BREACH_VELOCITY_MIN = 5.0

# Vanna squeeze: minimum IV drop (absolute points) to trigger
VANNA_IV_DROP_MIN = 1.5

# Blast signal cooldown: minimum minutes between blast signals
BLAST_COOLDOWN_MINUTES = 30

# Expiry day polling interval (faster than normal)
EXPIRY_DAY_POLL_INTERVAL = 60  # 1 minute on expiry day

# --- Telegram Alert Settings ---

# Enable/disable Telegram notifications
TELEGRAM_ENABLED = True

# Directional trend alerts: consecutive same-direction readings required
TREND_ALERT_MIN_CONSECUTIVE = 3

# Directional trend alerts: minimum price move (%) to confirm direction
TREND_ALERT_MIN_MOVE_PCT = 0.003  # 0.3%

# Directional trend alerts: cooldown between alerts (minutes)
TREND_ALERT_COOLDOWN_MINUTES = 30
