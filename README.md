# Binance Research Bot

This repo is split into two main folders:

- `backend/` - Python trading bot, API server, profile configs, data, reports, and Docker Compose file.
- `frontend/` - Next.js dashboard.

Older detailed project docs live in `backend/documentation/`.

## Prerequisites

- Python 3.11+
- Node.js 20+
- npm
- Docker Desktop, optional

## Run The Backend

Open a terminal from the repo root:

```powershell
cd backend
python -m pip install -r requirements.txt
```

Run the default backend API with `backend/.env`:

```powershell
python run_api.py
```

Default API URL:

```text
http://localhost:5000
```

Run one profile:

```powershell
python run_multiprofile.py --profile conservative
```

or:

```powershell
python run_multiprofile.py --profile aggressive
```

Profile API URLs:

```text
Conservative: http://localhost:5001
Aggressive:   http://localhost:5002
```

Run both profiles together:

```powershell
python run_multiprofile.py
```

## Run The Frontend

Open a second terminal from the repo root:

```powershell
cd frontend
npm install
npm run dev
```

Frontend URL:

```text
http://localhost:3000
```

For the multiprofile dashboard, keep both backend profiles running so the frontend can reach ports `5001` and `5002`.

## Run Everything With Docker

From the repo root:

```powershell
cd backend
docker compose up --build
```

Docker exposes:

```text
Frontend:     http://localhost:3000
Conservative: http://localhost:5001
Aggressive:   http://localhost:5002
```

Stop Docker:

```powershell
docker compose down
```

## Config Files

- `backend/.env` - default backend config.
- `backend/.env.conservative` - conservative profile reference.
- `backend/.env.aggressive` - aggressive profile reference.
- `backend/profiles/conservative.env` - runtime conservative profile used by `run_multiprofile.py`.
- `backend/profiles/aggressive.env` - runtime aggressive profile used by `run_multiprofile.py`.

Use paper mode first and keep real exchange keys out of shared commits.
