# Binance Research Bot (Paper First)

A simple, explainable Binance bot system for **spot + USD-M perpetual futures** with:

- One active strategy module at a time (regime-mapped)
- Strict risk controls for small accounts ($10-$100)
- Paper trading first (fees, funding, slippage simulation)
- Trade signal + alert delivery (console/webhook/Telegram/email)

No guaranteed profits. This is a research and risk-management framework.

## Features

- Primary timeframe: `5m` (alternatives supported: `1m`, `15m`, `1h`)
- Regime detection:
  - `TREND`
  - `BREAKOUT_EXPANSION`
  - `RANGE_HIGH_VOL`
  - `LOW_VOL_CHOP` (optional strict `M4` mean-reversion)
- Strategy modules:
  - `M1`: Trend Pullback with Liquidity Sweep
  - `M2`: Breakout Retest with Imbalance (FVG) Mitigation
  - `M3`: Liquidation Sweep Reversal
  - `M4`: Low-Volatility Mean-Reversion (band + RSI + range-edge reclaim)
  - `M5`: VWAP + Volume Profile Institutional Levels (overlay)
  - `M6`: Multi-Timeframe Divergence (overlay)
- Multi-timeframe confirmation (configurable, e.g. `15m` / `1h`) before entry
- Module performance scorecard with temporary auto-disable of underperforming modules
- Real partial exits + ATR trailing stop after first target
- Guardrails:
  - No trading outside whitelist (majors + Layer-1 symbols only)
  - Trade caps and cooldowns
  - Abnormal-spike/chasing-pump blocker
  - No re-entry without a fresh valid signal

## Project Structure

```text
binance_research_bot/
  README.md
  DEPLOYMENT.md
  docker-compose.yml
  backend/
    requirements.txt
    run_worker.py
    run_api.py
    run_multiprofile.py      # Run both profiles simultaneously
    run_backtest.py
    profiles/
      conservative.env       # Conservative profile settings
      aggressive.env         # Aggressive profile settings
    reports/                 # Auto-generated reports
    bot/
      __init__.py
      alerts.py
      api_server.py
      backtest.py
      config.py
      data.py
      engine.py
      execution.py
      indicators.py
      regime.py
      reporting.py           # Daily/weekly report generation
      risk.py
      state_store.py
      strategies.py
      types.py
  frontend/
    app/
    components/
      trading-dashboard.tsx
      profile-selector.tsx   # Multi-profile selector
    lib/
    package.json
```

## Quick Start

### 1. Install Dependencies

```bash
cd backend
python -m venv .venv
# Linux/Mac: source .venv/bin/activate
# Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure API Keys

Edit `backend/profiles/conservative.env` and `backend/profiles/aggressive.env` with your Binance API keys.

### 3. Run Both Profiles (Recommended)

```bash
cd backend
python run_multiprofile.py
```

This starts:
- Conservative profile: http://localhost:5001
- Aggressive profile: http://localhost:5002

### 4. Run Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000 to view both profiles.

### Alternative: Docker

```bash
docker-compose up -d
```

2. Configure:

```bash
cp .env.example .env
```

### Risk Profiles

Two pre-configured risk profiles are available:

**Conservative** (recommended for learning/paper trading):
```bash
cp .env.conservative .env
```
- Lower risk (0.5% per trade)
- Stricter filters
- Fewer modules (M1-M3 only)
- Tighter loss limits

**Aggressive** (after validation):
```bash
cp .env.aggressive .env
```
- Higher risk (1.2% per trade)
- All modules active (M1-M6)
- Looser filters
- 2x leverage enabled

3. Run worker (paper bot engine):

```bash
python run_worker.py
```

Legacy alias still works: `python run_paper.py`.

Run one cycle only:

```bash
python run_worker.py --once
```

## Frontend + Backend Split

This project now supports a strict split:

- `frontend/` (Next.js): UI/visuals only
- `run_worker.py` (Python worker): strategy/execution loop only
- `run_api.py` (Python API): read-only API + websocket stream only
- Shared persistence: SQLite (`BOT_STATE_DB_PATH`, default `out/bot_state.sqlite3`)

### Run both services (separate but connected)

Terminal 1 (Python worker):

```bash
# Linux/Mac
cd /home/bryan/VISION-TECH-REPOS/binance_research_bot
source .venv/bin/activate
python run_worker.py

# Windows (PowerShell)
cd D:\CODING_WORK_PROJECTS\AI-AUTOMATION\binance_research_bot
.\.venv\Scripts\Activate.ps1
python run_worker.py
```

Terminal 2 (Python API server):

```bash
# Linux/Mac
cd /home/bryan/VISION-TECH-REPOS/binance_research_bot
source .venv/bin/activate
python run_api.py

# Windows (PowerShell)
cd D:\CODING_WORK_PROJECTS\AI-AUTOMATION\binance_research_bot
.\.venv\Scripts\Activate.ps1
python run_api.py
```

Terminal 3 (Next.js frontend):

```bash
cd /home/bryan/VISION-TECH-REPOS/binance_research_bot/frontend
cp .env.local.example .env.local
npm install
npm run dev
```

Then open:

- Frontend UI: `http://localhost:3000`
- Python backend API: `http://localhost:5000`

The Next.js app consumes local proxy routes (`/api/bot/*`) for REST and connects directly to backend websocket for realtime updates.

Available backend endpoints:

- `GET /api/health`
- `GET /api/state`
- `GET /api/stats`
- `GET /api/trades`
- `GET /api/market?symbol=BTCUSDT&limit=120`

Realtime stream (Socket.IO):

- `state_update`
- `stats_update`
- `module_scores_update`
- `trade_added`

## Backtest

Backtest one or more historical CSV files:

```bash
python run_backtest.py --csv data/BTCUSDT_SPOT_5m.csv --csv data/BTCUSDT_PERP_5m.csv
```

Use glob + write outputs:

```bash
python run_backtest.py \
  --glob "data/*_5m.csv" \
  --out-summary-json out/summary.json \
  --out-trades-csv out/trades.csv \
  --out-equity-csv out/equity.csv
```

Download historical candles and run:

```bash
python run_backtest.py \
  --download-symbol BTCUSDT \
  --download-market SPOT \
  --download-timeframe 5m \
  --download-days 180
```

Generate synthetic sample data for a quick smoke test:

```bash
python scripts/generate_sample_data.py --out data/BTCUSDT_SPOT_5m.csv --bars 5000 --inject-sweeps
python run_backtest.py --csv data/BTCUSDT_SPOT_5m.csv
```

Optional walk-forward robustness report:

```bash
python run_backtest.py \
  --glob "data/*_5m.csv" \
  --walk-forward-months 3 \
  --walk-forward-test-months 1
```

### CSV Format

Required columns:

- `open_time` (or aliases: `timestamp`, `time`, `date`, `datetime`)
- `open`
- `high`
- `low`
- `close`
- `volume`

Optional columns:

- `bid`, `ask`
- `quote_volume_24h`
- `funding_rate`
- `open_interest`

## Notes on Binance Endpoints

- Spot testnet: `https://testnet.binance.vision`
- USD-M futures testnet REST: `https://demo-fapi.binance.com`
- Funding/history endpoints are used for carry filters and simulation.
- If testnet data is slow/unavailable, the bot can fallback to Binance mainnet **market data only** (`BOT_USE_MAINNET_DATA_FALLBACK=true`) while still keeping paper execution logic.

## Timeout Troubleshooting

If you see repeated `Read timed out` warnings:

- Lower request pressure:
  - `BOT_SYMBOLS_PER_TICK=2` (or `1`)
  - Keep `BOT_ALLOWED_SYMBOLS` short while testing
- Use faster retry settings:
  - `BOT_DATA_TIMEOUT_SECONDS=6`
  - `BOT_DATA_RETRIES=1` (or `0` for very strict time budget)
  - `BOT_DATA_RETRY_BACKOFF_SECONDS=0.4`
- Keep fallback enabled:
  - `BOT_USE_MAINNET_DATA_FALLBACK=true`
- If MTF confirmation increases API load:
  - `BOT_MTF_CONFIRM_TIMEFRAMES=15m` (single HTF)
  - or temporarily disable with `BOT_ENABLE_MTF_CONFIRMATION=false`

## Safety Defaults

- Leverage default `1x`; `2x` requires explicit enable.
- Per-trade risk default `0.75%` equity.
- `M4` risk uses a multiplier (default `0.5`) so chop trades size smaller than M1-M3.
- Daily and weekly max loss locks.
- Strategy can return `DO_NOTHING` at any time.

M4 controls in `.env`:

- `BOT_ENABLE_M4=true|false`
- `BOT_M4_RISK_MULTIPLIER=0.5`

M5/M6 overlay modules in `.env`:

- `BOT_ENABLE_M5=true|false` (VWAP + Volume Profile)
- `BOT_ENABLE_M6=true|false` (Multi-timeframe Divergence)

Advanced controls in `.env`:

- `BOT_ENABLE_MTF_CONFIRMATION=true|false`
- `BOT_MTF_CONFIRM_TIMEFRAMES=15m,1h`
- `BOT_ENABLE_MODULE_SCORECARD=true|false`
- `BOT_MODULE_PERF_LOOKBACK=10`
- `BOT_MODULE_PERF_MIN_TRADES=4`
- `BOT_MODULE_PERF_MIN_WIN_RATE=0.30`
- `BOT_MODULE_PERF_DISABLE_MINUTES=180`
- `BOT_ENABLE_PARTIAL_TP=true|false`
- `BOT_PARTIAL_TP_FRACTION=0.5`
- `BOT_ENABLE_TRAILING_STOP=true|false`
- `BOT_TRAILING_ATR_MULTIPLIER=1.2`

## Disclaimer

This code is for educational and research purposes. Crypto trading is risky.


python run_api.py
cd frontend
npm run dev -- --hostname 127.0.0.1 --port 3001