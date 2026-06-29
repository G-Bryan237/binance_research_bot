from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Optional

import requests

from .config import BotConfig
from .indicators import atr, detect_divergence, latest, macd, rsi
from .regime import detect_regime
from .strategies import generate_signal
from .types import Candle, ClosedTrade, Direction, MarketSnapshot, MarketType, Position, Signal, StrategyModule


def _to_float(value: str | float | int | None, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (float, int)):
        return float(value)
    text = str(value).strip()
    if not text:
        return default
    return float(text)


def _parse_timestamp_ms(raw: str) -> int:
    s = raw.strip()
    if not s:
        raise ValueError("empty timestamp")
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        n = int(s)
        # Heuristic: values below 1e11 are likely in seconds.
        if abs(n) < 100_000_000_000:
            return n * 1000
        return n
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def _utc_from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


def _timeframe_to_minutes(tf: str) -> int | None:
    s = tf.strip().lower()
    if s.endswith("m") and s[:-1].isdigit():
        return int(s[:-1])
    if s.endswith("h") and s[:-1].isdigit():
        return int(s[:-1]) * 60
    return None


@dataclass
class HistoricalSeries:
    symbol: str
    market: MarketType
    timeframe: str
    candles: list[Candle]
    bids: list[float]
    asks: list[float]
    quote_volumes_24h: list[float]
    funding_rates: list[float | None]
    open_interests: list[float | None]

    def snapshot_at(self, idx: int) -> MarketSnapshot:
        return MarketSnapshot(
            symbol=self.symbol,
            market=self.market,
            timeframe=self.timeframe,
            candles=self.candles[: idx + 1],
            bid=self.bids[idx],
            ask=self.asks[idx],
            quote_volume_24h=self.quote_volumes_24h[idx],
            funding_rate=self.funding_rates[idx],
            open_interest=self.open_interests[idx],
        )


@dataclass
class _OpenPositionState:
    position: Position
    open_time_ms: int
    initial_stop: float


@dataclass
class BacktestTradeRecord:
    closed_trade: ClosedTrade
    r_multiple: float
    hold_minutes: float
    strategy_module: str
    regime: str


@dataclass
class BacktestResult:
    initial_equity: float
    final_equity: float
    total_return_pct: float
    total_trades: int
    wins: int
    losses: int
    breakeven: int
    win_rate_pct: float
    profit_factor: float
    max_drawdown_pct: float
    sharpe_ratio_trade_level: float
    sortino_ratio_trade_level: float
    avg_drawdown_pct: float
    current_drawdown_pct: float
    max_drawdown_duration_bars: int
    expectancy_per_trade: float
    avg_r_multiple: float
    median_r_multiple: float
    avg_win: float
    avg_loss: float
    longest_win_streak: int
    longest_loss_streak: int
    by_module: dict[str, dict[str, float]]
    by_regime: dict[str, dict[str, float]]
    equity_curve: list[tuple[int, float]]
    trades: list[BacktestTradeRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "initial_equity": self.initial_equity,
            "final_equity": self.final_equity,
            "total_return_pct": self.total_return_pct,
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "breakeven": self.breakeven,
            "win_rate_pct": self.win_rate_pct,
            "profit_factor": self.profit_factor,
            "max_drawdown_pct": self.max_drawdown_pct,
            "sharpe_ratio_trade_level": self.sharpe_ratio_trade_level,
            "sortino_ratio_trade_level": self.sortino_ratio_trade_level,
            "avg_drawdown_pct": self.avg_drawdown_pct,
            "current_drawdown_pct": self.current_drawdown_pct,
            "max_drawdown_duration_bars": self.max_drawdown_duration_bars,
            "expectancy_per_trade": self.expectancy_per_trade,
            "avg_r_multiple": self.avg_r_multiple,
            "median_r_multiple": self.median_r_multiple,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "longest_win_streak": self.longest_win_streak,
            "longest_loss_streak": self.longest_loss_streak,
            "by_module": self.by_module,
            "by_regime": self.by_regime,
        }


class _BacktestRiskManager:
    def __init__(self, cfg: BotConfig) -> None:
        self.cfg = cfg
        self.equity = cfg.risk.start_equity
        self.daily_start_equity = cfg.risk.start_equity
        self.weekly_start_equity = cfg.risk.start_equity
        self.current_day = ""
        self.current_week = ""
        self.trades_today = 0
        self.trades_by_symbol_today: dict[str, int] = {}
        self.day_locked = False
        self.week_locked = False
        self.loss_streak = 0
        self.cooldown_until_ms: int | None = None
        self.last_entry_time_ms: int | None = None

    def _roll_periods(self, time_ms: int) -> None:
        dt = _utc_from_ms(time_ms)
        day = dt.strftime("%Y-%m-%d")
        week = dt.strftime("%G-W%V")
        if day != self.current_day:
            self.current_day = day
            self.daily_start_equity = self.equity
            self.trades_today = 0
            self.trades_by_symbol_today = {}
            self.day_locked = False
            self.loss_streak = 0
            self.cooldown_until_ms = None
        if week != self.current_week:
            self.current_week = week
            self.weekly_start_equity = self.equity
            self.week_locked = False

    def _daily_drawdown(self) -> float:
        if self.daily_start_equity <= 0:
            return 0.0
        return (self.daily_start_equity - self.equity) / self.daily_start_equity

    def _weekly_drawdown(self) -> float:
        if self.weekly_start_equity <= 0:
            return 0.0
        return (self.weekly_start_equity - self.equity) / self.weekly_start_equity

    def pre_trade_check(self, signal: Signal, time_ms: int) -> tuple[bool, str, float, int]:
        self._roll_periods(time_ms)

        if self.week_locked:
            return False, "Weekly lock active", 0.0, 1
        if self.day_locked:
            return False, "Daily lock active", 0.0, 1
        if self.cooldown_until_ms is not None and time_ms < self.cooldown_until_ms:
            return False, "Loss streak cooldown active", 0.0, 1

        # Global spacing between entries (overtrading guardrail).
        min_gap_ms = self.cfg.risk.cooldown_minutes * 60_000
        if self.last_entry_time_ms is not None and time_ms - self.last_entry_time_ms < min_gap_ms:
            return False, "Global entry cooldown active", 0.0, 1

        if self.trades_today >= self.cfg.risk.max_trades_per_day:
            return False, "Max trades/day reached", 0.0, 1
        if self.trades_by_symbol_today.get(signal.symbol, 0) >= self.cfg.risk.max_trades_per_symbol_day:
            return False, "Max symbol trades/day reached", 0.0, 1
        if signal.market == MarketType.SPOT and signal.direction == Direction.SHORT:
            return False, "Spot shorts disabled", 0.0, 1

        leverage = 1
        if signal.market == MarketType.PERP and self.cfg.risk.enable_leverage_2x:
            leverage = 2

        stop_distance = abs(signal.entry - signal.stop)
        if stop_distance <= 0:
            return False, "Invalid stop distance", 0.0, leverage
        risk_cash = self.equity * self.cfg.risk.risk_per_trade_pct
        if signal.strategy_module == StrategyModule.M4:
            risk_cash *= self.cfg.risk.m4_risk_multiplier
        qty = (risk_cash / stop_distance) * leverage
        notional = qty * signal.entry
        if notional < 5.0:
            return False, "Notional too small", 0.0, leverage
        return True, "Approved", qty, leverage

    def register_entry(self, symbol: str, time_ms: int) -> None:
        self._roll_periods(time_ms)
        self.last_entry_time_ms = time_ms
        self.trades_today += 1
        self.trades_by_symbol_today[symbol] = self.trades_by_symbol_today.get(symbol, 0) + 1

    def register_close(self, pnl: float, close_time_ms: int, count_for_streak: bool = True) -> None:
        self._roll_periods(close_time_ms)
        self.equity += pnl
        if count_for_streak:
            if pnl < 0:
                self.loss_streak += 1
            elif pnl > 0:
                self.loss_streak = 0

            if self.loss_streak >= 2:
                self.cooldown_until_ms = close_time_ms + self.cfg.risk.cooldown_after_two_losses_minutes * 60_000
            if self.loss_streak >= self.cfg.risk.max_loss_streak_for_day_stop:
                self.day_locked = True

        if self._daily_drawdown() >= self.cfg.risk.max_daily_loss_pct:
            self.day_locked = True
        if self._weekly_drawdown() >= self.cfg.risk.max_weekly_loss_pct:
            self.week_locked = True


def _round_qty(qty: float, step_size: float = 0.000001) -> float:
    if step_size <= 0:
        return qty
    k = int(qty / step_size)
    return max(k * step_size, 0.0)


def _compute_quote_volume_series(candles: list[Candle], window: int = 288) -> list[float]:
    out = [0.0] * len(candles)
    running = 0.0
    q = [c.close * c.volume for c in candles]
    for i, v in enumerate(q):
        running += v
        if i >= window:
            running -= q[i - window]
        out[i] = running
    return out


def _filename_guess(path: Path) -> tuple[str | None, MarketType | None, str | None]:
    # Expected patterns like BTCUSDT_SPOT_5m.csv or ETHUSDT-PERP-1h.csv
    stem = path.stem.upper().replace("-", "_")
    parts = [p for p in stem.split("_") if p]
    if len(parts) < 2:
        return None, None, None
    symbol = parts[0] if parts[0].endswith("USDT") else None
    market = None
    timeframe = None
    for p in parts[1:]:
        if p in {"SPOT", "PERP"}:
            market = MarketType[p]
        elif p in {"1M", "1MINS"}:
            timeframe = "1m"
        elif p.lower() in {"1m", "5m", "15m", "1h", "4h"}:
            timeframe = p.lower()
    return symbol, market, timeframe


def load_series_from_csv(
    csv_path: str | Path,
    symbol: str | None = None,
    market: MarketType | None = None,
    timeframe: str | None = None,
    default_spread_bps: float = 3.0,
) -> HistoricalSeries:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(path)

    guessed_symbol, guessed_market, guessed_tf = _filename_guess(path)
    symbol = (symbol or guessed_symbol or "BTCUSDT").upper()
    market = market or guessed_market or MarketType.SPOT
    timeframe = timeframe or guessed_tf or "5m"

    candles: list[Candle] = []
    bids: list[float] = []
    asks: list[float] = []
    quote_volumes: list[float] = []
    funding_rates: list[float | None] = []
    open_interests: list[float | None] = []

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = {h.lower().strip(): h for h in (reader.fieldnames or [])}

        def col(name_options: Iterable[str]) -> str | None:
            for n in name_options:
                if n in headers:
                    return headers[n]
            return None

        c_time = col(["open_time", "timestamp", "time", "date", "datetime"])
        c_open = col(["open", "o"])
        c_high = col(["high", "h"])
        c_low = col(["low", "l"])
        c_close = col(["close", "c"])
        c_vol = col(["volume", "v"])

        c_bid = col(["bid", "bid_price"])
        c_ask = col(["ask", "ask_price"])
        c_spread = col(["spread_bps", "spread"])
        c_qv = col(["quote_volume_24h", "quote_volume", "qv"])
        c_funding = col(["funding_rate", "funding"])
        c_oi = col(["open_interest", "oi"])

        if not all([c_time, c_open, c_high, c_low, c_close, c_vol]):
            raise ValueError(
                f"{path}: missing required columns. Need open_time/open/high/low/close/volume (or aliases)."
            )

        for row in reader:
            time_ms = _parse_timestamp_ms(str(row[c_time]))
            o = _to_float(row[c_open])
            h = _to_float(row[c_high])
            l = _to_float(row[c_low])
            c = _to_float(row[c_close])
            v = _to_float(row[c_vol])
            candles.append(Candle(open_time=time_ms, open=o, high=h, low=l, close=c, volume=v))

            if c_bid and c_ask:
                bid = _to_float(row[c_bid], c)
                ask = _to_float(row[c_ask], c)
            else:
                spread_bps = default_spread_bps
                if c_spread:
                    spread_bps = _to_float(row[c_spread], default_spread_bps)
                half = (spread_bps / 10_000.0) / 2.0
                bid = c * (1 - half)
                ask = c * (1 + half)
            bids.append(bid)
            asks.append(ask)

            quote_volumes.append(_to_float(row[c_qv], 0.0) if c_qv else 0.0)
            funding_rates.append(_to_float(row[c_funding], 0.0) if (c_funding and market == MarketType.PERP) else None)
            open_interests.append(_to_float(row[c_oi], 0.0) if c_oi else None)

    candles_sorted = sorted(
        zip(candles, bids, asks, quote_volumes, funding_rates, open_interests),
        key=lambda x: x[0].open_time,
    )
    candles = [x[0] for x in candles_sorted]
    bids = [x[1] for x in candles_sorted]
    asks = [x[2] for x in candles_sorted]
    quote_volumes = [x[3] for x in candles_sorted]
    funding_rates = [x[4] for x in candles_sorted]
    open_interests = [x[5] for x in candles_sorted]

    if not candles:
        raise ValueError(f"{path}: no candles loaded")

    if not any(v > 0 for v in quote_volumes):
        quote_volumes = _compute_quote_volume_series(candles, window=288)

    return HistoricalSeries(
        symbol=symbol,
        market=market,
        timeframe=timeframe,
        candles=candles,
        bids=bids,
        asks=asks,
        quote_volumes_24h=quote_volumes,
        funding_rates=funding_rates,
        open_interests=open_interests,
    )


def download_klines_to_csv(
    out_csv: str | Path,
    symbol: str,
    market: MarketType,
    timeframe: str,
    start_time_ms: int,
    end_time_ms: int,
) -> Path:
    out_path = Path(out_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    base = "https://api.binance.com" if market == MarketType.SPOT else "https://fapi.binance.com"
    endpoint = "/api/v3/klines" if market == MarketType.SPOT else "/fapi/v1/klines"
    cursor = start_time_ms
    rows: list[list] = []
    while cursor < end_time_ms:
        resp = requests.get(
            f"{base}{endpoint}",
            params={
                "symbol": symbol,
                "interval": timeframe,
                "startTime": cursor,
                "endTime": end_time_ms,
                "limit": 1000,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        rows.extend(data)
        last_open = int(data[-1][0])
        if last_open <= cursor:
            break
        # Move to the next candle.
        cursor = last_open + 1

    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["open_time", "open", "high", "low", "close", "volume", "quote_volume_24h"])
        for r in rows:
            w.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[7]])
    return out_path


class StrategyBacktester:
    def __init__(self, cfg: BotConfig, spread_bps_override: float | None = None) -> None:
        self.cfg = cfg
        self.risk = _BacktestRiskManager(cfg)
        self.spread_bps_override = spread_bps_override
        self.open_state: _OpenPositionState | None = None
        self.open_key: tuple[str, MarketType] | None = None
        self.last_fingerprint_by_symbol: dict[str, str] = {}

    @staticmethod
    def _last_candle_is_abnormal(snapshot: MarketSnapshot, atr_value: float) -> bool:
        if atr_value <= 0 or not snapshot.candles:
            return False
        c = snapshot.candles[-1]
        return (c.high - c.low) > (2.5 * atr_value)

    @staticmethod
    def _funding_filter(signal: Signal, snapshot: MarketSnapshot) -> bool:
        if signal.market != MarketType.PERP:
            return True
        if snapshot.funding_rate is None:
            return True
        fr = snapshot.funding_rate
        extreme = 0.0003
        if signal.direction == Direction.LONG and fr > extreme:
            return False
        if signal.direction == Direction.SHORT and fr < -extreme:
            return False
        return True

    def _funding_trend_ok(self, series: HistoricalSeries, idx: int, signal: Signal) -> bool:
        if not self.cfg.enable_funding_trend_analysis:
            return True
        if signal.market != MarketType.PERP:
            return True
        if idx <= 0:
            return True
        if series.funding_rates[idx] is None:
            return True

        window_ms = self.cfg.funding_trend_window_hours * 60 * 60 * 1000
        end_time = series.candles[idx].open_time
        points: list[tuple[int, float]] = []
        for j in range(idx, -1, -1):
            t = series.candles[j].open_time
            if (end_time - t) > window_ms:
                break
            fr = series.funding_rates[j]
            if fr is not None:
                points.append((t, fr))
        if len(points) < 3:
            return True

        points.reverse()
        first_t, first_v = points[0]
        last_t, last_v = points[-1]
        dt_hours = max((last_t - first_t) / 3_600_000.0, 1e-6)
        slope_bps = ((last_v - first_v) / dt_hours) * 10_000.0
        threshold = self.cfg.funding_trend_slope_bps_per_hour
        if signal.direction == Direction.LONG and slope_bps > threshold:
            return False
        if signal.direction == Direction.SHORT and slope_bps < -threshold:
            return False
        return True

    def _reversal_exit_reason(self, snapshot: MarketSnapshot, pos: Position) -> str | None:
        cfg = self.cfg.execution
        if not (cfg.enable_reversal_exits or cfg.enable_candle_pattern_exits):
            return None
        if len(snapshot.candles) < 10:
            return None

        last = snapshot.candles[-1]
        prev = snapshot.candles[-2]
        risk = abs(pos.entry - pos.initial_stop)
        if risk <= 0:
            return None
        move_r = (last.close - pos.entry) / risk if pos.direction == Direction.LONG else (pos.entry - last.close) / risk
        if move_r < 0.3 and not pos.stop_moved_to_be:
            return None

        closes = [c.close for c in snapshot.candles]
        if cfg.enable_reversal_exits and cfg.enable_divergence_detection:
            rsi_series = rsi(closes, 14)
            div = detect_divergence(closes, rsi_series, cfg.divergence_lookback, cfg.divergence_swing_window)
            if pos.direction == Direction.LONG and div == "bearish":
                return "REVERSAL_DIVERGENCE"
            if pos.direction == Direction.SHORT and div == "bullish":
                return "REVERSAL_DIVERGENCE"

        if cfg.enable_reversal_exits and cfg.enable_macd_divergence:
            macd_line, _, _ = macd(closes, 12, 26, 9)
            div = detect_divergence(closes, macd_line, cfg.divergence_lookback, cfg.divergence_swing_window)
            if pos.direction == Direction.LONG and div == "bearish":
                return "REVERSAL_MACD_DIVERGENCE"
            if pos.direction == Direction.SHORT and div == "bullish":
                return "REVERSAL_MACD_DIVERGENCE"

        if cfg.enable_candle_pattern_exits:
            body = abs(last.close - last.open)
            upper_wick = last.high - max(last.open, last.close)
            lower_wick = min(last.open, last.close) - last.low
            bearish_engulf = prev.close > prev.open and last.close < last.open and last.open >= prev.close and last.close <= prev.open
            bullish_engulf = prev.close < prev.open and last.close > last.open and last.open <= prev.close and last.close >= prev.open
            if pos.direction == Direction.LONG:
                shooting_star = body > 0 and upper_wick >= (1.8 * body) and last.close < last.open
                if bearish_engulf or shooting_star:
                    return "REVERSAL_CANDLE"
            if pos.direction == Direction.SHORT:
                hammer = body > 0 and lower_wick >= (1.8 * body) and last.close > last.open
                if bullish_engulf or hammer:
                    return "REVERSAL_CANDLE"

        return None

    def _build_mtf_snapshots_from_base(self, snapshot: MarketSnapshot) -> list[MarketSnapshot]:
        if not self.cfg.enable_mtf_confirmation:
            return []
        base_tf_min = _timeframe_to_minutes(snapshot.timeframe)
        if base_tf_min is None or base_tf_min <= 0:
            return []

        out: list[MarketSnapshot] = []
        for tf in self.cfg.mtf_confirm_timeframes:
            tf_min = _timeframe_to_minutes(tf)
            if tf_min is None or tf_min <= base_tf_min or tf_min % base_tf_min != 0:
                continue

            bucket_ms = tf_min * 60_000
            agg: list[Candle] = []
            current_bucket = None
            o = h = l = c = v = None
            for candle in snapshot.candles:
                bucket = (candle.open_time // bucket_ms) * bucket_ms
                if current_bucket is None:
                    current_bucket = bucket
                    o = candle.open
                    h = candle.high
                    l = candle.low
                    c = candle.close
                    v = candle.volume
                    continue
                if bucket != current_bucket:
                    agg.append(
                        Candle(
                            open_time=int(current_bucket),
                            open=float(o),
                            high=float(h),
                            low=float(l),
                            close=float(c),
                            volume=float(v),
                        )
                    )
                    current_bucket = bucket
                    o = candle.open
                    h = candle.high
                    l = candle.low
                    c = candle.close
                    v = candle.volume
                else:
                    h = max(float(h), candle.high)
                    l = min(float(l), candle.low)
                    c = candle.close
                    v = float(v) + candle.volume
            if current_bucket is not None and o is not None:
                agg.append(
                    Candle(
                        open_time=int(current_bucket),
                        open=float(o),
                        high=float(h),
                        low=float(l),
                        close=float(c),
                        volume=float(v),
                    )
                )
            if len(agg) < 260:
                continue
            last = agg[-1].close
            out.append(
                MarketSnapshot(
                    symbol=snapshot.symbol,
                    market=snapshot.market,
                    timeframe=tf,
                    candles=agg,
                    bid=last,
                    ask=last,
                    quote_volume_24h=snapshot.quote_volume_24h,
                    funding_rate=snapshot.funding_rate,
                    open_interest=snapshot.open_interest,
                )
            )
        return out

    def _passes_mtf_confirmation(self, signal: Signal, htf_snapshots: list[MarketSnapshot]) -> bool:
        if not self.cfg.enable_mtf_confirmation:
            return True
        if not htf_snapshots:
            return False

        support = 0
        opposition = 0
        evaluated = 0
        for htf in htf_snapshots:
            hctx = detect_regime(htf, enable_m4=False)
            if hctx is None:
                continue
            evaluated += 1
            strong_up = hctx.trend_up and hctx.adx >= 20
            strong_down = hctx.trend_down and hctx.adx >= 20
            if signal.direction == Direction.LONG:
                if strong_down:
                    opposition += 1
                if strong_up or hctx.breakout_up:
                    support += 1
            else:
                if strong_up:
                    opposition += 1
                if strong_down or hctx.breakout_down:
                    support += 1

            if signal.strategy_module in {StrategyModule.M3, StrategyModule.M4}:
                if signal.direction == Direction.LONG and strong_down and hctx.adx >= 25:
                    opposition += 1
                if signal.direction == Direction.SHORT and strong_up and hctx.adx >= 25:
                    opposition += 1

        if evaluated == 0:
            return False
        if opposition > 0:
            return False
        if signal.strategy_module in {StrategyModule.M1, StrategyModule.M2} and support == 0:
            return False
        return True

    @staticmethod
    def _fingerprint(sig: Signal, candle_open_time: int) -> str:
        return (
            f"{sig.symbol}|{sig.market.value}|{sig.direction.value}|{sig.strategy_module.value}|"
            f"{round(sig.entry, 6)}|{candle_open_time}"
        )

    def _choose_best(self, candidates: list[tuple[Signal, MarketSnapshot]]) -> tuple[Signal, MarketSnapshot] | None:
        if not candidates:
            return None
        spot_longs = [c for c in candidates if c[0].direction == Direction.LONG and c[0].market == MarketType.SPOT]
        if spot_longs:
            return max(spot_longs, key=lambda x: x[0].risk_multiple)
        return max(candidates, key=lambda x: x[0].risk_multiple)

    def _fee_rate(self, market: MarketType) -> float:
        bps = self.cfg.execution.spot_fee_bps if market == MarketType.SPOT else self.cfg.execution.perp_fee_bps
        return bps / 10_000.0

    def _tp_plan(self, pos: Position) -> list[tuple[float, float]]:
        risk = abs(pos.entry - pos.initial_stop)
        if risk <= 0:
            return []

        rr_levels = [x for x in self.cfg.execution.tp_rr_levels if x > 0]
        fractions = [max(x, 0.0) for x in self.cfg.execution.tp_close_fractions]
        if not rr_levels:
            rr_levels = [1.5, 2.5]
            fractions = [self.cfg.execution.partial_tp_fraction, 1.0 - self.cfg.execution.partial_tp_fraction]

        if not fractions:
            fractions = [1.0 / len(rr_levels)] * len(rr_levels)
        if len(fractions) < len(rr_levels):
            rem = max(1.0 - sum(fractions), 0.0)
            missing = len(rr_levels) - len(fractions)
            pad = (rem / missing) if missing > 0 else 0.0
            fractions.extend([pad] * missing)

        total = sum(fractions[: len(rr_levels)])
        if total <= 0:
            fractions = [1.0 / len(rr_levels)] * len(rr_levels)
        else:
            fractions = [f / total for f in fractions[: len(rr_levels)]]

        out: list[tuple[float, float]] = []
        for rr, frac in zip(rr_levels, fractions):
            if pos.direction == Direction.LONG:
                px = pos.entry + (rr * risk)
            else:
                px = pos.entry - (rr * risk)
            out.append((px, frac))
        return out

    def _sr_exit_reason(self, snapshot: MarketSnapshot, pos: Position, atr_now: float | None) -> str | None:
        cfg = self.cfg.execution
        if not cfg.enable_sr_exits:
            return None
        lookback = max(cfg.sr_lookback_bars, 8)
        if len(snapshot.candles) < lookback + 2:
            return None

        last = snapshot.candles[-1]
        window = snapshot.candles[-(lookback + 1) : -1]
        tol = (atr_now or 0.0) * cfg.sr_exit_tolerance_atr
        if tol <= 0:
            tol = max(last.close * 0.001, 1e-9)

        body = abs(last.close - last.open)
        if pos.direction == Direction.LONG:
            resistance = max(c.high for c in window)
            near = last.high >= (resistance - tol)
            bearish_reject = (last.high - last.close) > body and last.close < last.open
            if near and bearish_reject and last.close > pos.entry:
                return "RESISTANCE_EXIT"
            return None

        support = min(c.low for c in window)
        near = last.low <= (support + tol)
        bullish_reject = (last.close - last.low) > body and last.close > last.open
        if near and bullish_reject and last.close < pos.entry:
            return "SUPPORT_EXIT"
        return None

    def _close_trade(
        self,
        state: _OpenPositionState,
        exit_price: float,
        reason: str,
        funding_rate: float | None,
        close_time_ms: int,
        close_qty: float | None = None,
    ) -> BacktestTradeRecord:
        pos = state.position
        qty = pos.quantity if close_qty is None else max(min(close_qty, pos.quantity), 0.0)
        slip = self.cfg.execution.slippage_bps / 10_000.0
        if pos.direction == Direction.LONG:
            filled_exit = exit_price * (1 - slip)
            gross_pnl = (filled_exit - pos.entry) * qty
        else:
            filled_exit = exit_price * (1 + slip)
            gross_pnl = (pos.entry - filled_exit) * qty

        entry_notional = pos.entry * qty
        exit_notional = filled_exit * qty
        fees = (entry_notional + exit_notional) * self._fee_rate(pos.market)

        funding_cost = 0.0
        if pos.market == MarketType.PERP and funding_rate is not None:
            held_ms = max(close_time_ms - state.open_time_ms, 0)
            periods = held_ms // (8 * 60 * 60 * 1000)
            if periods > 0:
                if pos.direction == Direction.LONG:
                    funding_cost = entry_notional * funding_rate * periods
                else:
                    funding_cost = -entry_notional * funding_rate * periods

        pnl = gross_pnl - fees - funding_cost
        equity_before = max(self.risk.equity, 1e-9)
        pnl_pct_of_equity = (pnl / equity_before) * 100.0
        risk_per_unit = abs(pos.entry - state.initial_stop)
        risk_amount = risk_per_unit * qty
        r_mult = (pnl / risk_amount) if risk_amount > 0 else 0.0

        closed = ClosedTrade(
            symbol=pos.symbol,
            market=pos.market,
            direction=pos.direction,
            entry=pos.entry,
            exit_price=filled_exit,
            quantity=qty,
            pnl=pnl,
            pnl_pct_of_equity=pnl_pct_of_equity,
            fee_paid=fees,
            funding_paid=funding_cost,
            close_reason=reason,
            strategy_module=pos.strategy_module,
            regime=pos.regime,
            partial_exit=qty < pos.quantity,
            opened_at_utc=pos.opened_at_utc,
            closed_at_utc=_utc_from_ms(close_time_ms).isoformat(),
        )
        hold_minutes = max((close_time_ms - state.open_time_ms) / 60_000.0, 0.0)
        return BacktestTradeRecord(
            closed_trade=closed,
            r_multiple=r_mult,
            hold_minutes=hold_minutes,
            strategy_module=pos.strategy_module.value,
            regime=pos.regime.value,
        )

    def _mark_open_position(self, snapshot: MarketSnapshot, time_ms: int) -> list[BacktestTradeRecord]:
        if self.open_state is None:
            return []
        pos = self.open_state.position
        if (pos.symbol, pos.market) != (snapshot.symbol, snapshot.market):
            return []

        c = snapshot.candles[-1]
        atr_now = latest(
            atr(
                [x.high for x in snapshot.candles],
                [x.low for x in snapshot.candles],
                [x.close for x in snapshot.candles],
                14,
            )
        )
        closed: list[BacktestTradeRecord] = []

        if self.cfg.execution.enable_time_exit:
            held_minutes = max((time_ms - self.open_state.open_time_ms) / 60_000.0, 0.0)
            if held_minutes >= self.cfg.execution.max_hold_minutes:
                closed.append(self._close_trade(self.open_state, c.close, "TIME_EXIT", snapshot.funding_rate, time_ms))
                return closed

        tp_plan = self._tp_plan(pos)

        if pos.direction == Direction.LONG:
            if c.low <= pos.stop:
                closed.append(self._close_trade(self.open_state, pos.stop, "STOP_LOSS", snapshot.funding_rate, time_ms))
                return closed

            risk = abs(pos.entry - pos.initial_stop)
            if risk > 0 and not pos.trailing_active and self.cfg.execution.enable_trailing_stop:
                move_r = (c.close - pos.entry) / risk
                if move_r >= self.cfg.execution.trailing_activation_rr:
                    pos.trailing_active = True

            while pos.tp_hit_count < len(tp_plan) and c.high >= tp_plan[pos.tp_hit_count][0]:
                idx = pos.tp_hit_count
                tp_price, tp_frac = tp_plan[idx]
                is_last = idx == (len(tp_plan) - 1)
                close_qty = pos.quantity if is_last else max(pos.initial_quantity * tp_frac, 0.0)
                close_qty = min(close_qty, pos.quantity)
                if close_qty <= 0:
                    pos.tp_hit_count += 1
                    continue
                reason = f"TAKE_PROFIT_{idx + 1}" + ("" if is_last else "_PARTIAL")
                closed.append(
                    self._close_trade(
                        self.open_state,
                        tp_price,
                        reason,
                        snapshot.funding_rate,
                        time_ms,
                        close_qty=close_qty,
                    )
                )
                if close_qty < pos.quantity:
                    pos.quantity = max(pos.quantity - close_qty, 0.0)
                    pos.partial_taken = True
                    pos.stop = max(pos.stop, pos.entry)
                    pos.stop_moved_to_be = True
                    pos.trailing_active = self.cfg.execution.enable_trailing_stop
                    pos.trail_anchor = max(pos.trail_anchor, c.high)
                pos.tp_hit_count += 1
                break

            if closed:
                return closed

            if pos.trailing_active and atr_now is not None and atr_now > 0:
                pos.trail_anchor = max(pos.trail_anchor, c.high)
                trail_stop = pos.trail_anchor - (self.cfg.execution.trailing_atr_multiplier * atr_now)
                pos.stop = max(pos.stop, trail_stop)

            sr_reason = self._sr_exit_reason(snapshot, pos, atr_now)
            if sr_reason:
                closed.append(self._close_trade(self.open_state, c.close, sr_reason, snapshot.funding_rate, time_ms))
                return closed

            reason = self._reversal_exit_reason(snapshot, pos)
            if reason:
                closed.append(self._close_trade(self.open_state, c.close, reason, snapshot.funding_rate, time_ms))
            return closed

        if c.high >= pos.stop:
            closed.append(self._close_trade(self.open_state, pos.stop, "STOP_LOSS", snapshot.funding_rate, time_ms))
            return closed
        risk = abs(pos.entry - pos.initial_stop)
        if risk > 0 and not pos.trailing_active and self.cfg.execution.enable_trailing_stop:
            move_r = (pos.entry - c.close) / risk
            if move_r >= self.cfg.execution.trailing_activation_rr:
                pos.trailing_active = True

        while pos.tp_hit_count < len(tp_plan) and c.low <= tp_plan[pos.tp_hit_count][0]:
            idx = pos.tp_hit_count
            tp_price, tp_frac = tp_plan[idx]
            is_last = idx == (len(tp_plan) - 1)
            close_qty = pos.quantity if is_last else max(pos.initial_quantity * tp_frac, 0.0)
            close_qty = min(close_qty, pos.quantity)
            if close_qty <= 0:
                pos.tp_hit_count += 1
                continue
            reason = f"TAKE_PROFIT_{idx + 1}" + ("" if is_last else "_PARTIAL")
            closed.append(
                self._close_trade(
                    self.open_state,
                    tp_price,
                    reason,
                    snapshot.funding_rate,
                    time_ms,
                    close_qty=close_qty,
                )
            )
            if close_qty < pos.quantity:
                pos.quantity = max(pos.quantity - close_qty, 0.0)
                pos.partial_taken = True
                pos.stop = min(pos.stop, pos.entry)
                pos.stop_moved_to_be = True
                pos.trailing_active = self.cfg.execution.enable_trailing_stop
                pos.trail_anchor = min(pos.trail_anchor, c.low) if pos.trail_anchor > 0 else c.low
            pos.tp_hit_count += 1
            break

        if closed:
            return closed

        if pos.trailing_active and atr_now is not None and atr_now > 0:
            if pos.trail_anchor <= 0:
                pos.trail_anchor = c.low
            pos.trail_anchor = min(pos.trail_anchor, c.low)
            trail_stop = pos.trail_anchor + (self.cfg.execution.trailing_atr_multiplier * atr_now)
            pos.stop = min(pos.stop, trail_stop)

        sr_reason = self._sr_exit_reason(snapshot, pos, atr_now)
        if sr_reason:
            closed.append(self._close_trade(self.open_state, c.close, sr_reason, snapshot.funding_rate, time_ms))
            return closed

        reason = self._reversal_exit_reason(snapshot, pos)
        if reason:
            closed.append(self._close_trade(self.open_state, c.close, reason, snapshot.funding_rate, time_ms))
        return closed

    def _open_trade(self, signal: Signal, time_ms: int, qty: float) -> Position:
        qty = _round_qty(qty, 0.000001)
        slip = self.cfg.execution.slippage_bps / 10_000.0
        entry = signal.entry * (1 + slip) if signal.direction == Direction.LONG else signal.entry * (1 - slip)
        pos = Position(
            symbol=signal.symbol,
            market=signal.market,
            direction=signal.direction,
            entry=entry,
            stop=signal.stop,
            initial_stop=signal.stop,
            target_1=signal.target_1,
            target_2=signal.target_2,
            quantity=qty,
            initial_quantity=qty,
            opened_at_utc=_utc_from_ms(time_ms).isoformat(),
            strategy_module=signal.strategy_module,
            regime=signal.regime,
            trailing_active=self.cfg.execution.enable_trailing_stop and self.cfg.execution.enable_trailing_on_full_position,
            trail_anchor=entry,
        )
        self.open_state = _OpenPositionState(
            position=pos,
            open_time_ms=time_ms,
            initial_stop=signal.stop,
        )
        self.open_key = (signal.symbol, signal.market)
        return pos

    def run(self, series: list[HistoricalSeries], warmup_bars: int = 260) -> BacktestResult:
        if not series:
            raise ValueError("No historical series provided")

        # Build a synchronized event tape.
        events: dict[int, list[tuple[int, int]]] = {}
        for s_idx, s in enumerate(series):
            for i in range(warmup_bars, len(s.candles)):
                t = s.candles[i].open_time
                events.setdefault(t, []).append((s_idx, i))
        timeline = sorted(events.keys())
        if not timeline:
            raise ValueError("No timeline events; check warmup/data length")

        equity_curve: list[tuple[int, float]] = [(timeline[0], self.risk.equity)]
        trade_records: list[BacktestTradeRecord] = []

        for time_ms in timeline:
            slot = events[time_ms]
            closed_this_bar = False

            # 1) Manage open position first.
            if self.open_state is not None and self.open_key is not None:
                for s_idx, i in slot:
                    s = series[s_idx]
                    if (s.symbol, s.market) != self.open_key:
                        continue
                    snap = s.snapshot_at(i)
                    closed_records = self._mark_open_position(snap, time_ms)
                    if closed_records:
                        for rec in closed_records:
                            is_partial = rec.closed_trade.partial_exit or rec.closed_trade.close_reason == "TAKE_PROFIT_1_PARTIAL"
                            self.risk.register_close(rec.closed_trade.pnl, time_ms, count_for_streak=not is_partial)
                            equity_curve.append((time_ms, self.risk.equity))
                            trade_records.append(rec)
                        if any(not r.closed_trade.partial_exit for r in closed_records):
                            self.open_state = None
                            self.open_key = None
                            closed_this_bar = True
                    break

            # 2) Only one concurrent position, and no same-bar re-entry after close.
            if self.open_state is not None or closed_this_bar:
                continue

            # 3) Evaluate candidates.
            candidates: list[tuple[Signal, MarketSnapshot]] = []
            for s_idx, i in slot:
                s = series[s_idx]
                snap = s.snapshot_at(i)
                if len(snap.candles) < warmup_bars:
                    continue
                ctx = detect_regime(snap, enable_m4=self.cfg.enable_m4)
                if ctx is None or ctx.active_module.value == "DO_NOTHING":
                    continue
                if self._last_candle_is_abnormal(snap, ctx.atr):
                    continue
                dec = generate_signal(snap, ctx, self.cfg)
                if dec.signal is None:
                    continue
                sig = dec.signal
                if self.cfg.enable_mtf_confirmation:
                    htf_snaps = self._build_mtf_snapshots_from_base(snap)
                    if not self._passes_mtf_confirmation(sig, htf_snaps):
                        continue
                if sig.direction == Direction.SHORT and sig.market != MarketType.PERP:
                    continue
                if not self._funding_filter(sig, snap):
                    continue
                if not self._funding_trend_ok(s, i, sig):
                    continue
                fp = self._fingerprint(sig, snap.candles[-1].open_time)
                if self.last_fingerprint_by_symbol.get(sig.symbol) == fp:
                    continue
                candidates.append((sig, snap))

            chosen = self._choose_best(candidates)
            if chosen is None:
                continue

            sig, snap = chosen
            approved, _, qty, _ = self.risk.pre_trade_check(sig, time_ms)
            if not approved:
                continue

            # Basic execution constraints for historical data.
            mid = (snap.bid + snap.ask) / 2.0
            if mid <= 0:
                continue
            spread_pct = ((snap.ask - snap.bid) / mid) * 100.0
            spread_limit = (
                self.cfg.execution.spread_limit_spot_pct
                if snap.market == MarketType.SPOT
                else self.cfg.execution.spread_limit_perp_pct
            )
            if spread_pct > spread_limit:
                continue
            if snap.quote_volume_24h <= 0:
                continue

            pos = self._open_trade(sig, time_ms, qty)
            if pos.quantity <= 0:
                self.open_state = None
                self.open_key = None
                continue
            self.risk.register_entry(sig.symbol, time_ms)
            self.last_fingerprint_by_symbol[sig.symbol] = self._fingerprint(sig, snap.candles[-1].open_time)

        # Force-close any open trade at final bar close.
        if self.open_state is not None and self.open_key is not None:
            # Find latest available snapshot for this symbol/market.
            sym, market = self.open_key
            target_series = next((s for s in series if s.symbol == sym and s.market == market), None)
            if target_series is not None:
                i = len(target_series.candles) - 1
                snap = target_series.snapshot_at(i)
                close_ms = snap.candles[-1].open_time
                rec = self._close_trade(self.open_state, snap.candles[-1].close, "FORCED_EOD_CLOSE", snap.funding_rate, close_ms)
                self.risk.register_close(rec.closed_trade.pnl, close_ms)
                equity_curve.append((close_ms, self.risk.equity))
                trade_records.append(rec)
            self.open_state = None
            self.open_key = None

        return _build_metrics(
            initial_equity=self.cfg.risk.start_equity,
            final_equity=self.risk.equity,
            equity_curve=equity_curve,
            records=trade_records,
        )


def _max_drawdown_pct(equity_curve: list[tuple[int, float]]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0][1]
    max_dd = 0.0
    for _, e in equity_curve:
        if e > peak:
            peak = e
        if peak > 0:
            dd = (peak - e) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd * 100.0


def _drawdown_profile(equity_curve: list[tuple[int, float]]) -> tuple[float, float, float, int]:
    if not equity_curve:
        return 0.0, 0.0, 0.0, 0

    peak = equity_curve[0][1]
    max_dd = 0.0
    total_dd = 0.0
    max_duration = 0
    duration = 0
    current_dd = 0.0

    for _, equity in equity_curve:
        if equity > peak:
            peak = equity
            duration = 0
        if peak > 0:
            dd = (peak - equity) / peak
        else:
            dd = 0.0
        current_dd = dd
        total_dd += dd
        if dd > 0:
            duration += 1
            max_duration = max(max_duration, duration)
        else:
            duration = 0
        max_dd = max(max_dd, dd)

    avg_dd = total_dd / len(equity_curve)
    return max_dd * 100.0, avg_dd * 100.0, current_dd * 100.0, max_duration


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    arr = sorted(values)
    n = len(arr)
    mid = n // 2
    if n % 2 == 1:
        return arr[mid]
    return (arr[mid - 1] + arr[mid]) / 2.0


def _build_metrics(
    initial_equity: float,
    final_equity: float,
    equity_curve: list[tuple[int, float]],
    records: list[BacktestTradeRecord],
) -> BacktestResult:
    trades = [r.closed_trade for r in records]
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    breakeven = len([p for p in pnls if p == 0])
    total = len(pnls)
    win_rate = (len(wins) / total * 100.0) if total else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    trade_returns = []
    for r in records:
        entry_notional = r.closed_trade.entry * r.closed_trade.quantity
        if entry_notional > 0:
            trade_returns.append(r.closed_trade.pnl / entry_notional)
    sharpe = 0.0
    sortino = 0.0
    if len(trade_returns) >= 2:
        mean_ret = sum(trade_returns) / len(trade_returns)
        var = sum((x - mean_ret) ** 2 for x in trade_returns) / (len(trade_returns) - 1)
        std = math.sqrt(var) if var > 0 else 0.0
        if std > 0:
            sharpe = (mean_ret / std) * math.sqrt(len(trade_returns))
        downside = [x for x in trade_returns if x < 0]
        if len(downside) >= 2:
            down_var = sum((x - 0.0) ** 2 for x in downside) / (len(downside) - 1)
            down_std = math.sqrt(down_var) if down_var > 0 else 0.0
            if down_std > 0:
                sortino = (mean_ret / down_std) * math.sqrt(len(trade_returns))

    expectancy = (sum(pnls) / total) if total else 0.0
    r_mults = [r.r_multiple for r in records]
    avg_r = (sum(r_mults) / len(r_mults)) if r_mults else 0.0
    med_r = _median(r_mults)
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0

    lw = 0
    ll = 0
    cw = 0
    cl = 0
    for p in pnls:
        if p > 0:
            cw += 1
            cl = 0
        elif p < 0:
            cl += 1
            cw = 0
        else:
            cw = 0
            cl = 0
        lw = max(lw, cw)
        ll = max(ll, cl)

    by_module: dict[str, dict[str, float]] = {}
    for r in records:
        mod = r.strategy_module
        d = by_module.setdefault(mod, {"trades": 0.0, "wins": 0.0, "pnl": 0.0})
        d["trades"] += 1
        if r.closed_trade.pnl > 0:
            d["wins"] += 1
        d["pnl"] += r.closed_trade.pnl
    for mod in by_module:
        t = by_module[mod]["trades"]
        w = by_module[mod]["wins"]
        by_module[mod]["win_rate_pct"] = (w / t * 100.0) if t > 0 else 0.0

    by_regime: dict[str, dict[str, float]] = {}
    for r in records:
        regime = r.regime
        d = by_regime.setdefault(regime, {"trades": 0.0, "wins": 0.0, "pnl": 0.0})
        d["trades"] += 1
        if r.closed_trade.pnl > 0:
            d["wins"] += 1
        d["pnl"] += r.closed_trade.pnl
    for regime in by_regime:
        t = by_regime[regime]["trades"]
        w = by_regime[regime]["wins"]
        by_regime[regime]["win_rate_pct"] = (w / t * 100.0) if t > 0 else 0.0

    ret_pct = ((final_equity - initial_equity) / initial_equity * 100.0) if initial_equity > 0 else 0.0
    max_dd, avg_dd, current_dd, max_dd_bars = _drawdown_profile(equity_curve)

    return BacktestResult(
        initial_equity=initial_equity,
        final_equity=final_equity,
        total_return_pct=ret_pct,
        total_trades=total,
        wins=len(wins),
        losses=len(losses),
        breakeven=breakeven,
        win_rate_pct=win_rate,
        profit_factor=profit_factor,
        max_drawdown_pct=max_dd,
        sharpe_ratio_trade_level=sharpe,
        sortino_ratio_trade_level=sortino,
        avg_drawdown_pct=avg_dd,
        current_drawdown_pct=current_dd,
        max_drawdown_duration_bars=max_dd_bars,
        expectancy_per_trade=expectancy,
        avg_r_multiple=avg_r,
        median_r_multiple=med_r,
        avg_win=avg_win,
        avg_loss=avg_loss,
        longest_win_streak=lw,
        longest_loss_streak=ll,
        by_module=by_module,
        by_regime=by_regime,
        equity_curve=equity_curve,
        trades=records,
    )


def save_backtest_outputs(
    result: BacktestResult,
    out_summary_json: str | Path | None = None,
    out_trades_csv: str | Path | None = None,
    out_equity_csv: str | Path | None = None,
) -> None:
    if out_summary_json:
        p = Path(out_summary_json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")

    if out_trades_csv:
        p = Path(out_trades_csv)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "symbol",
                    "market",
                    "direction",
                    "entry",
                    "exit",
                    "qty",
                    "pnl",
                    "fee",
                    "funding",
                    "close_reason",
                    "partial_exit",
                    "r_multiple",
                    "hold_minutes",
                    "strategy_module",
                    "regime",
                    "opened_at_utc",
                    "closed_at_utc",
                ]
            )
            for r in result.trades:
                t = r.closed_trade
                w.writerow(
                    [
                        t.symbol,
                        t.market.value,
                        t.direction.value,
                        t.entry,
                        t.exit_price,
                        t.quantity,
                        t.pnl,
                        t.fee_paid,
                        t.funding_paid,
                        t.close_reason,
                        t.partial_exit,
                        r.r_multiple,
                        r.hold_minutes,
                        r.strategy_module,
                        r.regime,
                        t.opened_at_utc,
                        t.closed_at_utc,
                    ]
                )

    if out_equity_csv:
        p = Path(out_equity_csv)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time_ms", "time_utc", "equity"])
            for t, e in result.equity_curve:
                w.writerow([t, _utc_from_ms(t).isoformat(), e])
