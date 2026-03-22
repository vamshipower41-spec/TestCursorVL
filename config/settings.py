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
BLAST_MAX_SIGNALS_PER_DAY = 4  # expiry day — more opportunities to catch moves
BLAST_MAX_SIGNALS_NORMAL_DAY = 2  # non-expiry day — conservative

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

# Expiry day polling interval (faster than normal for scalping)
EXPIRY_DAY_POLL_INTERVAL = 15  # 15 seconds on expiry day — catches gamma blasts quickly

# --- Telegram Alert Settings ---

# Enable/disable Telegram notifications
TELEGRAM_ENABLED = True

# Directional trend alerts: consecutive same-direction readings required
TREND_ALERT_MIN_CONSECUTIVE = 3

# Directional trend alerts: minimum price move (%) to confirm direction
TREND_ALERT_MIN_MOVE_PCT = 0.003  # 0.3%

# Directional trend alerts: cooldown between alerts (minutes)
TREND_ALERT_COOLDOWN_MINUTES = 30

# --- Multi-Expiry GEX Settings ---
MULTI_EXPIRY_COUNT = 2  # Number of expiries to aggregate (nearest + next)
MULTI_EXPIRY_MIN_OI_SHARE = 0.05  # Min OI share to include an expiry (5%)

# --- OI Flow Settings ---
OI_FLOW_CONFIDENCE_THRESHOLD = 0.4  # Min confidence to use flow data in blast scoring
OI_FLOW_DOMINANCE_RATIO = 1.2  # Bull/bear ratio to classify dominant flow

# --- Pattern Matcher Settings ---
PATTERN_MATCH_MIN_SAMPLES = 5  # Min historical trades for confidence
PATTERN_MATCH_CONFIDENCE_GATE = 0.3  # Min confidence to adjust blast score

# --- WebSocket Trigger Settings ---
WS_TRIGGER_PROXIMITY_PCT = 0.002  # 0.2% from critical level triggers fetch
WS_MIN_TRIGGER_INTERVAL = 15  # Min seconds between triggered chain fetches (was 30)
WS_MAX_TRIGGER_INTERVAL = 60  # Max seconds between periodic fetches (was 120)
WS_VELOCITY_THRESHOLD = 5.0  # Points/sec for velocity-based trigger

# --- Prepare Alert Settings (early warning before blast) ---

# Enable/disable prepare alerts (Telegram heads-up before a trade)
PREPARE_ALERT_ENABLED = True

# Zone proximity: how close price must be to a key level (%) to trigger zone alert
PREPARE_ALERT_ZONE_PCT = 0.004  # 0.4% — e.g., within ~100 pts of a wall at NIFTY 24500

# Minimum blast warmup score to trigger prepare alert (models building, not yet fired)
PREPARE_ALERT_MIN_WARMUP_SCORE = 40  # Score 40-69 = warming up, 70+ = blast fires

# Cooldown between prepare alerts for the same zone (minutes)
PREPARE_ALERT_COOLDOWN_MINUTES = 10

# Maximum prepare alerts per day (avoid alert fatigue)
PREPARE_ALERT_MAX_PER_DAY = 8

# --- Paper Trade Settings ---
PAPER_TRADE_DIR = "data/paper_trades"
PAPER_TRADE_TELEGRAM_ALERTS = True  # Send outcome alerts to Telegram
PAPER_TRADE_DAILY_SUMMARY = True  # Send daily summary at market close
