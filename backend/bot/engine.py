from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Dict, List, Tuple

from .alerts import AlertManager
from .config import BotConfig
from .data import BinanceDataClient, SymbolRules
from .execution import PaperExecutionEngine
from .regime import detect_regime
from .risk import RiskManager
from .state_store import BotStateStore
from .strategies import StrategyDecision, generate_signal
from .types import Direction, MarketSnapshot, MarketType, Signal, StrategyModule

LOG = logging.getLogger(__name__)


@dataclass
class Candidate:
    signal: Signal
    snapshot: MarketSnapshot
    strategy_reason: str


class TradingBot:
    def __init__(self, cfg: BotConfig) -> None:
        self.cfg = cfg
        self.data = BinanceDataClient(cfg.api)
        self.risk = RiskManager(cfg.risk)
        self.execution = PaperExecutionEngine(cfg.execution)
        self.alerts = AlertManager(cfg.alerts)
        self.rules_cache: Dict[Tuple[str, MarketType], SymbolRules | None] = {}
        self.last_fingerprint_by_symbol: Dict[str, str] = {}
        self._module_outcomes = defaultdict(lambda: deque(maxlen=self.cfg.module_perf_lookback))
        self._module_disabled_until: Dict[StrategyModule, datetime] = {}
        self._funding_history: Dict[str, deque[tuple[int, float]]] = defaultdict(lambda: deque(maxlen=400))
        self._total_trades = 0
        self._winning_trades = 0
        self._losing_trades = 0
        self._r_multiples: list[float] = []

        # Shared state lives in SQLite so worker/API can run as separate processes.
        self.state_store = BotStateStore(self.cfg.state_db_path)
        self.state_store.initialize_defaults(
            starting_equity=self.risk.state.equity,
            environment="paper",
            data_source="binance market snapshots",
            update_interval_ms=self.cfg.dashboard_update_interval_ms,
        )
        self.state_store.update_state(
            status="running",
            starting_equity=self.risk.state.equity,
            equity=self.risk.state.equity,
            daily_pnl=0.0,
            open_position=None,
        )
        
        # For one-shot runs (`--once`), seed cursor by current epoch minute so
        # repeated invocations naturally rotate symbol chunks instead of always
        # scanning the first N symbols.
        if self.cfg.allowed_symbols:
            epoch_second = int(time.time())
            self._symbol_cursor = epoch_second % len(self.cfg.allowed_symbols)
        else:
            self._symbol_cursor = 0

    def _symbols_for_tick(self) -> List[str]:
        if not self.cfg.allowed_symbols:
            return []
        chunk = max(1, min(self.cfg.symbols_per_tick, len(self.cfg.allowed_symbols)))
        start = self._symbol_cursor
        end = start + chunk
        if end <= len(self.cfg.allowed_symbols):
            out = self.cfg.allowed_symbols[start:end]
        else:
            out = self.cfg.allowed_symbols[start:] + self.cfg.allowed_symbols[: end - len(self.cfg.allowed_symbols)]
        self._symbol_cursor = (start + chunk) % len(self.cfg.allowed_symbols)
        return out

    def _get_rules(self, symbol: str, market: MarketType) -> SymbolRules | None:
        k = (symbol, market)
        if k not in self.rules_cache:
            self.rules_cache[k] = self.data.fetch_symbol_rules(symbol, market)
        return self.rules_cache[k]

    @staticmethod
    def _last_candle_is_abnormal(snapshot: MarketSnapshot, atr_value: float) -> bool:
        if atr_value <= 0 or not snapshot.candles:
            return False
        c = snapshot.candles[-1]
        return (c.high - c.low) > (2.5 * atr_value)

    @staticmethod
    def _funding_filter(signal: Signal, snapshot: MarketSnapshot) -> tuple[bool, str]:
        if signal.market != MarketType.PERP:
            return True, "Not perp"
        if snapshot.funding_rate is None:
            return True, "No funding data"
        fr = snapshot.funding_rate
        extreme = 0.0003  # 3 bps per funding interval
        if signal.direction == Direction.LONG and fr > extreme:
            return False, f"Avoid long perp, funding too positive ({fr:.6f})"
        if signal.direction == Direction.SHORT and fr < -extreme:
            return False, f"Avoid short perp, funding too negative ({fr:.6f})"
        return True, "Funding acceptable"

    def _funding_trend_ok(self, signal: Signal, snapshot: MarketSnapshot) -> tuple[bool, str]:
        if not self.cfg.enable_funding_trend_analysis:
            return True, "Funding trend analysis disabled"
        if signal.market != MarketType.PERP:
            return True, "Not perp"
        if snapshot.funding_rate is None:
            return True, "No funding data"

        series = self._funding_history[snapshot.symbol]
        now_ms = int(time.time() * 1000)
        series.append((now_ms, snapshot.funding_rate))

        window_ms = self.cfg.funding_trend_window_hours * 60 * 60 * 1000
        while series and (now_ms - series[0][0]) > window_ms:
            series.popleft()

        if len(series) < 3:
            return True, "Funding trend window not ready"

        first_t, first_v = series[0]
        last_t, last_v = series[-1]
        dt_hours = max((last_t - first_t) / 3_600_000.0, 1e-6)
        slope_per_hour = (last_v - first_v) / dt_hours
        slope_bps = slope_per_hour * 10_000.0

        threshold = self.cfg.funding_trend_slope_bps_per_hour
        if signal.direction == Direction.LONG and slope_bps > threshold:
            return False, f"Funding accelerating against long ({slope_bps:.2f} bps/hr)"
        if signal.direction == Direction.SHORT and slope_bps < -threshold:
            return False, f"Funding accelerating against short ({slope_bps:.2f} bps/hr)"
        return True, "Funding trend acceptable"

    @staticmethod
    def _signal_fingerprint(sig: Signal, candle_open_time: int) -> str:
        return (
            f"{sig.symbol}|{sig.market.value}|{sig.direction.value}|"
            f"{sig.strategy_module.value}|{round(sig.entry, 6)}|{candle_open_time}"
        )

    def _choose_best_candidate(self, candidates: List[Candidate]) -> Candidate | None:
        if not candidates:
            return None

        # Spot preferred for longs (lower liquidation risk), perp required for shorts.
        spot_longs = [c for c in candidates if c.signal.direction == Direction.LONG and c.signal.market == MarketType.SPOT]
        if spot_longs:
            return max(spot_longs, key=lambda x: x.signal.risk_multiple)

        return max(candidates, key=lambda x: x.signal.risk_multiple)

    def _evaluate_snapshot(self, snapshot: MarketSnapshot) -> StrategyDecision:
        context = detect_regime(snapshot, enable_m4=self.cfg.enable_m4)
        if context is None:
            return StrategyDecision(None, "Insufficient data for regime")
        if context.active_module == StrategyModule.DO_NOTHING:
            return StrategyDecision(None, context.reason)
        if self._last_candle_is_abnormal(snapshot, context.atr):
            return StrategyDecision(None, "Pump/chase guardrail active")
        decision = generate_signal(snapshot, context, self.cfg)
        return decision

    def _fetch_mtf_snapshots(
        self,
        symbol: str,
        market: MarketType,
        cache: Dict[Tuple[str, MarketType, str], MarketSnapshot | None],
    ) -> list[MarketSnapshot]:
        snapshots: list[MarketSnapshot] = []
        for tf in self.cfg.mtf_confirm_timeframes:
            key = (symbol, market, tf)
            if key not in cache:
                cache[key] = self.data.fetch_snapshot(symbol, market, tf, limit=500)
            snap = cache[key]
            if snap is not None:
                snapshots.append(snap)
        return snapshots

    def _passes_mtf_confirmation(self, signal: Signal, htf_snapshots: list[MarketSnapshot]) -> tuple[bool, str]:
        if not self.cfg.enable_mtf_confirmation:
            return True, "MTF confirmation disabled"
        if not htf_snapshots:
            return False, "No higher-timeframe data available"

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
            return False, "Higher-timeframe regime unavailable"
        if opposition > 0:
            return False, "Higher-timeframe trend opposes trade direction"
        if signal.strategy_module in {StrategyModule.M1, StrategyModule.M2} and support == 0:
            return False, "Missing higher-timeframe directional support"
        return True, "MTF confirmed"

    def _is_module_temporarily_disabled(self, module: StrategyModule) -> tuple[bool, str]:
        until = self._module_disabled_until.get(module)
        if until is None:
            return False, ""
        now = datetime.now(tz=UTC)
        if now >= until:
            self._module_disabled_until.pop(module, None)
            return False, ""
        return True, f"{module.value} disabled until {until.isoformat()}"

    def _record_module_result(self, module: StrategyModule, pnl: float) -> None:
        if not self.cfg.enable_module_scorecard or module == StrategyModule.DO_NOTHING:
            return
        outcomes = self._module_outcomes[module]
        outcomes.append(1 if pnl > 0 else 0)
        if len(outcomes) < self.cfg.module_perf_min_trades:
            return
        win_rate = sum(outcomes) / len(outcomes)
        if win_rate < self.cfg.module_perf_min_win_rate:
            until = datetime.now(tz=UTC) + timedelta(minutes=self.cfg.module_perf_disable_minutes)
            self._module_disabled_until[module] = until
            self.alerts.notify(
                "MODULE_DISABLED",
                {
                    "module": module.value,
                    "lookback_trades": len(outcomes),
                    "win_rate": round(win_rate, 4),
                    "disabled_until_utc": until.isoformat(),
                },
            )

    def _sync_runtime_state(self) -> None:
        module_scores = {}
        for module, outcomes in self._module_outcomes.items():
            total = len(outcomes)
            win_rate = (sum(outcomes) / total) if total else 0.0
            disabled, _ = self._is_module_temporarily_disabled(module)
            module_scores[module.value] = {
                "win_rate": win_rate,
                "total_trades": total,
                "active": not disabled,
            }

        avg_r = (sum(self._r_multiples) / len(self._r_multiples)) if self._r_multiples else 0.0
        self.state_store.update_module_scores(module_scores)
        self.state_store.update_stats(
            total=self._total_trades,
            wins=self._winning_trades,
            losses=self._losing_trades,
            avg_r=avg_r,
        )

    def run_once(self) -> None:
        now = datetime.now(tz=UTC).isoformat()
        LOG.info("=== Bot Tick %s ===", now)
        self.state_store.update_state(status="running", current_tick=now)

        # First, mark open positions to market and emit exits.
        for sym, market in list(self.execution.open_positions.keys()):
            snapshot = self.data.fetch_snapshot(sym, market, self.cfg.timeframe)
            if snapshot is None:
                continue
            closed = self.execution.mark_to_market(snapshot)
            for trade in closed:
                is_partial = trade.partial_exit or trade.close_reason == "TAKE_PROFIT_1_PARTIAL"
                risk_state = self.risk.register_closed_trade(trade.pnl, count_for_streak=not is_partial)
                self.alerts.notify_closed_trade(trade, extra=risk_state)
                if not is_partial:
                    self._total_trades += 1
                    if trade.pnl > 0:
                        self._winning_trades += 1
                    elif trade.pnl < 0:
                        self._losing_trades += 1
                    risk_amt = abs(trade.entry - trade.exit_price) * trade.quantity
                    r_mult = (trade.pnl / risk_amt) if risk_amt > 0 else 0.0
                    self._r_multiples.append(r_mult)
                    self.state_store.add_trade(
                        {
                            "symbol": trade.symbol,
                            "direction": trade.direction.value,
                            "entry": trade.entry,
                            "exit": trade.exit_price,
                            "pnl": trade.pnl,
                            "r_multiple": r_mult,
                            "strategy_module": trade.strategy_module.value,
                            "regime": trade.regime.value,
                            "close_reason": trade.close_reason,
                            "partial_exit": trade.partial_exit,
                            "profile_id": self.cfg.profile_id,
                        }
                    )
                if not is_partial:
                    self._record_module_result(trade.strategy_module, trade.pnl)
                if risk_state["day_locked"]:
                    self.alerts.notify("DAILY_MAX_LOSS_REACHED", risk_state)
                if risk_state["week_locked"]:
                    self.alerts.notify("WEEKLY_MAX_LOSS_REACHED", risk_state)

        # Only one concurrent position by design.
        if self.execution.open_positions:
            LOG.info("Open position exists, skipping new entries")
            open_pos = next(iter(self.execution.open_positions.values()))
            self.state_store.update_state(
                open_position={
                    "symbol": open_pos.symbol,
                    "direction": open_pos.direction.value,
                    "entry": open_pos.entry,
                },
                equity=self.risk.state.equity,
                daily_pnl=(self.risk.state.equity - self.risk.state.daily_start_equity),
            )
            self._sync_runtime_state()
            return

        if self.execution.pending_order is not None:
            pending = self.execution.pending_order
            snap = self.data.fetch_snapshot(pending.signal.symbol, pending.signal.market, self.cfg.timeframe)
            if snap is not None:
                rules = self._get_rules(snap.symbol, snap.market)
                pos = self.execution.check_pending_order(snap, rules)
                if pos is not None:
                    self.risk.register_new_trade(pos.symbol)
                    self.alerts.notify_signal(pending.signal)
                    LOG.info("Filled pending limit order for %s", pos.symbol)
                    self.state_store.update_state(
                        open_position={
                            "symbol": pos.symbol,
                            "direction": pos.direction.value,
                            "entry": pos.entry,
                        },
                        equity=self.risk.state.equity,
                        daily_pnl=(self.risk.state.equity - self.risk.state.daily_start_equity),
                    )
                    self._sync_runtime_state()
                else:
                    LOG.info("Pending limit order still open for %s", pending.signal.symbol)
            return

        candidates: List[Candidate] = []
        mtf_cache: Dict[Tuple[str, MarketType, str], MarketSnapshot | None] = {}
        symbols_this_tick = self._symbols_for_tick()
        LOG.info("Scanning symbols this tick: %s", ",".join(symbols_this_tick))
        for sym in symbols_this_tick:
            spot_snapshot = self.data.fetch_snapshot(sym, MarketType.SPOT, self.cfg.timeframe)
            perp_snapshot = self.data.fetch_snapshot(sym, MarketType.PERP, self.cfg.timeframe)

            snapshots = [s for s in [spot_snapshot, perp_snapshot] if s is not None]
            for snap in snapshots:
                decision = self._evaluate_snapshot(snap)
                if decision.signal is None:
                    continue
                sig = decision.signal
                disabled, reason = self._is_module_temporarily_disabled(sig.strategy_module)
                if disabled:
                    LOG.info("Module scorecard blocked %s: %s", sig.strategy_module.value, reason)
                    continue

                if self.cfg.enable_mtf_confirmation:
                    htf_snaps = self._fetch_mtf_snapshots(sym, snap.market, mtf_cache)
                    mtf_ok, mtf_reason = self._passes_mtf_confirmation(sig, htf_snaps)
                    if not mtf_ok:
                        LOG.info(
                            "MTF blocked %s %s %s: %s",
                            sig.symbol,
                            sig.market.value,
                            sig.strategy_module.value,
                            mtf_reason,
                        )
                        continue

                # Spot/perps routing rules.
                if sig.direction == Direction.SHORT and sig.market != MarketType.PERP:
                    continue
                ok_funding, funding_reason = self._funding_filter(sig, snap)
                if not ok_funding:
                    LOG.info("Funding filter blocked %s %s: %s", sig.symbol, sig.market.value, funding_reason)
                    self.alerts.notify(
                        "FUNDING_SPIKE_WARNING",
                        {"symbol": sig.symbol, "market": sig.market.value, "reason": funding_reason},
                    )
                    continue
                trend_ok, trend_reason = self._funding_trend_ok(sig, snap)
                if not trend_ok:
                    LOG.info("Funding trend blocked %s %s: %s", sig.symbol, sig.market.value, trend_reason)
                    self.alerts.notify(
                        "FUNDING_TREND_WARNING",
                        {"symbol": sig.symbol, "market": sig.market.value, "reason": trend_reason},
                    )
                    continue
                candidates.append(Candidate(signal=sig, snapshot=snap, strategy_reason=decision.reason))

        chosen = self._choose_best_candidate(candidates)
        if chosen is None:
            LOG.info("No valid entry candidates")
            self.state_store.update_state(
                open_position=None,
                equity=self.risk.state.equity,
                daily_pnl=(self.risk.state.equity - self.risk.state.daily_start_equity),
            )
            self._sync_runtime_state()
            return

        sig = chosen.signal
        snap = chosen.snapshot
        fingerprint = self._signal_fingerprint(sig, snap.candles[-1].open_time)
        if self.last_fingerprint_by_symbol.get(sig.symbol) == fingerprint:
            LOG.info("No fresh signal for %s", sig.symbol)
            return

        risk_decision = self.risk.pre_trade_check(sig)
        if not risk_decision.approved:
            LOG.info("Risk blocked signal: %s", risk_decision.reason)
            return

        exec_decision = self.execution.execution_check(snap, sig.direction)
        if not exec_decision.approved:
            LOG.info("Execution blocked signal: %s", exec_decision.reason)
            return

        rules = self._get_rules(sig.symbol, sig.market)
        pos = None
        if self.cfg.enable_limit_orders:
            pos = self.execution.place_limit_order(sig, risk_decision.quantity, rules, snapshot=snap)
            if pos is None:
                LOG.info("Placed limit order for %s", sig.symbol)
        else:
            pos = self.execution.open_trade(sig, risk_decision.quantity, rules, snapshot=snap)
        if pos is None:
            if self.cfg.enable_limit_orders:
                return
            LOG.info("Could not open simulated position due to filters/notional/qty")
            return

        self.risk.register_new_trade(sig.symbol)
        self.last_fingerprint_by_symbol[sig.symbol] = fingerprint
        self.alerts.notify_signal(sig)
        self.state_store.update_state(
            open_position={
                "symbol": sig.symbol,
                "direction": sig.direction.value,
                "entry": sig.entry,
            },
            equity=self.risk.state.equity,
            daily_pnl=(self.risk.state.equity - self.risk.state.daily_start_equity),
        )
        self._sync_runtime_state()
        LOG.info(
            "Opened paper trade %s %s %s qty=%.6f entry=%.6f stop=%.6f t1=%.6f t2=%.6f",
            sig.symbol,
            sig.market.value,
            sig.direction.value,
            pos.quantity,
            pos.entry,
            pos.stop,
            pos.target_1,
            pos.target_2,
        )

        self._sync_runtime_state()

    def run_forever(self) -> None:
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                LOG.exception("bot tick failure: %s", exc)
            time.sleep(self.cfg.loop_seconds)
