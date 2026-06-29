#!/usr/bin/env python3
"""
Multi-Profile Runner

Runs both conservative and aggressive profiles simultaneously with:
- Separate databases
- Separate dashboard ports
- Separate report directories
- Scheduled daily/weekly report generation

Usage:
    python run_multiprofile.py                    # Run both profiles
    python run_multiprofile.py --profile conservative  # Run single profile
    python run_multiprofile.py --report daily         # Generate daily reports
    python run_multiprofile.py --report weekly        # Generate weekly reports
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Dict, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from bot.config import load_config
from bot.engine import TradingBot
from bot.api_server import BotApiServer
from bot.reporting import ReportGenerator

LOG = logging.getLogger(__name__)

PROFILES = {
    "conservative": "profiles/conservative.env",
    "aggressive": "profiles/aggressive.env",
}


class ProfileRunner:
    """Manages a single bot profile (worker + API server)."""

    def __init__(self, profile_id: str, env_file: str) -> None:
        self.profile_id = profile_id
        self.env_file = env_file
        self.worker_thread: Optional[threading.Thread] = None
        self.api_thread: Optional[threading.Thread] = None
        self.bot: Optional[TradingBot] = None
        self.api_server: Optional[BotApiServer] = None
        self.running = False

    def start(self) -> None:
        """Start both worker and API server for this profile."""
        LOG.info(f"Starting profile: {self.profile_id}")
        
        # Load config from profile-specific env file
        cfg = load_config(self.env_file)
        
        # Create bot instance
        self.bot = TradingBot(cfg)
        
        # Create API server instance
        cors_origin = os.getenv("BOT_DASHBOARD_CORS_ORIGIN", "*")
        self.api_server = BotApiServer(
            db_path=cfg.state_db_path,
            host=cfg.dashboard_host,
            port=cfg.dashboard_port,
            data_dir=os.getenv("DATA_DIR", "data"),
            out_dir=os.getenv("OUT_DIR", "out"),
            cors_origin=cors_origin,
            event_poll_ms=cfg.dashboard_update_interval_ms,
            profile_id=cfg.profile_id,
            profile_name=cfg.profile_name,
        )
        
        if not self.api_server.initialize():
            raise RuntimeError(f"Failed to initialize API server for {self.profile_id}")
        
        self.running = True
        
        # Start worker thread
        self.worker_thread = threading.Thread(
            target=self._run_worker,
            name=f"worker-{self.profile_id}",
            daemon=True,
        )
        self.worker_thread.start()
        
        # Start API server thread
        self.api_thread = threading.Thread(
            target=self._run_api,
            name=f"api-{self.profile_id}",
            daemon=True,
        )
        self.api_thread.start()
        
        LOG.info(f"Profile {self.profile_id} started - Dashboard: http://localhost:{cfg.dashboard_port}")

    def _run_worker(self) -> None:
        """Run the trading bot worker."""
        try:
            while self.running and self.bot:
                self.bot.run_once()
                time.sleep(self.bot.cfg.loop_seconds)
        except Exception as e:
            LOG.error(f"Worker error for {self.profile_id}: {e}")

    def _run_api(self) -> None:
        """Run the API server."""
        try:
            if self.api_server:
                self.api_server.run()
        except Exception as e:
            LOG.error(f"API error for {self.profile_id}: {e}")

    def stop(self) -> None:
        """Stop the profile."""
        LOG.info(f"Stopping profile: {self.profile_id}")
        self.running = False


class ReportScheduler:
    """Schedules and generates daily/weekly reports."""

    def __init__(self, profiles: Dict[str, str]) -> None:
        self.profiles = profiles
        self.generators: Dict[str, ReportGenerator] = {}
        self._init_generators()

    def _init_generators(self) -> None:
        """Initialize report generators for each profile."""
        for profile_id, env_file in self.profiles.items():
            cfg = load_config(env_file)
            self.generators[profile_id] = ReportGenerator(
                db_path=cfg.state_db_path,
                profile_id=cfg.profile_id,
                profile_name=cfg.profile_name,
                output_dir=cfg.report_output_dir,
            )

    def generate_daily_reports(self) -> None:
        """Generate daily reports for all profiles."""
        LOG.info("Generating daily reports...")
        for profile_id, generator in self.generators.items():
            try:
                report = generator.generate_daily_report()
                LOG.info(
                    f"Daily report for {profile_id}: "
                    f"P&L ${report.total_pnl:+.2f} ({report.pnl_pct:+.2f}%), "
                    f"{report.total_trades} trades, {report.win_rate:.1%} win rate"
                )
            except Exception as e:
                LOG.error(f"Failed to generate daily report for {profile_id}: {e}")

    def generate_weekly_reports(self) -> None:
        """Generate weekly reports for all profiles."""
        LOG.info("Generating weekly reports...")
        for profile_id, generator in self.generators.items():
            try:
                report = generator.generate_weekly_report()
                LOG.info(
                    f"Weekly report for {profile_id}: "
                    f"P&L ${report.total_pnl:+.2f} ({report.pnl_pct:+.2f}%), "
                    f"{report.total_trades} trades, {report.win_rate:.1%} win rate"
                )
            except Exception as e:
                LOG.error(f"Failed to generate weekly report for {profile_id}: {e}")

    def run_scheduler(self, check_interval_seconds: int = 60) -> None:
        """Run the report scheduler in a loop."""
        last_daily = None
        last_weekly = None
        
        while True:
            now = datetime.now(tz=UTC)
            
            # Generate daily report at midnight UTC
            if last_daily != now.date() and now.hour == 0 and now.minute < 5:
                self.generate_daily_reports()
                last_daily = now.date()
            
            # Generate weekly report on Monday at midnight UTC
            if last_weekly != now.date() and now.weekday() == 0 and now.hour == 0 and now.minute < 5:
                self.generate_weekly_reports()
                last_weekly = now.date()
            
            time.sleep(check_interval_seconds)


class MultiProfileManager:
    """Manages multiple bot profiles running simultaneously."""

    def __init__(self, profiles: Dict[str, str]) -> None:
        self.profiles = profiles
        self.runners: Dict[str, ProfileRunner] = {}
        self.report_scheduler: Optional[ReportScheduler] = None
        self.scheduler_thread: Optional[threading.Thread] = None
        self.shutdown_event = threading.Event()

    def start_all(self) -> None:
        """Start all profiles."""
        for profile_id, env_file in self.profiles.items():
            runner = ProfileRunner(profile_id, env_file)
            runner.start()
            self.runners[profile_id] = runner
        
        # Start report scheduler
        self.report_scheduler = ReportScheduler(self.profiles)
        self.scheduler_thread = threading.Thread(
            target=self.report_scheduler.run_scheduler,
            name="report-scheduler",
            daemon=True,
        )
        self.scheduler_thread.start()

    def start_profile(self, profile_id: str) -> None:
        """Start a specific profile."""
        if profile_id not in self.profiles:
            raise ValueError(f"Unknown profile: {profile_id}")
        
        runner = ProfileRunner(profile_id, self.profiles[profile_id])
        runner.start()
        self.runners[profile_id] = runner

    def stop_all(self) -> None:
        """Stop all profiles."""
        for runner in self.runners.values():
            runner.stop()
        self.shutdown_event.set()

    def wait(self) -> None:
        """Wait for shutdown signal."""
        self.shutdown_event.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-profile trading bot runner")
    parser.add_argument(
        "--profile",
        choices=list(PROFILES.keys()),
        help="Run a specific profile only",
    )
    parser.add_argument(
        "--report",
        choices=["daily", "weekly"],
        help="Generate reports and exit",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one tick and exit (for testing)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    # Handle report generation mode
    if args.report:
        scheduler = ReportScheduler(PROFILES)
        if args.report == "daily":
            scheduler.generate_daily_reports()
        else:
            scheduler.generate_weekly_reports()
        return

    # Handle single profile mode
    if args.profile:
        profiles_to_run = {args.profile: PROFILES[args.profile]}
    else:
        profiles_to_run = PROFILES

    # Create and start manager
    manager = MultiProfileManager(profiles_to_run)

    # Handle shutdown signals
    def signal_handler(signum, frame):
        LOG.info("Shutdown signal received")
        manager.stop_all()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start profiles
    if args.once:
        # Single tick mode for testing
        for profile_id, env_file in profiles_to_run.items():
            cfg = load_config(env_file)
            bot = TradingBot(cfg)
            bot.run_once()
            LOG.info(f"Completed one tick for {profile_id}")
        return

    try:
        manager.start_all()
        LOG.info("=" * 60)
        LOG.info("Multi-Profile Trading System Started")
        LOG.info("=" * 60)
        for profile_id in profiles_to_run:
            cfg = load_config(profiles_to_run[profile_id])
            LOG.info(f"  {profile_id.upper()}: http://localhost:{cfg.dashboard_port}")
        LOG.info("=" * 60)
        LOG.info("Press Ctrl+C to stop")
        
        # Keep main thread alive
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        LOG.info("Shutting down...")
        manager.stop_all()


if __name__ == "__main__":
    main()
