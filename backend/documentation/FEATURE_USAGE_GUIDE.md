# Feature Usage Guide - Quick Reference

## 1. Enabling Features

### Conservative Configuration (Recommended for live testing)
```bash
# Risk management
BOT_ENABLE_DRAWDOWN_SCALING=true          # Reduce size during drawdowns
BOT_DRAWDOWN_SCALE_THRESHOLD_PCT=0.01     # Start scaling at 1% drawdown

# Exit management
BOT_ENABLE_PARTIAL_TP=true                # Take 50% profit at T1
BOT_ENABLE_TRAILING_STOP=true             # Trail 50% remaining

# Signal quality
BOT_ENABLE_MTF_CONFIRMATION=true          # Validate on higher timeframes
BOT_ENABLE_MODULE_SCORECARD=true          # Disable underperforming modules
```

### Aggressive Configuration (Testnet/Paper trading only)
```bash
# All of the above, plus:
BOT_ENABLE_DEPTH_ANALYSIS=true            # Microstructure analysis
BOT_DYNAMIC_SLIPPAGE_ENABLED=true         # Dynamic entry slippage
BOT_DRAWDOWN_SCALE_FACTOR=0.5             # Aggressive size reduction
```

### Minimal Configuration (Debug/Research)
```bash
BOT_ENABLE_DRAWDOWN_SCALING=false
BOT_ENABLE_PARTIAL_TP=false
BOT_ENABLE_TRAILING_STOP=false
BOT_ENABLE_MTF_CONFIRMATION=false
BOT_ENABLE_MODULE_SCORECARD=false
BOT_ENABLE_DEPTH_ANALYSIS=false
BOT_DYNAMIC_SLIPPAGE_ENABLED=false
```

---

## 2. Monitoring Behavior

### Drawdown-Aware Sizing

**What to watch**:
- Position size decreases as daily/weekly drawdown increases
- Look for `BOT_DRAWDOWN_SCALE_FACTOR` applied to `risk_cash` in logs

**Expected behavior** (example with $100 equity, 0.75% normal risk):
```
Drawdown = 0%   → Scale = 1.00 → Risk = 0.75% ($0.75)
Drawdown = 1%   → Scale = 1.00 → Risk = 0.75% ($0.75)  [at threshold]
Drawdown = 2%   → Scale = 0.90 → Risk = 0.675% ($0.675)
Drawdown = 3%   → Scale = 0.80 → Risk = 0.60% ($0.60)
Drawdown = 5%   → Scale = 0.75 → Risk = 0.5625% ($0.5625) [minimum]
```

### Dynamic Slippage

**What to watch**:
- Entry prices vary based on orderbook conditions
- Bid/ask imbalance > 1.3 should increase slippage
- Check MarketSnapshot.depth.bid_ask_imbalance() method

**Expected behavior**:
```
Strong buying pressure (imbalance 1.5):
  - Base slippage: 8 bps
  - Extra slippage: (1.5 - 1.0) × 100 = 50 bps cap
  - Dynamic: 8 + 50 = 58 bps
  - Fill: entry * (1 + 0.0058) for longs

Strong selling pressure (imbalance 0.5):
  - Base slippage: 8 bps
  - Extra slippage: (1.0 - 0.5) × 100 = 50 bps
  - Dynamic: 8 + 50 = 58 bps
  - Fill: entry * (1 - 0.0058) for shorts
```

### Partial TP + Trailing

**What to watch**:
- First exit at target_1 closes exactly 50% (or configured fraction)
- Stop moves to breakeven after partial exit
- Remaining position trails with ATR stop

**Expected log sequence**:
```
1. Trade opened: qty=100, entry=100, target_1=120, target_2=140
2. Price reaches 120: TAKE_PROFIT_1_PARTIAL (qty=50, pnl=$10)
3. Stop updated to 100 (breakeven)
4. Trailing activated: trail_anchor=120, stop=120-(1.2*ATR)
5. Price moves to 135: trail_anchor=135, stop=135-(1.2*ATR)
6. Price drops: STOP_LOSS or TAKE_PROFIT_2 (remaining 50)
```

---

## 3. Configuration Tuning Guide

### Drawdown Scaling Fine-Tuning

**Conservative** (Reduce size quickly):
```bash
BOT_DRAWDOWN_SCALE_THRESHOLD_PCT=0.005    # Start at 0.5%
BOT_DRAWDOWN_SCALE_FACTOR=0.50            # Hard minimum 50%
```

**Aggressive** (Allow larger sizes during minor drawdowns):
```bash
BOT_DRAWDOWN_SCALE_THRESHOLD_PCT=0.03     # Start at 3%
BOT_DRAWDOWN_SCALE_FACTOR=0.90            # Hard minimum 90%
```

**Moderate** (Default - good balance):
```bash
BOT_DRAWDOWN_SCALE_THRESHOLD_PCT=0.01     # Start at 1%
BOT_DRAWDOWN_SCALE_FACTOR=0.75            # Hard minimum 75%
```

### Trailing Stop Tuning

**Tighter trailing** (capture more profit, risk more stops):
```bash
BOT_TRAILING_ATR_MULTIPLIER=0.8           # Tighter stop
```

**Looser trailing** (give more room, risk smaller stops):
```bash
BOT_TRAILING_ATR_MULTIPLIER=1.5           # Looser stop
```

### Module Scorecard Sensitivity

**Strict** (disable modules quickly):
```bash
BOT_MODULE_PERF_MIN_TRADES=3              # Only 3 trades before evaluating
BOT_MODULE_PERF_MIN_WIN_RATE=0.50         # Require 50% win rate
BOT_MODULE_PERF_DISABLE_MINUTES=60        # Re-enable after 1 hour
```

**Lenient** (keep modules enabled longer):
```bash
BOT_MODULE_PERF_MIN_TRADES=20             # Need 20 trades to evaluate
BOT_MODULE_PERF_MIN_WIN_RATE=0.20         # Accept 20% win rate
BOT_MODULE_PERF_DISABLE_MINUTES=360       # Re-enable after 6 hours
```

---

## 4. Real-World Scenarios

### Scenario 1: Recovery from Losing Streak

**Initial state**:
- Equity: $100
- 3 consecutive losses: $98.25 (1.75% daily drawdown)

**Drawdown scaling applied**:
- Drawdown: 1.75% (exceeds 1% threshold)
- Excess: 0.75%
- Reduction: 0.75% × 10 = 7.5%
- New scale: max(0.75, 1.0 - 0.075) = 0.925
- **Result**: Next trade uses 92.5% of normal risk size instead of 100%

### Scenario 2: Strong Buying Pressure Entry

**Orderbook conditions**:
- Bid volume (top 5 levels): 500 BTC
- Ask volume (top 5 levels): 300 BTC
- Imbalance ratio: 500/300 = 1.67

**Dynamic slippage calculation** (SHORT entry):
- Base slippage: 8 bps
- Imbalance > 1.0 (buying pressure)
- Extra: min((1.67 - 1.0) × 100, 50) = 50 bps
- **Dynamic slippage: 58 bps**
- Entry at 51,000: 51,000 × (1 - 0.0058) = 50,704 fill

### Scenario 3: Multi-Timeframe Rejection

**5m signal generated**:
- Signal: LONG on M1 module
- 5m: Strong uptrend (ADX=25)

**15m confirmation check**:
- 15m: Strong downtrend (ADX=30)
- **Result**: Entry BLOCKED - higher timeframe opposes signal

**1h confirmation check**:
- 1h: Weak trend (ADX=15)
- **Result**: Entry ALLOWED - 1h is neutral/supports long direction

---

## 5. Troubleshooting

### Orderbook depth returns None

**Issue**: `MarketSnapshot.depth` is `None` even with `BOT_ENABLE_DEPTH_ANALYSIS=true`

**Causes**:
1. API endpoint returned error (check logs for 429/403)
2. Symbol not supported by exchange
3. Network timeout

**Solution**:
- Check API rate limits
- Verify symbol is valid (e.g., BTCUSDT not BTC/USDT)
- Increase `BOT_DATA_TIMEOUT_SECONDS`

### Dynamic slippage not changing

**Issue**: Slippage stays constant at base value

**Causes**:
1. `BOT_ENABLE_DEPTH_ANALYSIS=false`
2. `BOT_DYNAMIC_SLIPPAGE_ENABLED=false`
3. Depth data not fetched (None)

**Solution**:
- Ensure both flags are `true`
- Verify orderbook depth fetching works
- Check logs for "optional market-data fetch skipped"

### Position sizes not scaling down during drawdown

**Issue**: Positions same size throughout drawdown

**Causes**:
1. `BOT_ENABLE_DRAWDOWN_SCALING=false`
2. Drawdown below `BOT_DRAWDOWN_SCALE_THRESHOLD_PCT`

**Solution**:
- Ensure `BOT_ENABLE_DRAWDOWN_SCALING=true`
- Lower threshold if drawdown is minor (e.g., 0.5% instead of 1%)
- Check daily/weekly drawdown calculation in logs

### Modules staying disabled too long

**Issue**: Module disabled but not re-enabling after time window

**Causes**:
1. New win after disable resets disable timer
2. Very conservative `BOT_MODULE_PERF_MIN_WIN_RATE`

**Solution**:
- Check disable time with `BOT_MODULE_PERF_DISABLE_MINUTES`
- Adjust min win rate to be more realistic for your data
- Consider disabling scorecard if too aggressive

---

## 6. Performance Testing Checklist

- [ ] Backtest with `BOT_ENABLE_DRAWDOWN_SCALING=false` → Baseline
- [ ] Backtest with `BOT_ENABLE_DRAWDOWN_SCALING=true` → Compare results
- [ ] Monitor partial TP closure at exactly 50% of position size
- [ ] Confirm ATR trailing stop activates after partial exit
- [ ] Verify multi-timeframe blocks at least some invalid entries
- [ ] Check module scorecard disables underperforming modules
- [ ] Enable depth analysis on testnet for 24+ hours → Verify stability
- [ ] Monitor API rate limits with depth enabled
- [ ] Compare fill prices with/without dynamic slippage

---

## 7. Best Practices

✅ **DO**:
- Start with conservative settings on testnet/paper
- Monitor first 100+ trades before live deployment
- Enable drawdown scaling (low risk, high reward)
- Use multi-timeframe confirmation on volatile pairs
- Test one new feature at a time

❌ **DON'T**:
- Enable depth analysis without monitoring API limits
- Set min win rate too high (>60% is unrealistic)
- Use aggressive drawdown scaling (< 0.5 factor) for small accounts
- Disable module scorecard entirely
- Use dynamic slippage without understanding order book depth

---

**Last Updated**: May 9, 2026
**Version**: 2.0 (with May 2026 features)
