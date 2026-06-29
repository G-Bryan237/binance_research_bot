from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


class BotStateStore:
    """SQLite-backed shared state for decoupled worker/API processes."""

    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()
        self._run_migrations()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS bot_runtime_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    status TEXT NOT NULL DEFAULT 'initializing',
                    current_tick TEXT,
                    last_update_utc TEXT,
                    environment TEXT NOT NULL DEFAULT 'paper',
                    data_source TEXT NOT NULL DEFAULT 'binance market snapshots',
                    update_interval_ms INTEGER NOT NULL DEFAULT 500,
                    equity REAL NOT NULL DEFAULT 0,
                    daily_pnl REAL NOT NULL DEFAULT 0,
                    starting_equity REAL NOT NULL DEFAULT 0,
                    open_position_json TEXT
                );

                CREATE TABLE IF NOT EXISTS trading_stats (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    total_trades INTEGER NOT NULL DEFAULT 0,
                    winning_trades INTEGER NOT NULL DEFAULT 0,
                    losing_trades INTEGER NOT NULL DEFAULT 0,
                    win_rate REAL NOT NULL DEFAULT 0,
                    avg_r_multiple REAL NOT NULL DEFAULT 0,
                    updated_at_utc TEXT
                );

                CREATE TABLE IF NOT EXISTS module_scores (
                    module TEXT PRIMARY KEY,
                    win_rate REAL NOT NULL,
                    total_trades INTEGER NOT NULL,
                    active INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS recent_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry REAL NOT NULL,
                    exit REAL NOT NULL,
                    pnl REAL NOT NULL,
                    r_multiple REAL NOT NULL,
                    strategy_module TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    close_reason TEXT NOT NULL,
                    partial_exit INTEGER NOT NULL,
                    profile_id TEXT NOT NULL DEFAULT 'default'
                );

                CREATE INDEX IF NOT EXISTS idx_recent_trades_id_desc
                ON recent_trades (id DESC);

                CREATE TABLE IF NOT EXISTS signal_journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_key TEXT NOT NULL UNIQUE,
                    generated_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry REAL NOT NULL,
                    stop REAL NOT NULL,
                    target_1 REAL NOT NULL,
                    target_2 REAL NOT NULL,
                    risk_multiple REAL NOT NULL,
                    regime TEXT NOT NULL,
                    strategy_module TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    strategy_reason TEXT NOT NULL,
                    quality_score REAL NOT NULL DEFAULT 0,
                    quality_label TEXT NOT NULL DEFAULT 'medium',
                    status TEXT NOT NULL DEFAULT 'GENERATED',
                    status_reason TEXT NOT NULL DEFAULT '',
                    opened_at_utc TEXT,
                    closed_at_utc TEXT,
                    exit_price REAL,
                    pnl REAL,
                    r_multiple REAL,
                    close_reason TEXT,
                    profile_id TEXT NOT NULL DEFAULT 'default'
                );

                CREATE INDEX IF NOT EXISTS idx_signal_journal_id_desc
                ON signal_journal (id DESC);

                CREATE INDEX IF NOT EXISTS idx_signal_journal_status
                ON signal_journal (status);

                CREATE INDEX IF NOT EXISTS idx_signal_journal_module
                ON signal_journal (strategy_module);
                CREATE TABLE IF NOT EXISTS equity_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    time TEXT NOT NULL,
                    equity REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_equity_history_id_desc
                ON equity_history (id DESC);

                CREATE TABLE IF NOT EXISTS bot_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at_utc TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_bot_events_id
                ON bot_events (id ASC);
                """
            )

    def _run_migrations(self) -> None:
        """Run all database migrations on startup."""
        self.migrate_profile_id_column()

    def initialize_defaults(
        self,
        *,
        starting_equity: float,
        environment: str = "paper",
        data_source: str = "binance market snapshots",
        update_interval_ms: int = 500,
    ) -> None:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO bot_runtime_state (
                    id, status, current_tick, last_update_utc, environment, data_source,
                    update_interval_ms, equity, daily_pnl, starting_equity, open_position_json
                ) VALUES (1, ?, NULL, ?, ?, ?, ?, ?, 0, ?, NULL)
                """,
                (
                    "initializing",
                    now,
                    environment,
                    data_source,
                    int(update_interval_ms),
                    float(starting_equity),
                    float(starting_equity),
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO trading_stats (
                    id, total_trades, winning_trades, losing_trades, win_rate, avg_r_multiple, updated_at_utc
                ) VALUES (1, 0, 0, 0, 0, 0, ?)
                """,
                (now,),
            )

    def _insert_event(self, conn: sqlite3.Connection, event_type: str, payload: Dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO bot_events (created_at_utc, event_type, payload_json)
            VALUES (?, ?, ?)
            """,
            (_now_iso(), event_type, json.dumps(payload)),
        )
        conn.execute(
            """
            DELETE FROM bot_events
            WHERE id NOT IN (
                SELECT id FROM bot_events ORDER BY id DESC LIMIT 5000
            )
            """
        )

    def update_state(self, **kwargs: Any) -> None:
        allowed_keys = {
            "status",
            "current_tick",
            "last_update_utc",
            "environment",
            "data_source",
            "update_interval_ms",
            "equity",
            "daily_pnl",
            "starting_equity",
            "open_position",
        }
        payload = {k: v for k, v in kwargs.items() if k in allowed_keys}
        if not payload:
            return

        if "last_update_utc" not in payload:
            payload["last_update_utc"] = _now_iso()

        column_map = {
            "status": "status",
            "current_tick": "current_tick",
            "last_update_utc": "last_update_utc",
            "environment": "environment",
            "data_source": "data_source",
            "update_interval_ms": "update_interval_ms",
            "equity": "equity",
            "daily_pnl": "daily_pnl",
            "starting_equity": "starting_equity",
            "open_position": "open_position_json",
        }

        updates: list[str] = []
        values: list[Any] = []
        for key, value in payload.items():
            col = column_map[key]
            updates.append(f"{col} = ?")
            if key == "open_position":
                values.append(None if value is None else json.dumps(value))
            else:
                values.append(value)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO bot_runtime_state (
                    id, status, last_update_utc, environment, data_source,
                    update_interval_ms, equity, daily_pnl, starting_equity, open_position_json
                ) VALUES (1, 'initializing', ?, 'paper', 'binance market snapshots', 500, 0, 0, 0, NULL)
                """,
                (_now_iso(),),
            )
            conn.execute(
                f"UPDATE bot_runtime_state SET {', '.join(updates)} WHERE id = 1",
                values,
            )

            if "equity" in payload:
                conn.execute(
                    "INSERT INTO equity_history (time, equity) VALUES (?, ?)",
                    (payload["last_update_utc"], float(payload["equity"])),
                )
                conn.execute(
                    """
                    DELETE FROM equity_history
                    WHERE id NOT IN (
                        SELECT id FROM equity_history ORDER BY id DESC LIMIT 2000
                    )
                    """
                )

            event_payload = {
                "status": payload.get("status"),
                "current_tick": payload.get("current_tick"),
                "last_update_utc": payload.get("last_update_utc"),
                "equity": payload.get("equity"),
                "daily_pnl": payload.get("daily_pnl"),
                "open_position": payload.get("open_position"),
            }
            self._insert_event(conn, "state_update", event_payload)

    def add_trade(self, trade_info: Dict[str, Any], profile_id: str = "default") -> None:
        row = {
            "timestamp": trade_info.get("timestamp") or _now_iso(),
            "symbol": str(trade_info.get("symbol") or ""),
            "direction": str(trade_info.get("direction") or ""),
            "entry": float(trade_info.get("entry") or 0.0),
            "exit": float(trade_info.get("exit") or 0.0),
            "pnl": float(trade_info.get("pnl") or 0.0),
            "r_multiple": float(trade_info.get("r_multiple") or 0.0),
            "strategy_module": str(trade_info.get("strategy_module") or ""),
            "regime": str(trade_info.get("regime") or ""),
            "close_reason": str(trade_info.get("close_reason") or ""),
            "partial_exit": 1 if bool(trade_info.get("partial_exit")) else 0,
            "profile_id": str(trade_info.get("profile_id") or profile_id),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO recent_trades (
                    timestamp, symbol, direction, entry, exit, pnl, r_multiple,
                    strategy_module, regime, close_reason, partial_exit, profile_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["timestamp"],
                    row["symbol"],
                    row["direction"],
                    row["entry"],
                    row["exit"],
                    row["pnl"],
                    row["r_multiple"],
                    row["strategy_module"],
                    row["regime"],
                    row["close_reason"],
                    row["partial_exit"],
                    row["profile_id"],
                ),
            )
            conn.execute(
                """
                DELETE FROM recent_trades
                WHERE id NOT IN (
                    SELECT id FROM recent_trades ORDER BY id DESC LIMIT 2000
                )
                """
            )
            payload = dict(row)
            payload["partial_exit"] = bool(payload["partial_exit"])
            self._insert_event(conn, "trade_added", payload)

    def add_signal(self, signal_info: Dict[str, Any], profile_id: str = "default") -> int:
        now = _now_iso()
        row = {
            "signal_key": str(signal_info.get("signal_key") or ""),
            "generated_at_utc": str(signal_info.get("generated_at_utc") or now),
            "updated_at_utc": now,
            "symbol": str(signal_info.get("symbol") or ""),
            "market": str(signal_info.get("market") or ""),
            "direction": str(signal_info.get("direction") or ""),
            "entry": float(signal_info.get("entry") or 0.0),
            "stop": float(signal_info.get("stop") or 0.0),
            "target_1": float(signal_info.get("target_1") or 0.0),
            "target_2": float(signal_info.get("target_2") or 0.0),
            "risk_multiple": float(signal_info.get("risk_multiple") or 0.0),
            "regime": str(signal_info.get("regime") or ""),
            "strategy_module": str(signal_info.get("strategy_module") or ""),
            "rationale": str(signal_info.get("rationale") or ""),
            "strategy_reason": str(signal_info.get("strategy_reason") or ""),
            "quality_score": float(signal_info.get("quality_score") or 0.0),
            "quality_label": str(signal_info.get("quality_label") or "medium"),
            "status": str(signal_info.get("status") or "GENERATED"),
            "status_reason": str(signal_info.get("status_reason") or "Signal generated by strategy module"),
            "profile_id": str(signal_info.get("profile_id") or profile_id),
        }
        if not row["signal_key"]:
            row["signal_key"] = "|".join(
                [
                    row["symbol"],
                    row["market"],
                    row["direction"],
                    row["strategy_module"],
                    str(round(row["entry"], 8)),
                    row["generated_at_utc"],
                ]
            )

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO signal_journal (
                    signal_key, generated_at_utc, updated_at_utc, symbol, market, direction,
                    entry, stop, target_1, target_2, risk_multiple, regime, strategy_module,
                    rationale, strategy_reason, quality_score, quality_label, status,
                    status_reason, profile_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["signal_key"],
                    row["generated_at_utc"],
                    row["updated_at_utc"],
                    row["symbol"],
                    row["market"],
                    row["direction"],
                    row["entry"],
                    row["stop"],
                    row["target_1"],
                    row["target_2"],
                    row["risk_multiple"],
                    row["regime"],
                    row["strategy_module"],
                    row["rationale"],
                    row["strategy_reason"],
                    row["quality_score"],
                    row["quality_label"],
                    row["status"],
                    row["status_reason"],
                    row["profile_id"],
                ),
            )
            existing = conn.execute(
                "SELECT id FROM signal_journal WHERE signal_key = ?",
                (row["signal_key"],),
            ).fetchone()
            signal_id = int(existing["id"]) if existing else 0
            conn.execute(
                """
                DELETE FROM signal_journal
                WHERE id NOT IN (
                    SELECT id FROM signal_journal ORDER BY id DESC LIMIT 5000
                )
                """
            )
            payload = {**row, "id": signal_id}
            self._insert_event(conn, "signal_added", payload)
            return signal_id

    def update_signal(self, signal_id: int | None, status: str, status_reason: str = "", **kwargs: Any) -> None:
        if signal_id is None or signal_id <= 0:
            return

        allowed = {
            "opened_at_utc",
            "closed_at_utc",
            "exit_price",
            "pnl",
            "r_multiple",
            "close_reason",
            "quality_score",
            "quality_label",
        }
        updates = ["status = ?", "status_reason = ?", "updated_at_utc = ?"]
        values: list[Any] = [status, status_reason, _now_iso()]
        for key, value in kwargs.items():
            if key not in allowed:
                continue
            updates.append(f"{key} = ?")
            values.append(value)
        values.append(int(signal_id))

        with self._connect() as conn:
            conn.execute(
                f"UPDATE signal_journal SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            row = conn.execute(
                "SELECT * FROM signal_journal WHERE id = ?",
                (int(signal_id),),
            ).fetchone()
            if row is not None:
                self._insert_event(conn, "signal_updated", self._signal_row_to_dict(row))

    def _signal_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": int(row["id"]),
            "signal_key": str(row["signal_key"]),
            "generated_at_utc": str(row["generated_at_utc"]),
            "updated_at_utc": str(row["updated_at_utc"]),
            "symbol": str(row["symbol"]),
            "market": str(row["market"]),
            "direction": str(row["direction"]),
            "entry": float(row["entry"]),
            "stop": float(row["stop"]),
            "target_1": float(row["target_1"]),
            "target_2": float(row["target_2"]),
            "risk_multiple": float(row["risk_multiple"]),
            "regime": str(row["regime"]),
            "strategy_module": str(row["strategy_module"]),
            "rationale": str(row["rationale"]),
            "strategy_reason": str(row["strategy_reason"]),
            "quality_score": float(row["quality_score"]),
            "quality_label": str(row["quality_label"]),
            "status": str(row["status"]),
            "status_reason": str(row["status_reason"]),
            "opened_at_utc": row["opened_at_utc"],
            "closed_at_utc": row["closed_at_utc"],
            "exit_price": None if row["exit_price"] is None else float(row["exit_price"]),
            "pnl": None if row["pnl"] is None else float(row["pnl"]),
            "r_multiple": None if row["r_multiple"] is None else float(row["r_multiple"]),
            "close_reason": row["close_reason"],
            "profile_id": str(row["profile_id"]) if row["profile_id"] else "default",
        }

    def get_signals(self, limit: int = 200) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(limit, 2000))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM signal_journal
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [self._signal_row_to_dict(row) for row in rows]

    def get_signal_stats(self) -> Dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT strategy_module, status, COUNT(*) AS count
                FROM signal_journal
                GROUP BY strategy_module, status
                """
            ).fetchall()

        terminal_statuses = {"SUCCEEDED", "FAILED"}
        opened_statuses = {"OPENED", "PENDING_ORDER", "SUCCEEDED", "FAILED"}
        blocked_statuses = {
            "BLOCKED_BY_RISK",
            "BLOCKED_BY_EXECUTION",
            "BLOCKED_BY_MTF",
            "BLOCKED_BY_FUNDING",
            "BLOCKED_BY_MODULE_SCORECARD",
            "NOT_SELECTED",
            "DUPLICATE",
            "EXPIRED",
        }

        totals = {
            "total_signals": 0,
            "opened_signals": 0,
            "blocked_signals": 0,
            "succeeded_signals": 0,
            "failed_signals": 0,
            "open_signals": 0,
        }
        by_module: Dict[str, Dict[str, Any]] = {}

        for row in rows:
            module = str(row["strategy_module"] or "UNKNOWN")
            status = str(row["status"] or "GENERATED")
            count = int(row["count"] or 0)
            mod = by_module.setdefault(
                module,
                {
                    "total_signals": 0,
                    "opened_signals": 0,
                    "blocked_signals": 0,
                    "succeeded_signals": 0,
                    "failed_signals": 0,
                    "open_signals": 0,
                    "success_rate": 0.0,
                    "open_rate": 0.0,
                },
            )
            totals["total_signals"] += count
            mod["total_signals"] += count
            if status in opened_statuses:
                totals["opened_signals"] += count
                mod["opened_signals"] += count
            if status in blocked_statuses or status.startswith("BLOCKED"):
                totals["blocked_signals"] += count
                mod["blocked_signals"] += count
            if status == "SUCCEEDED":
                totals["succeeded_signals"] += count
                mod["succeeded_signals"] += count
            if status == "FAILED":
                totals["failed_signals"] += count
                mod["failed_signals"] += count
            if status in {"OPENED", "PENDING_ORDER"}:
                totals["open_signals"] += count
                mod["open_signals"] += count

        resolved = totals["succeeded_signals"] + totals["failed_signals"]
        totals["success_rate"] = (totals["succeeded_signals"] / resolved) if resolved else 0.0
        totals["open_rate"] = (totals["opened_signals"] / totals["total_signals"]) if totals["total_signals"] else 0.0
        for mod in by_module.values():
            mod_resolved = mod["succeeded_signals"] + mod["failed_signals"]
            mod["success_rate"] = (mod["succeeded_signals"] / mod_resolved) if mod_resolved else 0.0
            mod["open_rate"] = (mod["opened_signals"] / mod["total_signals"]) if mod["total_signals"] else 0.0

        totals["by_module"] = by_module
        return totals
    def update_stats(self, total: int, wins: int, losses: int, avg_r: float) -> None:
        win_rate = (wins / total) if total > 0 else 0.0
        updated_at = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trading_stats (
                    id, total_trades, winning_trades, losing_trades, win_rate, avg_r_multiple, updated_at_utc
                ) VALUES (1, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    total_trades=excluded.total_trades,
                    winning_trades=excluded.winning_trades,
                    losing_trades=excluded.losing_trades,
                    win_rate=excluded.win_rate,
                    avg_r_multiple=excluded.avg_r_multiple,
                    updated_at_utc=excluded.updated_at_utc
                """,
                (int(total), int(wins), int(losses), float(win_rate), float(avg_r), updated_at),
            )
            self._insert_event(conn, "stats_update", self.get_stats())

    def update_module_scores(self, scores: Dict[str, Dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM module_scores")
            for module, data in scores.items():
                conn.execute(
                    """
                    INSERT INTO module_scores (module, win_rate, total_trades, active)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        module,
                        float(data.get("win_rate") or 0.0),
                        int(data.get("total_trades") or 0),
                        1 if bool(data.get("active", True)) else 0,
                    ),
                )
            self._insert_event(conn, "module_scores_update", self.get_module_scores())

    def get_stats(self) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM trading_stats WHERE id = 1").fetchone()
        if row is None:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "avg_r_multiple": 0.0,
            }
        return {
            "total_trades": int(row["total_trades"]),
            "winning_trades": int(row["winning_trades"]),
            "losing_trades": int(row["losing_trades"]),
            "win_rate": float(row["win_rate"]),
            "avg_r_multiple": float(row["avg_r_multiple"]),
        }

    def get_module_scores(self) -> Dict[str, Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT module, win_rate, total_trades, active FROM module_scores").fetchall()
        out: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            out[str(row["module"])] = {
                "win_rate": float(row["win_rate"]),
                "total_trades": int(row["total_trades"]),
                "active": bool(row["active"]),
            }
        return out

    def get_trades(self, limit: int = 200) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(limit, 2000))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT timestamp, symbol, direction, entry, exit, pnl, r_multiple,
                       strategy_module, regime, close_reason, partial_exit, profile_id
                FROM recent_trades
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "timestamp": str(row["timestamp"]),
                    "symbol": str(row["symbol"]),
                    "direction": str(row["direction"]),
                    "entry": float(row["entry"]),
                    "exit": float(row["exit"]),
                    "pnl": float(row["pnl"]),
                    "r_multiple": float(row["r_multiple"]),
                    "strategy_module": str(row["strategy_module"]),
                    "regime": str(row["regime"]),
                    "close_reason": str(row["close_reason"]),
                    "partial_exit": bool(row["partial_exit"]),
                    "profile_id": str(row["profile_id"]) if row["profile_id"] else "default",
                }
            )
        return out

    def get_equity_history(self, limit: int = 1200) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(limit, 10000))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT time, equity
                FROM equity_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        out = [{"time": str(row["time"]), "equity": float(row["equity"])} for row in rows]
        out.reverse()
        return out

    def get_state(self, *, limit_trades: int = 200, limit_equity: int = 1200) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM bot_runtime_state WHERE id = 1").fetchone()

        state: Dict[str, Any] = {
            "status": "initializing",
            "current_tick": None,
            "last_update_utc": None,
            "environment": "paper",
            "data_source": "binance market snapshots",
            "update_interval_ms": 500,
            "open_position": None,
            "equity": 0.0,
            "daily_pnl": 0.0,
            "starting_equity": 0.0,
        }

        if row is not None:
            state.update(
                {
                    "status": str(row["status"]),
                    "current_tick": row["current_tick"],
                    "last_update_utc": row["last_update_utc"],
                    "environment": str(row["environment"]),
                    "data_source": str(row["data_source"]),
                    "update_interval_ms": int(row["update_interval_ms"]),
                    "equity": float(row["equity"]),
                    "daily_pnl": float(row["daily_pnl"]),
                    "starting_equity": float(row["starting_equity"]),
                    "open_position": json.loads(row["open_position_json"]) if row["open_position_json"] else None,
                }
            )

        state["recent_trades"] = self.get_trades(limit=limit_trades)
        state["recent_signals"] = self.get_signals(limit=limit_trades)
        state["equity_history"] = self.get_equity_history(limit=limit_equity)
        state["module_scores"] = self.get_module_scores()
        state["trading_stats"] = self.get_stats()
        state["signal_stats"] = self.get_signal_stats()
        return state

    def get_latest_event_id(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM bot_events").fetchone()
        return int(row["max_id"]) if row else 0

    def get_events_after(self, after_id: int, limit: int = 200) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(limit, 1000))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at_utc, event_type, payload_json
                FROM bot_events
                WHERE id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (int(after_id), safe_limit),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            payload_raw = row["payload_json"]
            payload: Dict[str, Any]
            if payload_raw:
                payload = json.loads(payload_raw)
            else:
                payload = {}
            out.append(
                {
                    "id": int(row["id"]),
                    "created_at_utc": str(row["created_at_utc"]),
                    "event_type": str(row["event_type"]),
                    "payload": payload,
                }
            )
        return out

    def adjust_balance(self, new_balance: float, adjust_starting: bool = False) -> Dict[str, Any]:
        """
        Adjust the paper trading balance.
        
        Args:
            new_balance: The new balance to set
            adjust_starting: If True, also adjust starting_equity to match
            
        Returns:
            Dict with old and new balance values
        """
        with self._connect() as conn:
            row = conn.execute("SELECT equity, starting_equity FROM bot_runtime_state WHERE id = 1").fetchone()
            old_equity = float(row["equity"]) if row else 0.0
            old_starting = float(row["starting_equity"]) if row else 0.0
            
            new_equity = float(new_balance)
            new_starting = new_equity if adjust_starting else old_starting
            
            conn.execute(
                """
                UPDATE bot_runtime_state 
                SET equity = ?, starting_equity = ?, last_update_utc = ?
                WHERE id = 1
                """,
                (new_equity, new_starting, _now_iso()),
            )
            
            # Record in equity history
            conn.execute(
                "INSERT INTO equity_history (time, equity) VALUES (?, ?)",
                (_now_iso(), new_equity),
            )
            
            # Emit event
            self._insert_event(conn, "balance_adjusted", {
                "old_equity": old_equity,
                "new_equity": new_equity,
                "old_starting_equity": old_starting,
                "new_starting_equity": new_starting,
                "adjusted_at_utc": _now_iso(),
            })
        
        return {
            "old_equity": old_equity,
            "new_equity": new_equity,
            "old_starting_equity": old_starting,
            "new_starting_equity": new_starting,
        }

    def migrate_profile_id_column(self, default_profile_id: str = "default") -> None:
        """Add profile_id column to recent_trades if it doesn't exist."""
        with self._connect() as conn:
            # Check if column exists
            cursor = conn.execute("PRAGMA table_info(recent_trades)")
            columns = [row[1] for row in cursor.fetchall()]
            
            if "profile_id" not in columns:
                conn.execute(
                    f"ALTER TABLE recent_trades ADD COLUMN profile_id TEXT NOT NULL DEFAULT '{default_profile_id}'"
                )
