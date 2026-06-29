from __future__ import annotations

from dataclasses import dataclass

from .indicators import adx, atr, ema, latest, median_last, sma
from .types import MarketSnapshot, Regime, StrategyModule


@dataclass
class RegimeContext:
    regime: Regime
    active_module: StrategyModule
    adx: float
    atr: float
    atr_median: float
    ema20: float
    ema50: float
    ema200: float
    vol_sma20: float
    trend_up: bool
    trend_down: bool
    breakout_up: bool
    breakout_down: bool
    reason: str


def detect_regime(snapshot: MarketSnapshot, enable_m4: bool = True) -> RegimeContext | None:
    if len(snapshot.candles) < 260:
        return None

    closes = [c.close for c in snapshot.candles]
    highs = [c.high for c in snapshot.candles]
    lows = [c.low for c in snapshot.candles]
    volumes = [c.volume for c in snapshot.candles]

    ema20_series = ema(closes, 20)
    ema50_series = ema(closes, 50)
    ema200_series = ema(closes, 200)
    atr_series = atr(highs, lows, closes, 14)
    adx_series = adx(highs, lows, closes, 14)
    vol_sma_series = sma(volumes, 20)

    ema20 = latest(ema20_series)
    ema50 = latest(ema50_series)
    ema200 = latest(ema200_series)
    atr_now = latest(atr_series)
    adx_now = latest(adx_series)
    vol_sma20 = latest(vol_sma_series)
    atr_med = median_last(atr_series, 288) or 0.0

    if None in {ema20, ema50, ema200, atr_now, adx_now, vol_sma20}:
        return None

    last = snapshot.candles[-1]
    prev20 = snapshot.candles[-21:-1]
    prior_high = max(c.high for c in prev20)
    prior_low = min(c.low for c in prev20)
    candle_range = last.high - last.low

    breakout_up = (
        last.close > (prior_high + 0.3 * atr_now)
        and candle_range >= (1.2 * atr_now)
        and last.volume >= (1.5 * vol_sma20)
    )
    breakout_down = (
        last.close < (prior_low - 0.3 * atr_now)
        and candle_range >= (1.2 * atr_now)
        and last.volume >= (1.5 * vol_sma20)
    )

    trend_up = bool(ema20 > ema50 > ema200)
    trend_down = bool(ema20 < ema50 < ema200)
    trend_strength_ok = adx_now >= 23 and atr_now >= (0.8 * atr_med if atr_med > 0 else 0)
    low_vol = (adx_now < 18) or (atr_med > 0 and atr_now < 0.8 * atr_med)

    if breakout_up or breakout_down:
        return RegimeContext(
            regime=Regime.BREAKOUT_EXPANSION,
            active_module=StrategyModule.M2,
            adx=adx_now,
            atr=atr_now,
            atr_median=atr_med,
            ema20=ema20,
            ema50=ema50,
            ema200=ema200,
            vol_sma20=vol_sma20,
            trend_up=trend_up,
            trend_down=trend_down,
            breakout_up=breakout_up,
            breakout_down=breakout_down,
            reason="Breakout + volatility/volume expansion",
        )

    if trend_strength_ok and (trend_up or trend_down):
        return RegimeContext(
            regime=Regime.TREND,
            active_module=StrategyModule.M1,
            adx=adx_now,
            atr=atr_now,
            atr_median=atr_med,
            ema20=ema20,
            ema50=ema50,
            ema200=ema200,
            vol_sma20=vol_sma20,
            trend_up=trend_up,
            trend_down=trend_down,
            breakout_up=False,
            breakout_down=False,
            reason="MA alignment + ADX + ATR regime",
        )

    if adx_now < 20 and atr_med > 0 and atr_now >= 1.1 * atr_med:
        return RegimeContext(
            regime=Regime.RANGE_HIGH_VOL,
            active_module=StrategyModule.M3,
            adx=adx_now,
            atr=atr_now,
            atr_median=atr_med,
            ema20=ema20,
            ema50=ema50,
            ema200=ema200,
            vol_sma20=vol_sma20,
            trend_up=trend_up,
            trend_down=trend_down,
            breakout_up=False,
            breakout_down=False,
            reason="Range + elevated volatility",
        )

    low_vol_range = prior_high - prior_low
    tradable_chop = low_vol and low_vol_range >= max(1.2 * atr_now, last.close * 0.0025)
    regime = Regime.LOW_VOL_CHOP if low_vol else Regime.LOW_VOL_CHOP
    return RegimeContext(
        regime=regime,
        active_module=StrategyModule.M4 if (enable_m4 and tradable_chop) else StrategyModule.DO_NOTHING,
        adx=adx_now,
        atr=atr_now,
        atr_median=atr_med,
        ema20=ema20,
        ema50=ema50,
        ema200=ema200,
        vol_sma20=vol_sma20,
        trend_up=trend_up,
        trend_down=trend_down,
        breakout_up=False,
        breakout_down=False,
        reason=(
            "Low-volatility chop - mean reversion trading"
            if (enable_m4 and tradable_chop)
            else "Low-volatility or non-actionable chop"
        ),
    )
