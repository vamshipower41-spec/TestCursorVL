# GEX Signal Prediction System — Gamma Blast Scalper

A real-time Gamma Exposure (GEX) based options trading signal system built with Streamlit and Upstox API. Designed for expiry-day scalping on NIFTY and SENSEX with Telegram alerts.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Setup & Installation](#setup--installation)
  - [1. Upstox API Credentials](#1-upstox-api-credentials)
  - [2. Streamlit Cloud Deployment](#2-streamlit-cloud-deployment)
  - [3. Telegram Alerts Setup](#3-telegram-alerts-setup)
  - [4. Streamlit Secrets Configuration](#4-streamlit-secrets-configuration)
- [Daily Usage Procedure](#daily-usage-procedure)
  - [Morning Routine](#morning-routine)
  - [During Market Hours](#during-market-hours)
  - [Key Timing Windows](#key-timing-windows)
  - [iPad Usage Notes](#ipad-usage-notes)
- [Dashboard Pages](#dashboard-pages)
  - [Gamma Blast Scalper](#gamma-blast-scalper-main-page)
  - [Live GEX Monitor](#live-gex-monitor)
  - [Signal Timeline](#signal-timeline)
  - [Backtest Results](#backtest-results)
  - [Settings](#settings)
- [Signal Engine — 6 Gamma Blast Models](#signal-engine--6-gamma-blast-models)
  - [1. GEX Zero-Cross Cascade (25%)](#1-gex-zero-cross-cascade-25)
  - [2. Gamma Wall Breach (20%)](#2-gamma-wall-breach-20)
  - [3. Charm Flow Accelerator (15%)](#3-charm-flow-accelerator-15)
  - [4. Negative Gamma Squeeze (15%)](#4-negative-gamma-squeeze-15)
  - [5. Pin Break Blast (15%)](#5-pin-break-blast-15)
  - [6. Vanna Squeeze (10%)](#6-vanna-squeeze-10)
- [7 Quality Filters](#7-quality-filters)
- [5 Standard GEX Signals](#5-standard-gex-signals)
- [Telegram Alerts](#telegram-alerts)
  - [Blast Alerts](#blast-alerts)
  - [Directional Trend Alerts](#directional-trend-alerts)
- [Configuration Reference](#configuration-reference)
- [Instruments](#instruments)
- [CLI Scripts](#cli-scripts)
- [Project Structure](#project-structure)
- [Alternative Deployment Options](#alternative-deployment-options)

---

## Overview

This system monitors real-time options chain data from Upstox, computes Gamma Exposure (GEX) profiles, and detects high-conviction scalping opportunities on expiry days using 6 professional models and 7 quality filters. Signals are delivered via the Streamlit dashboard and Telegram alerts.

**Key Features:**
- Real-time GEX profile computation
- 6-model composite scoring for blast detection
- 7 quality filters to reduce false signals
- Telegram alerts for blast signals and sustained directional moves
- India VIX regime adaptation
- Auto-refresh dashboard with mobile-responsive design
- Entry, Stop Loss, and Target level computation

---

## Architecture

```
User (iPad/Browser)
     │
     ▼
Streamlit Cloud App (auto-refresh every 30s / 60s on expiry)
     │
     ├── OAuth Login → Upstox API (token exchange)
     │
     ├── Fetch Options Chain → Upstox API (every 3 min)
     │
     ├── Build GEX Profile → Compute gamma exposure per strike
     │
     ├── Detect Gamma Blast → 6 models + 7 filters → composite score
     │
     ├── Generate Standard Signals → 5 GEX signal types
     │
     └── Send Alerts → Telegram Bot API
```

**Data Flow:**
1. User logs in via Upstox OAuth
2. Token stored in Streamlit session state
3. Options chain fetched at regular intervals
4. GEX profile built (gamma flip, walls, regime)
5. Blast detection runs 6 weighted models
6. 7 quality filters applied to raw score
7. If filtered score >= 70: signal fires, Telegram alert sent
8. Directional tracker monitors sustained moves for trend alerts

---

## Setup & Installation

### 1. Upstox API Credentials

1. Go to [Upstox Developer Console](https://account.upstox.com/developer/apps)
2. Create a new app
3. Note down your **API Key** and **API Secret**
4. Set the **Redirect URI** to your Streamlit app URL (e.g., `https://yourapp.streamlit.app`)

### 2. Streamlit Cloud Deployment

1. Push this repository to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Deploy from your GitHub repo
4. Set the main file path to: `src/dashboard/app.py`

### 3. Telegram Alerts Setup

**Step 1: Create a Telegram Bot**
1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the prompts
3. Choose a name and username for your bot
4. BotFather will give you a **Bot Token** (format: `7123456789:AAF...`)
5. Save this token

**Step 2: Get Your Chat ID**
1. Open Telegram and search for your new bot
2. Send any message to it (e.g., "hello")
3. Open this URL in your browser: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
4. Look for `"chat":{"id":123456789}` in the JSON response
5. That number is your **Chat ID**

**Step 3: Verify Setup**
1. Go to the **Settings** page in your dashboard
2. Click "Send Test Alert" button
3. You should receive a test message in Telegram

### 4. Streamlit Secrets Configuration

In Streamlit Cloud, go to your app → **Settings** → **Secrets** and add:

```toml
UPSTOX_API_KEY = "your_api_key_here"
UPSTOX_API_SECRET = "your_api_secret_here"
REDIRECT_URI = "https://yourapp.streamlit.app"
TELEGRAM_BOT_TOKEN = "your_telegram_bot_token"
TELEGRAM_CHAT_ID = "your_telegram_chat_id"
```

Click **Save changes** — changes take about a minute to propagate.

---

## Daily Usage Procedure

### Morning Routine

1. **Before 9:15 AM IST** — Open your Streamlit app URL in the browser
2. **Click "Login with Upstox"** — Authorize the app (fresh token required each day)
3. **Dashboard loads automatically** — Starts polling data and auto-refreshing

That's it. The app handles everything else automatically.

### During Market Hours

- The dashboard auto-refreshes every **30 seconds** (normal) or **60 seconds** (expiry day)
- Options chain data is fetched every **3 minutes**
- Watch for **Telegram alerts** on your phone — no need to constantly watch the dashboard
- On expiry days, the **Gamma Blast Scalper** page is where signals fire

### Key Timing Windows

| Time (IST) | What Happens |
|-------------|-------------|
| **9:15 AM** | Market opens, app starts polling live data |
| **9:15 – 10:00 AM** | Morning penalty zone — 40% score reduction on blast signals (fewer false signals in opening volatility) |
| **10:00 AM – 1:30 PM** | Normal trading window — standard signal scoring |
| **1:30 PM onwards** | Charm acceleration zone — 15% score boost (best window for expiry-day signals due to accelerating theta/charm decay) |
| **3:30 PM** | Market closes, polling stops |

### Expiry Day Schedule

| Day | Instrument | Notes |
|-----|-----------|-------|
| **Tuesday** | NIFTY | Weekly expiry — Gamma Blast active |
| **Thursday** | SENSEX | Weekly expiry — Gamma Blast active |

On non-expiry days, use the **Live GEX Monitor** page for standard GEX signals.

### iPad Usage Notes

- **Keep the browser tab open** — If you minimize or switch apps, the browser pauses the tab and auto-refresh stops
- **Disable Auto-Lock** — Go to iPad Settings → Display & Brightness → Auto-Lock → **Never**
- **Keep charger connected** — Prevents battery drain with screen always on
- **Use Split View** (optional) — Slide the browser to one side so you can use other apps alongside
- **Telegram is your safety net** — Even if the browser tab is active, always keep Telegram notifications on as a backup way to receive alerts

**Important:** The Streamlit Cloud app only runs while someone has the page open in a browser. If the tab is closed or the browser is backgrounded, data polling and alert generation will stop.

---

## Dashboard Pages

### Gamma Blast Scalper (Main Page)

**Purpose:** Detect high-conviction scalping opportunities on expiry days.

**Display Metrics:**
- Spot Price, Gamma Flip Level, GEX Regime (Positive/Negative)
- Max Gamma Strike (pin level), Call Wall, Put Wall
- India VIX value and regime classification
- Trend Bias (Bullish/Bearish/Flat) with strength indicator

**Signal Rules:**
- Minimum composite score: **70** (after all 7 filters)
- Maximum **2 signals per expiry day** (strict cap for disciplined scalping)
- **30-minute cooldown** between signals
- Expiry guard: Only fires within 7 hours of expiry during market hours

**When a Blast Fires:**
- Dashboard shows signal card with direction, score, entry/SL/target
- Telegram alert sent automatically with full details
- Component breakdown shows which models contributed most

### Live GEX Monitor

**Purpose:** Continuous real-time monitoring of gamma exposure profile with standard signals.

**Displays:**
- Real-time GEX profile chart (strike vs. GEX)
- Price chart with gamma walls overlay
- Signal timeline visualization
- 6 key metrics in a grid layout

**Generates 5 signal types** (see [5 Standard GEX Signals](#5-standard-gex-signals) below).

### Signal Timeline

**Purpose:** Historical view of all generated signals with filtering.

- Filter by signal type and minimum strength
- Interactive Plotly timeline chart
- Review past signals and their outcomes

### Backtest Results

**Purpose:** View results from backtesting module.

- Populated via the `src/backtest/` module
- Historical performance metrics

### Settings

**Purpose:** Configuration, token management, and Telegram verification.

**Features:**
- View token validity status (masked display)
- Quick token update — paste new token directly in UI
- View instrument configurations (NIFTY/SENSEX details)
- System settings table (intervals, lot sizes, thresholds)
- Telegram setup instructions and **Test Alert** button

---

## Signal Engine — 6 Gamma Blast Models

The blast detection engine uses 6 weighted models. Their scores are combined into a composite score (0-100), then filtered through 7 quality filters. All weights sum to 1.0.

### 1. GEX Zero-Cross Cascade (25%)

**The strongest single predictor.** Detects when spot price crosses the gamma flip level, triggering a dealer hedging cascade.

- **Bullish crossing** (spot moves above flip level into positive gamma): Base score 80
- **Bearish crossing** (spot drops below flip level into negative gamma): Base score 90 (stronger signal — negative gamma amplifies moves)
- **Boost:** Up to +10 based on GEX magnitude change across flip level
- **Imminent crossing** (spot within 0.2% of flip): Score 40 (early warning)

### 2. Gamma Wall Breach (20%)

Detects when price breaks through a major call wall (upside) or put wall (downside) with velocity confirmation.

- **Base score:** 70 on confirmed breach
- **Velocity boost:** Up to +20 (requires price moving faster than `WALL_BREACH_VELOCITY_MIN` pts/minute)
- **Overshoot bonus:** +10 if price has moved significantly past the wall
- **Direction:** Bullish for call wall breach, bearish for put wall breach

### 3. Charm Flow Accelerator (15%)

Exploits expiry-day delta decay that creates directional dealer hedging flow as options lose time value rapidly.

- **Scoring:** Intensity-based (0-100)
- **Pre-acceleration zone:** Score reduced to 30% (charm hasn't kicked in yet)
- **Acceleration zone** (last `CHARM_ACCELERATION_HOURS` before expiry): Score boosted by time proximity factor
- **Direction:** Follows the sign of net charm flow
- **Best window:** 1:30 PM onwards on expiry day

### 4. Negative Gamma Squeeze (15%)

In negative gamma regime, dealer hedging amplifies price moves (dealers hedge in the same direction as the move). This model scores higher when the negative gamma is deeper.

- **Only fires in negative gamma** (net GEX total < 0)
- **Mildly negative:** Score 30
- **Deeply negative:** Score 60-100 (scaled by depth)
- **Direction:** Follows recent spot movement — bullish if price trending up, bearish if down

### 5. Pin Break Blast (15%)

Detects price breaking away from the max gamma pin strike. On expiry day, dealers try to pin price at max gamma, so a breakout from pin is a significant event.

- **Detection logic:**
  - Was price pinned? (within `PIN_BREAK_MIN_MOVE_PCT` of max gamma strike)
  - Is price now moving away? (distance exceeds threshold)
- **Base score:** 50 on confirmed pin break
- **Magnitude boost:** Up to +15 based on distance from pin
- **Direction:** Bullish if breaking above pin, bearish if below

### 6. Vanna Squeeze (10%)

IV crush combined with vanna exposure creates directional dealer hedging flow. When implied volatility drops sharply, dealers must adjust their delta hedges.

- **Trigger:** IV drop exceeds `VANNA_IV_DROP_MIN` (1.5 points)
- **No significant IV move but high intensity:** Score = intensity × 0.3
- **Direction:** Follows the sign of net vanna flow

---

## 7 Quality Filters

Applied to the raw composite score to reduce false signals:

| # | Filter | Effect |
|---|--------|--------|
| 1 | **Trend Filter** | EMA-based trend detection. Penalizes counter-trend signals by up to 25 points |
| 2 | **VIX Regime Adaptation** | Adjusts model weights based on India VIX: LOW (<14), NORMAL (14-18), HIGH (18-22), EXTREME (>22) |
| 3 | **Volume Confirmation** | 30% score penalty if volume confirmation is absent |
| 4 | **Smart Timing** | Morning (9:15-10:00): 40% penalty. Charm zone (1:30 PM+): 15% boost |
| 5 | **Monthly vs Weekly Expiry** | Suppresses signals near max gamma on monthly expiry (different dynamics) |
| 6 | **Sensex Liquidity** | Penalizes signals on low OI Sensex chains (less reliable gamma data) |
| 7 | **Max Pain Proximity** | Suppresses signals when price is pinned near max pain (low breakout probability) |

---

## 5 Standard GEX Signals

Generated on every data refresh in the Live GEX Monitor:

| Signal | Trigger | Direction | Strength Calculation |
|--------|---------|-----------|---------------------|
| **Gamma Flip** | Spot crosses gamma flip level | Bullish (above flip) / Bearish (below flip) | GEX magnitude change across flip level |
| **Pin Risk** | Spot within 0.5% of max gamma strike AND < 4 hours to expiry | None (risk signal) | 50% proximity + 50% time factor |
| **Breakout** | Spot breaches outermost gamma wall in negative gamma (>0.5% past wall) | Bullish (call wall breach) / Bearish (put wall breach) | Normalized to 3x breakout threshold |
| **Vol Crush** | Net GEX shifts from negative to positive (regime change) | None (regime signal) | GEX magnitude shift |
| **Zero GEX Instability** | Spot within 0.3% of zero-GEX level | None (risk signal) | Inverse proximity (1.0 at exact level) |

---

## Telegram Alerts

### Blast Alerts

Sent when composite score >= 70 on expiry day. Format:

```
GAMMA BLAST — [BULLISH/BEARISH]

Score: 78/100 (raw: 85)
Direction: Bullish
Time to Expiry: 2.5 hours

Top Contributors:
1. GEX Zero-Cross (25%) — Score: 90
2. Wall Breach (20%) — Score: 75
3. Charm Flow (15%) — Score: 80

Levels:
Entry: 24,500
SL: 24,420 (-80 pts)
Target: 24,620 (+120 pts)
R:R: 1:1.5
```

### Directional Trend Alerts

Sent when sustained directional movement is detected (not choppy consolidation):

**Requirements:**
- Minimum 3 consecutive same-direction EMA readings
- Minimum 0.3% absolute price movement from window start
- Chop filter: rejects if 3+ reversals in last 5 readings
- 30-minute cooldown between same-direction alerts
- Rolling window of up to 20 trend readings

**Format includes:** Direction, strength bar, current spot, move %, timestamp

### Credential Priority (for Telegram)

1. Environment variables (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`)
2. Streamlit Secrets (cloud deployment)

---

## Configuration Reference

All configurable parameters in `config/settings.py`:

### Polling & Refresh

| Parameter | Value | Description |
|-----------|-------|-------------|
| `CHAIN_POLL_INTERVAL` | 180s (3 min) | Options chain fetch interval |
| `DASHBOARD_REFRESH_INTERVAL` | 30s | Dashboard auto-refresh (non-expiry) |
| `EXPIRY_DAY_POLL_INTERVAL` | 60s (1 min) | Faster polling on expiry day |

### Blast Detection

| Parameter | Value | Description |
|-----------|-------|-------------|
| `BLAST_MIN_SCORE` | 70 | Minimum filtered score to fire signal |
| `BLAST_MAX_SIGNALS_PER_DAY` | 2 | Max signals per expiry day |
| `BLAST_COOLDOWN_MINUTES` | 30 | Cooldown between blast signals |
| `CHARM_ACCELERATION_HOURS` | 3.0 | Hours before expiry for charm boost |
| `NEGATIVE_GAMMA_THRESHOLD` | -0.3 | Normalized ratio for deep negative gamma |
| `OI_SURGE_PCT` | 0.15 (15%) | OI change threshold for buildup detection |
| `PIN_BREAK_MIN_MOVE_PCT` | 0.003 (0.3%) | Min move from pin to confirm break |
| `WALL_BREACH_VELOCITY_MIN` | 5.0 | Min pts/minute for wall breach velocity |
| `VANNA_IV_DROP_MIN` | 1.5 | Min IV drop (points) for vanna trigger |

### Standard Signals

| Parameter | Value | Description |
|-----------|-------|-------------|
| `PIN_RISK_PROXIMITY_PCT` | 0.005 (0.5%) | Spot distance for pin risk trigger |
| `PIN_RISK_MAX_HOURS_TO_EXPIRY` | 4.0 | Time window for pin risk signals |
| `BREAKOUT_MIN_MOVE_PCT` | 0.01 (1%) | Min move past wall for breakout |
| `ZERO_GEX_PROXIMITY_PCT` | 0.003 (0.3%) | Distance for zero-GEX instability |
| `VOL_CRUSH_WINDOW_MINUTES` | 15 | Lookback window for vol crush |
| `GEX_TOP_N_WALLS` | 5 | Number of top call/put walls tracked |

### Telegram & Trend Alerts

| Parameter | Value | Description |
|-----------|-------|-------------|
| `TELEGRAM_ENABLED` | True | Master toggle for Telegram |
| `TREND_ALERT_MIN_CONSECUTIVE` | 3 | Same-direction readings before alert |
| `TREND_ALERT_MIN_MOVE_PCT` | 0.003 (0.3%) | Min price move to confirm direction |
| `TREND_ALERT_COOLDOWN_MINUTES` | 30 | Cooldown between same-direction alerts |

### Market Hours (IST)

| Parameter | Value |
|-----------|-------|
| `MARKET_OPEN` | 9:15 AM |
| `MARKET_CLOSE` | 3:30 PM |

---

## Instruments

Configured in `config/instruments.py`:

| Parameter | NIFTY | SENSEX |
|-----------|-------|--------|
| Exchange | NSE | BSE |
| Instrument Key | `NSE_INDEX\|Nifty 50` | `BSE_INDEX\|SENSEX` |
| Option Prefix | `NSE_FO` | `BSE_FO` |
| Contract Multiplier | 65 | 20 |
| Tick Size | 0.05 | 0.05 |
| Weekly Expiry Day | Tuesday | Thursday |

---

## CLI Scripts

### Live Monitoring (run_live.py)

Command-line polling loop with blast detection and Telegram alerts. Runs independently of the Streamlit dashboard.

```bash
# Monitor NIFTY with default settings
python scripts/run_live.py --instrument NIFTY

# Monitor SENSEX with custom interval
python scripts/run_live.py --instrument SENSEX --interval 120

# Run without Telegram alerts
python scripts/run_live.py --instrument NIFTY --no-telegram

# Specify expiry date manually
python scripts/run_live.py --instrument NIFTY --expiry 2025-01-14
```

**What it does:**
1. Validates Upstox access token
2. Fetches nearest expiry
3. Polls options chain data at set intervals
4. Builds GEX profile, generates signals
5. Detects gamma blasts
6. Sends Telegram alerts for blasts + directional moves
7. Displays ASCII table with live metrics
8. Runs until interrupted (Ctrl+C)

**Requires:** `UPSTOX_ACCESS_TOKEN` in `.env` file or environment variable.

### Historical Data Collection (fetch_historical_chains.py)

Collects options chain snapshots for backtesting.

```bash
# Collect NIFTY chain data
python scripts/fetch_historical_chains.py --instrument NIFTY

# Custom interval
python scripts/fetch_historical_chains.py --instrument NIFTY --interval 180
```

**Features:**
- Only collects during market hours (9:15 AM – 3:30 PM IST)
- Saves snapshots to `data/historical/`
- Records timestamp, spot price, and full chain DataFrame per snapshot

---

## Project Structure

```
TestCursorVL/
├── config/
│   ├── instruments.py          # NIFTY/SENSEX specifications
│   └── settings.py             # All configurable thresholds & intervals
├── data/
│   └── historical/             # Collected chain snapshots for backtesting
├── scripts/
│   ├── run_live.py             # CLI polling loop with alerts
│   └── fetch_historical_chains.py  # Historical data collection
├── src/
│   ├── auth/
│   │   └── upstox_auth.py      # OAuth 2.0 login & token management
│   ├── backtest/               # Backtesting module
│   ├── dashboard/
│   │   ├── app.py              # Main Streamlit entry point (OAuth gate)
│   │   └── pages/
│   │       ├── gamma_blast.py  # Expiry-day scalping page
│   │       ├── live_gex.py     # Continuous GEX monitoring
│   │       ├── signals.py      # Signal timeline view
│   │       ├── backtest_results.py  # Backtest results viewer
│   │       └── settings_page.py     # Config & Telegram setup
│   ├── data/
│   │   └── models.py           # Pydantic data models (GEXProfile, GammaBlast, etc.)
│   ├── engine/
│   │   ├── gamma_blast.py      # 6-model blast detection engine
│   │   └── signal_generator.py # 5 standard GEX signal types
│   └── notifications/
│       └── telegram.py         # Telegram alerts & DirectionalTracker
├── tests/                      # Test suite
├── requirements.txt            # Python dependencies
├── pyproject.toml              # Project configuration
└── README.md                   # This file
```

---

## Authentication Flow

The app uses Upstox OAuth 2.0 Code Flow:

1. User clicks "Login with Upstox" → redirected to Upstox authorization page
2. User authorizes → Upstox redirects back with authorization `code` in URL
3. App exchanges `code` for `access_token` via POST to token endpoint
4. Token stored in Streamlit session state for the session

**Token Priority (when loading):**
1. Streamlit session state (set by OAuth flow)
2. `.env` file (`UPSTOX_ACCESS_TOKEN`)
3. Streamlit Secrets (`UPSTOX_ACCESS_TOKEN`)

**Token Validation:** Lightweight check via `GET /user/profile` endpoint.

**Note:** Upstox tokens expire daily. You must log in each morning before market open.

---

## Alternative Deployment Options (For Always-On Alerts Without Browser)

If you want Telegram alerts without keeping a browser tab open, you can deploy the CLI script (`run_live.py`) on an always-on cloud service:

| Service | Free Tier | Always On | Best For |
|---------|-----------|-----------|----------|
| **PythonAnywhere** | Free | Yes (scheduled tasks) | Easiest setup |
| **Render** | Free | Spins down after inactivity | Web apps |
| **Railway** | $5 free credit/month | Yes | Background workers |
| **Oracle Cloud** | Always Free VPS | Yes | Full control |

**PythonAnywhere Setup (Easiest):**
1. Sign up at pythonanywhere.com (free account)
2. Open a Bash console
3. Clone the repository
4. Install dependencies: `pip install -r requirements.txt`
5. Set environment variables for Upstox token and Telegram credentials
6. Set up a scheduled task to run `scripts/run_live.py` at 9:10 AM IST daily
7. The script runs in the cloud, polls data, and sends alerts to Telegram
8. Just check Telegram on your phone/iPad — no browser needed

---

## Dependencies

```
upstox-python-sdk>=2.0
pandas>=2.0
numpy>=1.24
pydantic>=2.0
plotly>=5.0
streamlit>=1.30
streamlit-autorefresh>=1.0
requests>=2.31
python-dotenv>=1.0
pyarrow>=14.0
```

---

## VIX Regime Classification

India VIX levels affect model weights and signal behavior:

| Regime | VIX Range | Market Behavior |
|--------|-----------|----------------|
| **LOW** | < 14 | Calm market, smaller moves, gamma walls hold well |
| **NORMAL** | 14 – 18 | Standard conditions, balanced signal generation |
| **HIGH** | 18 – 22 | Elevated volatility, wider SL needed, stronger breakouts |
| **EXTREME** | > 22 | Very volatile, gamma walls break easily, risk management critical |

---

## Entry / Stop Loss / Target Calculation

When a blast signal fires, levels are computed automatically:

**Bullish Signal:**
- **Entry:** Current spot price
- **Stop Loss:** Put wall level (or spot × 0.995 if no clear put wall)
- **Target:** Call wall level (or spot × 1.008 if no clear call wall)

**Bearish Signal:**
- **Entry:** Current spot price
- **Stop Loss:** Call wall level (or spot × 1.005 if no clear call wall)
- **Target:** Put wall level (or spot × 0.992 if no clear put wall)

Risk:Reward ratio is displayed with each signal.
