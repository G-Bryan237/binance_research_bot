# Binance Bot Frontend (Next.js)

This is the visual layer for the Binance research bot.

- Framework: Next.js App Router
- Backend target: Python dashboard API (`http://127.0.0.1:5000` by default)
- Contract: frontend route proxies under `/api/bot/*`
- Realtime: Socket.IO client subscribes to backend events

## Setup

```bash
cd /home/bryan/VISION-TECH-REPOS/binance_research_bot/frontend
cp .env.local.example .env.local
npm install
npm run dev
```

Open `http://localhost:3000`.

## Environment

- `PYTHON_BACKEND_BASE_URL`: Base URL for Python API (`http://127.0.0.1:5000`)
- `NEXT_PUBLIC_PYTHON_BACKEND_WS_URL`: Websocket origin for realtime (`http://127.0.0.1:5000`)

## Proxy routes

- `/api/bot/health` -> `/api/health`
- `/api/bot/state` -> `/api/state`
- `/api/bot/stats` -> `/api/stats`
- `/api/bot/trades` -> `/api/trades`
- `/api/bot/market` -> `/api/market`
