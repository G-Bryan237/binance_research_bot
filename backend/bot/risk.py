from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Dict

from .config import RiskConfig
from .types import Direction, MarketType, RiskDecision, Signal, StrategyModule


@dataclass
class RiskState:
    equity: float
    daily_start_equity: float
    weekly_start_equity: float
    current_day: str
    current_week: str
    trades_today: int = 0
    trades_by_symbol_today: Dict[str, int] = field(default_factory=dict)
    loss_streak: int = 0
    cooldown_until: datetime | None = None
    day_locked: bool = False
    week_locked: bool = False


class RiskManager:
    def __init__(self, cfg: RiskConfig) -> None:
        now = datetime.now(tz=UTC)
        day = now.strftime("%Y-%m-%d")
        week = now.strftime("%G-W%V")
        self.cfg = cfg
        self.state = RiskState(
            equity=cfg.start_equity,
            daily_start_equity=cfg.start_equity,
            weekly_start_equity=cfg.start_equity,
            current_day=day,
            current_week=week,
        )

    def _roll_periods(self, now: datetime) -> None:
        day = now.strftime("%Y-%m-%d")
        week = now.strftime("%G-W%V")
        if day != self.state.current_day:
            self.state.current_day = day
            self.state.daily_start_equity = self.state.equity
            self.state.trades_today = 0
            self.state.trades_by_symbol_today = {}
            self.state.day_locked = False
            self.state.loss_streak = 0
            self.state.cooldown_until = None
        if week != self.state.current_week:
            self.state.current_week = week
            self.state.weekly_start_equity = self.state.equity
            self.state.week_locked = False

    def _daily_drawdown_pct(self) -> float:
        if self.state.daily_start_equity <= 0:
            return 0.0
        return (self.state.daily_start_equity - self.state.equity) / self.state.daily_start_equity

    def _weekly_drawdown_pct(self) -> float:
        if self.state.weekly_start_equity <= 0:
            return 0.0
        return (self.state.weekly_start_equity - self.state.equity) / self.state.weekly_start_equity

    def _get_drawdown_scale_factor(self) -> float:
        """
        Calculate position size scale factor based on current drawdown.
        As drawdown increases, scale factor decreases (more conservative).
        """
        if not self.cfg.enable_drawdown_scaling:
            return 1.0
        
        daily_dd = self._daily_drawdown_pct()
        weekly_dd = self._weekly_drawdown_pct()
        max_dd = max(daily_dd, weekly_dd)
        
        if max_dd <= self.cfg.drawdown_scale_threshold_pct:
            return 1.0
        
        excess_dd = max_dd - self.cfg.drawdown_scale_threshold_pct
        reduction = 1.0 - (excess_dd * 10.0)
        factor = max(self.cfg.drawdown_scale_factor, reduction)
        return max(factor, 0.2)

    def pre_trade_check(self, signal: Signal) -> RiskDecision:
        now = datetime.now(tz=UTC)
        self._roll_periods(now)

        if self.state.week_locked:
            return RiskDecision(False, "Weekly loss lock active")
        if self.state.day_locked:
            return RiskDecision(False, "Daily loss lock active")
        if self.state.cooldown_until and now < self.state.cooldown_until:
            return RiskDecision(False, f"Cooldown active until {self.state.cooldown_until.isoformat()}")
        if self.state.trades_today >= self.cfg.max_trades_per_day:
            return RiskDecision(False, "Reached max trades for day")
        if self.state.trades_by_symbol_today.get(signal.symbol, 0) >= self.cfg.max_trades_per_symbol_day:
            return RiskDecision(False, "Reached max trades for symbol today")

        if signal.market == MarketType.SPOT and signal.direction == Direction.SHORT:
            return RiskDecision(False, "Spot shorts disabled")

        leverage = 1
        if signal.market == MarketType.PERP and self.cfg.enable_leverage_2x:
            leverage = 2

        risk_cash = self.state.equity * self.cfg.risk_per_trade_pct
        if signal.strategy_module == StrategyModule.M4:
            risk_cash *= self.cfg.m4_risk_multiplier
        
        drawdown_scale = self._get_drawdown_scale_factor()
        risk_cash *= drawdown_scale
        
        stop_distance = abs(signal.entry - signal.stop)
        if stop_distance <= 0:
            return RiskDecision(False, "Invalid stop distance")
        quantity = (risk_cash / stop_distance) * leverage

        notional = quantity * signal.entry
        if notional < 5.0:
            return RiskDecision(False, "Order notional too small for practical execution")

        return RiskDecision(True, "Approved", quantity=quantity, leverage=leverage)

    def register_new_trade(self, symbol: str) -> None:
        now = datetime.now(tz=UTC)
        self._roll_periods(now)
        self.state.trades_today += 1
        self.state.trades_by_symbol_today[symbol] = self.state.trades_by_symbol_today.get(symbol, 0) + 1

    def register_closed_trade(self, pnl: float, count_for_streak: bool = True) -> dict:
        now = datetime.now(tz=UTC)
        self._roll_periods(now)
        self.state.equity += pnl
        if count_for_streak:
            if pnl < 0:
                self.state.loss_streak += 1
            else:
                self.state.loss_streak = 0

            if self.state.loss_streak >= 2:
                self.state.cooldown_until = now + timedelta(minutes=self.cfg.cooldown_after_two_losses_minutes)
            if self.state.loss_streak >= self.cfg.max_loss_streak_for_day_stop:
                self.state.day_locked = True

        if self._daily_drawdown_pct() >= self.cfg.max_daily_loss_pct:
            self.state.day_locked = True
        if self._weekly_drawdown_pct() >= self.cfg.max_weekly_loss_pct:
            self.state.week_locked = True

        return {
            "equity": round(self.state.equity, 4),
            "loss_streak": self.state.loss_streak,
            "day_locked": self.state.day_locked,
            "week_locked": self.state.week_locked,
            "daily_drawdown_pct": round(self._daily_drawdown_pct() * 100, 3),
            "weekly_drawdown_pct": round(self._weekly_drawdown_pct() * 100, 3),
        }
