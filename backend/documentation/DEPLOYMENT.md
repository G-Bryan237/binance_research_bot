# Deployment Guide

This guide covers deploying the multi-profile trading system.

## Quick Start (Local Development)

### Option 1: Run Both Profiles Together

```bash
cd backend
python run_multiprofile.py
```

This starts:
- **Conservative profile**: http://localhost:5001
- **Aggressive profile**: http://localhost:5002
- **Report scheduler**: Generates daily/weekly reports automatically

### Option 2: Run Profiles Separately

Terminal 1 (Conservative):
```bash
cd backend
python run_worker.py --env profiles/conservative.env
```

Terminal 2 (Conservative API):
```bash
cd backend  
python run_api.py --env profiles/conservative.env
```

Terminal 3 (Aggressive):
```bash
cd backend
python run_worker.py --env profiles/aggressive.env
```

Terminal 4 (Aggressive API):
```bash
cd backend
python run_api.py --env profiles/aggressive.env
```

Terminal 5 (Frontend):
```bash
cd frontend
npm install
npm run dev
```

## Docker Deployment

### Build and Run

```bash
# Build all services
docker-compose build

# Start all services
docker-compose up -d

# View logs
docker-compose logs -f
```

### Services

| Service | Port | Description |
|---------|------|-------------|
| frontend | 3000 | Next.js dashboard |
| backend-conservative | 5001 | Conservative profile API |
| backend-aggressive | 5002 | Aggressive profile API |
| report-generator | - | Scheduled report generation |

### Stop Services

```bash
docker-compose down
```

## Profile Configuration

Profiles are in `backend/profiles/`:

- `conservative.env` - Lower risk, stricter filters
- `aggressive.env` - Higher risk, all modules active

### Key Differences

| Setting | Conservative | Aggressive |
|---------|--------------|------------|
| Risk per trade | 0.5% | 1.2% |
| Daily loss limit | 1.5% | 4% |
| Modules enabled | M1-M3 | M1-M6 |
| Leverage | 1x | 2x |
| Symbols | 3 | 8 |

## Reports

Reports are generated automatically:
- **Daily**: Every day at midnight UTC
- **Weekly**: Every Monday at midnight UTC

Manual generation:
```bash
cd backend

# Generate daily reports
python run_multiprofile.py --report daily

# Generate weekly reports
python run_multiprofile.py --report weekly
```

Reports are saved to:
- `backend/reports/conservative/` - Conservative profile reports
- `backend/reports/aggressive/` - Aggressive profile reports

### Report API Endpoints

- `GET /api/reports` - List all reports
- `GET /api/reports/daily` - Latest daily report
- `GET /api/reports/daily/{date}` - Specific daily report
- `GET /api/reports/weekly` - Latest weekly report
- `GET /api/reports/weekly/{date}` - Specific weekly report

## Frontend Dashboard

The dashboard supports:
- **Single view**: Focus on one profile
- **Split view**: Compare both profiles side-by-side

Profile selector shows real-time:
- Connection status
- Current equity
- Daily P&L

## Environment Variables

### Backend (Profile-specific)

```env
BOT_PROFILE_ID=conservative
BOT_PROFILE_NAME=Conservative Profile
BOT_START_EQUITY=100
BOT_DASHBOARD_PORT=5001
BOT_STATE_DB_PATH=out/conservative_state.sqlite3
BOT_REPORT_OUTPUT_DIR=reports/conservative
```

### Frontend

```env
CONSERVATIVE_API_URL=http://localhost:5001
AGGRESSIVE_API_URL=http://localhost:5002
NEXT_PUBLIC_CONSERVATIVE_API_URL=http://localhost:5001
NEXT_PUBLIC_AGGRESSIVE_API_URL=http://localhost:5002
```

## Production Checklist

1. [ ] Update API keys in profile env files
2. [ ] Set `BOT_PAPER_MODE=false` when ready for live trading
3. [ ] Configure proper CORS origins
4. [ ] Set up SSL/TLS for API endpoints
5. [ ] Configure persistent volumes for databases and reports
6. [ ] Set up monitoring and alerting
7. [ ] Backtest both profiles extensively before live trading
