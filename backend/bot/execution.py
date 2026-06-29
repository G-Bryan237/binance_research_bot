from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Dict, List, Tuple

from .config import ExecutionConfig
from .data import SymbolRules
from .indicators import atr, detect_divergence, latest, macd, rsi
from .types import ClosedTrade, Direction, MarketSnapshot, MarketType, Position, Signal


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _apply_step_size(qty: float, step: float) -> float:
    if step <= 0:
        return qty
    k = int(qty / step)
    return max(k * step, 0.0)


def _fee_rate_bps(cfg: ExecutionConfig, market: MarketType) -> float:
    return cfg.spot_fee_bps if market == MarketType.SPOT else cfg.perp_fee_bps


@dataclass
class ExecutionDecision:
    approved: bool
    reason: str
    spread_pct: float = 0.0
    est_slippage_pct: float = 0.0


@dataclass
class PendingOrder:
    signal: Signal
    quantity: float
    limit_price: float
    created_at_utc: str
    timeout_seconds: int

    def expired(self, now: datetime) -> bool:
        created = datetime.fromisoformat(self.created_at_utc)
        return (now - created).total_seconds() > self.timeout_seconds


def _calculate_dynamic_slippage(snapshot: MarketSnapshot, cfg: ExecutionConfig, direction: Direction) -> float:
    """
    Calculate dynamic slippage based on orderbook depth and imbalance.
    Returns slippage in basis points.
    """
    base_slippage = cfg.slippage_bps + cfg.latency_bps
    if snapshot.depth is None or not cfg.dynamic_slippage_enabled:
        return base_slippage

    depth = snapshot.depth
    mid = (snapshot.bid + snapshot.ask) / 2.0
    try:
        imbalance = depth.bid_ask_imbalance(levels=5)
        if direction == Direction.LONG and imbalance < 1.0:
            delta = 1.0 - imbalance
            base_slippage += min(delta * 100.0, 50.0)
        elif direction == Direction.SHORT and imbalance > 1.0:
            delta = imbalance - 1.0
            base_slippage += min(delta * 100.0, 50.0)
    except Exception:
        pass

    if mid > 0 and cfg.slippage_volatility_multiplier > 0:
        atr_now = latest(
            atr(
                [x.high for x in snapshot.candles],
                [x.low for x in snapshot.candles],
                [x.close for x in snapshot.candles],
                14,
            )
        )
        if atr_now is not None and atr_now > 0:
            atr_bps = (atr_now / mid) * 10000.0
            base_slippage += atr_bps * cfg.slippage_volatility_multiplier

    if mid > 0 and cfg.slippage_spread_multiplier > 0:
        spread_bps = ((snapshot.ask - snapshot.bid) / mid) * 10000.0
        base_slippage += spread_bps * cfg.slippage_spread_multiplier

    return base_slippage


def _limit_price_from_snapshot(snapshot: MarketSnapshot, direction: Direction, offset_bps: float) -> float:
    mid = (snapshot.bid + snapshot.ask) / 2.0
    if mid <= 0:
        return 0.0
    offset = offset_bps / 10_000.0
    if direction == Direction.LONG:
        return mid * (1 - offset)
    return mid * (1 + offset)


def _limit_order_fillable(snapshot: MarketSnapshot, limit_price: float) -> bool:
    if not snapshot.candles:
        return False
    c = snapshot.candles[-1]
    return c.low <= limit_price <= c.high


def _bearish_reversal_candle(last, prev) -> bool:
    body = abs(last.close - last.open)
    upper_wick = last.high - max(last.open, last.close)
    bearish_engulf = prev.close > prev.open and last.close < last.open and last.open >= prev.close and last.close <= prev.open
    shooting_star = body > 0 and upper_wick >= (1.8 * body) and last.close < last.open
    return bearish_engulf or shooting_star


def _bullish_reversal_candle(last, prev) -> bool:
    body = abs(last.close - last.open)
    lower_wick = min(last.open, last.close) - last.low
    bullish_engulf = prev.close < prev.open and last.close > last.open and last.open <= prev.close and last.close >= prev.open
    hammer = body > 0 and lower_wick >= (1.8 * body) and last.close > last.open
    return bullish_engulf or hammer


def _reversal_exit_reason(snapshot: MarketSnapshot, pos: Position, cfg: ExecutionConfig) -> str | None:
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
        if pos.direction == Direction.LONG and _bearish_reversal_candle(last, prev):
            return "REVERSAL_CANDLE"
        if pos.direction == Direction.SHORT and _bullish_reversal_candle(last, prev):
            return "REVERSAL_CANDLE"

    return None


def _sr_exit_reason(snapshot: MarketSnapshot, pos: Position, cfg: ExecutionConfig, atr_now: float | None) -> str | None:
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


class PaperExecutionEngine:
    def __init__(self, cfg: ExecutionConfig) -> None:
        self.cfg = cfg
        self.open_positions: Dict[Tuple[str, MarketType], Position] = {}
        self.closed_trades: List[ClosedTrade] = []
        self.pending_order: PendingOrder | None = None

    def has_position(self, symbol: str, market: MarketType) -> bool:
        return (symbol, market) in self.open_positions

    def _tp_plan(self, pos: Position) -> list[tuple[float, float]]:
        risk = abs(pos.entry - pos.initial_stop)
        if risk <= 0:
            return []

        rr_levels = [x for x in self.cfg.tp_rr_levels if x > 0]
        fractions = [max(x, 0.0) for x in self.cfg.tp_close_fractions]
        if not rr_levels:
            rr_levels = [1.5, 2.5]
            fractions = [self.cfg.partial_tp_fraction, 1.0 - self.cfg.partial_tp_fraction]

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

    def execution_check(self, snapshot: MarketSnapshot, direction: Direction | None = None) -> ExecutionDecision:
        mid = (snapshot.bid + snapshot.ask) / 2.0
        if mid <= 0:
            return ExecutionDecision(False, "Invalid mid-price")
        spread_pct = ((snapshot.ask - snapshot.bid) / mid) * 100.0
        spread_limit = self.cfg.spread_limit_spot_pct if snapshot.market == MarketType.SPOT else self.cfg.spread_limit_perp_pct
        if spread_pct > spread_limit:
            return ExecutionDecision(False, f"Spread too wide: {spread_pct:.4f}% > {spread_limit:.4f}%")

        est_slippage_bps = self.cfg.slippage_bps + self.cfg.latency_bps
        if direction is not None:
            est_slippage_bps = _calculate_dynamic_slippage(snapshot, self.cfg, direction)
        est_slippage_pct = est_slippage_bps / 100.0
        if est_slippage_pct > self.cfg.slippage_limit_pct:
            return ExecutionDecision(False, "Estimated slippage too high")

        min_quote_volume = 1_000_000 if snapshot.market == MarketType.SPOT else 5_000_000
        if snapshot.quote_volume_24h < min_quote_volume:
            return ExecutionDecision(False, "24h quote volume too low")

        if (self.cfg.enable_depth_analysis or self.cfg.enable_orderbook_analysis) and snapshot.depth is not None:
            depth = snapshot.depth
            if len(depth.bid_levels) < self.cfg.min_orderbook_levels or len(depth.ask_levels) < self.cfg.min_orderbook_levels:
                return ExecutionDecision(False, "Orderbook depth too thin")
            bid_notional, ask_notional = depth.depth_notional(self.cfg.orderbook_depth_limit)
            min_notional = (
                self.cfg.min_orderbook_notional_spot
                if snapshot.market == MarketType.SPOT
                else self.cfg.min_orderbook_notional_perp
            )
            if min(bid_notional, ask_notional) < min_notional:
                return ExecutionDecision(False, "Orderbook notional too low")

            if direction is not None and self.cfg.enable_orderbook_analysis:
                wall_side = "ask" if direction == Direction.LONG else "bid"
                wall = depth.detect_wall(
                    wall_side,
                    levels=self.cfg.orderbook_depth_limit,
                    multiplier=self.cfg.orderbook_wall_multiplier,
                )
                if wall is not None:
                    wall_price, _ = wall
                    if mid > 0:
                        wall_bps = abs(wall_price - mid) / mid * 10000.0
                        if wall_bps <= self.cfg.orderbook_wall_range_bps:
                            return ExecutionDecision(False, "Liquidity wall near mid-price")

        return ExecutionDecision(True, "Execution checks passed", spread_pct=spread_pct, est_slippage_pct=est_slippage_pct)

    def open_trade(
        self,
        signal: Signal,
        quantity: float,
        rules: SymbolRules | None,
        snapshot: MarketSnapshot | None = None,
        fill_price: float | None = None,
        use_slippage: bool = True,
    ) -> Position | None:
        if rules is not None:
            quantity = _apply_step_size(quantity, rules.step_size)
        if quantity <= 0:
            return None

        if fill_price is None:
            fill_price = signal.entry
            if use_slippage:
                slippage_bps = self.cfg.slippage_bps
                if snapshot is not None:
                    slippage_bps = _calculate_dynamic_slippage(snapshot, self.cfg, signal.direction)
                slip = slippage_bps / 10_000.0
                if signal.direction == Direction.LONG:
                    fill_price = signal.entry * (1 + slip)
                else:
                    fill_price = signal.entry * (1 - slip)

        if rules is not None and quantity * fill_price < rules.min_notional:
            return None

        pos = Position(
            symbol=signal.symbol,
            market=signal.market,
            direction=signal.direction,
            entry=fill_price,
            stop=signal.stop,
            initial_stop=signal.stop,
            target_1=signal.target_1,
            target_2=signal.target_2,
            quantity=quantity,
            initial_quantity=quantity,
            opened_at_utc=_now_iso(),
            strategy_module=signal.strategy_module,
            regime=signal.regime,
            trailing_active=self.cfg.enable_trailing_stop and self.cfg.enable_trailing_on_full_position,
            trail_anchor=fill_price,
            signal_id=signal.signal_id,
        )
        self.open_positions[(signal.symbol, signal.market)] = pos
        return pos

    def place_limit_order(
        self,
        signal: Signal,
        quantity: float,
        rules: SymbolRules | None,
        snapshot: MarketSnapshot | None = None,
    ) -> Position | None:
        if snapshot is None:
            return None
        if rules is not None:
            quantity = _apply_step_size(quantity, rules.step_size)
        if quantity <= 0:
            return None

        limit_price = _limit_price_from_snapshot(snapshot, signal.direction, self.cfg.limit_order_offset_bps)
        if limit_price <= 0:
            return None
        if rules is not None and quantity * limit_price < rules.min_notional:
            return None

        if _limit_order_fillable(snapshot, limit_price):
            return self.open_trade(
                signal,
                quantity,
                rules,
                snapshot=snapshot,
                fill_price=limit_price,
                use_slippage=False,
            )

        self.pending_order = PendingOrder(
            signal=signal,
            quantity=quantity,
            limit_price=limit_price,
            created_at_utc=_now_iso(),
            timeout_seconds=self.cfg.limit_order_timeout_seconds,
        )
        return None

    def check_pending_order(self, snapshot: MarketSnapshot, rules: SymbolRules | None) -> Position | None:
        if self.pending_order is None:
            return None
        pending = self.pending_order
        if (pending.signal.symbol, pending.signal.market) != (snapshot.symbol, snapshot.market):
            return None
        if pending.expired(datetime.now(tz=UTC)):
            self.pending_order = None
            return None
        if not _limit_order_fillable(snapshot, pending.limit_price):
            return None

        pos = self.open_trade(
            pending.signal,
            pending.quantity,
            rules,
            snapshot=snapshot,
            fill_price=pending.limit_price,
            use_slippage=False,
        )
        self.pending_order = None
        return pos

    def _close_trade(
        self,
        pos: Position,
        exit_px: float,
        reason: str,
        funding_rate: float | None,
        close_qty: float | None = None,
    ) -> ClosedTrade:
        qty = pos.quantity if close_qty is None else max(min(close_qty, pos.quantity), 0.0)
        slip = self.cfg.slippage_bps / 10_000.0
        if pos.direction == Direction.LONG:
            filled_exit = exit_px * (1 - slip)
            gross_pnl = (filled_exit - pos.entry) * qty
        else:
            filled_exit = exit_px * (1 + slip)
            gross_pnl = (pos.entry - filled_exit) * qty

        fee_rate = _fee_rate_bps(self.cfg, pos.market) / 10_000.0
        entry_notional = pos.entry * qty
        exit_notional = filled_exit * qty
        fees = (entry_notional + exit_notional) * fee_rate

        funding_cost = 0.0
        if pos.market == MarketType.PERP and funding_rate is not None:
            dt0 = datetime.fromisoformat(pos.opened_at_utc)
            held_hours = max((datetime.now(tz=UTC) - dt0).total_seconds() / 3600.0, 0.0)
            periods = int(held_hours // 8)
            if periods > 0:
                if pos.direction == Direction.LONG:
                    funding_cost = (pos.entry * qty) * funding_rate * periods
                else:
                    funding_cost = -(pos.entry * qty) * funding_rate * periods

        net_pnl = gross_pnl - fees - funding_cost
        pct = (net_pnl / max(entry_notional, 1e-9)) * 100.0
        return ClosedTrade(
            symbol=pos.symbol,
            market=pos.market,
            direction=pos.direction,
            entry=pos.entry,
            exit_price=filled_exit,
            quantity=qty,
            pnl=net_pnl,
            pnl_pct_of_equity=pct,
            fee_paid=fees,
            funding_paid=funding_cost,
            close_reason=reason,
            strategy_module=pos.strategy_module,
            regime=pos.regime,
            partial_exit=qty < pos.quantity,
            opened_at_utc=pos.opened_at_utc,
            closed_at_utc=_now_iso(),
            signal_id=pos.signal_id,
        )

    def mark_to_market(self, snapshot: MarketSnapshot) -> list[ClosedTrade]:
        key = (snapshot.symbol, snapshot.market)
        pos = self.open_positions.get(key)
        if pos is None:
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
        closed: list[ClosedTrade] = []

        if self.cfg.enable_time_exit:
            held_min = max((datetime.now(tz=UTC) - datetime.fromisoformat(pos.opened_at_utc)).total_seconds() / 60.0, 0.0)
            if held_min >= self.cfg.max_hold_minutes:
                t = self._close_trade(pos, c.close, "TIME_EXIT", snapshot.funding_rate)
                closed.append(t)

        if closed:
            self.open_positions.pop(key, None)
            self.closed_trades.extend(closed)
            return closed

        tp_plan = self._tp_plan(pos)

        if pos.direction == Direction.LONG:
            if c.low <= pos.stop:
                t = self._close_trade(pos, pos.stop, "STOP_LOSS", snapshot.funding_rate)
                closed.append(t)

            risk = abs(pos.entry - pos.initial_stop)
            if risk > 0 and not pos.trailing_active and self.cfg.enable_trailing_stop:
                move_r = (c.close - pos.entry) / risk
                if move_r >= self.cfg.trailing_activation_rr:
                    pos.trailing_active = True

            # Process any TP levels crossed this candle.
            while not closed and pos.tp_hit_count < len(tp_plan) and c.high >= tp_plan[pos.tp_hit_count][0]:
                idx = pos.tp_hit_count
                tp_price, tp_frac = tp_plan[idx]
                is_last = idx == (len(tp_plan) - 1)
                close_qty = pos.quantity if is_last else max(pos.initial_quantity * tp_frac, 0.0)
                close_qty = min(close_qty, pos.quantity)
                if close_qty <= 0:
                    pos.tp_hit_count += 1
                    continue
                reason = f"TAKE_PROFIT_{idx + 1}" + ("" if is_last else "_PARTIAL")
                t = self._close_trade(pos, tp_price, reason, snapshot.funding_rate, close_qty=close_qty)
                closed.append(t)
                if close_qty < pos.quantity:
                    pos.quantity = max(pos.quantity - close_qty, 0.0)
                    pos.partial_taken = True
                    pos.stop = max(pos.stop, pos.entry)
                    pos.stop_moved_to_be = True
                    pos.trailing_active = self.cfg.enable_trailing_stop
                    pos.trail_anchor = max(pos.trail_anchor, c.high)
                pos.tp_hit_count += 1
                if is_last or pos.quantity <= 0:
                    break

            if not closed and pos.trailing_active and atr_now is not None and atr_now > 0:
                pos.trail_anchor = max(pos.trail_anchor, c.high)
                trail_stop = pos.trail_anchor - (self.cfg.trailing_atr_multiplier * atr_now)
                pos.stop = max(pos.stop, trail_stop)

            if not closed:
                sr_reason = _sr_exit_reason(snapshot, pos, self.cfg, atr_now)
                if sr_reason:
                    t = self._close_trade(pos, c.close, sr_reason, snapshot.funding_rate)
                    closed.append(t)

            if not closed:
                reason = _reversal_exit_reason(snapshot, pos, self.cfg)
                if reason:
                    t = self._close_trade(pos, c.close, reason, snapshot.funding_rate)
                    closed.append(t)
        else:
            if c.high >= pos.stop:
                t = self._close_trade(pos, pos.stop, "STOP_LOSS", snapshot.funding_rate)
                closed.append(t)

            risk = abs(pos.entry - pos.initial_stop)
            if risk > 0 and not pos.trailing_active and self.cfg.enable_trailing_stop:
                move_r = (pos.entry - c.close) / risk
                if move_r >= self.cfg.trailing_activation_rr:
                    pos.trailing_active = True

            while not closed and pos.tp_hit_count < len(tp_plan) and c.low <= tp_plan[pos.tp_hit_count][0]:
                idx = pos.tp_hit_count
                tp_price, tp_frac = tp_plan[idx]
                is_last = idx == (len(tp_plan) - 1)
                close_qty = pos.quantity if is_last else max(pos.initial_quantity * tp_frac, 0.0)
                close_qty = min(close_qty, pos.quantity)
                if close_qty <= 0:
                    pos.tp_hit_count += 1
                    continue
                reason = f"TAKE_PROFIT_{idx + 1}" + ("" if is_last else "_PARTIAL")
                t = self._close_trade(pos, tp_price, reason, snapshot.funding_rate, close_qty=close_qty)
                closed.append(t)
                if close_qty < pos.quantity:
                    pos.quantity = max(pos.quantity - close_qty, 0.0)
                    pos.partial_taken = True
                    pos.stop = min(pos.stop, pos.entry)
                    pos.stop_moved_to_be = True
                    pos.trailing_active = self.cfg.enable_trailing_stop
                    pos.trail_anchor = min(pos.trail_anchor, c.low) if pos.trail_anchor > 0 else c.low
                pos.tp_hit_count += 1
                if is_last or pos.quantity <= 0:
                    break

            if not closed and pos.trailing_active and atr_now is not None and atr_now > 0:
                if pos.trail_anchor <= 0:
                    pos.trail_anchor = c.low
                pos.trail_anchor = min(pos.trail_anchor, c.low)
                trail_stop = pos.trail_anchor + (self.cfg.trailing_atr_multiplier * atr_now)
                pos.stop = min(pos.stop, trail_stop)

            if not closed:
                sr_reason = _sr_exit_reason(snapshot, pos, self.cfg, atr_now)
                if sr_reason:
                    t = self._close_trade(pos, c.close, sr_reason, snapshot.funding_rate)
                    closed.append(t)

            if not closed:
                reason = _reversal_exit_reason(snapshot, pos, self.cfg)
                if reason:
                    t = self._close_trade(pos, c.close, reason, snapshot.funding_rate)
                    closed.append(t)

        if closed:
            if pos.quantity <= 0 or any(not t.partial_exit for t in closed):
                self.open_positions.pop(key, None)
            self.closed_trades.extend(closed)
        return closed
