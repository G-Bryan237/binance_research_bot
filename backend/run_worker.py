from __future__ import annotations

import argparse
import logging

from bot.config import load_config
from bot.engine import TradingBot


def main() -> None:
    parser = argparse.ArgumentParser(description="Binance research bot worker (paper mode)")
    parser.add_argument("--once", action="store_true", help="Run one bot tick and exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    cfg = load_config(".env")
    bot = TradingBot(cfg)
    if args.once:
        bot.run_once()
        return
    bot.run_forever()


if __name__ == "__main__":
    main()
