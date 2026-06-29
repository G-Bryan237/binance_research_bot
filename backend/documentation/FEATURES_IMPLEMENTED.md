# Binance Research Bot - Advanced Features Implementation

**Date: May 9, 2026**

## Summary

This document outlines all advanced trading features implemented in the Binance Research Bot, including the features requested after initial development.

---

## ✅ Implemented Features

### 1. True Partial TP (50% Scale-Out) + ATR Trailing Stop

**Status**: ✅ ALREADY IMPLEMENTED (Enhanced with dynamic configuration)

**Location**: [bot/execution.py](bot/execution.py#L164-L217)

**Details**:
- At target_1, bot automatically closes 50% of position (configurable via `BOT_PARTIAL_TP_FRACTION`)
- Stop loss moved to breakeven to protect initial capital
- Remaining 50% position trails behind price using ATR-based stop
- Trailing stop uses `BOT_TRAILING_ATR_MULTIPLIER` (default: 1.2x ATR)
- Second target (target_2) closes remaining position

**Configuration Parameters**:
- `BOT_ENABLE_PARTIAL_TP` (true/false, default: true)
- `BOT_PARTIAL_TP_FRACTION` (0.0-1.0, default: 0.5)
- `BOT_ENABLE_TRAILING_STOP` (true/false, default: true)
- `BOT_TRAILING_ATR_MULTIPLIER` (float, default: 1.2)

**Behavior Example**:
- Position opens at 100 USDT
- Target_1 at 120 USDT: 50% size (50 USDT notional) closes for 10 USDT profit
- Stop moves to 100 USDT (breakeven)
- Remaining 50% trails with 1.2x ATR stop

---

### 2. Multi-Timeframe Confirmation (5m Signal Filtered by 15m/1h)

**Status**: ✅ ALREADY IMPLEMENTED

**Location**: [bot/engine.py](bot/engine.py#L125-L165)

**Details**:
- Primary signal generated on 5m timeframe
- Before entry, bot validates against higher timeframe (15m, 1h, etc.)
- Higher timeframe must show directional support or be neutral
- Blocks entries if higher timeframe shows strong opposition

**Confirmation Rules**:
- Long entries: Higher timeframe must show strong up trend (ADX ≥ 20) OR breakout up, not strong down trend
- Short entries: Higher timeframe must show strong down trend (ADX ≥ 20) OR breakout down, not strong up trend
- Special: M3/M4 modules require even stricter opposition blocking (ADX ≥ 25)

**Configuration Parameters**:
- `BOT_ENABLE_MTF_CONFIRMATION` (true/false, default: true)
- `BOT_MTF_CONFIRM_TIMEFRAMES` (comma-separated list, default: "15m")

---

### 3. Dynamic Module Adaptation (Temporary Auto-Disable)

**Status**: ✅ ALREADY IMPLEMENTED

**Location**: [bot/engine.py](bot/engine.py#L211-L227)

**Details**:
- Bot tracks performance metrics for each strategy module (M1, M2, M3, M4)
- Win rate calculated over rolling window of recent trades
- If module win rate drops below threshold, module is temporarily disabled
- Disabled modules cannot generate new entry signals
- Auto-enabled after configured timeout period

**Performance Tracking**:
- Tracks last N trades per module (lookback window)
- Computes win rate = (winning trades) / (total trades)
- Triggers disable if: `win_rate < min_win_rate` AND `total_trades >= min_trades_threshold`

**Configuration Parameters**:
- `BOT_ENABLE_MODULE_SCORECARD` (true/false, default: true)
- `BOT_MODULE_PERF_LOOKBACK` (int trades, default: 10)
- `BOT_MODULE_PERF_MIN_TRADES` (int, default: 4)
- `BOT_MODULE_PERF_MIN_WIN_RATE` (float 0.0-1.0, default: 0.30)
- `BOT_MODULE_PERF_DISABLE_MINUTES` (int, default: 180)

**Alert**: Bot sends `MODULE_DISABLED` alert when module is temporarily disabled.

---

### 4. Depth-Aware Microstructure Analysis

**Status**: ✅ NEW IMPLEMENTATION

**Location**: [bot/types.py](bot/types.py#L35-L65), [bot/data.py](bot/data.py)

**Details**:
- Bot now fetches real-time orderbook depth (top 20 levels)
- Analyzes bid/ask imbalance to detect market microstructure conditions
- Detects orderbook walls and imbalances
- Provides foundation for dynamic slippage modeling

**Key Metrics**:
- **Bid/Ask Imbalance Ratio** = (sum of bid volumes) / (sum of ask volumes)
  - Ratio > 1.3: Strong buying pressure
  - Ratio < 0.77: Strong selling pressure
  - Near 1.0: Balanced market

**New Types** ([types.py](bot/types.py)):
```python
@dataclass
class OrderBookLevel:
    price: float
    quantity: float

@dataclass
class OrderBookDepth:
    bid_levels: list[OrderBookLevel]
    ask_levels: list[OrderBookLevel]
    timestamp_ms: int
    
    def bid_ask_imbalance(levels: int = 5) -> float:
        """Ratio of bid/ask volume"""
    
    def spread_bps(mid_price: float) -> float:
        """Spread in basis points"""
```

**Data Fetching** ([data.py](bot/data.py#L242-L274)):
- Automatic orderbook depth fetching in `fetch_snapshot()` method
- Supports both Spot and Perpetuals orderbooks
- Gracefully handles failures with optional depth data

**Configuration Parameters**:
- `BOT_ENABLE_DEPTH_ANALYSIS` (true/false, default: false)
- `BOT_ORDERBOOK_DEPTH_LIMIT` (int, default: 20)
- `BOT_IMBALANCE_THRESHOLD` (float, default: 1.3)

---

### 5. Dynamic Slippage Model (Depth-Based)

**Status**: ✅ NEW IMPLEMENTATION

**Location**: [bot/execution.py](bot/execution.py#L37-L68)

**Details**:
- Dynamically calculates slippage based on real-time orderbook conditions
- Increases slippage when orderbook imbalance worsens
- Decreases slippage when orderbook imbalance improves
- Prevents over-aggressive entries in unfavorable microstructure

**Slippage Calculation Logic**:
- **Base**: Start with configured `BOT_SLIPPAGE_BPS`
- **Long Entry**: If bid/ask ratio < 1.0 (selling pressure), add extra slippage based on imbalance severity
- **Short Entry**: If bid/ask ratio > 1.0 (buying pressure), add extra slippage based on imbalance severity
- **Max Addition**: +50 bps maximum extra slippage to prevent excessive penalties

**Formula**:
```
dynamic_slippage = base_slippage + (imbalance_severity × 100, capped at 50)
```

**Configuration Parameters**:
- `BOT_ENABLE_DEPTH_ANALYSIS` (required: true)
- `BOT_DYNAMIC_SLIPPAGE_ENABLED` (true/false, default: false)
- `BOT_SLIPPAGE_BPS` (base slippage, default: 8)
- `BOT_SLIPPAGE_LIMIT_PCT` (max allowed slippage, default: 0.10%)

**Integration**: 
- Automatically used in `open_trade()` method when snapshot is provided
- Falls back to fixed slippage if depth data unavailable

---

### 6. Drawdown-Aware Dynamic Position Sizing

**Status**: ✅ NEW IMPLEMENTATION

**Location**: [bot/risk.py](bot/risk.py#L60-L80)

**Details**:
- Position size scales down as daily/weekly drawdown increases
- Protects account by reducing risk exposure during losing streaks
- Scaling is progressive: more severe drawdown = smaller positions
- Independently configured scaling factors and thresholds

**Scaling Logic**:
- If current drawdown ≤ threshold: Normal position sizing (scale factor = 1.0)
- If current drawdown > threshold: Reduced sizing based on excess drawdown
- Scale reduction accelerates as drawdown deepens
- Hard minimum scale factor of 0.2 (20% of normal size) prevents complete trading halt

**New Method** - `_get_drawdown_scale_factor()`:
```python
def _get_drawdown_scale_factor(self) -> float:
    """Returns 0.2 to 1.0 based on current drawdown"""
```

**Integration**:
- Applied in `pre_trade_check()` method
- Works in conjunction with existing risk multipliers
- For M4 module: `risk_cash *= m4_risk_multiplier *= drawdown_scale_factor`

**Configuration Parameters** (New):
- `BOT_ENABLE_DRAWDOWN_SCALING` (true/false, default: true)
- `BOT_DRAWDOWN_SCALE_THRESHOLD_PCT` (float, default: 0.01 = 1%)
- `BOT_DRAWDOWN_SCALE_FACTOR` (float, default: 0.75 = 75% minimum)

**Behavior Example**:
- Start equity: $100, Risk per trade: 0.75%
- Daily drawdown reaches 2%:
  - Excess drawdown = 2% - 1% = 1%
  - Reduction = 1% × 10 = 10%
  - Scale factor = max(0.75, 1.0 - 0.10) = 0.90
  - New risk per trade = 0.75% × 0.90 = 0.675%

---

## Risk Management: Correlation Stacking

**Status**: ✅ NATURALLY LIMITED BY DESIGN

**Details**:
- Bot allows maximum **1 open position at a time** (by design)
- Prevents portfolio-level correlation risk stacking
- Even if multiple high-correlation assets generate signals simultaneously, only best signal is selected
- Selection prioritizes: Spot longs > other longs > shorts (risk management ordering)

**Implementation** ([engine.py](bot/engine.py#L198-L201)):
```python
def _choose_best_candidate(candidates: List[Candidate]) -> Candidate | None:
    spot_longs = [c for c in candidates if c.signal.direction == Direction.LONG 
                  and c.signal.market == MarketType.SPOT]
    if spot_longs:
        return max(spot_longs, key=lambda x: x.signal.risk_multiple)
    return max(candidates, key=lambda x: x.signal.risk_multiple)
```

---

## Complete Configuration Reference

### New Environment Variables (May 2026 Update)

**Drawdown Scaling**:
```bash
BOT_ENABLE_DRAWDOWN_SCALING=true
BOT_DRAWDOWN_SCALE_THRESHOLD_PCT=0.01
BOT_DRAWDOWN_SCALE_FACTOR=0.75
```

**Microstructure & Depth Analysis**:
```bash
BOT_ENABLE_DEPTH_ANALYSIS=false          # Requires API rate limits
BOT_ORDERBOOK_DEPTH_LIMIT=20
BOT_IMBALANCE_THRESHOLD=1.3
BOT_DYNAMIC_SLIPPAGE_ENABLED=false       # Requires depth enabled
```

**Existing Parameters** (Unchanged):
```bash
BOT_ENABLE_PARTIAL_TP=true
BOT_PARTIAL_TP_FRACTION=0.5
BOT_ENABLE_TRAILING_STOP=true
BOT_TRAILING_ATR_MULTIPLIER=1.2
BOT_ENABLE_MTF_CONFIRMATION=true
BOT_MTF_CONFIRM_TIMEFRAMES=15m
BOT_ENABLE_MODULE_SCORECARD=true
BOT_MODULE_PERF_LOOKBACK=10
BOT_MODULE_PERF_MIN_TRADES=4
BOT_MODULE_PERF_MIN_WIN_RATE=0.30
BOT_MODULE_PERF_DISABLE_MINUTES=180
```

---

## API Rate Limit Considerations

**Current Impacts**:
- Fetching orderbook depth adds ~1-2 API calls per symbol per tick
- With 4 symbols per tick and 30-second loop: ~8 calls/minute for depth
- **Recommendation**: 
  - Keep `BOT_ENABLE_DEPTH_ANALYSIS=false` initially (uses cached bid/ask spreads only)
  - Enable only after confirming API rate limits are sufficient
  - Mainnet has generous limits; testnet may be more restrictive

---

## Testing Checklist

- [x] Partial TP triggers at 50% of position at target_1
- [x] ATR trailing stop activates after partial exit
- [x] Multi-timeframe confirmation blocks invalid entries
- [x] Module scorecard disables underperforming modules
- [x] Orderbook depth fetching works correctly
- [x] Bid/ask imbalance calculation is accurate
- [x] Dynamic slippage increases when imbalance worsens
- [x] Drawdown scaling reduces positions during losing streaks
- [x] No syntax or import errors

---

## References

- **Types Definition**: [bot/types.py](bot/types.py)
- **Configuration**: [bot/config.py](bot/config.py)
- **Risk Management**: [bot/risk.py](bot/risk.py)
- **Execution Logic**: [bot/execution.py](bot/execution.py)
- **Data Fetching**: [bot/data.py](bot/data.py)
- **Engine/Orchestration**: [bot/engine.py](bot/engine.py)

---

## Notes

- All new features are **optional** and can be enabled/disabled via environment configuration
- Default configuration maintains backward compatibility
- Features are designed to work independently but can be combined for maximum protection
- Depth analysis is the most API-intensive feature and should be tested on testnet first
