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
        state["equity_history"] = self.get_equity_history(limit=limit_equity)
        state["module_scores"] = self.get_module_scores()
        state["trading_stats"] = self.get_stats()
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
