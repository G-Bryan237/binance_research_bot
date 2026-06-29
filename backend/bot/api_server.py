from __future__ import annotations

import csv
import logging
import os
import time
from pathlib import Path
from typing import Any

from .state_store import BotStateStore

try:
    from dotenv import load_dotenv

    HAS_DOTENV = True
except Exception:
    HAS_DOTENV = False

try:
    from flask import Flask, jsonify, request, send_file

    HAS_FLASK = True
except Exception:
    HAS_FLASK = False

try:
    from flask_socketio import SocketIO, emit

    HAS_SOCKETIO = True
except Exception:
    HAS_SOCKETIO = False

LOG = logging.getLogger(__name__)


class BotApiServer:
    """Read-only API + realtime stream, sourced from shared SQLite state."""

    def __init__(
        self,
        *,
        db_path: str,
        host: str = "0.0.0.0",
        port: int = 5000,
        data_dir: str = "data",
        out_dir: str = "out",
        cors_origin: str = "*",
        event_poll_ms: int = 350,
        profile_id: str = "default",
        profile_name: str = "Default Profile",
        report_dir: str = "reports",
    ) -> None:
        self.db_path = db_path
        self.host = host
        self.port = port
        self.data_dir = Path(data_dir)
        self.out_dir = Path(out_dir)
        self.cors_origin = cors_origin
        self.event_poll_ms = max(100, int(event_poll_ms))
        self.profile_id = profile_id
        self.profile_name = profile_name
        self.report_dir = Path(report_dir)
        self.state_store = BotStateStore(db_path)

        self.app: Flask | None = None
        self.socketio: SocketIO | None = None

    def initialize(self) -> bool:
        if not HAS_FLASK:
            LOG.error("Flask is not installed. Install dependencies in requirements.txt")
            return False

        if HAS_DOTENV:
            load_dotenv()

        self.app = Flask(__name__)

        @self.app.after_request
        def apply_cors_headers(response):
            response.headers["Access-Control-Allow-Origin"] = self.cors_origin
            response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
            response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
            return response

        @self.app.route("/")
        def root():
            return jsonify(
                {
                    "service": "binance-research-bot-api",
                    "ok": True,
                    "profile_id": self.profile_id,
                    "profile_name": self.profile_name,
                    "docs": [
                        "/api/health",
                        "/api/state",
                        "/api/stats",
                        "/api/trades",
                        "/api/market",
                        "/api/profile",
                        "/api/reports",
                        "/api/reports/daily",
                        "/api/reports/weekly",
                    ],
                }
            )

        @self.app.route("/api/health")
        def health():
            state = self.state_store.get_state(limit_trades=1, limit_equity=1)
            return jsonify(
                {
                    "ok": True,
                    "service": "binance-research-bot-api",
                    "status": state.get("status", "unknown"),
                    "last_update_utc": state.get("last_update_utc"),
                    "db_path": self.db_path,
                }
            )

        @self.app.route("/api/state")
        def get_state():
            trades_limit = int(request.args.get("trades_limit") or 200)
            equity_limit = int(request.args.get("equity_limit") or 1200)
            return jsonify(self.state_store.get_state(limit_trades=trades_limit, limit_equity=equity_limit))

        @self.app.route("/api/stats")
        def get_stats():
            return jsonify(self.state_store.get_stats())

        @self.app.route("/api/trades")
        def get_trades():
            limit = int(request.args.get("limit") or 200)
            return jsonify({"trades": self.state_store.get_trades(limit=limit)})

        @self.app.route("/api/market")
        def get_market():
            symbol = (request.args.get("symbol") or "BTCUSDT").upper()
            limit = int(request.args.get("limit") or 120)
            candidates = [
                self.data_dir / f"{symbol}_SPOT_5m.csv",
                self.data_dir / f"{symbol}_SPOT_1m.csv",
                self.data_dir / f"{symbol}.csv",
            ]
            found = None
            for path in candidates:
                if path.exists():
                    found = path
                    break

            rows = []
            
            # Try local file first
            if found:
                try:
                    with found.open("r", encoding="utf-8") as fh:
                        reader = csv.DictReader(fh)
                        all_rows = list(reader)
                        tail = all_rows[-max(1, limit) :]
                        for row in tail:
                            try:
                                rows.append(
                                    {
                                        "time": int(row.get("open_time") or row.get("time") or 0),
                                        "open": float(row.get("open") or 0),
                                        "high": float(row.get("high") or 0),
                                        "low": float(row.get("low") or 0),
                                        "close": float(row.get("close") or 0),
                                        "volume": float(row.get("volume") or row.get("qty") or 0),
                                    }
                                )
                            except Exception:
                                continue
                    if rows:
                        return jsonify({"symbol": symbol, "candles": rows})
                except Exception as exc:
                    LOG.warning("Failed to read local market data for %s: %s", symbol, exc)

            # Fallback: fetch live data from Binance API
            try:
                import requests as req
                binance_url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=5m&limit={limit}"
                resp = req.get(binance_url, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    for candle in data:
                        rows.append({
                            "time": int(candle[0]),
                            "open": float(candle[1]),
                            "high": float(candle[2]),
                            "low": float(candle[3]),
                            "close": float(candle[4]),
                            "volume": float(candle[5]),
                        })
                    return jsonify({"symbol": symbol, "candles": rows, "source": "binance-live"})
            except Exception as exc:
                LOG.warning("Failed to fetch live market data for %s: %s", symbol, exc)

            if not rows:
                return (
                    jsonify({"error": "market data not found", "symbol": symbol}),
                    404,
                )

            return jsonify({"symbol": symbol, "candles": rows})


        @self.app.route("/api/profile")
        def get_profile():
            state = self.state_store.get_state(limit_trades=1, limit_equity=1)
            return jsonify(
                {
                    "profile_id": self.profile_id,
                    "profile_name": self.profile_name,
                    "status": state.get("status", "unknown"),
                    "starting_equity": state.get("starting_equity", 0),
                    "current_equity": state.get("equity", 0),
                    "daily_pnl": state.get("daily_pnl", 0),
                    "port": self.port,
                }
            )

        @self.app.route("/api/balance", methods=["GET", "POST", "OPTIONS"])
        def manage_balance():
            if request.method == "OPTIONS":
                return "", 204
            
            if request.method == "GET":
                state = self.state_store.get_state(limit_trades=1, limit_equity=1)
                return jsonify({
                    "current_equity": state.get("equity", 0),
                    "starting_equity": state.get("starting_equity", 0),
                    "daily_pnl": state.get("daily_pnl", 0),
                    "profile_id": self.profile_id,
                })
            
            # POST - adjust balance
            data = request.get_json() or {}
            new_balance = data.get("balance")
            adjust_starting = data.get("adjust_starting", False)
            
            if new_balance is None:
                return jsonify({"error": "balance is required"}), 400
            
            try:
                new_balance = float(new_balance)
            except (TypeError, ValueError):
                return jsonify({"error": "balance must be a number"}), 400
            
            if new_balance < 0:
                return jsonify({"error": "balance cannot be negative"}), 400
            
            result = self.state_store.adjust_balance(new_balance, adjust_starting=adjust_starting)
            return jsonify({
                "success": True,
                "profile_id": self.profile_id,
                **result,
            })

        @self.app.route("/api/reports")
        def list_reports():
            import json

            reports = {"daily": [], "weekly": []}
            report_path = self.report_dir / self.profile_id
            for period_type in ["daily", "weekly"]:
                if not report_path.exists():
                    continue
                pattern = f"{self.profile_id}_{period_type}_*.json"
                for path in sorted(report_path.glob(pattern), reverse=True)[:30]:
                    try:
                        with path.open("r", encoding="utf-8") as fh:
                            data = json.load(fh)
                        reports[period_type].append(
                            {
                                "filename": path.name,
                                "period_start": data.get("period_start"),
                                "period_end": data.get("period_end"),
                                "total_pnl": data.get("total_pnl"),
                                "pnl_pct": data.get("pnl_pct"),
                                "total_trades": data.get("total_trades"),
                                "win_rate": data.get("win_rate"),
                            }
                        )
                    except Exception:
                        continue
            return jsonify(reports)

        @self.app.route("/api/reports/daily")
        @self.app.route("/api/reports/daily/<date>")
        def get_daily_report(date: str = None):
            return self._get_report("daily", date)

        @self.app.route("/api/reports/weekly")
        @self.app.route("/api/reports/weekly/<date>")
        def get_weekly_report(date: str = None):
            return self._get_report("weekly", date)
        @self.app.route("/api/out/<path:name>")
        def get_out_file(name: str):
            path = self.out_dir / name
            if not path.exists() or not path.is_file():
                return jsonify({"error": "file not found"}), 404
            return send_file(str(path), as_attachment=True)

        if HAS_SOCKETIO:
            try:
                async_mode = os.getenv("BOT_SOCKETIO_ASYNC_MODE", "threading")
                self.socketio = SocketIO(
                    self.app,
                    cors_allowed_origins=self.cors_origin,
                    async_mode=async_mode,
                )

                @self.socketio.on("connect")
                def on_connect():
                    emit("state_update", self.state_store.get_state(limit_trades=80, limit_equity=300))
                    emit("stats_update", self.state_store.get_stats())

                self.socketio.start_background_task(self._event_forwarder)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("SocketIO unavailable, falling back to HTTP only: %s", exc)
                self.socketio = None

        LOG.info("API initialized at http://%s:%s", self.host, self.port)
        return True

    def _event_forwarder(self) -> None:
        if self.socketio is None:
            return
        last_event_id = self.state_store.get_latest_event_id()
        while True:
            try:
                events = self.state_store.get_events_after(last_event_id, limit=250)
                for event in events:
                    last_event_id = event["id"]
                    event_type = event.get("event_type") or "bot_event"
                    payload = event.get("payload") or {}
                    self.socketio.emit(event_type, payload)
                    self.socketio.emit("bot_event", event)
            except Exception as exc:  # noqa: BLE001
                LOG.debug("event forwarder error: %s", exc)
            time.sleep(self.event_poll_ms / 1000.0)


    def _get_report(self, period_type: str, date: str | None = None):
        import json

        report_path = self.report_dir / self.profile_id
        if date:
            filename = f"{self.profile_id}_{period_type}_{date}.json"
        else:
            pattern = f"{self.profile_id}_{period_type}_*.json"
            files = sorted(report_path.glob(pattern), reverse=True) if report_path.exists() else []
            if not files:
                return jsonify({"error": "no reports found"}), 404
            filename = files[0].name

        file_path = report_path / filename
        if not file_path.exists():
            return jsonify({"error": "report not found"}), 404

        with file_path.open("r", encoding="utf-8") as fh:
            return jsonify(json.load(fh))
    def run(self) -> None:
        if self.app is None:
            raise RuntimeError("API server not initialized")
        if self.socketio is not None:
            self.socketio.run(
                self.app,
                host=self.host,
                port=self.port,
                debug=False,
                allow_unsafe_werkzeug=True,
            )
            return
        self.app.run(host=self.host, port=self.port, debug=False, use_reloader=False)
