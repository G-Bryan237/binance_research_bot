from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class MarketType(str, Enum):
    SPOT = "SPOT"
    PERP = "PERP"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class Regime(str, Enum):
    TREND = "TREND"
    BREAKOUT_EXPANSION = "BREAKOUT_EXPANSION"
    RANGE_HIGH_VOL = "RANGE_HIGH_VOL"
    LOW_VOL_CHOP = "LOW_VOL_CHOP"


class StrategyModule(str, Enum):
    M1 = "M1"
    M2 = "M2"
    M3 = "M3"
    M4 = "M4"
    M5 = "M5"  # VWAP + Volume Profile institutional levels
    M6 = "M6"  # Multi-timeframe divergence
    DO_NOTHING = "DO_NOTHING"


@dataclass
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class OrderBookLevel:
    price: float
    quantity: float


@dataclass
class OrderBookDepth:
    bid_levels: list[OrderBookLevel]
    ask_levels: list[OrderBookLevel]
    timestamp_ms: int

    def bid_ask_imbalance(self, levels: int = 5) -> float:
        """
        Calculate bid/ask imbalance ratio.
        Ratio = (total bid volume) / (total ask volume)
        > 1.0 means more buying pressure, < 1.0 means more selling pressure.
        """
        bid_vol = sum(l.quantity for l in self.bid_levels[:levels])
        ask_vol = sum(l.quantity for l in self.ask_levels[:levels])
        if ask_vol <= 0:
            return 1.0
        return bid_vol / ask_vol

    def spread_bps(self, mid_price: float) -> float:
        """Calculate spread in basis points."""
        if not self.bid_levels or not self.ask_levels or mid_price <= 0:
            return 0.0
        best_bid = self.bid_levels[0].price
        best_ask = self.ask_levels[0].price
        return ((best_ask - best_bid) / mid_price) * 10000.0

    def depth_notional(self, levels: int = 10) -> tuple[float, float]:
        """Return bid/ask notional for top levels."""
        bid_notional = sum(l.price * l.quantity for l in self.bid_levels[:levels])
        ask_notional = sum(l.price * l.quantity for l in self.ask_levels[:levels])
        return bid_notional, ask_notional

    def detect_wall(self, side: str, levels: int = 10, multiplier: float = 3.0) -> tuple[float, float] | None:
        """Detect a large liquidity wall on bid or ask side."""
        if side not in {"bid", "ask"}:
            return None
        book = self.bid_levels if side == "bid" else self.ask_levels
        if not book:
            return None
        slice_levels = book[:levels]
        avg_qty = sum(l.quantity for l in slice_levels) / max(len(slice_levels), 1)
        if avg_qty <= 0:
            return None
        for level in slice_levels:
            if level.quantity >= (avg_qty * multiplier):
                return level.price, level.quantity
        return None


@dataclass
class MarketSnapshot:
    symbol: str
    market: MarketType
    timeframe: str
    candles: list[Candle]
    bid: float
    ask: float
    quote_volume_24h: float
    funding_rate: Optional[float] = None
    open_interest: Optional[float] = None
    depth: Optional[OrderBookDepth] = None


@dataclass
class Signal:
    symbol: str
    market: MarketType
    direction: Direction
    entry: float
    stop: float
    target_1: float
    target_2: float
    regime: Regime
    strategy_module: StrategyModule
    rationale: str
    risk_multiple: float
    timestamp_utc: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    quantity: float = 0.0
    leverage: int = 1


@dataclass
class Position:
    symbol: str
    market: MarketType
    direction: Direction
    entry: float
    stop: float
    initial_stop: float
    target_1: float
    target_2: float
    quantity: float
    initial_quantity: float
    opened_at_utc: str
    strategy_module: StrategyModule
    regime: Regime
    stop_moved_to_be: bool = False
    partial_taken: bool = False
    tp_hit_count: int = 0
    trailing_active: bool = False
    trail_anchor: float = 0.0


@dataclass
class ClosedTrade:
    symbol: str
    market: MarketType
    direction: Direction
    entry: float
    exit_price: float
    quantity: float
    pnl: float
    pnl_pct_of_equity: float
    fee_paid: float
    funding_paid: float
    close_reason: str
    strategy_module: StrategyModule
    regime: Regime
    partial_exit: bool
    opened_at_utc: str
    closed_at_utc: str
