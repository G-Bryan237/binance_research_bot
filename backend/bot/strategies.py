from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import BotConfig
from .indicators import (
    bollinger_bands,
    detect_divergence,
    ichimoku,
    latest,
    macd,
    obv,
    rolling_std,
    rsi,
    sma,
    stochastic_rsi,
    vwap,
    volume_profile,
)
from .regime import RegimeContext
from .types import Direction, MarketSnapshot, Signal, StrategyModule


@dataclass
class StrategyDecision:
    signal: Optional[Signal]
    reason: str


def _find_recent_swing_low(candles, start: int, end: int) -> float | None:
    if end - start < 5:
        return None
    window = candles[start:end]
    return min(c.low for c in window)


def _find_recent_swing_high(candles, start: int, end: int) -> float | None:
    if end - start < 5:
        return None
    window = candles[start:end]
    return max(c.high for c in window)


def _build_signal(
    snapshot: MarketSnapshot,
    direction: Direction,
    entry: float,
    stop: float,
    target_1: float,
    target_2: float,
    context: RegimeContext,
    module: StrategyModule,
    rationale: str,
) -> Signal | None:
    if direction == Direction.LONG:
        risk = entry - stop
    else:
        risk = stop - entry
    if risk <= 0:
        return None
    rr = abs((target_2 - entry) / risk) if direction == Direction.LONG else abs((entry - target_2) / risk)
    return Signal(
        symbol=snapshot.symbol,
        market=snapshot.market,
        direction=direction,
        entry=entry,
        stop=stop,
        target_1=target_1,
        target_2=target_2,
        regime=context.regime,
        strategy_module=module,
        rationale=rationale,
        risk_multiple=round(rr, 2),
    )


def module_1_trend_pullback_sweep(
    snapshot: MarketSnapshot,
    context: RegimeContext,
    cfg: BotConfig,
) -> StrategyDecision:
    candles = snapshot.candles
    if len(candles) < 60:
        return StrategyDecision(None, "Not enough candles")
    if context.adx < 18 or (context.atr_median > 0 and context.atr < 0.8 * context.atr_median):
        return StrategyDecision(None, "Trend weak or volatility too low")

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    last_close = closes[-1]

    if cfg.enable_macd:
        macd_line, signal_line, _ = macd(closes, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
        macd_now = latest(macd_line)
        signal_now = latest(signal_line)
        if macd_now is None or signal_now is None:
            return StrategyDecision(None, "MACD unavailable")
        if context.trend_up and macd_now <= signal_now:
            return StrategyDecision(None, "MACD momentum not supportive")
        if context.trend_down and macd_now >= signal_now:
            return StrategyDecision(None, "MACD momentum not supportive")

    if cfg.enable_ichimoku:
        tenkan, kijun, span_a, span_b, _, _, _ = ichimoku(
            highs,
            lows,
            closes,
            cfg.ichimoku_conversion,
            cfg.ichimoku_base,
            cfg.ichimoku_span_b,
            cfg.ichimoku_displacement,
        )
        tenkan_now = latest(tenkan)
        kijun_now = latest(kijun)
        span_a_now = latest(span_a)
        span_b_now = latest(span_b)
        if None in {tenkan_now, kijun_now, span_a_now, span_b_now}:
            return StrategyDecision(None, "Ichimoku unavailable")
        cloud_top = max(span_a_now, span_b_now)
        cloud_bottom = min(span_a_now, span_b_now)
        if context.trend_up and (last_close <= cloud_top or tenkan_now <= kijun_now):
            return StrategyDecision(None, "Ichimoku trend filter blocked")
        if context.trend_down and (last_close >= cloud_bottom or tenkan_now >= kijun_now):
            return StrategyDecision(None, "Ichimoku trend filter blocked")

    sweep = candles[-2]
    confirm = candles[-1]

    pullback_long = min(c.low for c in candles[-4:]) <= max(context.ema20, context.ema50)
    pullback_short = max(c.high for c in candles[-4:]) >= min(context.ema20, context.ema50)
    delta_abs = max(0.15 * context.atr, confirm.close * 0.0008)

    if context.trend_up and pullback_long:
        swing_low = _find_recent_swing_low(candles, len(candles) - 42, len(candles) - 2)
        if swing_low is None:
            return StrategyDecision(None, "No prior swing low found")
        swept = sweep.low < (swing_low - delta_abs) and sweep.close > swing_low
        confirmed = confirm.close > sweep.high
        if swept and confirmed:
            entry = confirm.close
            stop = sweep.low - (0.2 * context.atr)
            risk = entry - stop
            t1 = entry + 1.5 * risk
            t2 = entry + 2.5 * risk
            sig = _build_signal(
                snapshot,
                Direction.LONG,
                entry,
                stop,
                t1,
                t2,
                context,
                StrategyModule.M1,
                "Trend pullback + liquidity sweep + momentum reclaim",
            )
            return StrategyDecision(sig, "M1 long setup")

    if context.trend_down and pullback_short:
        swing_high = _find_recent_swing_high(candles, len(candles) - 42, len(candles) - 2)
        if swing_high is None:
            return StrategyDecision(None, "No prior swing high found")
        swept = sweep.high > (swing_high + delta_abs) and sweep.close < swing_high
        confirmed = confirm.close < sweep.low
        if swept and confirmed:
            entry = confirm.close
            stop = sweep.high + (0.2 * context.atr)
            risk = stop - entry
            t1 = entry - 1.5 * risk
            t2 = entry - 2.5 * risk
            sig = _build_signal(
                snapshot,
                Direction.SHORT,
                entry,
                stop,
                t1,
                t2,
                context,
                StrategyModule.M1,
                "Trend pullback + liquidity sweep + momentum reclaim",
            )
            return StrategyDecision(sig, "M1 short setup")

    return StrategyDecision(None, "M1 setup not present")


def module_2_breakout_retest_fvg(
    snapshot: MarketSnapshot,
    context: RegimeContext,
    cfg: BotConfig,
) -> StrategyDecision:
    candles = snapshot.candles
    if len(candles) < 80:
        return StrategyDecision(None, "Not enough candles")

    # Search for a fresh breakout in recent candles, then require FVG mitigation entry.
    breakout_idx: int | None = None
    breakout_direction: Direction | None = None

    start = max(25, len(candles) - 18)
    for i in range(start, len(candles) - 1):
        base = candles[i - 20 : i]
        prior_high = max(c.high for c in base)
        prior_low = min(c.low for c in base)
        c = candles[i]
        range_i = c.high - c.low
        vol_ok = c.volume >= (1.4 * context.vol_sma20)
        vol_expand = range_i >= (1.2 * context.atr)
        if c.close > prior_high + (0.3 * context.atr) and vol_ok and vol_expand:
            breakout_idx = i
            breakout_direction = Direction.LONG
        if c.close < prior_low - (0.3 * context.atr) and vol_ok and vol_expand:
            breakout_idx = i
            breakout_direction = Direction.SHORT

    if breakout_idx is None or breakout_direction is None:
        return StrategyDecision(None, "No clean breakout found")

    closes = [c.close for c in candles]
    volumes = [c.volume for c in candles]
    last = candles[-1]

    if cfg.enable_obv:
        obv_series = obv(closes, volumes)
        if len(obv_series) >= 6:
            obv_slope = obv_series[-1] - obv_series[-6]
            if breakout_direction == Direction.LONG and obv_slope <= 0:
                return StrategyDecision(None, "OBV not confirming breakout")
            if breakout_direction == Direction.SHORT and obv_slope >= 0:
                return StrategyDecision(None, "OBV not confirming breakout")

    if cfg.enable_vwap:
        vwap_series = vwap(candles, cfg.vwap_lookback, cfg.vwap_use_current_session)
        vwap_now = latest(vwap_series)
        if vwap_now is None:
            return StrategyDecision(None, "VWAP unavailable")
        if breakout_direction == Direction.LONG and last.close < vwap_now:
            return StrategyDecision(None, "VWAP not confirming breakout")
        if breakout_direction == Direction.SHORT and last.close > vwap_now:
            return StrategyDecision(None, "VWAP not confirming breakout")

    if cfg.enable_volume_profile:
        profile = volume_profile(
            candles,
            lookback=cfg.volume_profile_lookback,
            bins=cfg.volume_profile_bins,
            value_area_pct=cfg.volume_profile_value_area_pct,
        )
        if profile is not None:
            if breakout_direction == Direction.LONG and last.close <= profile["value_area_high"]:
                return StrategyDecision(None, "Volume profile not confirming breakout")
            if breakout_direction == Direction.SHORT and last.close >= profile["value_area_low"]:
                return StrategyDecision(None, "Volume profile not confirming breakout")

    # FVG detection: gap between candle N and N+2 with momentum at N+1.
    # We check small recent window after breakout for non-retail imbalance.
    n0 = max(2, breakout_idx - 2)
    n1 = min(len(candles) - 3, breakout_idx + 2)
    fvg_zone: tuple[float, float] | None = None
    for n in range(n0, n1 + 1):
        c0 = candles[n]
        c1 = candles[n + 1]
        c2 = candles[n + 2]
        momentum = (c1.high - c1.low) >= (1.1 * context.atr)
        if breakout_direction == Direction.LONG:
            if c0.high < c2.low and momentum:
                fvg_zone = (c0.high, c2.low)  # [lower, upper]
        else:
            if c0.low > c2.high and momentum:
                fvg_zone = (c2.high, c0.low)  # [lower, upper]

    if fvg_zone is None:
        return StrategyDecision(None, "No valid FVG after breakout")

    # Wait one candle after imbalance + enter on mitigation into the gap.
    low_gap, high_gap = fvg_zone
    prev = candles[-2]
    if last.open_time <= candles[min(len(candles) - 1, breakout_idx + 3)].open_time:
        return StrategyDecision(None, "Waiting one candle after imbalance")

    if breakout_direction == Direction.LONG:
        mitigated = last.low <= high_gap and last.high >= low_gap
        reclaim = last.close > high_gap and prev.close >= low_gap
        if mitigated and reclaim:
            entry = last.close
            stop = low_gap - (0.2 * context.atr)
            risk = entry - stop
            t1 = entry + 1.5 * risk
            t2 = entry + 2.2 * risk
            sig = _build_signal(
                snapshot,
                Direction.LONG,
                entry,
                stop,
                t1,
                t2,
                context,
                StrategyModule.M2,
                "Breakout retest + bullish FVG mitigation",
            )
            return StrategyDecision(sig, "M2 long setup")
    else:
        mitigated = last.high >= low_gap and last.low <= high_gap
        reclaim = last.close < low_gap and prev.close <= high_gap
        if mitigated and reclaim:
            entry = last.close
            stop = high_gap + (0.2 * context.atr)
            risk = stop - entry
            t1 = entry - 1.5 * risk
            t2 = entry - 2.2 * risk
            sig = _build_signal(
                snapshot,
                Direction.SHORT,
                entry,
                stop,
                t1,
                t2,
                context,
                StrategyModule.M2,
                "Breakout retest + bearish FVG mitigation",
            )
            return StrategyDecision(sig, "M2 short setup")

    return StrategyDecision(None, "Breakout found but FVG mitigation entry missing")


def module_3_liquidation_sweep_reversal(
    snapshot: MarketSnapshot,
    context: RegimeContext,
    cfg: BotConfig,
) -> StrategyDecision:
    candles = snapshot.candles
    if len(candles) < 40:
        return StrategyDecision(None, "Not enough candles")

    # Avoid countertrend reversals when trend regime is strong.
    if context.adx >= 25 and (context.trend_up or context.trend_down):
        return StrategyDecision(None, "Strong trend - no mean-reversion reversal")

    last = candles[-1]
    prev = candles[-2]
    closes = [c.close for c in candles]
    lookback = candles[-22:-2]
    prior_high = max(c.high for c in lookback)
    prior_low = min(c.low for c in lookback)
    prior_mid = (prior_high + prior_low) / 2.0

    body = abs(last.close - last.open)
    upper_wick = last.high - max(last.open, last.close)
    lower_wick = min(last.open, last.close) - last.low
    vol_spike = last.volume >= (1.8 * context.vol_sma20)
    sweep_buffer = max(0.12 * context.atr, last.close * 0.0008)

    divergence = None
    if cfg.enable_divergence_detection:
        rsi_series = rsi(closes, 14)
        divergence = detect_divergence(
            closes,
            rsi_series,
            lookback=cfg.divergence_lookback,
            swing_window=cfg.divergence_swing_window,
        )

    if cfg.enable_vwap:
        vwap_series = vwap(candles, cfg.vwap_lookback, cfg.vwap_use_current_session)
        vwap_now = latest(vwap_series)
        if vwap_now is None:
            return StrategyDecision(None, "VWAP unavailable")
        if last.close > vwap_now and context.trend_up:
            return StrategyDecision(None, "VWAP filter blocked reversal")
        if last.close < vwap_now and context.trend_down:
            return StrategyDecision(None, "VWAP filter blocked reversal")

    profile = None
    if cfg.enable_volume_profile:
        profile = volume_profile(
            candles,
            lookback=cfg.volume_profile_lookback,
            bins=cfg.volume_profile_bins,
            value_area_pct=cfg.volume_profile_value_area_pct,
        )

    # Long reversal after downside sweep.
    swept_low = last.low < (prior_low - sweep_buffer)
    long_wick_down = body > 0 and (lower_wick / body) >= 1.8
    closes_inside = last.close > prior_low
    bullish_structure = last.close > prev.high
    oi_confirm = True
    if snapshot.open_interest is not None:
        oi_confirm = snapshot.open_interest > 0
    if swept_low and long_wick_down and vol_spike and closes_inside and bullish_structure and oi_confirm:
        if divergence == "bearish":
            return StrategyDecision(None, "Bearish divergence against long reversal")
        if profile is not None and last.close > profile["value_area_low"]:
            return StrategyDecision(None, "Volume profile not at value low")
        entry = last.close
        stop = last.low - (0.2 * context.atr)
        risk = entry - stop
        t1 = prior_mid
        t2 = prior_high
        sig = _build_signal(
            snapshot,
            Direction.LONG,
            entry,
            stop,
            t1,
            t2,
            context,
            StrategyModule.M3,
            "Liquidation sweep reversal to range mean/opposite edge",
        )
        return StrategyDecision(sig, "M3 long setup")

    # Short reversal after upside sweep.
    swept_high = last.high > (prior_high + sweep_buffer)
    long_wick_up = body > 0 and (upper_wick / body) >= 1.8
    closes_inside = last.close < prior_high
    bearish_structure = last.close < prev.low
    if swept_high and long_wick_up and vol_spike and closes_inside and bearish_structure and oi_confirm:
        if divergence == "bullish":
            return StrategyDecision(None, "Bullish divergence against short reversal")
        if profile is not None and last.close < profile["value_area_high"]:
            return StrategyDecision(None, "Volume profile not at value high")
        entry = last.close
        stop = last.high + (0.2 * context.atr)
        risk = stop - entry
        t1 = prior_mid
        t2 = prior_low
        sig = _build_signal(
            snapshot,
            Direction.SHORT,
            entry,
            stop,
            t1,
            t2,
            context,
            StrategyModule.M3,
            "Liquidation sweep reversal to range mean/opposite edge",
        )
        return StrategyDecision(sig, "M3 short setup")

    return StrategyDecision(None, "M3 setup not present")


def module_4_low_vol_mean_reversion(
    snapshot: MarketSnapshot,
    context: RegimeContext,
    cfg: BotConfig,
) -> StrategyDecision:
    candles = snapshot.candles
    if len(candles) < 80:
        return StrategyDecision(None, "Not enough candles")

    closes = [c.close for c in candles]
    rsi_series = rsi(closes, 14)
    rsi_now = latest(rsi_series)
    rsi_prev = rsi_series[-2] if len(rsi_series) >= 2 else None
    if rsi_now is None or rsi_prev is None:
        return StrategyDecision(None, "M4 RSI unavailable")

    sma20_series = sma(closes, 20)
    sma20 = latest(sma20_series)
    sma20_prev = sma20_series[-2] if len(sma20_series) >= 2 else None
    if sma20 is None or sma20_prev is None:
        return StrategyDecision(None, "M4 SMA unavailable")

    if cfg.enable_bollinger_bands:
        mid, upper, lower = bollinger_bands(closes, cfg.bb_length, cfg.bb_std_dev)
        upper_band = latest(upper)
        lower_band = latest(lower)
        if upper_band is None or lower_band is None:
            return StrategyDecision(None, "Bollinger bands unavailable")
        band_width = upper_band - lower_band
    else:
        std20_series = rolling_std(closes, 20)
        std20 = latest(std20_series)
        if std20 is None:
            return StrategyDecision(None, "M4 bands unavailable")
        upper_band = sma20 + (1.8 * std20)
        lower_band = sma20 - (1.8 * std20)
        band_width = upper_band - lower_band

    if band_width <= 0:
        return StrategyDecision(None, "M4 band width invalid")

    last = candles[-1]
    prev = candles[-2]

    range_window = candles[-30:]
    range_high = max(c.high for c in range_window)
    range_low = min(c.low for c in range_window)
    range_width = range_high - range_low
    if range_width <= max(1.2 * context.atr, last.close * 0.002):
        return StrategyDecision(None, "M4 range too tight")

    near_low = last.low <= (range_low + 0.2 * range_width)
    near_high = last.high >= (range_high - 0.2 * range_width)
    bullish_reclaim = last.close > prev.close and last.close > last.open
    bearish_reclaim = last.close < prev.close and last.close < last.open
    flat_mean = abs(sma20 - sma20_prev) / max(last.close, 1e-9) <= 0.0007
    volume_ok = last.volume >= (0.8 * context.vol_sma20)

    # M4 long: oversold + lower-band/range-edge interaction + reclaim.
    long_cross = rsi_prev <= 35 and rsi_now >= 36
    band_reclaim_long = last.low <= lower_band and last.close > lower_band
    range_reclaim_long = near_low and last.close > (range_low + 0.15 * range_width)
    if cfg.enable_stoch_rsi:
        k_series, d_series = stochastic_rsi(closes, cfg.stoch_rsi_length, cfg.stoch_rsi_k, cfg.stoch_rsi_d)
        k_now = latest(k_series)
        k_prev = k_series[-2] if len(k_series) >= 2 else None
        if k_now is None or k_prev is None:
            return StrategyDecision(None, "Stoch RSI unavailable")
        long_cross = long_cross and k_prev <= 20 and k_now >= 25

    if cfg.enable_vwap:
        vwap_series = vwap(candles, cfg.vwap_lookback, cfg.vwap_use_current_session)
        vwap_now = latest(vwap_series)
        if vwap_now is None:
            return StrategyDecision(None, "VWAP unavailable")
        if last.close > vwap_now:
            return StrategyDecision(None, "VWAP filter blocked mean reversion")

    if cfg.enable_volume_profile:
        profile = volume_profile(
            candles,
            lookback=cfg.volume_profile_lookback,
            bins=cfg.volume_profile_bins,
            value_area_pct=cfg.volume_profile_value_area_pct,
        )
        if profile is not None:
            tol = max(last.close * 0.001, 0.2 * context.atr)
            if last.close > (profile["value_area_low"] + tol):
                return StrategyDecision(None, "Volume profile not at value low")
    if long_cross and band_reclaim_long and range_reclaim_long and bullish_reclaim and flat_mean and volume_ok:
        entry = last.close
        stop = min(last.low, range_low) - max(0.2 * context.atr, entry * 0.0008)
        risk = entry - stop
        if risk <= 0:
            return StrategyDecision(None, "M4 long risk invalid")
        t1 = max(sma20, entry + (0.8 * risk))
        t2 = min(range_high, entry + (1.4 * risk))
        if t2 <= t1:
            t2 = entry + (1.4 * risk)
        sig = _build_signal(
            snapshot,
            Direction.LONG,
            entry,
            stop,
            t1,
            t2,
            context,
            StrategyModule.M4,
            "Low-vol mean reversion: oversold band/range-edge reclaim",
        )
        return StrategyDecision(sig, "M4 long setup")

    # M4 short: overbought + upper-band/range-edge interaction + reclaim.
    short_cross = rsi_prev >= 65 and rsi_now <= 64
    band_reclaim_short = last.high >= upper_band and last.close < upper_band
    range_reclaim_short = near_high and last.close < (range_high - 0.15 * range_width)
    if cfg.enable_stoch_rsi:
        k_series, d_series = stochastic_rsi(closes, cfg.stoch_rsi_length, cfg.stoch_rsi_k, cfg.stoch_rsi_d)
        k_now = latest(k_series)
        k_prev = k_series[-2] if len(k_series) >= 2 else None
        if k_now is None or k_prev is None:
            return StrategyDecision(None, "Stoch RSI unavailable")
        short_cross = short_cross and k_prev >= 80 and k_now <= 75

    if cfg.enable_vwap:
        vwap_series = vwap(candles, cfg.vwap_lookback, cfg.vwap_use_current_session)
        vwap_now = latest(vwap_series)
        if vwap_now is None:
            return StrategyDecision(None, "VWAP unavailable")
        if last.close < vwap_now:
            return StrategyDecision(None, "VWAP filter blocked mean reversion")

    if cfg.enable_volume_profile:
        profile = volume_profile(
            candles,
            lookback=cfg.volume_profile_lookback,
            bins=cfg.volume_profile_bins,
            value_area_pct=cfg.volume_profile_value_area_pct,
        )
        if profile is not None:
            tol = max(last.close * 0.001, 0.2 * context.atr)
            if last.close < (profile["value_area_high"] - tol):
                return StrategyDecision(None, "Volume profile not at value high")
    if short_cross and band_reclaim_short and range_reclaim_short and bearish_reclaim and flat_mean and volume_ok:
        entry = last.close
        stop = max(last.high, range_high) + max(0.2 * context.atr, entry * 0.0008)
        risk = stop - entry
        if risk <= 0:
            return StrategyDecision(None, "M4 short risk invalid")
        t1 = min(sma20, entry - (0.8 * risk))
        t2 = max(range_low, entry - (1.4 * risk))
        if t2 >= t1:
            t2 = entry - (1.4 * risk)
        sig = _build_signal(
            snapshot,
            Direction.SHORT,
            entry,
            stop,
            t1,
            t2,
            context,
            StrategyModule.M4,
            "Low-vol mean reversion: overbought band/range-edge reclaim",
        )
        return StrategyDecision(sig, "M4 short setup")

    return StrategyDecision(None, "M4 setup not present")


def module_5_vwap_volume_profile(
    snapshot: MarketSnapshot,
    context: RegimeContext,
    cfg: BotConfig,
) -> StrategyDecision:
    """
    M5: VWAP + Volume Profile institutional level trading.
    
    This module trades mean reversion at key institutional levels:
    - VWAP deviation extremes (price far from VWAP)
    - Volume Profile value area boundaries (VAH/VAL rejection)
    - POC (Point of Control) as target
    
    Entry requires:
    - Price at value area edge + VWAP deviation
    - Volume confirmation
    - Reclaim candle structure
    """
    candles = snapshot.candles
    if len(candles) < 200:
        return StrategyDecision(None, "Not enough candles for M5")

    if not cfg.enable_vwap or not cfg.enable_volume_profile:
        return StrategyDecision(None, "M5 requires VWAP and Volume Profile enabled")

    closes = [c.close for c in candles]
    last = candles[-1]
    prev = candles[-2]

    # Calculate VWAP
    vwap_series = vwap(candles, cfg.vwap_lookback, cfg.vwap_use_current_session)
    vwap_now = latest(vwap_series)
    if vwap_now is None:
        return StrategyDecision(None, "VWAP unavailable")

    # Calculate Volume Profile
    profile = volume_profile(
        candles,
        lookback=cfg.volume_profile_lookback,
        bins=cfg.volume_profile_bins,
        value_area_pct=cfg.volume_profile_value_area_pct,
    )
    if profile is None:
        return StrategyDecision(None, "Volume profile unavailable")

    poc = profile["poc"]
    val = profile["value_area_low"]
    vah = profile["value_area_high"]

    # Calculate VWAP deviation (how far price is from VWAP)
    vwap_dev = (last.close - vwap_now) / vwap_now if vwap_now > 0 else 0
    vwap_dev_threshold = 0.008  # 0.8% deviation minimum

    # Volume confirmation
    volume_ok = last.volume >= (0.9 * context.vol_sma20)

    # Calculate value area position
    va_range = vah - val
    if va_range <= 0:
        return StrategyDecision(None, "Invalid value area range")

    near_val = last.low <= (val + 0.15 * va_range)
    near_vah = last.high >= (vah - 0.15 * va_range)

    # Reclaim structure
    bullish_reclaim = last.close > prev.close and last.close > last.open
    bearish_reclaim = last.close < prev.close and last.close < last.open

    # RSI filter for exhaustion confirmation
    rsi_series = rsi(closes, 14)
    rsi_now = latest(rsi_series)
    if rsi_now is None:
        return StrategyDecision(None, "RSI unavailable for M5")

    # M5 Long: Price at VAL + below VWAP + oversold + bullish reclaim
    if (
        near_val
        and vwap_dev < -vwap_dev_threshold
        and rsi_now <= 40
        and bullish_reclaim
        and volume_ok
        and last.close > val
    ):
        entry = last.close
        stop = min(last.low, val) - (0.25 * context.atr)
        risk = entry - stop
        if risk <= 0:
            return StrategyDecision(None, "M5 long risk invalid")
        t1 = poc  # First target at POC
        t2 = vwap_now  # Second target at VWAP
        if t1 <= entry:
            t1 = entry + (0.8 * risk)
        if t2 <= t1:
            t2 = entry + (1.4 * risk)
        sig = _build_signal(
            snapshot,
            Direction.LONG,
            entry,
            stop,
            t1,
            t2,
            context,
            StrategyModule.M5,
            "VWAP deviation + VAL rejection + bullish reclaim",
        )
        return StrategyDecision(sig, "M5 long setup")

    # M5 Short: Price at VAH + above VWAP + overbought + bearish reclaim
    if (
        near_vah
        and vwap_dev > vwap_dev_threshold
        and rsi_now >= 60
        and bearish_reclaim
        and volume_ok
        and last.close < vah
    ):
        entry = last.close
        stop = max(last.high, vah) + (0.25 * context.atr)
        risk = stop - entry
        if risk <= 0:
            return StrategyDecision(None, "M5 short risk invalid")
        t1 = poc  # First target at POC
        t2 = vwap_now  # Second target at VWAP
        if t1 >= entry:
            t1 = entry - (0.8 * risk)
        if t2 >= t1:
            t2 = entry - (1.4 * risk)
        sig = _build_signal(
            snapshot,
            Direction.SHORT,
            entry,
            stop,
            t1,
            t2,
            context,
            StrategyModule.M5,
            "VWAP deviation + VAH rejection + bearish reclaim",
        )
        return StrategyDecision(sig, "M5 short setup")

    return StrategyDecision(None, "M5 setup not present")


def module_6_mtf_divergence(
    snapshot: MarketSnapshot,
    context: RegimeContext,
    cfg: BotConfig,
    htf_candles: list | None = None,
) -> StrategyDecision:
    """
    M6: Multi-timeframe divergence trading.
    
    This module catches reversals using:
    - Higher timeframe (HTF) RSI or MACD divergence as signal
    - Lower timeframe (LTF) entry trigger confirmation
    - Volume spike on reversal candle
    
    Entry requires:
    - Divergence detected on HTF (bullish or bearish)
    - LTF confirmation candle (engulfing or strong close)
    - Volume expansion
    """
    candles = snapshot.candles
    if len(candles) < 60:
        return StrategyDecision(None, "Not enough candles for M6")

    if not cfg.enable_divergence_detection:
        return StrategyDecision(None, "M6 requires divergence detection enabled")

    closes = [c.close for c in candles]
    last = candles[-1]
    prev = candles[-2]

    # Check for RSI divergence on current timeframe (LTF)
    rsi_series = rsi(closes, 14)
    rsi_now = latest(rsi_series)
    if rsi_now is None:
        return StrategyDecision(None, "RSI unavailable for M6")

    ltf_divergence = detect_divergence(
        closes,
        rsi_series,
        lookback=cfg.divergence_lookback,
        swing_window=cfg.divergence_swing_window,
    )

    # Check MACD divergence if enabled
    macd_divergence = None
    if cfg.enable_macd_divergence:
        macd_line, signal_line, histogram = macd(closes, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
        macd_divergence = detect_divergence(
            closes,
            macd_line,
            lookback=cfg.divergence_lookback,
            swing_window=cfg.divergence_swing_window,
        )

    # Combine divergence signals (either RSI or MACD)
    bullish_div = ltf_divergence == "bullish" or macd_divergence == "bullish"
    bearish_div = ltf_divergence == "bearish" or macd_divergence == "bearish"

    if not bullish_div and not bearish_div:
        return StrategyDecision(None, "No divergence detected")

    # HTF divergence check (if htf_candles provided)
    htf_confirmed = True
    if htf_candles and len(htf_candles) >= 30:
        htf_closes = [c.close for c in htf_candles]
        htf_rsi = rsi(htf_closes, 14)
        htf_div = detect_divergence(
            htf_closes,
            htf_rsi,
            lookback=min(cfg.divergence_lookback, 15),
            swing_window=2,
        )
        # HTF should align with LTF divergence
        if bullish_div and htf_div != "bullish":
            htf_confirmed = False
        if bearish_div and htf_div != "bearish":
            htf_confirmed = False

    # Volume spike confirmation
    vol_spike = last.volume >= (1.3 * context.vol_sma20)

    # Confirmation candle structure
    body = abs(last.close - last.open)
    prev_body = abs(prev.close - prev.open)
    bullish_confirm = (
        last.close > last.open
        and last.close > prev.high
        and body >= (0.8 * prev_body)
    )
    bearish_confirm = (
        last.close < last.open
        and last.close < prev.low
        and body >= (0.8 * prev_body)
    )

    # Avoid counter-trend in strong trends
    if context.adx >= 28:
        if bullish_div and context.trend_down:
            pass  # Allow bullish divergence in downtrend (potential reversal)
        elif bearish_div and context.trend_up:
            pass  # Allow bearish divergence in uptrend (potential reversal)
        else:
            return StrategyDecision(None, "M6 blocked: divergence aligns with trend, not reversal")

    # M6 Long: Bullish divergence + confirmation
    if bullish_div and bullish_confirm and vol_spike and htf_confirmed:
        entry = last.close
        # Find recent swing low for stop
        recent_lows = [c.low for c in candles[-20:]]
        swing_low = min(recent_lows)
        stop = swing_low - (0.2 * context.atr)
        risk = entry - stop
        if risk <= 0:
            return StrategyDecision(None, "M6 long risk invalid")
        t1 = entry + (1.2 * risk)
        t2 = entry + (2.0 * risk)
        sig = _build_signal(
            snapshot,
            Direction.LONG,
            entry,
            stop,
            t1,
            t2,
            context,
            StrategyModule.M6,
            "Bullish RSI/MACD divergence + volume spike + confirmation candle",
        )
        return StrategyDecision(sig, "M6 long setup")

    # M6 Short: Bearish divergence + confirmation
    if bearish_div and bearish_confirm and vol_spike and htf_confirmed:
        entry = last.close
        # Find recent swing high for stop
        recent_highs = [c.high for c in candles[-20:]]
        swing_high = max(recent_highs)
        stop = swing_high + (0.2 * context.atr)
        risk = stop - entry
        if risk <= 0:
            return StrategyDecision(None, "M6 short risk invalid")
        t1 = entry - (1.2 * risk)
        t2 = entry - (2.0 * risk)
        sig = _build_signal(
            snapshot,
            Direction.SHORT,
            entry,
            stop,
            t1,
            t2,
            context,
            StrategyModule.M6,
            "Bearish RSI/MACD divergence + volume spike + confirmation candle",
        )
        return StrategyDecision(sig, "M6 short setup")

    return StrategyDecision(None, "M6 divergence found but confirmation missing")


def generate_signal(snapshot: MarketSnapshot, context: RegimeContext, cfg: BotConfig) -> StrategyDecision:
    """
    Generate trading signal based on regime context.
    
    Primary modules (M1-M4) are regime-mapped.
    Secondary modules (M5-M6) are checked as overlays if primary produces no signal.
    """
    if context.active_module == StrategyModule.DO_NOTHING:
        return StrategyDecision(None, "Do nothing regime")
    
    # Try primary regime-mapped module first
    primary_result: StrategyDecision | None = None
    if context.active_module == StrategyModule.M1:
        primary_result = module_1_trend_pullback_sweep(snapshot, context, cfg)
    elif context.active_module == StrategyModule.M2:
        primary_result = module_2_breakout_retest_fvg(snapshot, context, cfg)
    elif context.active_module == StrategyModule.M3:
        primary_result = module_3_liquidation_sweep_reversal(snapshot, context, cfg)
    elif context.active_module == StrategyModule.M4:
        primary_result = module_4_low_vol_mean_reversion(snapshot, context, cfg)
    elif context.active_module == StrategyModule.M5:
        primary_result = module_5_vwap_volume_profile(snapshot, context, cfg)
    elif context.active_module == StrategyModule.M6:
        primary_result = module_6_mtf_divergence(snapshot, context, cfg)
    
    # If primary module produced a signal, return it
    if primary_result and primary_result.signal is not None:
        return primary_result
    
    # Try M5 (VWAP/Volume Profile) as secondary overlay if enabled
    if cfg.enable_m5 and context.active_module != StrategyModule.M5:
        m5_result = module_5_vwap_volume_profile(snapshot, context, cfg)
        if m5_result.signal is not None:
            return m5_result
    
    # Try M6 (MTF Divergence) as secondary overlay if enabled
    if cfg.enable_m6 and context.active_module != StrategyModule.M6:
        m6_result = module_6_mtf_divergence(snapshot, context, cfg)
        if m6_result.signal is not None:
            return m6_result
    
    # Return primary result (no signal) with its reason
    if primary_result:
        return primary_result
    return StrategyDecision(None, "Unhandled strategy module")
