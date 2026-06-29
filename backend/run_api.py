from __future__ import annotations

import logging
import os
import threading

from bot.api_server import BotApiServer
from bot.config import load_config
from bot.engine import TradingBot


def run_worker_loop(bot: TradingBot) -> None:
    """Run the trading bot in a background thread."""
    LOG = logging.getLogger("worker")
    LOG.info("Starting trading worker in background thread...")
    try:
        bot.run_forever()
    except Exception as exc:
        LOG.exception("Worker thread crashed: %s", exc)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    cfg = load_config(".env")
    cors_origin = os.getenv("BOT_DASHBOARD_CORS_ORIGIN", "*")
    
    # Check if we should auto-start the worker
    auto_start_worker = os.getenv("BOT_AUTO_START_WORKER", "true").lower() in {"1", "true", "yes", "on"}
    
    # Start the trading worker in a background thread
    if auto_start_worker:
        bot = TradingBot(cfg)
        worker_thread = threading.Thread(target=run_worker_loop, args=(bot,), daemon=True)
        worker_thread.start()
        logging.info("Trading worker started in background (daemon thread)")
    else:
        logging.info("Worker auto-start disabled (BOT_AUTO_START_WORKER=false)")
    
    server = BotApiServer(
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

    if not server.initialize():
        raise SystemExit("Failed to initialize API server")

    server.run()


if __name__ == "__main__":
    main()
