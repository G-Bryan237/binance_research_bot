"""
Daily and Weekly Performance Reporting Module.

Generates reports by:
- Profile (conservative vs aggressive)
- Strategy module (M1-M6)
- Time period (daily, weekly)
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Dict, List, Optional, Any

LOG = logging.getLogger(__name__)


@dataclass
class StrategyStats:
    module: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    avg_r_multiple: float
    best_trade_pnl: float
    worst_trade_pnl: float


@dataclass
class PeriodReport:
    profile_id: str
    profile_name: str
    period_type: str  # "daily" or "weekly"
    period_start: str
    period_end: str
    starting_equity: float
    ending_equity: float
    total_pnl: float
    pnl_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_r_multiple: float
    max_drawdown_pct: float
    strategy_breakdown: List[StrategyStats]
    generated_at: str


class ReportGenerator:
    """Generates daily and weekly performance reports from trade history."""

    def __init__(
        self,
        db_path: str,
        profile_id: str,
        profile_name: str,
        output_dir: str = "reports",
    ) -> None:
        self.db_path = Path(db_path)
        self.profile_id = profile_id
        self.profile_name = profile_name
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_trades_for_period(
        self, start_time: datetime, end_time: datetime
    ) -> List[Dict[str, Any]]:
        """Fetch trades within the specified time period."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT 
                    timestamp, symbol, direction, entry, exit, pnl,
                    r_multiple, strategy_module, regime, close_reason
                FROM recent_trades
                WHERE timestamp >= ? AND timestamp < ?
                ORDER BY timestamp ASC
                """,
                (start_time.isoformat(), end_time.isoformat()),
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def _get_equity_history_for_period(
        self, start_time: datetime, end_time: datetime
    ) -> List[Dict[str, Any]]:
        """Fetch equity history within the specified time period."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT time, equity
                FROM equity_history
                WHERE time >= ? AND time < ?
                ORDER BY time ASC
                """,
                (start_time.isoformat(), end_time.isoformat()),
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def _get_current_equity(self) -> float:
        """Get current equity from bot state."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT equity, starting_equity FROM bot_runtime_state WHERE id = 1"
            )
            row = cursor.fetchone()
            if row:
                return row["equity"]
            return 0.0

    def _get_starting_equity(self) -> float:
        """Get starting equity from bot state."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT starting_equity FROM bot_runtime_state WHERE id = 1"
            )
            row = cursor.fetchone()
            if row:
                return row["starting_equity"]
            return 0.0

    def _calculate_strategy_stats(self, trades: List[Dict]) -> List[StrategyStats]:
        """Calculate stats breakdown by strategy module."""
        by_module: Dict[str, List[Dict]] = {}
        for trade in trades:
            module = trade["strategy_module"]
            if module not in by_module:
                by_module[module] = []
            by_module[module].append(trade)

        stats: List[StrategyStats] = []
        for module, module_trades in by_module.items():
            total = len(module_trades)
            winners = [t for t in module_trades if t["pnl"] > 0]
            losers = [t for t in module_trades if t["pnl"] <= 0]
            pnls = [t["pnl"] for t in module_trades]
            r_multiples = [t["r_multiple"] for t in module_trades]

            stats.append(
                StrategyStats(
                    module=module,
                    total_trades=total,
                    winning_trades=len(winners),
                    losing_trades=len(losers),
                    win_rate=len(winners) / total if total > 0 else 0,
                    total_pnl=sum(pnls),
                    avg_r_multiple=sum(r_multiples) / total if total > 0 else 0,
                    best_trade_pnl=max(pnls) if pnls else 0,
                    worst_trade_pnl=min(pnls) if pnls else 0,
                )
            )

        return sorted(stats, key=lambda s: s.total_pnl, reverse=True)

    def _calculate_max_drawdown(self, equity_history: List[Dict]) -> float:
        """Calculate maximum drawdown percentage from equity history."""
        if not equity_history:
            return 0.0

        equities = [e["equity"] for e in equity_history]
        if not equities:
            return 0.0

        peak = equities[0]
        max_dd = 0.0
        for equity in equities:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        return max_dd * 100  # Return as percentage

    def generate_daily_report(self, date: Optional[datetime] = None) -> PeriodReport:
        """Generate report for a specific day (defaults to yesterday)."""
        if date is None:
            date = datetime.now(tz=UTC) - timedelta(days=1)

        start_time = datetime(date.year, date.month, date.day, 0, 0, 0, tzinfo=UTC)
        end_time = start_time + timedelta(days=1)

        trades = self._get_trades_for_period(start_time, end_time)
        equity_history = self._get_equity_history_for_period(start_time, end_time)

        # Calculate metrics
        total_trades = len(trades)
        winners = [t for t in trades if t["pnl"] > 0]
        losers = [t for t in trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in trades)
        r_multiples = [t["r_multiple"] for t in trades]

        # Get equity values
        starting_equity = self._get_starting_equity()
        if equity_history:
            period_start_equity = equity_history[0]["equity"]
            period_end_equity = equity_history[-1]["equity"]
        else:
            period_start_equity = starting_equity
            period_end_equity = starting_equity + total_pnl

        report = PeriodReport(
            profile_id=self.profile_id,
            profile_name=self.profile_name,
            period_type="daily",
            period_start=start_time.isoformat(),
            period_end=end_time.isoformat(),
            starting_equity=period_start_equity,
            ending_equity=period_end_equity,
            total_pnl=total_pnl,
            pnl_pct=(total_pnl / period_start_equity * 100) if period_start_equity > 0 else 0,
            total_trades=total_trades,
            winning_trades=len(winners),
            losing_trades=len(losers),
            win_rate=len(winners) / total_trades if total_trades > 0 else 0,
            avg_r_multiple=sum(r_multiples) / total_trades if total_trades > 0 else 0,
            max_drawdown_pct=self._calculate_max_drawdown(equity_history),
            strategy_breakdown=self._calculate_strategy_stats(trades),
            generated_at=datetime.now(tz=UTC).isoformat(),
        )

        # Save report
        self._save_report(report, "daily", start_time)
        return report

    def generate_weekly_report(self, week_start: Optional[datetime] = None) -> PeriodReport:
        """Generate report for a specific week (defaults to last week)."""
        if week_start is None:
            # Get start of last week (Monday)
            today = datetime.now(tz=UTC)
            days_since_monday = today.weekday()
            this_monday = today - timedelta(days=days_since_monday)
            week_start = this_monday - timedelta(days=7)

        start_time = datetime(
            week_start.year, week_start.month, week_start.day, 0, 0, 0, tzinfo=UTC
        )
        end_time = start_time + timedelta(days=7)

        trades = self._get_trades_for_period(start_time, end_time)
        equity_history = self._get_equity_history_for_period(start_time, end_time)

        # Calculate metrics
        total_trades = len(trades)
        winners = [t for t in trades if t["pnl"] > 0]
        losers = [t for t in trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in trades)
        r_multiples = [t["r_multiple"] for t in trades]

        # Get equity values
        starting_equity = self._get_starting_equity()
        if equity_history:
            period_start_equity = equity_history[0]["equity"]
            period_end_equity = equity_history[-1]["equity"]
        else:
            period_start_equity = starting_equity
            period_end_equity = starting_equity + total_pnl

        report = PeriodReport(
            profile_id=self.profile_id,
            profile_name=self.profile_name,
            period_type="weekly",
            period_start=start_time.isoformat(),
            period_end=end_time.isoformat(),
            starting_equity=period_start_equity,
            ending_equity=period_end_equity,
            total_pnl=total_pnl,
            pnl_pct=(total_pnl / period_start_equity * 100) if period_start_equity > 0 else 0,
            total_trades=total_trades,
            winning_trades=len(winners),
            losing_trades=len(losers),
            win_rate=len(winners) / total_trades if total_trades > 0 else 0,
            avg_r_multiple=sum(r_multiples) / total_trades if total_trades > 0 else 0,
            max_drawdown_pct=self._calculate_max_drawdown(equity_history),
            strategy_breakdown=self._calculate_strategy_stats(trades),
            generated_at=datetime.now(tz=UTC).isoformat(),
        )

        # Save report
        self._save_report(report, "weekly", start_time)
        return report

    def _save_report(
        self, report: PeriodReport, period_type: str, period_start: datetime
    ) -> None:
        """Save report to JSON and markdown files."""
        date_str = period_start.strftime("%Y-%m-%d")
        base_name = f"{self.profile_id}_{period_type}_{date_str}"

        # Save JSON
        json_path = self.output_dir / f"{base_name}.json"
        with open(json_path, "w") as f:
            json.dump(self._report_to_dict(report), f, indent=2)

        # Save Markdown
        md_path = self.output_dir / f"{base_name}.md"
        with open(md_path, "w") as f:
            f.write(self._report_to_markdown(report))

        LOG.info(f"Report saved: {json_path}")

    def _report_to_dict(self, report: PeriodReport) -> Dict:
        """Convert report to dictionary for JSON serialization."""
        data = asdict(report)
        data["strategy_breakdown"] = [asdict(s) for s in report.strategy_breakdown]
        return data

    def _report_to_markdown(self, report: PeriodReport) -> str:
        """Generate markdown formatted report."""
        lines = [
            f"# {report.profile_name} - {report.period_type.title()} Report",
            "",
            f"**Period:** {report.period_start[:10]} to {report.period_end[:10]}",
            f"**Generated:** {report.generated_at[:19]}",
            "",
            "## Summary",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Starting Equity | ${report.starting_equity:.2f} |",
            f"| Ending Equity | ${report.ending_equity:.2f} |",
            f"| Total P&L | ${report.total_pnl:+.2f} ({report.pnl_pct:+.2f}%) |",
            f"| Total Trades | {report.total_trades} |",
            f"| Win Rate | {report.win_rate:.1%} ({report.winning_trades}W / {report.losing_trades}L) |",
            f"| Avg R-Multiple | {report.avg_r_multiple:.2f}R |",
            f"| Max Drawdown | {report.max_drawdown_pct:.2f}% |",
            "",
        ]

        if report.strategy_breakdown:
            lines.extend([
                "## Strategy Breakdown",
                "",
                "| Strategy | Trades | Win Rate | P&L | Avg R |",
                "|----------|--------|----------|-----|-------|",
            ])
            for s in report.strategy_breakdown:
                lines.append(
                    f"| {s.module} | {s.total_trades} | {s.win_rate:.1%} | "
                    f"${s.total_pnl:+.2f} | {s.avg_r_multiple:.2f}R |"
                )
            lines.append("")

        return "\n".join(lines)

    def get_all_reports(self, period_type: str = "daily", limit: int = 30) -> List[Dict]:
        """List recent reports of a given type."""
        pattern = f"{self.profile_id}_{period_type}_*.json"
        reports = []
        for path in sorted(self.output_dir.glob(pattern), reverse=True)[:limit]:
            with open(path) as f:
                reports.append(json.load(f))
        return reports
