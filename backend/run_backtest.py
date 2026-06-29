from __future__ import annotations

import argparse
import glob
import logging
from datetime import UTC, datetime, timedelta

from bot.backtest import (
    HistoricalSeries,
    StrategyBacktester,
    download_klines_to_csv,
    load_series_from_csv,
    save_backtest_outputs,
)
from bot.config import load_config
from bot.types import MarketType


def _parse_market(s: str) -> MarketType:
    v = s.strip().upper()
    if v not in {"SPOT", "PERP"}:
        raise ValueError("market must be SPOT or PERP")
    return MarketType[v]


def _fmt_num(x: float) -> str:
    return f"{x:,.4f}"


def _print_result_table(result) -> None:
    print("\n=== Backtest Summary ===")
    print(f"Initial Equity:           ${_fmt_num(result.initial_equity)}")
    print(f"Final Equity:             ${_fmt_num(result.final_equity)}")
    print(f"Total Return:             {result.total_return_pct:.2f}%")
    print(f"Total Trades:             {result.total_trades}")
    print(f"Wins / Losses / BE:       {result.wins} / {result.losses} / {result.breakeven}")
    print(f"Win Rate:                 {result.win_rate_pct:.2f}%")
    print(f"Profit Factor:            {result.profit_factor:.3f}")
    print(f"Max Drawdown:             {result.max_drawdown_pct:.2f}%")
    print(f"Avg Drawdown:             {result.avg_drawdown_pct:.2f}%")
    print(f"Current Drawdown:         {result.current_drawdown_pct:.2f}%")
    print(f"Max DD Duration (bars):   {result.max_drawdown_duration_bars}")
    print(f"Sharpe (trade-level):     {result.sharpe_ratio_trade_level:.3f}")
    print(f"Sortino (trade-level):    {result.sortino_ratio_trade_level:.3f}")
    print(f"Expectancy / trade:       ${_fmt_num(result.expectancy_per_trade)}")
    print(f"Avg R-multiple:           {result.avg_r_multiple:.3f}")
    print(f"Median R-multiple:        {result.median_r_multiple:.3f}")
    print(f"Avg Win / Avg Loss:       ${_fmt_num(result.avg_win)} / ${_fmt_num(result.avg_loss)}")
    print(f"Longest Win/Loss Streak:  {result.longest_win_streak} / {result.longest_loss_streak}")

    if result.by_module:
        print("\nBy Strategy Module:")
        for mod, d in sorted(result.by_module.items()):
            print(
                f"  {mod}: trades={int(d.get('trades', 0))}, "
                f"win_rate={d.get('win_rate_pct', 0.0):.2f}%, pnl=${_fmt_num(d.get('pnl', 0.0))}"
            )

    if result.by_regime:
        print("\nWin Rate by Regime:")
        for regime, d in sorted(result.by_regime.items()):
            print(
                f"  {regime}: trades={int(d.get('trades', 0))}, "
                f"win_rate={d.get('win_rate_pct', 0.0):.2f}%, pnl=${_fmt_num(d.get('pnl', 0.0))}"
            )
    print()


def _load_data_from_paths(paths: list[str], market_override: MarketType | None, timeframe_override: str | None) -> list[HistoricalSeries]:
    series: list[HistoricalSeries] = []
    for p in paths:
        s = load_series_from_csv(
            p,
            market=market_override,
            timeframe=timeframe_override,
        )
        series.append(s)
    return series


def _date_to_ms_utc(date_str: str) -> int:
    dt = datetime.fromisoformat(date_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    return int(dt.timestamp() * 1000)


def _filter_by_date(series: list[HistoricalSeries], start_ms: int | None, end_ms: int | None) -> list[HistoricalSeries]:
    out: list[HistoricalSeries] = []
    for s in series:
        keep_idx = []
        for i, c in enumerate(s.candles):
            if start_ms is not None and c.open_time < start_ms:
                continue
            if end_ms is not None and c.open_time > end_ms:
                continue
            keep_idx.append(i)
        if not keep_idx:
            continue
        out.append(
            HistoricalSeries(
                symbol=s.symbol,
                market=s.market,
                timeframe=s.timeframe,
                candles=[s.candles[i] for i in keep_idx],
                bids=[s.bids[i] for i in keep_idx],
                asks=[s.asks[i] for i in keep_idx],
                quote_volumes_24h=[s.quote_volumes_24h[i] for i in keep_idx],
                funding_rates=[s.funding_rates[i] for i in keep_idx],
                open_interests=[s.open_interests[i] for i in keep_idx],
            )
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run historical backtest and print win rate / PF / DD / Sharpe")
    parser.add_argument("--csv", action="append", default=[], help="Path to CSV file (repeatable)")
    parser.add_argument("--glob", dest="glob_pattern", default="", help="Glob pattern for CSV files")
    parser.add_argument("--market", default="", help="Override market (SPOT or PERP)")
    parser.add_argument("--timeframe", default="", help="Override timeframe label (e.g. 5m)")
    parser.add_argument("--warmup-bars", type=int, default=260, help="Warmup candles before signals")

    parser.add_argument("--download-symbol", default="", help="Download historical klines for symbol")
    parser.add_argument("--download-market", default="SPOT", help="SPOT or PERP for download")
    parser.add_argument("--download-timeframe", default="5m", help="Interval for download")
    parser.add_argument("--download-days", type=int, default=0, help="Days of history to download")
    parser.add_argument("--download-out", default="", help="Output CSV path for downloaded history")

    parser.add_argument("--start-date", default="", help="Backtest start date (YYYY-MM-DD or ISO datetime)")
    parser.add_argument("--end-date", default="", help="Backtest end date (YYYY-MM-DD or ISO datetime)")

    parser.add_argument("--walk-forward-months", type=int, default=0, help="Optional walk-forward train months")
    parser.add_argument("--walk-forward-test-months", type=int, default=0, help="Optional walk-forward test months")

    parser.add_argument("--out-summary-json", default="", help="Write summary JSON")
    parser.add_argument("--out-trades-csv", default="", help="Write trade log CSV")
    parser.add_argument("--out-equity-csv", default="", help="Write equity curve CSV")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    cfg = load_config(".env")

    # Optional downloader path for quick setup.
    if args.download_symbol and args.download_days > 0:
        market = _parse_market(args.download_market)
        end_dt = datetime.now(tz=UTC)
        start_dt = end_dt - timedelta(days=args.download_days)
        out_name = (
            args.download_out
            or f"data/{args.download_symbol.upper()}_{market.value}_{args.download_timeframe}.csv"
        )
        out_path = download_klines_to_csv(
            out_csv=out_name,
            symbol=args.download_symbol.upper(),
            market=market,
            timeframe=args.download_timeframe,
            start_time_ms=int(start_dt.timestamp() * 1000),
            end_time_ms=int(end_dt.timestamp() * 1000),
        )
        print(f"Downloaded history to {out_path}")
        if not args.csv and not args.glob_pattern:
            args.csv = [str(out_path)]

    paths: list[str] = list(args.csv)
    if args.glob_pattern:
        paths.extend(sorted(glob.glob(args.glob_pattern)))
    if not paths:
        raise SystemExit("No input data provided. Use --csv or --glob, or use --download-* options.")

    market_override = _parse_market(args.market) if args.market else None
    timeframe_override = args.timeframe.strip() or None
    series = _load_data_from_paths(paths, market_override, timeframe_override)
    if not series:
        raise SystemExit("No valid series loaded")

    start_ms = _date_to_ms_utc(args.start_date) if args.start_date else None
    end_ms = _date_to_ms_utc(args.end_date) if args.end_date else None
    if start_ms is not None or end_ms is not None:
        series = _filter_by_date(series, start_ms, end_ms)
    if not series:
        raise SystemExit("No candles left after date filter")

    # Main backtest
    bt = StrategyBacktester(cfg)
    result = bt.run(series=series, warmup_bars=args.warmup_bars)
    _print_result_table(result)

    save_backtest_outputs(
        result,
        out_summary_json=args.out_summary_json or None,
        out_trades_csv=args.out_trades_csv or None,
        out_equity_csv=args.out_equity_csv or None,
    )

    # Optional walk-forward reporting (same strategy params, no fitting, split for robustness checks).
    if args.walk_forward_months > 0 and args.walk_forward_test_months > 0:
        print("=== Walk-Forward (No parameter fitting, robustness split only) ===")
        # Determine common date range from first series.
        first = series[0].candles
        start_dt = datetime.fromtimestamp(first[0].open_time / 1000, tz=UTC)
        end_dt = datetime.fromtimestamp(first[-1].open_time / 1000, tz=UTC)
        train_days = args.walk_forward_months * 30
        test_days = args.walk_forward_test_months * 30
        cursor = start_dt
        fold = 1
        while True:
            train_start = cursor
            train_end = train_start + timedelta(days=train_days)
            test_end = train_end + timedelta(days=test_days)
            if test_end > end_dt:
                break

            test_series = _filter_by_date(
                series,
                int(train_end.timestamp() * 1000),
                int(test_end.timestamp() * 1000),
            )
            if test_series:
                fold_bt = StrategyBacktester(cfg)
                fold_result = fold_bt.run(test_series, warmup_bars=args.warmup_bars)
                print(
                    f"Fold {fold}: "
                    f"test={train_end.date()}..{test_end.date()} "
                    f"trades={fold_result.total_trades} "
                    f"win_rate={fold_result.win_rate_pct:.2f}% "
                    f"pf={fold_result.profit_factor:.2f} "
                    f"max_dd={fold_result.max_drawdown_pct:.2f}%"
                )
            cursor = cursor + timedelta(days=test_days)
            fold += 1


if __name__ == "__main__":
    main()
