from __future__ import annotations

import math
from statistics import median
from datetime import datetime, timezone
from collections import deque


def sma(values: list[float], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if length <= 0 or len(values) < length:
        return out
    rolling = sum(values[:length])
    out[length - 1] = rolling / length
    for i in range(length, len(values)):
        rolling += values[i] - values[i - length]
        out[i] = rolling / length
    return out


def ema(values: list[float], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if length <= 0 or len(values) < length:
        return out
    alpha = 2.0 / (length + 1.0)
    seed = sum(values[:length]) / length
    out[length - 1] = seed
    prev = seed
    for i in range(length, len(values)):
        prev = alpha * values[i] + (1 - alpha) * prev
        out[i] = prev
    return out


def true_range(high: list[float], low: list[float], close: list[float]) -> list[float]:
    out = [0.0] * len(close)
    for i in range(len(close)):
        if i == 0:
            out[i] = high[i] - low[i]
            continue
        out[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    return out


def atr(high: list[float], low: list[float], close: list[float], length: int = 14) -> list[float | None]:
    n = len(close)
    out: list[float | None] = [None] * n
    if n <= length:
        return out
    tr = true_range(high, low, close)
    seed = sum(tr[1 : length + 1]) / length
    out[length] = seed
    prev = seed
    for i in range(length + 1, n):
        prev = ((prev * (length - 1)) + tr[i]) / length
        out[i] = prev
    return out


def adx(high: list[float], low: list[float], close: list[float], length: int = 14) -> list[float | None]:
    n = len(close)
    out: list[float | None] = [None] * n
    if n <= (length * 2):
        return out

    tr = true_range(high, low, close)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n

    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0

    tr14 = [0.0] * n
    plus14 = [0.0] * n
    minus14 = [0.0] * n

    tr14[length] = sum(tr[1 : length + 1])
    plus14[length] = sum(plus_dm[1 : length + 1])
    minus14[length] = sum(minus_dm[1 : length + 1])

    plus_di = [None] * n
    minus_di = [None] * n
    dx = [None] * n

    for i in range(length, n):
        if i > length:
            tr14[i] = tr14[i - 1] - (tr14[i - 1] / length) + tr[i]
            plus14[i] = plus14[i - 1] - (plus14[i - 1] / length) + plus_dm[i]
            minus14[i] = minus14[i - 1] - (minus14[i - 1] / length) + minus_dm[i]

        if tr14[i] == 0:
            continue
        pdi = 100.0 * (plus14[i] / tr14[i])
        mdi = 100.0 * (minus14[i] / tr14[i])
        plus_di[i] = pdi
        minus_di[i] = mdi
        denom = pdi + mdi
        if denom == 0:
            continue
        dx[i] = 100.0 * abs(pdi - mdi) / denom

    first_adx_idx = length * 2
    window = [x for x in dx[length : first_adx_idx + 1] if x is not None]
    if len(window) < length:
        return out

    seed_adx = sum(window[:length]) / length
    out[first_adx_idx] = seed_adx
    prev = seed_adx

    for i in range(first_adx_idx + 1, n):
        if dx[i] is None:
            continue
        prev = ((prev * (length - 1)) + dx[i]) / length
        out[i] = prev
    return out


def latest(values: list[float | None]) -> float | None:
    for v in reversed(values):
        if v is not None:
            return v
    return None


def median_last(values: list[float | None], window: int) -> float | None:
    cleaned = [v for v in values if v is not None]
    if len(cleaned) < window:
        return None
    return float(median(cleaned[-window:]))


def rolling_std(values: list[float], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if length <= 1 or len(values) < length:
        return out
    for i in range(length - 1, len(values)):
        window = values[i - length + 1 : i + 1]
        mean = sum(window) / length
        variance = sum((x - mean) ** 2 for x in window) / length
        out[i] = math.sqrt(variance)
    return out


def rsi(values: list[float], length: int = 14) -> list[float | None]:
    n = len(values)
    out: list[float | None] = [None] * n
    if n <= length:
        return out

    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        change = values[i] - values[i - 1]
        gains[i] = max(change, 0)
        losses[i] = max(-change, 0)

    avg_gain = [None] * n
    avg_loss = [None] * n
    seed_gain = sum(gains[1 : length + 1]) / length
    seed_loss = sum(losses[1 : length + 1]) / length
    avg_gain[length] = seed_gain
    avg_loss[length] = seed_loss

    for i in range(length + 1, n):
        avg_gain[i] = (avg_gain[i - 1] * (length - 1) + gains[i]) / length
        avg_loss[i] = (avg_loss[i - 1] * (length - 1) + losses[i]) / length

    for i in range(length, n):
        if avg_loss[i] == 0:
            out[i] = 100.0
        else:
            rs = avg_gain[i] / avg_loss[i]
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def _rolling_sma_optional(values: list[float | None], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if length <= 0 or len(values) < length:
        return out
    for i in range(length - 1, len(values)):
        window = values[i - length + 1 : i + 1]
        if any(v is None for v in window):
            continue
        out[i] = sum(float(v) for v in window if v is not None) / length
    return out


def stochastic_rsi(
    values: list[float],
    length: int = 14,
    smooth_k: int = 3,
    smooth_d: int = 3,
) -> tuple[list[float | None], list[float | None]]:
    """Stochastic RSI (%K and %D), scaled 0-100."""
    rsi_series = rsi(values, length)
    stoch: list[float | None] = [None] * len(values)
    if len(values) < length:
        return stoch, stoch
    for i in range(length, len(values)):
        if rsi_series[i] is None:
            continue
        window = [v for v in rsi_series[i - length + 1 : i + 1] if v is not None]
        if len(window) < length:
            continue
        min_r = min(window)
        max_r = max(window)
        if max_r == min_r:
            stoch[i] = 0.0
        else:
            stoch[i] = ((rsi_series[i] - min_r) / (max_r - min_r)) * 100.0

    k = _rolling_sma_optional(stoch, smooth_k)
    d = _rolling_sma_optional(k, smooth_d)
    return k, d


def obv(closes: list[float], volumes: list[float]) -> list[float]:
    """On-Balance Volume (OBV) cumulative series."""
    if not closes or len(closes) != len(volumes):
        return []
    out = [0.0] * len(closes)
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            out[i] = out[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            out[i] = out[i - 1] - volumes[i]
        else:
            out[i] = out[i - 1]
    return out


def _midpoint(highs: list[float], lows: list[float], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(highs)
    if length <= 0 or len(highs) < length:
        return out
    for i in range(length - 1, len(highs)):
        window_high = max(highs[i - length + 1 : i + 1])
        window_low = min(lows[i - length + 1 : i + 1])
        out[i] = (window_high + window_low) / 2.0
    return out


def ichimoku(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    conversion: int = 9,
    base: int = 26,
    span_b: int = 52,
    displacement: int = 26,
) -> tuple[
    list[float | None],
    list[float | None],
    list[float | None],
    list[float | None],
    list[float | None],
    list[float | None],
    list[float | None],
]:
    """Return Ichimoku lines: tenkan, kijun, span_a, span_b, lead_a, lead_b, chikou."""
    n = len(highs)
    tenkan = _midpoint(highs, lows, conversion)
    kijun = _midpoint(highs, lows, base)
    span_b_line = _midpoint(highs, lows, span_b)
    span_a_line = [None] * n
    for i in range(n):
        if tenkan[i] is not None and kijun[i] is not None:
            span_a_line[i] = (tenkan[i] + kijun[i]) / 2.0

    lead_span_a = [None] * n
    lead_span_b = [None] * n
    for i in range(n):
        if span_a_line[i] is not None and i + displacement < n:
            lead_span_a[i + displacement] = span_a_line[i]
        if span_b_line[i] is not None and i + displacement < n:
            lead_span_b[i + displacement] = span_b_line[i]

    chikou = [None] * n
    for i in range(n):
        idx = i - displacement
        if idx >= 0:
            chikou[idx] = closes[i]

    return tenkan, kijun, span_a_line, span_b_line, lead_span_a, lead_span_b, chikou


def vwap(
    candles: list,
    length: int = 60,
    use_session: bool = False,
) -> list[float | None]:
    """VWAP series using HLC3 and volume; supports rolling or session reset."""
    out: list[float | None] = [None] * len(candles)
    if not candles:
        return out

    if use_session:
        cum_pv = 0.0
        cum_vol = 0.0
        current_day: str | None = None
        for i, c in enumerate(candles):
            day = datetime.fromtimestamp(c.open_time / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
            if current_day is None or day != current_day:
                current_day = day
                cum_pv = 0.0
                cum_vol = 0.0
            price = (c.high + c.low + c.close) / 3.0
            cum_pv += price * c.volume
            cum_vol += c.volume
            if cum_vol > 0:
                out[i] = cum_pv / cum_vol
        return out

    if length <= 0:
        length = len(candles)
    window: deque[tuple[float, float]] = deque()
    window_pv = 0.0
    window_vol = 0.0
    for i, c in enumerate(candles):
        price = (c.high + c.low + c.close) / 3.0
        pv = price * c.volume
        window.append((pv, c.volume))
        window_pv += pv
        window_vol += c.volume
        if len(window) > length:
            old_pv, old_vol = window.popleft()
            window_pv -= old_pv
            window_vol -= old_vol
        if window_vol > 0:
            out[i] = window_pv / window_vol
    return out


def volume_profile(
    candles: list,
    lookback: int = 200,
    bins: int = 50,
    value_area_pct: float = 0.7,
) -> dict[str, float] | None:
    """Compute a simple volume profile over recent candles."""
    if not candles:
        return None
    sample = candles[-lookback:] if lookback > 0 else candles
    low = min(c.low for c in sample)
    high = max(c.high for c in sample)
    if high <= low or bins <= 0:
        return None

    bin_size = (high - low) / bins
    volumes = [0.0] * bins
    for c in sample:
        price = (c.high + c.low + c.close) / 3.0
        idx = int((price - low) / bin_size)
        idx = max(0, min(idx, bins - 1))
        volumes[idx] += c.volume

    total = sum(volumes)
    if total <= 0:
        return None

    poc_idx = max(range(bins), key=lambda i: volumes[i])
    ranked = sorted(range(bins), key=lambda i: volumes[i], reverse=True)
    selected = set()
    running = 0.0
    for i in ranked:
        selected.add(i)
        running += volumes[i]
        if (running / total) >= value_area_pct:
            break

    va_low_idx = min(selected) if selected else poc_idx
    va_high_idx = max(selected) if selected else poc_idx
    return {
        "poc": low + (poc_idx + 0.5) * bin_size,
        "value_area_low": low + va_low_idx * bin_size,
        "value_area_high": low + (va_high_idx + 1) * bin_size,
    }


def detect_divergence(
    prices: list[float],
    indicator: list[float | None],
    lookback: int = 20,
    swing_window: int = 3,
) -> str | None:
    """Detect basic bullish/bearish divergence between price and an indicator."""
    if len(prices) < (lookback + swing_window * 2 + 1):
        return None
    start = max(0, len(prices) - lookback)
    lows: list[int] = []
    highs: list[int] = []
    for i in range(start + swing_window, len(prices) - swing_window):
        window = prices[i - swing_window : i + swing_window + 1]
        if prices[i] == min(window):
            lows.append(i)
        if prices[i] == max(window):
            highs.append(i)

    def _last_two(points: list[int]) -> tuple[int, int] | None:
        if len(points) < 2:
            return None
        return points[-2], points[-1]

    low_pair = _last_two(lows)
    if low_pair:
        i1, i2 = low_pair
        if indicator[i1] is not None and indicator[i2] is not None:
            if prices[i2] < prices[i1] and indicator[i2] > indicator[i1]:
                return "bullish"

    high_pair = _last_two(highs)
    if high_pair:
        i1, i2 = high_pair
        if indicator[i1] is not None and indicator[i2] is not None:
            if prices[i2] > prices[i1] and indicator[i2] < indicator[i1]:
                return "bearish"

    return None


def macd(values: list[float], fast: int = 12, slow: int = 26, signal_len: int = 9) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Calculate MACD (fast EMA - slow EMA), signal line (EMA of MACD), and histogram."""
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    
    macd_line = [None if f is None or s is None else f - s for f, s in zip(fast_ema, slow_ema)]
    signal_line = ema([m for m in macd_line if m is not None], signal_len)
    
    # Pad signal_line to match length
    padded_signal = [None] * len(macd_line)
    signal_idx = 0
    for i, m in enumerate(macd_line):
        if m is not None:
            if signal_idx < len(signal_line) and signal_line[signal_idx] is not None:
                padded_signal[i] = signal_line[signal_idx]
            signal_idx += 1
    
    histogram = [None if m is None or s is None else m - s for m, s in zip(macd_line, padded_signal)]
    return macd_line, padded_signal, histogram


def bollinger_bands(values: list[float], length: int = 20, std_dev: float = 2.0) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Calculate Bollinger Bands: middle (SMA), upper (SMA + std), lower (SMA - std)."""
    n = len(values)
    middle = [None] * n
    upper = [None] * n
    lower = [None] * n
    
    if length <= 0 or n < length:
        return middle, upper, lower
    
    for i in range(length - 1, n):
        window = values[i - length + 1 : i + 1]
        sma_val = sum(window) / length
        variance = sum((x - sma_val) ** 2 for x in window) / length
        std = variance ** 0.5
        
        middle[i] = sma_val
        upper[i] = sma_val + (std_dev * std)
        lower[i] = sma_val - (std_dev * std)
    
    return middle, upper, lower
