# System Spec

## Regime Rules
- `TREND`: ADX(14) >= 23, EMA20/50/200 aligned, ATR14 >= 0.8 x rolling ATR median.
- `BREAKOUT_EXPANSION`: 20-bar break with close beyond boundary by >= 0.3 ATR, candle range >= 1.2 ATR, volume >= 1.5 x volume SMA20.
- `RANGE_HIGH_VOL`: ADX < 20 and ATR14 >= 1.1 x rolling ATR median.
- `LOW_VOL_CHOP`: ADX < 18 or ATR14 < 0.8 x rolling ATR median.
- Module map: `BREAKOUT_EXPANSION -> M2`, `TREND -> M1`, `RANGE_HIGH_VOL -> M3`, `LOW_VOL_CHOP -> M4` (if enabled and tradable range), else `DO_NOTHING`.
- Multi-timeframe confirmation can require higher-timeframe directional alignment before entry.

## Strategy Rules
- `M1` Trend Pullback + Liquidity Sweep:
  - Trend by EMA alignment + ADX.
  - Pullback into EMA20/EMA50 zone.
  - Sweep of prior swing low/high by max(0.15 ATR, 0.08%) and close back inside.
  - Entry after close reclaim confirmation.
  - No trade if trend weak or vol too low.
- `M2` Breakout Retest + FVG Mitigation:
  - No consolidation trades.
  - Require clean breakout + volatility + volume expansion.
  - FVG from candle N to N+2 around momentum candle.
  - Wait one candle, then enter only on mitigation + reclaim.
  - Skip weak/low-volume fakeouts.
- `M3` Liquidation Sweep Reversal:
  - Sweep beyond recent high/low with long wick and volume spike.
  - Optional OI/funding confirmation.
  - Entry only after close back inside range + reversal structure.
  - Targets at range midpoint and opposite edge.
- `M4` Low-Volatility Mean-Reversion:
  - Only in low-vol chop with adequate range width.
  - Entry requires RSI extreme + band/range-edge interaction + reclaim candle.
  - Long: oversold reclaim from lower band / range low.
  - Short: overbought reclaim from upper band / range high.
  - Target structure favors fast mean reversion to SMA/range interior.
- `M5` VWAP + Volume Profile (Institutional Levels):
  - Overlay module (can trigger in any regime when enabled).
  - Trades mean reversion at institutional levels.
  - Entry requires: price at Value Area edge (VAL/VAH) + VWAP deviation extreme.
  - Long: price at VAL + below VWAP + RSI <= 40 + bullish reclaim.
  - Short: price at VAH + above VWAP + RSI >= 60 + bearish reclaim.
  - Targets at POC (Point of Control) and VWAP.
  - Requires `BOT_ENABLE_VWAP=true` and `BOT_ENABLE_VOLUME_PROFILE=true`.
- `M6` Multi-Timeframe Divergence:
  - Overlay module (can trigger in any regime when enabled).
  - Catches reversals using RSI/MACD divergence.
  - Entry requires: bullish or bearish divergence + confirmation candle + volume spike.
  - Optional HTF divergence alignment for higher conviction.
  - Blocked in strong trends unless divergence is counter-trend (reversal).
  - Requires `BOT_ENABLE_DIVERGENCE_DETECTION=true`.

## Risk Controls
- Equity range designed for $10-$100 accounts.
- Risk per trade: 0.75% equity by default.
- M4 position risk is scaled down with `BOT_M4_RISK_MULTIPLIER` (default 0.5).
- Optional module performance scorecard can temporarily disable underperforming modules.
- Daily stop: 2.5% or equivalent risk lock.
- Weekly stop: 6% lock.
- Max trades/day and per symbol/day limits.
- Loss-streak cooldown and day lock.
- Default leverage `1x`, optional `2x` for perps if enabled.
- Always supports `DO_NOTHING`.

## Spot vs Perps Logic
- Spot preferred for longs where possible.
- Perps used for shorts and two-sided setups.
- Funding filter blocks high negative carry direction.
- Perps only in isolated-risk style logic (no cross-risk assumptions).

## Execution Rules
- Pre-trade checks: spread, slippage estimate, 24h quote volume.
- Quantity rounded to exchange step size.
- Respect min notional and symbol filters.
- Frequency cap and fresh-signal requirement.
- Optional partial TP at `target_1` and ATR-based trailing stop for remaining size.

## Paper Trading Setup
- Spot testnet base URL: `https://testnet.binance.vision`.
- Perp testnet base URL: `https://demo-fapi.binance.com`.
- Simulates fees, slippage, and funding.
- Logs all entries/exits and risk lock events.

## Signals and Alerts
- Signal payload includes:
  - asset, direction, entry, stop, target(s), regime, module, risk multiple.
- Alerts include:
  - entry, exit, stop-loss hit, daily max loss lock, funding spike warning.
- Delivery: console + optional webhook/Telegram/email.

## Mistakes Avoided
- Pump chasing blocked by abnormal candle guard.
- Meme coin block via hard whitelist only.
- Over-trading prevented by caps/cooldowns/fresh-signal checks.

## Data Requirements
- Required: OHLCV, spread (bid/ask), ATR, 24h quote volume, funding rate.
- Optional: open interest.

## Backtest Checklist
- Minimum 12 months on 5m data (24 months preferred).
- Metrics: win rate, profit factor, max drawdown, Sharpe, expectancy.
- Include fees, slippage, funding.
- Use out-of-sample/walk-forward validation.
