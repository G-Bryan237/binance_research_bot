from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_csv(value: str | None, default: List[str]) -> List[str]:
    if not value:
        return default
    return [s.strip().upper() for s in value.split(",") if s.strip()]


def _parse_float_csv(value: str | None, default: List[float]) -> List[float]:
    if not value:
        return default
    out: List[float] = []
    for part in value.split(","):
        s = part.strip()
        if not s:
            continue
        out.append(float(s))
    return out or default


def _default_profile_id(env_file: str) -> str:
    name = Path(env_file).name.lower()
    if "conservative" in name:
        return "conservative"
    if "aggressive" in name:
        return "aggressive"
    return "default"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        value = value.strip()
        if value and not (value.startswith('"') or value.startswith("'")):
            hash_idx = value.find(" #")
            if hash_idx != -1:
                value = value[:hash_idx].rstrip()
        os.environ[key.strip()] = value


@dataclass(frozen=True)
class AlertConfig:
    webhook_url: str
    telegram_bot_token: str
    telegram_chat_id: str
    email_smtp_host: str
    email_smtp_port: int
    email_user: str
    email_password: str
    email_from: str
    email_to: str


@dataclass(frozen=True)
class ApiConfig:
    spot_api_key: str
    spot_api_secret: str
    futures_api_key: str
    futures_api_secret: str
    spot_base_url: str = "https://testnet.binance.vision"
    futures_base_url: str = "https://demo-fapi.binance.com"
    request_timeout_seconds: float = 8.0
    request_retries: int = 2
    request_retry_backoff_seconds: float = 0.6
    use_mainnet_data_fallback: bool = True


@dataclass(frozen=True)
class RiskConfig:
    start_equity: float
    risk_per_trade_pct: float
    max_daily_loss_pct: float
    max_weekly_loss_pct: float
    max_trades_per_day: int
    max_trades_per_symbol_day: int
    cooldown_minutes: int
    enable_leverage_2x: bool
    m4_risk_multiplier: float
    max_loss_streak_for_day_stop: int = 3
    cooldown_after_two_losses_minutes: int = 60
    enable_drawdown_scaling: bool = True
    drawdown_scale_threshold_pct: float = 0.01
    drawdown_scale_factor: float = 0.75


@dataclass(frozen=True)
class ExecutionConfig:
    spot_fee_bps: float
    perp_fee_bps: float
    slippage_bps: float
    enable_partial_tp: bool
    partial_tp_fraction: float
    tp_rr_levels: List[float]
    tp_close_fractions: List[float]
    enable_trailing_stop: bool
    enable_trailing_on_full_position: bool
    trailing_activation_rr: float
    trailing_atr_multiplier: float
    enable_time_exit: bool = True
    max_hold_minutes: int = 360
    enable_sr_exits: bool = True
    sr_lookback_bars: int = 40
    sr_exit_tolerance_atr: float = 0.25
    enable_depth_analysis: bool = False
    enable_orderbook_analysis: bool = False
    orderbook_depth_limit: int = 20
    imbalance_threshold: float = 1.3
    dynamic_slippage_enabled: bool = False
    slippage_volatility_multiplier: float = 0.0
    slippage_spread_multiplier: float = 0.0
    latency_bps: float = 0.0
    orderbook_wall_multiplier: float = 3.5
    orderbook_wall_range_bps: float = 12.0
    min_orderbook_levels: int = 5
    min_orderbook_notional_spot: float = 50000.0
    min_orderbook_notional_perp: float = 100000.0
    spread_limit_spot_pct: float = 0.08
    spread_limit_perp_pct: float = 0.10
    slippage_limit_pct: float = 0.10
    enable_limit_orders: bool = False
    limit_order_offset_bps: float = 5.0
    limit_order_timeout_seconds: int = 10
    enable_reversal_exits: bool = True
    enable_candle_pattern_exits: bool = True
    enable_divergence_detection: bool = True
    enable_macd_divergence: bool = True
    divergence_lookback: int = 20
    divergence_swing_window: int = 3


@dataclass(frozen=True)
class BotConfig:
    profile_id: str
    profile_name: str
    paper_mode: bool
    timeframe: str
    loop_seconds: int
    symbols_per_tick: int
    enable_m4: bool
    enable_m5: bool  # VWAP + Volume Profile institutional levels
    enable_m6: bool  # Multi-timeframe divergence
    enable_mtf_confirmation: bool
    mtf_confirm_timeframes: List[str]
    enable_module_scorecard: bool
    module_perf_lookback: int
    module_perf_min_trades: int
    module_perf_min_win_rate: float
    module_perf_disable_minutes: int
    # Advanced indicators
    enable_macd: bool
    enable_bollinger_bands: bool
    enable_stoch_rsi: bool
    enable_obv: bool
    enable_ichimoku: bool
    macd_fast: int
    macd_slow: int
    macd_signal: int
    bb_length: int
    bb_std_dev: float
    stoch_rsi_length: int
    stoch_rsi_k: int
    stoch_rsi_d: int
    ichimoku_conversion: int
    ichimoku_base: int
    ichimoku_span_b: int
    ichimoku_displacement: int
    enable_divergence_detection: bool
    enable_macd_divergence: bool
    divergence_lookback: int
    divergence_swing_window: int
    # Exit signals
    enable_reversal_exits: bool
    enable_candle_pattern_exits: bool
    # Funding analysis
    enable_funding_trend_analysis: bool
    funding_trend_window_hours: int
    funding_trend_slope_bps_per_hour: float
    funding_spike_alert_bps: float
    # Order book & microstructure
    enable_orderbook_analysis: bool
    enable_volume_profile: bool
    enable_vwap: bool
    enable_limit_orders: bool
    vwap_lookback: int
    vwap_use_current_session: bool
    volume_profile_bins: int
    volume_profile_lookback: int
    volume_profile_value_area_pct: float
    # Web dashboard
    enable_web_dashboard: bool
    dashboard_host: str
    dashboard_port: int
    dashboard_update_interval_ms: int
    state_db_path: str
    enable_reporting: bool
    report_output_dir: str
    # Symbol list and configs
    allowed_symbols: List[str]
    api: ApiConfig
    risk: RiskConfig
    execution: ExecutionConfig
    alerts: AlertConfig


DEFAULT_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "ADAUSDT",
    "XRPUSDT",
    "DOTUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "ATOMUSDT",
    "NEARUSDT",
    "LTCUSDT",
]


def load_config(env_file: str = ".env") -> BotConfig:
    _load_env_file(Path(env_file))
    profile_id = os.getenv("BOT_PROFILE_ID") or _default_profile_id(env_file)
    profile_name = os.getenv("BOT_PROFILE_NAME") or f"{profile_id.title()} Profile"

    alert_cfg = AlertConfig(
        webhook_url=os.getenv("ALERT_WEBHOOK_URL", ""),
        telegram_bot_token=os.getenv("ALERT_TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("ALERT_TELEGRAM_CHAT_ID", ""),
        email_smtp_host=os.getenv("ALERT_EMAIL_SMTP_HOST", ""),
        email_smtp_port=int(os.getenv("ALERT_EMAIL_SMTP_PORT", "587")),
        email_user=os.getenv("ALERT_EMAIL_USER", ""),
        email_password=os.getenv("ALERT_EMAIL_PASSWORD", ""),
        email_from=os.getenv("ALERT_EMAIL_FROM", ""),
        email_to=os.getenv("ALERT_EMAIL_TO", ""),
    )

    api_cfg = ApiConfig(
        spot_api_key=os.getenv("BINANCE_SPOT_API_KEY", ""),
        spot_api_secret=os.getenv("BINANCE_SPOT_API_SECRET", ""),
        futures_api_key=os.getenv("BINANCE_FUTURES_API_KEY", ""),
        futures_api_secret=os.getenv("BINANCE_FUTURES_API_SECRET", ""),
        request_timeout_seconds=float(os.getenv("BOT_DATA_TIMEOUT_SECONDS", "6")),
        request_retries=int(os.getenv("BOT_DATA_RETRIES", "1")),
        request_retry_backoff_seconds=float(os.getenv("BOT_DATA_RETRY_BACKOFF_SECONDS", "0.6")),
        use_mainnet_data_fallback=_parse_bool(os.getenv("BOT_USE_MAINNET_DATA_FALLBACK"), True),
    )

    risk_cfg = RiskConfig(
        start_equity=float(os.getenv("BOT_START_EQUITY", "50")),
        risk_per_trade_pct=float(os.getenv("BOT_RISK_PER_TRADE_PCT", "0.0075")),
        max_daily_loss_pct=float(os.getenv("BOT_MAX_DAILY_LOSS_PCT", "0.025")),
        max_weekly_loss_pct=float(os.getenv("BOT_MAX_WEEKLY_LOSS_PCT", "0.06")),
        max_trades_per_day=int(os.getenv("BOT_MAX_TRADES_PER_DAY", "5")),
        max_trades_per_symbol_day=int(os.getenv("BOT_MAX_TRADES_PER_SYMBOL_DAY", "2")),
        cooldown_minutes=int(os.getenv("BOT_COOLDOWN_MINUTES", "15")),
        enable_leverage_2x=_parse_bool(os.getenv("BOT_ENABLE_LEVERAGE_2X"), False),
        m4_risk_multiplier=float(os.getenv("BOT_M4_RISK_MULTIPLIER", "0.5")),
        enable_drawdown_scaling=_parse_bool(os.getenv("BOT_ENABLE_DRAWDOWN_SCALING"), True),
        drawdown_scale_threshold_pct=float(os.getenv("BOT_DRAWDOWN_SCALE_THRESHOLD_PCT", "0.01")),
        drawdown_scale_factor=float(os.getenv("BOT_DRAWDOWN_SCALE_FACTOR", "0.75")),
    )

    exec_cfg = ExecutionConfig(
        spot_fee_bps=float(os.getenv("BOT_SPOT_FEE_BPS", "10")),
        perp_fee_bps=float(os.getenv("BOT_PERP_FEE_BPS", "4")),
        slippage_bps=float(os.getenv("BOT_SLIPPAGE_BPS", "8")),
        enable_partial_tp=_parse_bool(os.getenv("BOT_ENABLE_PARTIAL_TP"), True),
        partial_tp_fraction=float(os.getenv("BOT_PARTIAL_TP_FRACTION", "0.5")),
        tp_rr_levels=_parse_float_csv(os.getenv("BOT_TP_RR_LEVELS"), [1.0, 1.8, 2.6]),
        tp_close_fractions=_parse_float_csv(os.getenv("BOT_TP_CLOSE_FRACTIONS"), [0.3, 0.3, 0.4]),
        enable_trailing_stop=_parse_bool(os.getenv("BOT_ENABLE_TRAILING_STOP"), True),
        enable_trailing_on_full_position=_parse_bool(os.getenv("BOT_ENABLE_TRAILING_ON_FULL_POSITION"), True),
        trailing_activation_rr=float(os.getenv("BOT_TRAILING_ACTIVATION_RR", "0.0")),
        trailing_atr_multiplier=float(os.getenv("BOT_TRAILING_ATR_MULTIPLIER", "1.2")),
        enable_time_exit=_parse_bool(os.getenv("BOT_ENABLE_TIME_EXIT"), True),
        max_hold_minutes=int(os.getenv("BOT_MAX_HOLD_MINUTES", "360")),
        enable_sr_exits=_parse_bool(os.getenv("BOT_ENABLE_SR_EXITS"), True),
        sr_lookback_bars=int(os.getenv("BOT_SR_LOOKBACK_BARS", "40")),
        sr_exit_tolerance_atr=float(os.getenv("BOT_SR_EXIT_TOLERANCE_ATR", "0.25")),
        enable_depth_analysis=_parse_bool(os.getenv("BOT_ENABLE_DEPTH_ANALYSIS"), False),
        enable_orderbook_analysis=_parse_bool(os.getenv("BOT_ENABLE_ORDERBOOK_ANALYSIS"), False),
        orderbook_depth_limit=int(os.getenv("BOT_ORDERBOOK_DEPTH_LIMIT", "20")),
        imbalance_threshold=float(os.getenv("BOT_IMBALANCE_THRESHOLD", "1.3")),
        dynamic_slippage_enabled=_parse_bool(os.getenv("BOT_DYNAMIC_SLIPPAGE_ENABLED"), False),
        slippage_volatility_multiplier=float(os.getenv("BOT_SLIPPAGE_VOLATILITY_MULTIPLIER", "0.0")),
        slippage_spread_multiplier=float(os.getenv("BOT_SLIPPAGE_SPREAD_MULTIPLIER", "0.0")),
        latency_bps=float(os.getenv("BOT_LATENCY_BPS", "0.0")),
        orderbook_wall_multiplier=float(os.getenv("BOT_ORDERBOOK_WALL_MULTIPLIER", "3.5")),
        orderbook_wall_range_bps=float(os.getenv("BOT_ORDERBOOK_WALL_RANGE_BPS", "12.0")),
        min_orderbook_levels=int(os.getenv("BOT_MIN_ORDERBOOK_LEVELS", "5")),
        min_orderbook_notional_spot=float(os.getenv("BOT_MIN_ORDERBOOK_NOTIONAL_SPOT", "50000")),
        min_orderbook_notional_perp=float(os.getenv("BOT_MIN_ORDERBOOK_NOTIONAL_PERP", "100000")),
        spread_limit_spot_pct=float(os.getenv("BOT_SPREAD_LIMIT_SPOT_PCT", "0.08")),
        spread_limit_perp_pct=float(os.getenv("BOT_SPREAD_LIMIT_PERP_PCT", "0.10")),
        slippage_limit_pct=float(os.getenv("BOT_SLIPPAGE_LIMIT_PCT", "0.10")),
        enable_limit_orders=_parse_bool(os.getenv("BOT_ENABLE_LIMIT_ORDERS"), False),
        limit_order_offset_bps=float(os.getenv("BOT_LIMIT_ORDER_OFFSET_BPS", "5")),
        limit_order_timeout_seconds=int(os.getenv("BOT_LIMIT_ORDER_TIMEOUT_SECONDS", "10")),
        enable_reversal_exits=_parse_bool(os.getenv("BOT_ENABLE_REVERSAL_EXITS"), True),
        enable_candle_pattern_exits=_parse_bool(os.getenv("BOT_ENABLE_CANDLE_PATTERN_EXITS"), True),
        enable_divergence_detection=_parse_bool(os.getenv("BOT_ENABLE_DIVERGENCE_DETECTION"), True),
        enable_macd_divergence=_parse_bool(os.getenv("BOT_ENABLE_MACD_DIVERGENCE"), True),
        divergence_lookback=int(os.getenv("BOT_DIVERGENCE_LOOKBACK", "20")),
        divergence_swing_window=int(os.getenv("BOT_DIVERGENCE_SWING_WINDOW", "3")),
    )

    mtf_tfs = [s.strip() for s in os.getenv("BOT_MTF_CONFIRM_TIMEFRAMES", "15m").split(",") if s.strip()]

    return BotConfig(
        profile_id=profile_id,
        profile_name=profile_name,
        paper_mode=_parse_bool(os.getenv("BOT_PAPER_MODE"), True),
        timeframe=os.getenv("BOT_TIMEFRAME", "5m"),
        loop_seconds=int(os.getenv("BOT_LOOP_SECONDS", "30")),
        symbols_per_tick=int(os.getenv("BOT_SYMBOLS_PER_TICK", "4")),
        enable_m4=_parse_bool(os.getenv("BOT_ENABLE_M4"), True),
        enable_m5=_parse_bool(os.getenv("BOT_ENABLE_M5"), True),
        enable_m6=_parse_bool(os.getenv("BOT_ENABLE_M6"), True),
        enable_mtf_confirmation=_parse_bool(os.getenv("BOT_ENABLE_MTF_CONFIRMATION"), True),
        mtf_confirm_timeframes=mtf_tfs,
        enable_module_scorecard=_parse_bool(os.getenv("BOT_ENABLE_MODULE_SCORECARD"), True),
        module_perf_lookback=int(os.getenv("BOT_MODULE_PERF_LOOKBACK", "10")),
        module_perf_min_trades=int(os.getenv("BOT_MODULE_PERF_MIN_TRADES", "4")),
        module_perf_min_win_rate=float(os.getenv("BOT_MODULE_PERF_MIN_WIN_RATE", "0.30")),
        module_perf_disable_minutes=int(os.getenv("BOT_MODULE_PERF_DISABLE_MINUTES", "180")),
        # Advanced indicators
        enable_macd=_parse_bool(os.getenv("BOT_ENABLE_MACD"), True),
        enable_bollinger_bands=_parse_bool(os.getenv("BOT_ENABLE_BOLLINGER_BANDS"), True),
        enable_stoch_rsi=_parse_bool(os.getenv("BOT_ENABLE_STOCH_RSI"), True),
        enable_obv=_parse_bool(os.getenv("BOT_ENABLE_OBV"), True),
        enable_ichimoku=_parse_bool(os.getenv("BOT_ENABLE_ICHIMOKU"), False),
        macd_fast=int(os.getenv("BOT_MACD_FAST", "12")),
        macd_slow=int(os.getenv("BOT_MACD_SLOW", "26")),
        macd_signal=int(os.getenv("BOT_MACD_SIGNAL", "9")),
        bb_length=int(os.getenv("BOT_BB_LENGTH", "20")),
        bb_std_dev=float(os.getenv("BOT_BB_STD_DEV", "2.0")),
        stoch_rsi_length=int(os.getenv("BOT_STOCH_RSI_LENGTH", "14")),
        stoch_rsi_k=int(os.getenv("BOT_STOCH_RSI_K", "3")),
        stoch_rsi_d=int(os.getenv("BOT_STOCH_RSI_D", "3")),
        ichimoku_conversion=int(os.getenv("BOT_ICHIMOKU_CONVERSION", "9")),
        ichimoku_base=int(os.getenv("BOT_ICHIMOKU_BASE", "26")),
        ichimoku_span_b=int(os.getenv("BOT_ICHIMOKU_SPAN_B", "52")),
        ichimoku_displacement=int(os.getenv("BOT_ICHIMOKU_DISPLACEMENT", "26")),
        enable_divergence_detection=_parse_bool(os.getenv("BOT_ENABLE_DIVERGENCE_DETECTION"), True),
        enable_macd_divergence=_parse_bool(os.getenv("BOT_ENABLE_MACD_DIVERGENCE"), True),
        divergence_lookback=int(os.getenv("BOT_DIVERGENCE_LOOKBACK", "20")),
        divergence_swing_window=int(os.getenv("BOT_DIVERGENCE_SWING_WINDOW", "3")),
        # Exit signals
        enable_reversal_exits=_parse_bool(os.getenv("BOT_ENABLE_REVERSAL_EXITS"), True),
        enable_candle_pattern_exits=_parse_bool(os.getenv("BOT_ENABLE_CANDLE_PATTERN_EXITS"), True),
        # Funding analysis
        enable_funding_trend_analysis=_parse_bool(os.getenv("BOT_ENABLE_FUNDING_TREND_ANALYSIS"), True),
        funding_trend_window_hours=int(os.getenv("BOT_FUNDING_TREND_WINDOW", "24")),
        funding_trend_slope_bps_per_hour=float(os.getenv("BOT_FUNDING_TREND_SLOPE_BPS_PER_HOUR", "4")),
        funding_spike_alert_bps=float(os.getenv("BOT_FUNDING_SPIKE_ALERT_BPS", "50")),
        # Order book & microstructure
        enable_orderbook_analysis=_parse_bool(os.getenv("BOT_ENABLE_ORDERBOOK_ANALYSIS"), False),
        enable_volume_profile=_parse_bool(os.getenv("BOT_ENABLE_VOLUME_PROFILE"), False),
        enable_vwap=_parse_bool(os.getenv("BOT_ENABLE_VWAP"), True),
        enable_limit_orders=_parse_bool(os.getenv("BOT_ENABLE_LIMIT_ORDERS"), False),
        vwap_lookback=int(os.getenv("BOT_VWAP_LOOKBACK", "60")),
        vwap_use_current_session=_parse_bool(os.getenv("BOT_VWAP_USE_CURRENT_SESSION"), False),
        volume_profile_bins=int(os.getenv("BOT_VOLUME_PROFILE_BINS", "50")),
        volume_profile_lookback=int(os.getenv("BOT_VOLUME_PROFILE_LOOKBACK", "200")),
        volume_profile_value_area_pct=float(os.getenv("BOT_VOLUME_PROFILE_VALUE_AREA_PCT", "0.7")),
        # Web dashboard
        enable_web_dashboard=_parse_bool(os.getenv("BOT_ENABLE_WEB_DASHBOARD"), True),
        dashboard_host=os.getenv("BOT_DASHBOARD_HOST", "0.0.0.0"),
        dashboard_port=int(os.getenv("BOT_DASHBOARD_PORT", "5000")),
        dashboard_update_interval_ms=int(os.getenv("BOT_DASHBOARD_UPDATE_INTERVAL_MS", "500")),
        state_db_path=os.getenv("BOT_STATE_DB_PATH", "out/bot_state.sqlite3"),
        enable_reporting=_parse_bool(os.getenv("BOT_ENABLE_REPORTING"), True),
        report_output_dir=os.getenv("BOT_REPORT_OUTPUT_DIR", "reports"),
        allowed_symbols=_parse_csv(os.getenv("BOT_ALLOWED_SYMBOLS"), DEFAULT_SYMBOLS),
        api=api_cfg,
        risk=risk_cfg,
        execution=exec_cfg,
        alerts=alert_cfg,
    )
