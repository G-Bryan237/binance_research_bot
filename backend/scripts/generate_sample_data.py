from __future__ import annotations

import argparse
import csv
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import Random


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic BTCUSDT 5m sample CSV for backtest smoke tests")
    parser.add_argument("--out", default="data/BTCUSDT_SPOT_5m.csv")
    parser.add_argument("--bars", type=int, default=5000)
    parser.add_argument("--start-price", type=float, default=25000.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--inject-sweeps", action="store_true", help="Inject synthetic liquidity sweeps for easier signal testing")
    args = parser.parse_args()

    rng = Random(args.seed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    start_dt = datetime(2024, 1, 1, tzinfo=UTC)
    price = args.start_price
    pending_confirm: dict | None = None

    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["open_time", "open", "high", "low", "close", "volume", "quote_volume_24h"])
        rolling_qv = 0.0
        history_qv: list[float] = []
        for i in range(args.bars):
            t = start_dt + timedelta(minutes=5 * i)
            o = price
            regime_wave = math.sin(i / 180.0) * 0.0025
            noise = rng.uniform(-0.0015, 0.0015)
            drift = 0.00006

            if pending_confirm and pending_confirm["bar"] == i:
                c = max(10.0, pending_confirm["target_close"])
                h = max(c * 1.0012, pending_confirm["sweep_high"] * 1.001)
                l = min(o, c) * 0.9991
                v = 1400 + rng.uniform(0, 900)
                pending_confirm = None
            elif args.inject_sweeps and i > 260 and i % 120 == 0:
                # Forced downside sweep bar: long wick below structure, then close back inside.
                c = max(10.0, o * (1 - 0.0012))
                h = max(o, c) * 1.0008
                l = min(o, c) * 0.985
                v = 1200 + rng.uniform(0, 800)
                pending_confirm = {
                    "bar": i + 1,
                    "sweep_high": h,
                    "target_close": h * 1.003,
                }
            else:
                ret = drift + regime_wave + noise
                c = max(10.0, o * (1 + ret))
                wick_scale = abs(ret) + rng.uniform(0.0002, 0.0014)
                h = max(o, c) * (1 + wick_scale)
                l = min(o, c) * (1 - wick_scale)
                v = 300 + rng.uniform(0, 700)
            qv = c * v

            history_qv.append(qv)
            rolling_qv += qv
            if len(history_qv) > 288:
                rolling_qv -= history_qv[-289]

            w.writerow([int(t.timestamp() * 1000), o, h, l, c, v, rolling_qv])
            price = c

    print(f"Wrote sample data: {out}")


if __name__ == "__main__":
    main()
