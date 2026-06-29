from __future__ import annotations

import json
import logging
import os
import csv
from pathlib import Path
try:
    from dotenv import load_dotenv
    HAS_DOTENV = True
except Exception:
    HAS_DOTENV = False
from datetime import datetime, timezone
from typing import Any, Dict
import threading
import time as _time
try:
    import websocket
    HAS_WS_CLIENT = True
except Exception:
    HAS_WS_CLIENT = False

try:
    from flask import Flask, jsonify, render_template_string
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False
try:
    from flask_socketio import SocketIO
    HAS_SOCKETIO = True
except Exception:
    HAS_SOCKETIO = False

LOG = logging.getLogger(__name__)


class DashboardServer:
    """Simple Flask-based web dashboard for real-time bot monitoring."""

    def __init__(self, host: str = "0.0.0.0", port: int = 5000):
        self.host = host
        self.port = port
        self.app: Flask | None = None
        self.state: Dict[str, Any] = {
            "status": "initializing",
            "current_tick": None,
            "last_update_utc": None,
            "environment": "paper",
            "data_source": "binance market snapshots",
            "update_interval_ms": 500,
            "open_position": None,
            "recent_trades": [],
            "equity_history": [],
            "equity": 0.0,
            "daily_pnl": 0.0,
            "starting_equity": 0.0,
            "module_scores": {},
            "trading_stats": {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "avg_r_multiple": 0.0,
            },
        }

    def initialize(self) -> bool:
        """Initialize Flask app and routes."""
        if not HAS_FLASK:
            LOG.warning("Flask not installed. Dashboard disabled. Install with: pip install flask")
            return False

        # load .env if available so DATA_DIR/OUT_DIR can be configured
        if HAS_DOTENV:
            load_dotenv()
        self.data_dir = Path(os.getenv('DATA_DIR', 'data'))
        self.out_dir = Path(os.getenv('OUT_DIR', 'out'))

        self.app = Flask(__name__)
        cors_origin = os.getenv("BOT_DASHBOARD_CORS_ORIGIN", "*")

        @self.app.after_request
        def apply_cors_headers(response):
            response.headers["Access-Control-Allow-Origin"] = cors_origin
            response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
            response.headers["Access-Control-Allow-Methods"] = "GET,OPTIONS"
            return response

        self.socketio = None
        if HAS_SOCKETIO:
            try:
                self.socketio = SocketIO(self.app, cors_allowed_origins='*')
            except Exception:
                self.socketio = None

        @self.app.route("/")
        def dashboard():
            return render_template_string(self._get_html_template())

        @self.app.route("/api/state")
        def get_state():
            return jsonify(self.state)

        @self.app.route("/api/trades")
        def get_trades():
            return jsonify({"trades": self.state.get("recent_trades", [])})

        @self.app.route("/api/stats")
        def get_stats():
            return jsonify(self.state.get("trading_stats", {}))

        @self.app.route("/api/health")
        def health_check():
            return jsonify(
                {
                    "ok": True,
                    "service": "binance-research-bot-backend",
                    "status": self.state.get("status", "unknown"),
                    "last_update_utc": self.state.get("last_update_utc"),
                }
            )

        @self.app.route('/api/market')
        def get_market():
            """Return recent OHLCV for a symbol from `data/` CSV files.
            Query params: symbol (e.g., BTCUSDT), limit (int)
            """
            from flask import request
            symbol = (request.args.get('symbol') or 'BTCUSDT').upper()
            limit = int(request.args.get('limit') or 120)
            # try common filename patterns in data_dir
            candidates = [
                self.data_dir / f"{symbol}_SPOT_5m.csv",
                self.data_dir / f"{symbol}_SPOT_1m.csv",
                self.data_dir / f"{symbol}.csv",
            ]
            found = None
            for c in candidates:
                if c.exists():
                    found = c
                    break
            if not found:
                return jsonify({"error": "market data not found", "candidates": [str(x) for x in candidates]}), 404

            rows = []
            try:
                with found.open('r') as fh:
                    reader = csv.DictReader(fh)
                    all_rows = list(reader)
                    tail = all_rows[-limit:]
                    for r in tail:
                        try:
                            rows.append({
                                "time": int(r.get('open_time') or r.get('time') or 0),
                                "open": float(r.get('open') or 0),
                                "high": float(r.get('high') or 0),
                                "low": float(r.get('low') or 0),
                                "close": float(r.get('close') or 0),
                                "volume": float(r.get('volume') or r.get('qty') or 0),
                            })
                        except Exception:
                            continue
            except Exception as e:
                return jsonify({"error": "failed to read data", "detail": str(e)}), 500

            return jsonify({"symbol": symbol, "candles": rows})

        @self.app.route('/api/out/<path:name>')
        def get_out_file(name: str):
            """Serve files from the `out/` folder for quick inspection/download."""
            from flask import send_file, abort
            p = self.out_dir / name
            if not p.exists():
                abort(404)
            return send_file(str(p), as_attachment=True)

        LOG.info(f"Dashboard initialized at http://{self.host}:{self.port}")
        # start background market broadcaster when socketio available
        if self.socketio:
            try:
                self.socketio.start_background_task(self._market_broadcaster)
            except Exception:
                LOG.debug('Failed to start market broadcaster')
        # optionally start Binance websocket ingestion
        if os.getenv('ENABLE_BINANCE_WS', 'true').lower() in ('1', 'true', 'yes') and HAS_WS_CLIENT:
            try:
                # run in a separate thread to avoid blocking eventlet greenlets
                t = threading.Thread(target=self._binance_ws_listener, daemon=True)
                t.start()
            except Exception:
                LOG.debug('Failed to start Binance WS listener')
        return True

    def _market_broadcaster(self):
        """Background task that emits the latest market candle every second via SocketIO."""
        if not self.socketio:
            return
        import time
        last_ts = None
        symbol = os.getenv('DASH_SYMBOL', 'BTCUSDT')
        file_candidates = [
            self.data_dir / f"{symbol}_SPOT_1m.csv",
            self.data_dir / f"{symbol}_SPOT_5m.csv",
            self.data_dir / f"{symbol}.csv",
        ]
        while True:
            try:
                found = None
                for f in file_candidates:
                    if f.exists():
                        found = f
                        break
                if not found:
                    time.sleep(1.0)
                    continue
                with found.open('r') as fh:
                    reader = csv.DictReader(fh)
                    rows = list(reader)
                    if not rows:
                        time.sleep(1.0)
                        continue
                    last = rows[-1]
                    ts = int(last.get('open_time') or last.get('time') or 0)
                    if ts != last_ts:
                        last_ts = ts
                        candle = {
                            'time': ts,
                            'open': float(last.get('open') or 0),
                            'high': float(last.get('high') or 0),
                            'low': float(last.get('low') or 0),
                            'close': float(last.get('close') or 0),
                            'volume': float(last.get('volume') or last.get('qty') or 0),
                        }
                        try:
                            self.socketio.emit('market_tick', candle)
                        except Exception:
                            pass
                time.sleep(1.0)
            except Exception:
                time.sleep(1.0)

    def _binance_ws_listener(self):
        """Connect to Binance public websocket for kline or trade updates and write candles to CSV.

        Environment vars:
        - DASH_SYMBOL (default BTCUSDT)
        - DASH_INTERVAL (default 1m) used for kline
        - BINANCE_WS_TYPE (trade|kline) default 'trade'
        - ENABLE_BINANCE_WS (default true)
        """
        if not HAS_WS_CLIENT:
            LOG.warning('websocket-client not available; Binance WS disabled')
            return
        base = os.getenv('BINANCE_WS_BASE', 'wss://stream.binance.com:9443/ws')
        symbol = os.getenv('DASH_SYMBOL', 'BTCUSDT').lower()
        interval = os.getenv('DASH_INTERVAL', '1m')
        ws_type = os.getenv('BINANCE_WS_TYPE', 'trade').lower()
        if ws_type == 'kline':
            stream = f"{symbol}@kline_{interval}"
        else:
            stream = f"{symbol}@trade"
        url = f"{base}/{stream}"

        def _ensure_csv(path: Path):
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('w', newline='') as fh:
                    writer = csv.writer(fh)
                    writer.writerow(['open_time','open','high','low','close','volume','quote_volume_24h'])

        def on_message(wsapp, message):
            try:
                payload = json.loads(message)
                # handle kline messages (k present)
                if 'k' in payload or (isinstance(payload.get('data'), dict) and 'k' in payload.get('data')):
                    k = payload.get('k') or payload.get('data') or {}
                    candle = {
                        'open_time': int(k.get('t') or 0),
                        'open': float(k.get('o') or 0),
                        'high': float(k.get('h') or 0),
                        'low': float(k.get('l') or 0),
                        'close': float(k.get('c') or 0),
                        'volume': float(k.get('v') or 0),
                    }
                    fname = self.data_dir / f"{symbol.upper()}_SPOT_{interval}.csv"
                    _ensure_csv(fname)
                    try:
                        rows = []
                        with fname.open('r', newline='') as fh:
                            reader = list(csv.DictReader(fh))
                            rows = reader
                    except Exception:
                        rows = []
                    if rows and int(rows[-1].get('open_time') or 0) == candle['open_time']:
                        rows[-1] = {'open_time': candle['open_time'], 'open': candle['open'], 'high': candle['high'], 'low': candle['low'], 'close': candle['close'], 'volume': candle['volume'], 'quote_volume_24h': ''}
                    else:
                        rows.append({'open_time': candle['open_time'], 'open': candle['open'], 'high': candle['high'], 'low': candle['low'], 'close': candle['close'], 'volume': candle['volume'], 'quote_volume_24h': ''})
                    try:
                        with fname.open('w', newline='') as fh:
                            writer = csv.DictWriter(fh, fieldnames=['open_time','open','high','low','close','volume','quote_volume_24h'])
                            writer.writeheader()
                            for r in rows[-2000:]:
                                writer.writerow(r)
                    except Exception:
                        pass
                    try:
                        if self.socketio:
                            self.socketio.emit('market_tick', {
                                'time': candle['open_time'],
                                'open': candle['open'],
                                'high': candle['high'],
                                'low': candle['low'],
                                'close': candle['close'],
                                'volume': candle['volume'],
                            })
                    except Exception:
                        pass
                else:
                    # try trade message formats
                    data = payload.get('data') or payload
                    price = data.get('p') or data.get('price')
                    ttime = data.get('T') or data.get('tradeTime') or data.get('E') or 0
                    qty = data.get('q') or data.get('q') or 0
                    try:
                        price = float(price)
                    except Exception:
                        price = None
                    try:
                        ttime = int(ttime or 0)
                    except Exception:
                        ttime = 0
                    trade = {
                        'time': ttime,
                        'price': price,
                        'qty': float(qty or 0)
                    }
                    try:
                        if self.socketio:
                            self.socketio.emit('trade_tick', trade)
                    except Exception:
                        pass
            except Exception:
                return

        def on_error(wsapp, err):
            LOG.debug('Binance WS error: %s', err)

        def on_close(wsapp, code, reason):
            LOG.debug('Binance WS closed: %s %s', code, reason)

        def on_open(wsapp):
            LOG.info('Binance WS connected to %s', url)

        while True:
            try:
                wsapp = websocket.WebSocketApp(url, on_message=on_message, on_error=on_error, on_close=on_close, on_open=on_open)
                # run in current thread; will block until closed – that is why we spawn a thread
                wsapp.run_forever()
            except Exception as e:
                LOG.debug('Binance WS run error, reconnecting: %s', e)
            _time.sleep(2.0)

    def update_state(self, **kwargs) -> None:
        """Update dashboard state with new data."""
        for key, value in kwargs.items():
            if key in self.state:
                self.state[key] = value
        self.state["last_update_utc"] = datetime.now(tz=timezone.utc).isoformat()
        if "equity" in kwargs:
            self.state["equity_history"].append(
                {
                    "time": self.state["last_update_utc"],
                    "equity": float(kwargs.get("equity") or 0.0),
                }
            )
            self.state["equity_history"] = self.state["equity_history"][-1200:]

    def add_trade(self, trade_info: Dict[str, Any]) -> None:
        """Add a trade to recent trades list (keep last 50)."""
        trade_info["timestamp"] = datetime.now(tz=timezone.utc).isoformat()
        self.state["recent_trades"].insert(0, trade_info)
        self.state["recent_trades"] = self.state["recent_trades"][:200]

    def update_stats(self, total: int, wins: int, losses: int, avg_r: float) -> None:
        """Update trading statistics."""
        self.state["trading_stats"] = {
            "total_trades": total,
            "winning_trades": wins,
            "losing_trades": losses,
            "win_rate": wins / total if total > 0 else 0.0,
            "avg_r_multiple": avg_r,
        }

    def update_module_scores(self, scores: Dict[str, Dict[str, Any]]) -> None:
        """Update module performance scores."""
        self.state["module_scores"] = scores

    def run(self) -> None:
        """Start the Flask server (blocking)."""
        if not self.app:
            LOG.error("Dashboard not initialized")
            return
        try:
            if self.socketio:
                # use SocketIO run when available (supports websocket transport)
                # eventlet/gevent is preferred; SocketIO will select best available
                self.socketio.run(self.app, host=self.host, port=self.port, debug=False)
            else:
                self.app.run(host=self.host, port=self.port, debug=False, use_reloader=False)
        except Exception as e:
            LOG.error(f"Dashboard error: {e}")

    @staticmethod
    def _get_html_template() -> str:
        """Return HTML template for the dashboard."""
        return """
<!DOCTYPE html>
<html>
<head>
    <title>Binance Research Bot Control Room</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;600&family=Fraunces:opsz,wght@9..144,600;9..144,700&display=swap');
    </style>
    <style>
        :root {
            --bg-top: #f3efe7;
            --bg-bottom: #dde9e8;
            --surface: #ffffff;
            --surface-soft: #f8faf9;
            --ink: #1d292d;
            --muted: #5d696f;
            --line: #dfe5e2;
            --accent: #0f766e;
            --accent-soft: #dcf4f2;
            --highlight: #c06f09;
            --success: #167a52;
            --danger: #b42a2a;
            --warning: #a66a11;
            --radius-lg: 20px;
            --radius-md: 14px;
            --shadow: 0 14px 32px rgba(17, 39, 46, 0.10);
        }
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: 'Space Grotesk', sans-serif;
            color: var(--ink);
            min-height: 100vh;
            padding: 20px;
            background:
                radial-gradient(circle at 10% 20%, rgba(15, 118, 110, 0.16), transparent 38%),
                radial-gradient(circle at 85% 8%, rgba(192, 111, 9, 0.16), transparent 35%),
                linear-gradient(160deg, var(--bg-top), var(--bg-bottom));
        }
        body::before {
            content: '';
            position: fixed;
            inset: 0;
            background-image: linear-gradient(90deg, rgba(26, 43, 49, 0.03) 1px, transparent 1px),
                linear-gradient(rgba(26, 43, 49, 0.03) 1px, transparent 1px);
            background-size: 30px 30px;
            pointer-events: none;
            opacity: 0.45;
        }
        .container {
            max-width: 1500px;
            margin: 0 auto;
            position: relative;
            z-index: 1;
        }
        .masthead {
            background: rgba(255, 255, 255, 0.78);
            border: 1px solid rgba(255, 255, 255, 0.5);
            border-radius: var(--radius-lg);
            box-shadow: var(--shadow);
            backdrop-filter: blur(8px);
            padding: 20px;
            margin-bottom: 20px;
            display: grid;
            grid-template-columns: 1.2fr 1fr;
            gap: 16px;
            align-items: center;
        }
        .title-wrap {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .eyebrow {
            letter-spacing: 0.11em;
            text-transform: uppercase;
            font-size: 0.74rem;
            color: var(--muted);
            font-weight: 600;
        }
        h1 {
            font-family: 'Fraunces', serif;
            font-size: clamp(1.7rem, 2.8vw, 2.7rem);
            line-height: 1.05;
            letter-spacing: -0.01em;
        }
        .subtitle {
            color: var(--muted);
            font-size: 0.95rem;
            font-weight: 500;
        }

        .status-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
        }
        .status-chip {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
            border-radius: 999px;
            border: 1px solid var(--line);
            background: rgba(255, 255, 255, 0.75);
            padding: 8px 12px;
            min-width: 0;
        }
        .status-chip .label {
            font-size: 0.72rem;
            letter-spacing: 0.07em;
            text-transform: uppercase;
            color: var(--muted);
            font-weight: 600;
        }
        .status-chip .content {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            font-weight: 600;
            font-size: 0.82rem;
            font-family: 'IBM Plex Mono', monospace;
        }
        .status-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 96px;
            padding: 6px 10px;
            border-radius: 999px;
            font-weight: 600;
            font-size: 0.74rem;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            background: var(--warning);
            color: #fff;
        }
        .status-badge.running {
            background: var(--success);
            color: #fff;
        }
        .status-badge.initializing {
            background: var(--highlight);
            color: #fff;
        }

        .main-grid {
            display: grid;
            grid-template-columns: repeat(12, minmax(0, 1fr));
            gap: 14px;
            margin-bottom: 14px;
        }

        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            gap: 14px;
            grid-column: span 12;
        }
        .card {
            background: var(--surface);
            border-radius: var(--radius-md);
            padding: 16px;
            border: 1px solid var(--line);
            box-shadow: var(--shadow);
            transition: transform 0.2s ease, box-shadow 0.2s ease;
            animation: floatIn 0.45s ease both;
        }
        .card:hover {
            transform: translateY(-2px);
            box-shadow: 0 20px 40px rgba(31, 42, 46, 0.13);
        }
        .kpi .label {
            color: var(--muted);
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 8px;
            font-weight: 600;
        }
        .value {
            font-size: clamp(1.15rem, 1.6vw, 1.65rem);
            font-weight: 700;
            font-family: 'IBM Plex Mono', monospace;
            color: var(--ink);
        }
        .subtext {
            font-size: 0.77rem;
            color: var(--muted);
            margin-top: 7px;
        }
        .positive { color: var(--success); }
        .negative { color: var(--danger); }
        .neutral { color: var(--muted); }

        .charts-panel {
            grid-column: span 8;
            display: grid;
            gap: 14px;
        }
        .analytics-panel {
            grid-column: span 4;
            display: grid;
            gap: 14px;
        }

        .panel-title {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            margin-bottom: 12px;
        }
        .panel-title h2 {
            font-size: 0.95rem;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            color: var(--accent);
        }
        .panel-title span {
            color: var(--muted);
            font-family: 'IBM Plex Mono', monospace;
            font-size: 0.72rem;
        }

        .chart-wrap {
            background: var(--surface-soft);
            border: 1px solid var(--line);
            border-radius: 12px;
            padding: 10px;
        }
        .chart-wrap canvas {
            width: 100%;
            height: 220px;
            display: block;
        }
        .chart-wrap.small canvas {
            height: 130px;
        }

        .section-card {
            background: var(--surface);
            border-radius: var(--radius-md);
            padding: 16px;
            border: 1px solid var(--line);
            box-shadow: var(--shadow);
        }

        .analytics-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
        }
        .metric-box {
            border: 1px solid var(--line);
            border-radius: 10px;
            background: var(--surface-soft);
            padding: 10px;
        }
        .metric-box .k {
            font-size: 0.7rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: var(--muted);
            margin-bottom: 5px;
        }
        .metric-box .v {
            font-family: 'IBM Plex Mono', monospace;
            font-weight: 700;
            font-size: 0.98rem;
        }

        .breakdown-list {
            display: grid;
            gap: 8px;
        }
        .breakdown-item {
            display: grid;
            grid-template-columns: 86px 1fr auto;
            align-items: center;
            gap: 8px;
            font-size: 0.78rem;
        }
        .bar {
            width: 100%;
            height: 8px;
            border-radius: 999px;
            background: #e8eeeb;
            overflow: hidden;
        }
        .bar > span {
            display: block;
            height: 100%;
            width: 0%;
            background: linear-gradient(90deg, var(--accent), #36b8a8);
        }

        .lower-grid {
            display: grid;
            grid-template-columns: repeat(12, minmax(0, 1fr));
            gap: 14px;
            margin-top: 14px;
        }
        .module-panel {
            grid-column: span 5;
        }
        .regime-panel {
            grid-column: span 3;
        }
        .trades-panel {
            grid-column: span 12;
        }

        .modules-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
        }
        .module-card {
            background: var(--surface-soft);
            border: 1px solid var(--line);
            border-radius: 10px;
            padding: 10px;
            text-align: left;
        }
        .module-card.active {
            border-color: rgba(15, 118, 110, 0.38);
            background: rgba(15, 118, 110, 0.10);
        }
        .module-card.disabled {
            opacity: 0.56;
        }
        .module-name {
            font-weight: 600;
            margin-bottom: 5px;
        }
        .module-stat {
            font-size: 0.74rem;
            color: var(--muted);
            margin: 2px 0;
        }

        .regime-table {
            width: 100%;
            border-collapse: collapse;
            font-family: 'IBM Plex Mono', monospace;
            font-size: 0.75rem;
        }
        .regime-table th,
        .regime-table td {
            text-align: left;
            border-bottom: 1px solid var(--line);
            padding: 8px 6px;
        }
        .regime-table th {
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: var(--muted);
            font-size: 0.68rem;
        }

        .trades-table {
            background: var(--surface);
            border-radius: var(--radius-md);
            overflow: hidden;
            border: 1px solid var(--line);
            box-shadow: var(--shadow);
        }
        .trades-table table {
            width: 100%;
            border-collapse: collapse;
            font-family: 'IBM Plex Mono', monospace;
        }
        .trades-table th {
            background: rgba(15, 118, 110, 0.12);
            color: var(--ink);
            padding: 11px 12px;
            text-align: left;
            font-weight: 600;
            font-size: 0.67rem;
            letter-spacing: 0.06em;
            text-transform: uppercase;
        }
        .trades-table td {
            padding: 10px 12px;
            border-bottom: 1px solid var(--line);
            font-size: 0.76rem;
        }
        .trades-table tr:hover {
            background: rgba(31, 42, 46, 0.04);
        }
        .long-badge,
        .short-badge {
            padding: 4px 8px;
            border-radius: 6px;
            font-size: 0.8em;
            font-weight: 600;
            display: inline-block;
        }
        .long-badge {
            background: rgba(22, 122, 82, 0.17);
            color: var(--success);
        }
        .short-badge {
            background: rgba(180, 42, 42, 0.15);
            color: var(--danger);
        }
        .loading {
            text-align: center;
            padding: 18px;
            color: var(--muted);
            font-size: 0.84rem;
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        @keyframes floatIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .spinner {
            display: inline-block;
            width: 28px;
            height: 28px;
            border: 3px solid rgba(31, 42, 46, 0.15);
            border-radius: 50%;
            border-top-color: var(--accent);
            animation: spin 1s linear infinite;
        }

        .empty-note {
            color: var(--muted);
            font-size: 0.8rem;
            text-align: center;
            padding: 16px;
        }

        .action-bar {
            display: flex;
            gap: 10px;
            margin-top: 16px;
            padding-top: 12px;
            border-top: 1px solid var(--line);
        }
        .action-btn {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 7px 14px;
            border-radius: 8px;
            border: 1px solid var(--line);
            background: var(--surface-soft);
            color: var(--ink);
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        .action-btn:hover {
            background: var(--surface);
            border-color: var(--accent);
            color: var(--accent);
        }

        .health-row {
            display: flex;
            gap: 8px;
            align-items: center;
            font-size: 0.72rem;
            font-family: 'IBM Plex Mono', monospace;
            color: var(--muted);
            margin-top: 6px;
        }
        .health-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--success);
            animation: pulse 1.5s ease-in-out infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.4; }
        }

        @media (max-width: 1180px) {
            .kpi-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
            .charts-panel { grid-column: span 12; }
            .analytics-panel { grid-column: span 12; }
            .module-panel { grid-column: span 7; }
            .regime-panel { grid-column: span 5; }
        }

        @media (max-width: 900px) {
            .masthead { grid-template-columns: 1fr; }
            .status-grid { grid-template-columns: 1fr; }
            .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .module-panel,
            .regime-panel,
            .trades-panel { grid-column: span 12; }
            .modules-grid { grid-template-columns: 1fr; }
            .trades-table {
                overflow-x: auto;
            }
            .trades-table table {
                min-width: 980px;
            }
        }

        @media (max-width: 560px) {
            .kpi-grid { grid-template-columns: 1fr; }
            body { padding: 12px; }
            .masthead,
            .card,
            .section-card,
            .trades-table { border-radius: 12px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <section class="masthead">
            <div class="title-wrap">
                <div class="eyebrow">Trading Operations Dashboard</div>
                <h1>Binance Research Bot Control Room</h1>
                <p class="subtitle">Execution quality, risk posture, and strategy diagnostics in one live view.</p>
            </div>
            <div class="status-grid">
                <div class="status-chip">
                    <span class="label">Status</span>
                    <span class="status-badge" id="status-badge">Connecting</span>
                </div>
                <div class="status-chip">
                    <span class="label">Current Tick</span>
                    <span class="content" id="current-tick">--</span>
                </div>
                <div class="status-chip">
                    <span class="label">Last Update</span>
                    <span class="content" id="last-update">--</span>
                </div>
                <div class="status-chip">
                    <span class="label">Environment</span>
                    <span class="content" id="environment">paper</span>
                </div>
                <div class="status-chip" style="grid-column: span 2;">
                    <span class="label">Data Source</span>
                    <span class="content" id="data-source">binance market snapshots</span>
                </div>
                <div class="status-chip">
                    <span class="label">Market Price</span>
                    <span class="content" id="market-price">--</span>
                </div>
            </div>
            <div class="action-bar" style="grid-column: span 2;">
                <button class="action-btn" onclick="exportCsv()">📊 Export CSV</button>
                <button class="action-btn" onclick="exportJson()">📄 Export JSON</button>
                <div class="health-row" style="margin-left: auto;">
                    <div class="health-dot"></div>
                    <span id="latency-info">API latency: --ms</span>
                </div>
            </div>
        </section>

        <section class="main-grid">
            <div class="kpi-grid">
            <div class="card kpi">
                <div class="label">Current Equity</div>
                <div class="value" id="equity">$0.00</div>
                <div class="subtext" id="equity-start">Starting: $0.00</div>
            </div>
            <div class="card kpi">
                <div class="label">Daily P&L</div>
                <div class="value" id="daily-pnl">$0.00</div>
                <div class="subtext" id="daily-pnl-pct">0%</div>
            </div>
            <div class="card kpi">
                <div class="label">Open Position</div>
                <div class="value" id="open-position">None</div>
                <div class="subtext" id="position-info">Waiting for signal...</div>
            </div>
            <div class="card kpi">
                <div class="label">Win Rate</div>
                <div class="value" id="win-rate">0%</div>
                <div class="subtext" id="trade-count">0 trades</div>
            </div>
            <div class="card kpi">
                <div class="label">Profit Factor</div>
                <div class="value" id="profit-factor">0.00</div>
                <div class="subtext">Gross wins / losses</div>
            </div>
            <div class="card kpi">
                <div class="label">Average R</div>
                <div class="value" id="avg-r">0.00R</div>
                <div class="subtext" id="sortino">Sortino: 0.00</div>
            </div>
            </div>

            <div class="charts-panel">
                <div class="section-card">
                    <div class="panel-title">
                        <h2>Equity Curve</h2>
                        <span id="equity-points">0 points</span>
                    </div>
                    <div class="chart-wrap">
                        <canvas id="equity-canvas" width="1000" height="260"></canvas>
                    </div>
                </div>
                <div class="section-card">
                    <div class="panel-title">
                        <h2>Market Candles</h2>
                        <span id="market-info">BTCUSDT • 5m</span>
                    </div>
                    <div class="chart-wrap small">
                        <div id="lightweight-chart" style="width:100%; height:160px;"></div>
                        <canvas id="candle-canvas" width="1000" height="160" style="display:none;"></canvas>
                    </div>
                </div>
                <div class="section-card">
                    <div class="panel-title">
                        <h2>Drawdown Profile</h2>
                        <span id="max-dd">Max DD: 0.00%</span>
                    </div>
                    <div class="chart-wrap small">
                        <canvas id="dd-canvas" width="1000" height="160"></canvas>
                    </div>
                </div>
            </div>

            <div class="analytics-panel">
                <div class="section-card">
                    <div class="panel-title">
                        <h2>Risk Diagnostics</h2>
                        <span>Live trade-level</span>
                    </div>
                    <div class="analytics-grid">
                        <div class="metric-box">
                            <div class="k">Sharpe</div>
                            <div class="v" id="sharpe">0.00</div>
                        </div>
                        <div class="metric-box">
                            <div class="k">Sortino</div>
                            <div class="v" id="sortino-main">0.00</div>
                        </div>
                        <div class="metric-box">
                            <div class="k">Expectancy</div>
                            <div class="v" id="expectancy">$0.00</div>
                        </div>
                        <div class="metric-box">
                            <div class="k">Avg Win / Loss</div>
                            <div class="v" id="avg-win-loss">$0.00/$0.00</div>
                        </div>
                        <div class="metric-box">
                            <div class="k">Win / Loss Streak</div>
                            <div class="v" id="streaks">0 / 0</div>
                        </div>
                        <div class="metric-box">
                            <div class="k">Trades</div>
                            <div class="v" id="total-trades">0</div>
                        </div>
                    </div>
                </div>
                <div class="section-card">
                    <div class="panel-title">
                        <h2>Exit Type Breakdown</h2>
                        <span id="exit-total">0 closed</span>
                    </div>
                    <div class="breakdown-list" id="exit-breakdown"></div>
                </div>
            </div>
        </section>

        <section class="lower-grid">
            <div class="section-card module-panel">
                <div class="panel-title">
                    <h2>Module Performance</h2>
                    <span>Adaptive scorecard</span>
                </div>
                <div class="modules-grid" id="modules-grid">
                    <div class="loading"><span class="spinner"></span></div>
                </div>
            </div>

            <div class="section-card regime-panel">
                <div class="panel-title">
                    <h2>Win Rate by Regime</h2>
                    <span>Live trades</span>
                </div>
                <table class="regime-table">
                    <thead>
                        <tr>
                            <th>Regime</th>
                            <th>Trades</th>
                            <th>Win %</th>
                        </tr>
                    </thead>
                    <tbody id="regime-body">
                        <tr><td colspan="3" class="empty-note">No regime data yet</td></tr>
                    </tbody>
                </table>
            </div>

            <div class="trades-table trades-panel">
            <div class="panel-title" style="padding: 14px 14px 6px 14px; margin-bottom: 0;">
                <h2 style="margin: 0;">Recent Trades</h2>
                <span>Most recent 20</span>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Symbol</th>
                        <th>Direction</th>
                        <th>Module</th>
                        <th>Regime</th>
                        <th>Entry</th>
                        <th>Exit</th>
                        <th>Reason</th>
                        <th>P&L</th>
                        <th>R Multiple</th>
                    </tr>
                </thead>
                <tbody id="trades-tbody">
                    <tr>
                        <td colspan="10" style="text-align: center; padding: 30px; color: #999;">No trades yet</td>
                    </tr>
                </tbody>
            </table>
            </div>
        </section>
    </div>

    <script>
        const UPDATE_INTERVAL = 500;
        let lastUpdateMs = Date.now();

        function fmtCurrency(v) {
            const n = Number.isFinite(v) ? v : 0;
            return `$${n.toFixed(2)}`;
        }

        function fmtPct(v) {
            const n = Number.isFinite(v) ? v : 0;
            return `${n.toFixed(2)}%`;
        }

        function calcStd(values) {
            if (!values || values.length < 2) return 0;
            const mean = values.reduce((a, b) => a + b, 0) / values.length;
            const variance = values.reduce((acc, x) => acc + (x - mean) * (x - mean), 0) / (values.length - 1);
            return Math.sqrt(Math.max(variance, 0));
        }

        function exportCsv() {
            const data = document.getElementById('state-data') ? JSON.parse(document.getElementById('state-data').textContent) : null;
            if (!data || !data.recent_trades) { alert('No data to export'); return; }
            const trades = data.recent_trades;
            let csv = 'Symbol,Direction,Entry,Exit,PnL,RMultiple,Module,Regime,CloseReason,Time\\n';
            for (const t of trades) {
                csv += `${t.symbol},${t.direction},${t.entry},${t.exit || ''},${t.pnl},${t.r_multiple || ''},${t.strategy_module || ''},${t.regime || ''},${t.close_reason || ''},${t.timestamp}\\n`;
            }
            const link = document.createElement('a');
            link.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
            link.download = `trades_${new Date().toISOString().slice(0, 10)}.csv`;
            link.click();
        }

        function exportJson() {
            const data = document.getElementById('state-data') ? JSON.parse(document.getElementById('state-data').textContent) : null;
            if (!data) { alert('No data to export'); return; }
            const link = document.createElement('a');
            link.href = 'data:application/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(data, null, 2));
            link.download = `dashboard_${new Date().toISOString().slice(0, 10)}.json`;
            link.click();
        }

        function drawLineChart(canvasId, values, opts = {}) {
            const canvas = document.getElementById(canvasId);
            if (!canvas) return;
            const ctx = canvas.getContext('2d');
            const w = canvas.width;
            const h = canvas.height;
            ctx.clearRect(0, 0, w, h);

            if (!values || values.length < 2) {
                ctx.fillStyle = '#7a878d';
                ctx.font = '14px IBM Plex Mono';
                ctx.fillText('Waiting for enough data points...', 20, h / 2);
                return;
            }

            let min = Math.min(...values);
            let max = Math.max(...values);
            if (Math.abs(max - min) < 1e-9) {
                max += 1;
                min -= 1;
            }

            const left = 48;
            const right = 16;
            const top = 16;
            const bottom = 28;
            const chartW = w - left - right;
            const chartH = h - top - bottom;

            ctx.strokeStyle = 'rgba(22, 43, 49, 0.12)';
            ctx.lineWidth = 1;
            for (let i = 0; i < 5; i++) {
                const y = top + (chartH * i) / 4;
                ctx.beginPath();
                ctx.moveTo(left, y);
                ctx.lineTo(w - right, y);
                ctx.stroke();
            }

            ctx.fillStyle = '#6a767c';
            ctx.font = '11px IBM Plex Mono';
            ctx.fillText(min.toFixed(2), 6, h - 10);
            ctx.fillText(max.toFixed(2), 6, top + 10);

            const points = values.map((v, i) => {
                const x = left + (i / (values.length - 1)) * chartW;
                const y = top + ((max - v) / (max - min)) * chartH;
                return { x, y };
            });

            if (opts.fill) {
                const gradient = ctx.createLinearGradient(0, top, 0, h);
                gradient.addColorStop(0, opts.fillTop || 'rgba(15, 118, 110, 0.25)');
                gradient.addColorStop(1, 'rgba(15, 118, 110, 0.02)');
                ctx.beginPath();
                ctx.moveTo(points[0].x, h - bottom);
                points.forEach(p => ctx.lineTo(p.x, p.y));
                ctx.lineTo(points[points.length - 1].x, h - bottom);
                ctx.closePath();
                ctx.fillStyle = gradient;
                ctx.fill();
            }

            ctx.beginPath();
            points.forEach((p, i) => {
                if (i === 0) ctx.moveTo(p.x, p.y);
                else ctx.lineTo(p.x, p.y);
            });
            ctx.strokeStyle = opts.stroke || '#0f766e';
            ctx.lineWidth = 2.2;
            ctx.stroke();

            const latest = points[points.length - 1];
            ctx.beginPath();
            ctx.arc(latest.x, latest.y, 3.3, 0, Math.PI * 2);
            ctx.fillStyle = opts.stroke || '#0f766e';
            ctx.fill();
        }

        function drawCandles(canvasId, candles) {
            const canvas = document.getElementById(canvasId);
            if (!canvas) return;
            const ctx = canvas.getContext('2d');
            const w = canvas.width;
            const h = canvas.height;
            ctx.clearRect(0, 0, w, h);
            if (!candles || candles.length === 0) {
                ctx.fillStyle = '#7a878d';
                ctx.font = '13px IBM Plex Mono';
                ctx.fillText('No market data', 20, h/2);
                return;
            }
            const highs = candles.map(c => c.high);
            const lows = candles.map(c => c.low);
            const max = Math.max(...highs);
            const min = Math.min(...lows);
            const left = 40;
            const right = 16;
            const top = 10;
            const bottom = 18;
            const chartW = w - left - right;
            const chartH = h - top - bottom;
            const n = candles.length;
            const candleW = Math.max(2, chartW / n * 0.7);
            for (let i = 0; i < n; i++) {
                const c = candles[i];
                const xCenter = left + (i + 0.5) * (chartW / n);
                const yOpen = top + ((max - c.open) / (max - min)) * chartH;
                const yClose = top + ((max - c.close) / (max - min)) * chartH;
                const yHigh = top + ((max - c.high) / (max - min)) * chartH;
                const yLow = top + ((max - c.low) / (max - min)) * chartH;
                const isUp = c.close >= c.open;
                ctx.strokeStyle = isUp ? '#167a52' : '#b42a2a';
                ctx.fillStyle = isUp ? 'rgba(22,122,82,0.18)' : 'rgba(180,42,42,0.12)';
                // wick
                ctx.beginPath();
                ctx.moveTo(xCenter, yHigh);
                ctx.lineTo(xCenter, yLow);
                ctx.lineWidth = 1;
                ctx.stroke();
                // body
                const bodyTop = Math.min(yOpen, yClose);
                const bodyH = Math.max(1, Math.abs(yClose - yOpen));
                ctx.fillRect(xCenter - candleW/2, bodyTop, candleW, bodyH);
                ctx.strokeRect(xCenter - candleW/2, bodyTop, candleW, bodyH);
            }
            // last price marker
            const last = candles[candles.length -1];
            const lastY = top + ((max - last.close) / (max - min)) * chartH;
            ctx.fillStyle = '#0f766e';
            ctx.fillRect(w - 60, lastY - 8, 56, 16);
            ctx.fillStyle = '#fff';
            ctx.font = '12px IBM Plex Mono';
            ctx.fillText(last.close.toFixed(2), w - 56, lastY + 4);
        }

        // load Socket.IO client and Lightweight-Charts dynamically
        function loadScript(src) {
            return new Promise((resolve, reject) => {
                const s = document.createElement('script');
                s.src = src;
                s.onload = () => resolve();
                s.onerror = (e) => reject(e);
                document.head.appendChild(s);
            });
        }

        let lwChart = null;
        let candleSeries = null;
        async function setupLiveChart() {
            try {
                await loadScript('https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.4/dist/lightweight-charts.standalone.production.js');
                await loadScript('https://cdn.socket.io/4.6.1/socket.io.min.js');
            } catch (e) {
                console.warn('Failed to load external libs, falling back to canvas candles');
                return;
            }
            try {
                const container = document.getElementById('lightweight-chart');
                if (!container) return;
                lwChart = LightweightCharts.createChart(container, { layout: { background: { color: 'transparent' }, textColor: '#1d292d' }, width: container.clientWidth, height: 160 });
                candleSeries = lwChart.addCandlestickSeries();

                // fetch initial market data
                const res = await fetch('/api/market?symbol=BTCUSDT&limit=200');
                if (res.ok) {
                    const payload = await res.json();
                    const data = (payload.candles || []).map(c => ({ time: Math.floor(Number(c.time)/1000), open: c.open, high: c.high, low: c.low, close: c.close }));
                    if (data.length) candleSeries.setData(data);
                }

                // connect Socket.IO
                const socket = window.io();
                // optional price line series for tick updates
                let priceSeries = null;
                try {
                    priceSeries = lwChart.addLineSeries({ color: '#0f766e', lineWidth: 1 });
                } catch (e) {
                    priceSeries = null;
                }
                socket.on('market_tick', tick => {
                    try {
                        const item = { time: Math.floor(Number(tick.time)/1000), open: tick.open, high: tick.high, low: tick.low, close: tick.close };
                        if (candleSeries) candleSeries.update(item);
                        // also update mini price chip
                        document.getElementById('market-price').textContent = `$${Number(tick.close).toFixed(2)}`;
                        if (priceSeries) priceSeries.update({ time: Math.floor(Number(tick.time)/1000), value: Number(tick.close) });
                    } catch (e) { }
                });
                socket.on('trade_tick', tick => {
                    try {
                        // tick: {time, price, qty}
                        if (tick && tick.price) {
                            const ts = Math.floor(Number(tick.time)/1000) || Math.floor(Date.now()/1000);
                            document.getElementById('market-price').textContent = `$${Number(tick.price).toFixed(2)}`;
                            if (priceSeries) priceSeries.update({ time: ts, value: Number(tick.price) });
                        }
                    } catch (e) { }
                });
            } catch (e) {
                console.warn('Live chart setup failed', e);
            }
        }

        function computeTradeAnalytics(trades) {
            const pnl = trades.map(t => Number(t.pnl || 0));
            const wins = pnl.filter(x => x > 0);
            const losses = pnl.filter(x => x < 0);
            const total = pnl.length;
            const expectancy = total ? pnl.reduce((a, b) => a + b, 0) / total : 0;
            const grossProfit = wins.reduce((a, b) => a + b, 0);
            const grossLoss = Math.abs(losses.reduce((a, b) => a + b, 0));
            const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : (grossProfit > 0 ? 999 : 0);

            const returns = trades.map(t => Number(t.r_multiple || 0));
            const meanRet = returns.length ? returns.reduce((a, b) => a + b, 0) / returns.length : 0;
            const stdRet = calcStd(returns);
            const sharpe = stdRet > 0 ? (meanRet / stdRet) * Math.sqrt(returns.length) : 0;

            const downside = returns.filter(x => x < 0);
            const downsideStd = calcStd(downside.length > 1 ? downside : [0, 0]);
            const sortino = downsideStd > 0 ? (meanRet / downsideStd) * Math.sqrt(returns.length) : 0;

            let winStreak = 0;
            let lossStreak = 0;
            let curWin = 0;
            let curLoss = 0;
            for (const p of pnl.slice().reverse()) {
                if (p > 0) {
                    curWin += 1;
                    curLoss = 0;
                } else if (p < 0) {
                    curLoss += 1;
                    curWin = 0;
                } else {
                    curWin = 0;
                    curLoss = 0;
                }
                winStreak = Math.max(winStreak, curWin);
                lossStreak = Math.max(lossStreak, curLoss);
            }

            return {
                total,
                expectancy,
                avgWin: wins.length ? wins.reduce((a, b) => a + b, 0) / wins.length : 0,
                avgLoss: losses.length ? losses.reduce((a, b) => a + b, 0) / losses.length : 0,
                profitFactor,
                sharpe,
                sortino,
                winStreak,
                lossStreak,
            };
        }

        function computeRegimeTable(trades) {
            const agg = {};
            for (const t of trades) {
                const regime = (t.regime || 'UNKNOWN').toString();
                if (!agg[regime]) agg[regime] = { trades: 0, wins: 0 };
                agg[regime].trades += 1;
                if ((t.pnl || 0) > 0) agg[regime].wins += 1;
            }
            return Object.entries(agg)
                .map(([regime, v]) => ({ regime, trades: v.trades, winRate: v.trades ? (v.wins / v.trades) * 100 : 0 }))
                .sort((a, b) => b.trades - a.trades);
        }

        function computeExitBreakdown(trades) {
            const agg = {};
            for (const t of trades) {
                const k = (t.close_reason || 'UNKNOWN').toString();
                agg[k] = (agg[k] || 0) + 1;
            }
            const total = trades.length;
            const rows = Object.entries(agg).map(([k, v]) => ({ reason: k, count: v, pct: total ? (v / total) * 100 : 0 }));
            rows.sort((a, b) => b.count - a.count);
            return { total, rows };
        }

        async function updateDashboard() {
            const now = Date.now();
            const latency = now - lastUpdateMs;
            lastUpdateMs = now;
            
            try {
                const response = await fetch('/api/state');
                const data = await response.json();
                document.getElementById('state-data').textContent = JSON.stringify(data);

                const status = data.status || 'unknown';
                const badge = document.getElementById('status-badge');
                badge.textContent = status.toUpperCase();
                badge.className = `status-badge ${status}`;

                const tick = data.current_tick ? new Date(data.current_tick).toLocaleString() : '--';
                document.getElementById('current-tick').textContent = tick;
                document.getElementById('last-update').textContent = data.last_update_utc ? new Date(data.last_update_utc).toLocaleTimeString() : '--';
                document.getElementById('environment').textContent = (data.environment || 'paper').toString();
                document.getElementById('data-source').textContent = (data.data_source || 'binance market snapshots').toString();
                document.getElementById('latency-info').textContent = `API latency: ${Math.round(latency)}ms`;

                document.getElementById('equity').textContent = `$${(data.equity || 0).toFixed(2)}`;
                const startEquity = data.starting_equity || data.equity || 0;
                document.getElementById('equity-start').textContent = `Starting: $${startEquity.toFixed(2)}`;

                const dailyPnl = data.daily_pnl || 0;
                const pnlElem = document.getElementById('daily-pnl');
                pnlElem.textContent = `$${dailyPnl.toFixed(2)}`;
                pnlElem.className = dailyPnl >= 0 ? 'value positive' : 'value negative';

                const pnlPct = startEquity > 0 ? (dailyPnl / startEquity * 100) : 0;
                document.getElementById('daily-pnl-pct').textContent = `${pnlPct.toFixed(2)}%`;

                const pos = data.open_position;
                if (pos) {
                    document.getElementById('open-position').textContent = 
                        pos.direction === 'LONG' ? 'LONG' : 'SHORT';
                    document.getElementById('position-info').textContent = 
                        `${pos.symbol} @ $${pos.entry.toFixed(4)}`;
                } else {
                    document.getElementById('open-position').textContent = 'None';
                    document.getElementById('position-info').textContent = 'Waiting for signal...';
                }

                const stats = data.trading_stats || {};
                const winRate = stats.total_trades > 0 ? (stats.winning_trades / stats.total_trades * 100) : 0;
                document.getElementById('win-rate').textContent = `${winRate.toFixed(1)}%`;
                document.getElementById('trade-count').textContent = 
                    `${stats.total_trades} trades (${stats.winning_trades}W / ${stats.losing_trades}L)`;
                document.getElementById('avg-r').textContent = `${(stats.avg_r_multiple || 0).toFixed(2)}R`;

                const scores = data.module_scores || {};
                const modulesGrid = document.getElementById('modules-grid');
                if (Object.keys(scores).length > 0) {
                    modulesGrid.innerHTML = Object.entries(scores).map(([module, info]) => `
                        <div class="module-card ${info.active ? 'active' : 'disabled'}">
                            <div class="module-name">${module}</div>
                            <div class="module-stat">Win Rate: ${(Number(info.win_rate || 0) * 100).toFixed(1)}%</div>
                            <div class="module-stat">Trades: ${info.total_trades}</div>
                            <div class="module-stat">State: ${info.active ? 'active' : 'disabled'}</div>
                        </div>
                    `).join('');
                } else {
                    modulesGrid.innerHTML = '<div class="empty-note">No module data yet</div>';
                }

                const trades = data.recent_trades || [];
                const analytics = computeTradeAnalytics(trades);
                document.getElementById('profit-factor').textContent = analytics.profitFactor > 999 ? '999+' : analytics.profitFactor.toFixed(2);
                document.getElementById('sortino').textContent = `Sortino: ${analytics.sortino.toFixed(2)}`;
                document.getElementById('sharpe').textContent = analytics.sharpe.toFixed(2);
                document.getElementById('sortino-main').textContent = analytics.sortino.toFixed(2);
                document.getElementById('expectancy').textContent = fmtCurrency(analytics.expectancy);
                document.getElementById('avg-win-loss').textContent = `${fmtCurrency(analytics.avgWin)}/${fmtCurrency(analytics.avgLoss)}`;
                document.getElementById('streaks').textContent = `${analytics.winStreak} / ${analytics.lossStreak}`;
                document.getElementById('total-trades').textContent = String(analytics.total);

                const regimeRows = computeRegimeTable(trades);
                const regimeBody = document.getElementById('regime-body');
                if (regimeRows.length > 0) {
                    regimeBody.innerHTML = regimeRows.map(r => `
                        <tr>
                            <td>${r.regime}</td>
                            <td>${r.trades}</td>
                            <td>${r.winRate.toFixed(1)}%</td>
                        </tr>
                    `).join('');
                } else {
                    regimeBody.innerHTML = '<tr><td colspan="3" class="empty-note">No regime data yet</td></tr>';
                }

                const exit = computeExitBreakdown(trades);
                document.getElementById('exit-total').textContent = `${exit.total} closed`;
                const exitContainer = document.getElementById('exit-breakdown');
                if (exit.rows.length > 0) {
                    exitContainer.innerHTML = exit.rows.map(r => `
                        <div class="breakdown-item">
                            <span>${r.reason}</span>
                            <div class="bar"><span style="width:${r.pct.toFixed(1)}%"></span></div>
                            <span>${r.count}</span>
                        </div>
                    `).join('');
                } else {
                    exitContainer.innerHTML = '<div class="empty-note">No exits recorded yet</div>';
                }

                const history = data.equity_history || [];
                const eqVals = history.map(x => Number(x.equity || 0));
                document.getElementById('equity-points').textContent = `${eqVals.length} points`;
                drawLineChart('equity-canvas', eqVals, { stroke: '#0f766e', fill: true, fillTop: 'rgba(15, 118, 110, 0.24)' });

                let peak = -Infinity;
                const dd = [];
                for (const e of eqVals) {
                    peak = Math.max(peak, e);
                    const d = peak > 0 ? ((peak - e) / peak) * 100 : 0;
                    dd.push(d);
                }
                const maxDd = dd.length ? Math.max(...dd) : 0;
                document.getElementById('max-dd').textContent = `Max DD: ${maxDd.toFixed(2)}%`;
                drawLineChart('dd-canvas', dd, { stroke: '#b42a2a', fill: true, fillTop: 'rgba(180, 42, 42, 0.20)' });

                // fetch market candles
                try {
                    const mkRes = await fetch('/api/market?symbol=BTCUSDT&limit=80');
                    if (mkRes.ok) {
                        const mk = await mkRes.json();
                        const candles = (mk.candles || []).map(c => ({
                            time: Number(c.time),
                            open: Number(c.open),
                            high: Number(c.high),
                            low: Number(c.low),
                            close: Number(c.close),
                            volume: Number(c.volume)
                        }));
                        if (candles.length) {
                            document.getElementById('market-price').textContent = `$${candles[candles.length-1].close.toFixed(2)}`;
                            document.getElementById('market-info').textContent = `${mk.symbol} • latest`;
                            drawCandles('candle-canvas', candles.slice(-60));
                        }
                    }
                } catch (e) {
                    // ignore market fetch errors
                }

                const tbody = document.getElementById('trades-tbody');
                if (trades.length > 0) {
                    tbody.innerHTML = trades.slice(0, 20).map(trade => `
                        <tr>
                            <td>${new Date(trade.timestamp).toLocaleTimeString()}</td>
                            <td><strong>${trade.symbol}</strong></td>
                            <td><span class="${trade.direction === 'LONG' ? 'long-badge' : 'short-badge'}">${trade.direction}</span></td>
                            <td>${trade.strategy_module || '-'}</td>
                            <td>${trade.regime || '-'}</td>
                            <td>$${(trade.entry || 0).toFixed(4)}</td>
                            <td>${trade.exit ? '$' + trade.exit.toFixed(4) : '-'}</td>
                            <td>${trade.close_reason || '-'}</td>
                            <td><span class="${trade.pnl >= 0 ? 'positive' : 'negative'}">${trade.pnl >= 0 ? '+' : ''}${trade.pnl.toFixed(2)}</span></td>
                            <td>${(trade.r_multiple || 0).toFixed(2)}R</td>
                        </tr>
                    `).join('');
                } else {
                    tbody.innerHTML = '<tr><td colspan="10" style="text-align: center; padding: 24px; color: #9aa3a7;">No trades yet</td></tr>';
                }
            } catch (error) {
                console.error('Error updating dashboard:', error);
            }
        }

        updateDashboard();
        setInterval(updateDashboard, UPDATE_INTERVAL);
    </script>
    <div id="state-data" style="display:none;"></div>
</body>
</html>
        """
