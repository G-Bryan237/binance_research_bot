"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { io } from "socket.io-client";

type OpenPosition = {
  symbol: string;
  direction: string;
  entry: number;
};

type Trade = {
  symbol: string;
  direction: string;
  entry: number;
  exit: number;
  pnl: number;
  strategy_module: string;
  close_reason: string;
  timestamp: string;
  partial_exit?: boolean;
  regime?: string;
  r_multiple?: number;
  fee_paid?: number;
  funding_paid?: number;
  simulated_friction?: number;
  execution_model?: string;
  profile_id?: string;
};

type ModuleScore = {
  win_rate: number;
  total_trades: number;
  active: boolean;
};

type TradingStats = {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  avg_r_multiple: number;
};

type EquityPoint = {
  time: string;
  equity: number;
};

type SignalJournal = {
  id: number;
  generated_at_utc: string;
  updated_at_utc: string;
  symbol: string;
  market: string;
  direction: string;
  entry: number;
  stop: number;
  target_1: number;
  target_2: number;
  risk_multiple: number;
  regime: string;
  strategy_module: string;
  rationale: string;
  strategy_reason: string;
  quality_score: number;
  quality_label: string;
  status: string;
  status_reason: string;
  opened_at_utc?: string | null;
  closed_at_utc?: string | null;
  exit_price?: number | null;
  pnl?: number | null;
  r_multiple?: number | null;
  close_reason?: string | null;
  forecast_status?: string;
  forecast_result?: string;
  forecast_reason?: string;
  forecast_expires_at_utc?: string | null;
  forecast_hit_at_utc?: string | null;
  forecast_hit_price?: number | null;
  forecast_r_multiple?: number | null;
  forecast_updated_at_utc?: string | null;
  profile_id?: string;
};

type SignalStats = {
  total_signals: number;
  opened_signals: number;
  blocked_signals: number;
  succeeded_signals: number;
  failed_signals: number;
  open_signals?: number;
  forecast_watching?: number;
  forecast_resolved?: number;
  forecast_successes?: number;
  forecast_failures?: number;
  forecast_expired?: number;
  tp1_hits?: number;
  tp2_hits?: number;
  stop_hits?: number;
  expired_no_hit?: number;
  success_rate: number;
  open_rate: number;
  forecast_success_rate?: number;
  forecast_resolution_rate?: number;
  tp_hit_rate?: number;
  stop_hit_rate?: number;
  expired_rate?: number;
  avg_quality_score?: number;
  by_module?: Record<string, SignalStats>;
  by_quality?: Record<string, SignalStats>;
};

type SimulationStats = {
  execution_model: string;
  total_closed_trades: number;
  total_fees: number;
  total_funding: number;
  total_friction: number;
  gross_estimated_pnl: number;
  net_pnl: number;
  friction_drag_pct: number;
  avg_fee_per_trade: number;
  partial_exits: number;
  blocked_execution: number;
  blocked_risk: number;
  pending_orders: number;
  expired_orders: number;
};
type SignalsResponse = {
  signals: SignalJournal[];
  stats: SignalStats;
};
type BotState = {
  status: string;
  current_tick: string | null;
  last_update_utc: string | null;
  environment: string;
  data_source?: string;
  update_interval_ms?: number;
  equity: number;
  starting_equity: number;
  daily_pnl: number;
  open_position: OpenPosition | null;
  recent_trades: Trade[];
  equity_history?: EquityPoint[];
  module_scores: Record<string, ModuleScore>;
  trading_stats: TradingStats;
  recent_signals: SignalJournal[];
  signal_stats: SignalStats;
  simulation_stats: SimulationStats;
};

type HealthResponse = {
  ok: boolean;
  service: string;
  status: string;
  last_update_utc: string | null;
  db_path: string | null;
};

type MarketCandle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

type MarketResponse = {
  symbol: string;
  candles: MarketCandle[];
};

type TradesResponse = {
  trades: Trade[];
};

type FetchResult<T> = {
  data: T;
  source: string;
};

type RouteStatus = {
  name: string;
  ok: boolean;
  source: string;
  detail: string;
};

type Theme = "dark" | "light";
type TradeFilter = "all" | "wins" | "losses";
type DashboardView = "overview" | "markets" | "strategies" | "signals" | "readiness" | "trades" | "system" | "reports";
type ReportPeriod = "daily" | "weekly" | "monthly";

type LiveTicker = {
  price: number;
  eventTime: number;
};

const FALLBACK_REFRESH_MS = 30000;
const MARKET_SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "BNBUSDT"];
const MARKET_SYMBOL = MARKET_SYMBOLS[0];
const DASHBOARD_VIEWS: Array<{ id: DashboardView; label: string; short: string }> = [
  { id: "overview", label: "Overview", short: "OV" },
  { id: "markets", label: "Markets", short: "MK" },
  { id: "strategies", label: "Strategy Trades", short: "ST" },
  { id: "signals", label: "Signals", short: "SG" },
  { id: "reports", label: "Reports", short: "RP" },
  { id: "system", label: "System", short: "SY" },
];
const STRATEGY_LABELS: Record<string, string> = {
  M1: "Trend Pullback with Liquidity Sweep",
  M2: "Breakout Retest with FVG Mitigation",
  M3: "Liquidation Sweep Reversal",
  M4: "Low-Volatility Mean Reversion",
  M5: "VWAP + Volume Profile Levels",
  M6: "Multi-Timeframe Divergence",
  DO_NOTHING: "No-Trade Regime",
};
const STRATEGY_DETAILS: Record<string, string> = {
  M1: "Pullback, sweep, and momentum reclaim setup",
  M2: "Breakout retest with imbalance mitigation",
  M3: "Sweep reversal back toward mean or range edge",
  M4: "Band, RSI, and range-edge reclaim setup",
  M5: "VWAP deviation + Value Area edge rejection",
  M6: "RSI/MACD divergence with confirmation",
  DO_NOTHING: "Regime filter is blocking new entries",
};
const CONFIGURED_STRATEGIES = ["M1", "M2", "M3", "M4", "M5", "M6"];
const WS_URL =
  process.env.NEXT_PUBLIC_PYTHON_BACKEND_WS_URL || "http://127.0.0.1:5000";

const EMPTY_STATE: BotState = {
  status: "initializing",
  current_tick: null,
  last_update_utc: null,
  environment: "paper",
  data_source: "waiting for data",
  update_interval_ms: FALLBACK_REFRESH_MS,
  equity: 0,
  starting_equity: 0,
  daily_pnl: 0,
  open_position: null,
  recent_trades: [],
  equity_history: [],
  module_scores: {},
  trading_stats: {
    total_trades: 0,
    winning_trades: 0,
    losing_trades: 0,
    win_rate: 0,
    avg_r_multiple: 0,
  },
  recent_signals: [],
  simulation_stats: {
    execution_model: "paper-slippage-fee-model",
    total_closed_trades: 0,
    total_fees: 0,
    total_funding: 0,
    total_friction: 0,
    gross_estimated_pnl: 0,
    net_pnl: 0,
    friction_drag_pct: 0,
    avg_fee_per_trade: 0,
    partial_exits: 0,
    blocked_execution: 0,
    blocked_risk: 0,
    pending_orders: 0,
    expired_orders: 0,
  },
  signal_stats: {
    total_signals: 0,
    opened_signals: 0,
    blocked_signals: 0,
    succeeded_signals: 0,
    failed_signals: 0,
    open_signals: 0,
    forecast_watching: 0,
    forecast_resolved: 0,
    forecast_successes: 0,
    forecast_failures: 0,
    forecast_expired: 0,
    tp1_hits: 0,
    tp2_hits: 0,
    stop_hits: 0,
    expired_no_hit: 0,
    success_rate: 0,
    open_rate: 0,
    forecast_success_rate: 0,
    forecast_resolution_rate: 0,
    tp_hit_rate: 0,
    stop_hit_rate: 0,
    expired_rate: 0,
    avg_quality_score: 0,
    by_module: {},
    by_quality: {},
  },
};

async function fetchJson<T>(url: string): Promise<FetchResult<T>> {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return {
    data: (await response.json()) as T,
    source: response.headers.get("x-bot-data-source") || "backend",
  };
}

async function settleFetch<T>(name: string, url: string): Promise<{
  name: string;
  result: FetchResult<T> | null;
  status: RouteStatus;
}> {
  try {
    const result = await fetchJson<T>(url);
    return {
      name,
      result,
      status: {
        name,
        ok: true,
        source: result.source,
        detail: "200 OK",
      },
    };
  } catch (error) {
    return {
      name,
      result: null,
      status: {
        name,
        ok: false,
        source: "none",
        detail: error instanceof Error ? error.message : "Request failed",
      },
    };
  }
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(value ?? 0);
}

function formatNumber(value: number, maximumFractionDigits = 2): string {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits }).format(value ?? 0);
}

function formatSignedCurrency(value: number): string {
  const formatted = formatCurrency(Math.abs(value));
  return `${value >= 0 ? "+" : "-"}${formatted}`;
}

function formatPercent(value: number): string {
  return `${((value || 0) * 100).toFixed(2)}%`;
}

function formatDate(value: string | null): string {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function formatCompactDate(value: string | null): string {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function normalizeStrategyCode(value: string | undefined | null): string {
  if (!value) {
    return "";
  }
  const cleaned = value.toUpperCase().replace(/^STRATEGYMODULE\./, "").trim();
  return cleaned || "";
}

function strategyLabel(value: string | undefined | null): string {
  const code = normalizeStrategyCode(value);
  return code ? (STRATEGY_LABELS[code] ?? code) : "Regime-Mapped Strategy Stack";
}

function strategyDetail(value: string | undefined | null): string {
  const code = normalizeStrategyCode(value);
  return code ? (STRATEGY_DETAILS[code] ?? "Custom strategy module") : "M1-M4 selected by detected market regime";
}


function mergeState(current: BotState | null, patch: Partial<BotState>): BotState {
  const base = current ?? EMPTY_STATE;
  return {
    ...base,
    ...patch,
    recent_trades: patch.recent_trades ?? base.recent_trades,
    equity_history: patch.equity_history ?? base.equity_history,
    module_scores: patch.module_scores ?? base.module_scores,
    trading_stats: patch.trading_stats ?? base.trading_stats,
    recent_signals: patch.recent_signals ?? base.recent_signals,
    signal_stats: patch.signal_stats ?? base.signal_stats,
    simulation_stats: patch.simulation_stats ?? base.simulation_stats,
  };
}

function statusClass(status: string | undefined): string {
  return (status || "initializing").toLowerCase().replace(/[^a-z0-9]+/g, "-");
}


function signalStatusClass(status: string | undefined): string {
  return (status || "generated").toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

function signalTone(status: string | undefined): "neutral" | "positive" | "negative" {
  const normalized = (status || "").toUpperCase();
  if (normalized === "SUCCEEDED" || normalized === "OPENED") {
    return "positive";
  }
  if (normalized === "FAILED" || normalized.startsWith("BLOCKED") || normalized === "EXPIRED") {
    return "negative";
  }
  return "neutral";
}

function formatSignalResult(signal: SignalJournal): string {
  if (signal.pnl === null || signal.pnl === undefined) {
    return signal.close_reason || "-";
  }
  return `${formatSignedCurrency(signal.pnl)}${signal.r_multiple !== null && signal.r_multiple !== undefined ? ` / ${signal.r_multiple.toFixed(2)}R` : ""}`;
}

function formatForecastResult(signal: SignalJournal): string {
  const result = signal.forecast_result || "watching";
  if (result === "watching") {
    return signal.forecast_expires_at_utc ? `watching until ${formatCompactDate(signal.forecast_expires_at_utc)}` : "watching";
  }
  if (result === "tp1_hit") {
    return `TP1 hit${signal.forecast_r_multiple !== null && signal.forecast_r_multiple !== undefined ? ` / ${signal.forecast_r_multiple.toFixed(2)}R` : ""}`;
  }
  if (result === "tp2_hit") {
    return `TP2 hit${signal.forecast_r_multiple !== null && signal.forecast_r_multiple !== undefined ? ` / ${signal.forecast_r_multiple.toFixed(2)}R` : ""}`;
  }
  if (result === "stop_hit") {
    return "stop hit / -1.00R";
  }
  if (result === "expired_no_hit") {
    return "expired no hit";
  }
  return result.replace(/_/g, " ");
}
function buildLinePath(values: number[], height = 42): string {
  const cleanValues = values.filter((value) => Number.isFinite(value));
  if (cleanValues.length < 2) {
    return "";
  }

  const min = Math.min(...cleanValues);
  const max = Math.max(...cleanValues);
  const range = max - min || Math.max(Math.abs(max) * 0.02, 1);
  const top = 6;
  const bottom = height - 4;

  return cleanValues
    .map((value, index) => {
      const x = (index / (cleanValues.length - 1)) * 100;
      const y = bottom - ((value - min) / range) * (bottom - top);
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

function buildEquityPath(points: EquityPoint[] | undefined): string {
  return buildLinePath((points ?? []).map((point) => point.equity));
}

function MetricCard({
  label,
  value,
  foot,
  tone = "neutral",
}: {
  label: string;
  value: string;
  foot: string;
  tone?: "neutral" | "positive" | "negative";
}) {
  return (
    <article className={`metric-card metric-${tone}`}>
      <p className="metric-label">{label}</p>
      <p className="metric-value">{value}</p>
      <p className="metric-foot">{foot}</p>
    </article>
  );
}

function ReportMetricCard({
  label,
  value,
  foot,
  tone = "neutral",
}: {
  label: string;
  value: string;
  foot: string;
  tone?: "neutral" | "positive" | "negative";
}) {
  return (
    <article className={`report-metric-card metric-${tone}`}>
      <p className="metric-label">{label}</p>
      <p className="metric-value">{value}</p>
      <p className="metric-foot">{foot}</p>
    </article>
  );
}

type ProfileOption = {
  id: string;
  label: string;
  status?: string;
  equity?: number;
  pnl?: number;
};

type TradingDashboardProps = {
  profileId?: string;
  wsUrl?: string;
  apiBase?: string;
  compact?: boolean;
  profileSelector?: React.ReactNode;
  profileOptions?: ProfileOption[];
  onProfileChange?: (profileId: string) => void;
  allProfilesTrades?: Trade[];
};

export function TradingDashboard({ 
  profileId = "default",
  wsUrl = WS_URL,
  apiBase = "",
  compact = false,
  profileSelector,
  profileOptions = [],
  onProfileChange,
  allProfilesTrades = [],
}: TradingDashboardProps) {
  const [state, setState] = useState<BotState | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [market, setMarket] = useState<MarketResponse | null>(null);
  const [markets, setMarkets] = useState<Record<string, MarketResponse>>({});
  const [liveTickers, setLiveTickers] = useState<Record<string, LiveTicker>>({});
  const [priceStreamConnected, setPriceStreamConnected] = useState<boolean>(false);
  const [priceStreamIssue, setPriceStreamIssue] = useState<string>("");
  const [routeStatuses, setRouteStatuses] = useState<RouteStatus[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string>("");
  const [connectionIssue, setConnectionIssue] = useState<string>("");
  const [lastRefresh, setLastRefresh] = useState<string | null>(null);
  const [lastSource, setLastSource] = useState<string>("backend");
  const [realtimeConnected, setRealtimeConnected] = useState<boolean>(false);
  const [tradeFilter, setTradeFilter] = useState<TradeFilter>("all");
  const [selectedStrategy, setSelectedStrategy] = useState<string>("all");
  const [selectedSymbol, setSelectedSymbol] = useState<string>("all");
  const [dateFrom, setDateFrom] = useState<string>("");
  const [dateTo, setDateTo] = useState<string>("");
  const [activeView, setActiveView] = useState<DashboardView>("overview");
  const [theme, setTheme] = useState<Theme>("dark");
  const [balanceInput, setBalanceInput] = useState<string>("");
  const [adjustStarting, setAdjustStarting] = useState<boolean>(true);
  const [balanceMessage, setBalanceMessage] = useState<string>("");
  const [reportPeriod, setReportPeriod] = useState<ReportPeriod>("daily");
  const [profileDropdownOpen, setProfileDropdownOpen] = useState<boolean>(false);

  // Build API URL based on profile
  const buildApiUrl = useCallback((path: string) => {
    if (apiBase) {
      return `${apiBase}${path}`;
    }
    // Use profile-specific route if profileId is set
    if (profileId && profileId !== "default") {
      return `/api/profiles/${profileId}${path.replace("/api/bot", "")}`;
    }
    return path;
  }, [apiBase, profileId]);

  useEffect(() => {
    const storedTheme = window.localStorage.getItem("bot-dashboard-theme") as Theme | null;
    const prefersLight = window.matchMedia("(prefers-color-scheme: light)").matches;
    setTheme(storedTheme ?? (prefersLight ? "light" : "dark"));
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("bot-dashboard-theme", theme);
  }, [theme]);

  const loadData = useCallback(async () => {
    const stateUrl = apiBase ? `${apiBase}/api/state` : buildApiUrl("/api/bot/state");
    const statsUrl = apiBase ? `${apiBase}/api/stats` : buildApiUrl("/api/bot/stats");
    const tradesUrl = apiBase ? `${apiBase}/api/trades` : buildApiUrl("/api/bot/trades");
    const signalsUrl = apiBase ? `${apiBase}/api/signals` : buildApiUrl("/api/bot/signals");
    const healthUrl = apiBase ? `${apiBase}/api/health` : buildApiUrl("/api/bot/health");
    
    const [stateResult, statsResult, tradesResult, signalsResult, healthResult, marketResults] = await Promise.all([
      settleFetch<BotState>("state", stateUrl),
      settleFetch<TradingStats>("stats", statsUrl),
      settleFetch<TradesResponse>("trades", tradesUrl),
      settleFetch<SignalsResponse>("signals", signalsUrl),
      settleFetch<HealthResponse>("health", healthUrl),
      Promise.all(
        MARKET_SYMBOLS.map((symbol) => {
          const marketUrl = apiBase 
            ? `${apiBase}/api/market?symbol=${symbol}&limit=120`
            : `/api/bot/market?symbol=${symbol}&limit=120`;
          return settleFetch<MarketResponse>(`market:${symbol}`, marketUrl);
        }),
      ),
    ]);

    setRouteStatuses([stateResult, statsResult, tradesResult, signalsResult, healthResult, ...marketResults].map((item) => item.status));

    if (!stateResult.result) {
      setError(stateResult.status.detail);
      setIsLoading(false);
      return;
    }

    const stateFetch = stateResult.result;
    const marketMap = Object.fromEntries(
      marketResults
        .filter((item): item is typeof item & { result: FetchResult<MarketResponse> } => item.result !== null)
        .map((item) => [item.result.data.symbol.toUpperCase(), item.result.data]),
    );

    setState((prev) =>
      mergeState(prev, {
        ...stateFetch.data,
        trading_stats: statsResult.result?.data ?? stateFetch.data.trading_stats,
        recent_trades: tradesResult.result?.data.trades ?? stateFetch.data.recent_trades,
        recent_signals: signalsResult.result?.data.signals ?? stateFetch.data.recent_signals,
        signal_stats: signalsResult.result?.data.stats ?? stateFetch.data.signal_stats,
      }),
    );
    setHealth(healthResult.result?.data ?? null);
    setMarkets((prev) => ({ ...prev, ...marketMap }));
    setMarket(marketMap[MARKET_SYMBOL] ?? null);
    setLastSource(stateFetch.source);
    setError("");
    setLastRefresh(new Date().toISOString());
    setIsLoading(false);
  }, [apiBase, buildApiUrl]);

  const handleBalanceAdjust = useCallback(async () => {
    const newBalance = parseFloat(balanceInput);
    if (isNaN(newBalance) || newBalance < 0) {
      setBalanceMessage("Please enter a valid positive number");
      return;
    }
    
    try {
      const url = apiBase ? `${apiBase}/api/balance` : "/api/bot/balance";
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ balance: newBalance, adjust_starting: adjustStarting }),
      });
      
      if (response.ok) {
        const data = await response.json();
        setBalanceMessage(`Balance updated: $${data.old_equity.toFixed(2)} → $${data.new_equity.toFixed(2)}`);
        setBalanceInput("");
        // Refresh data
        void loadData();
      } else {
        const errorData = await response.json();
        setBalanceMessage(`Error: ${errorData.error || "Failed to update balance"}`);
      }
    } catch (err) {
      setBalanceMessage("Failed to connect to API");
    }
  }, [balanceInput, adjustStarting, apiBase, loadData]);

  useEffect(() => {
    void loadData();

    const fallbackTimer = setInterval(() => {
      void loadData();
    }, FALLBACK_REFRESH_MS);

    return () => clearInterval(fallbackTimer);
  }, [loadData]);


  useEffect(() => {
    const streams = MARKET_SYMBOLS.map((symbol) => `${symbol.toLowerCase()}@trade`).join("/");
    const socket = new WebSocket(`wss://stream.binance.com:9443/stream?streams=${streams}`);

    socket.onopen = () => {
      setPriceStreamConnected(true);
      setPriceStreamIssue("");
    };

    socket.onclose = () => {
      setPriceStreamConnected(false);
      setPriceStreamIssue("Binance live price stream disconnected. REST candles remain active.");
    };

    socket.onerror = () => {
      setPriceStreamConnected(false);
      setPriceStreamIssue("Binance live price stream unavailable. REST candles remain active.");
    };

    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data as string) as {
          data?: { s?: string; p?: string; T?: number; E?: number };
        };
        const symbol = payload.data?.s?.toUpperCase();
        const price = Number(payload.data?.p);
        if (!symbol || !MARKET_SYMBOLS.includes(symbol) || !Number.isFinite(price) || price <= 0) {
          return;
        }
        setLiveTickers((prev) => ({
          ...prev,
          [symbol]: {
            price,
            eventTime: Number(payload.data?.T || payload.data?.E || Date.now()),
          },
        }));
      } catch {
        // Ignore malformed exchange messages.
      }
    };

    return () => {
      socket.close();
    };
  }, []);

  useEffect(() => {
    // Use profile-specific WebSocket URL
    const socketUrl = wsUrl || WS_URL;
    const socket = io(socketUrl, {
      transports: ["websocket", "polling"],
      withCredentials: false,
    });

    socket.on("connect", () => {
      setRealtimeConnected(true);
      setConnectionIssue("");
    });

    socket.on("disconnect", () => {
      setRealtimeConnected(false);
      setConnectionIssue("Realtime stream disconnected. HTTP refresh remains active.");
    });

    socket.on("connect_error", () => {
      setRealtimeConnected(false);
      setConnectionIssue("Realtime stream offline. Start the Python API for live websocket updates.");
    });

    socket.on("state_update", (payload: Partial<BotState>) => {
      setState((prev) => mergeState(prev, payload));
      setLastSource("backend");
      setLastRefresh(new Date().toISOString());
    });

    socket.on("stats_update", (payload: TradingStats) => {
      setState((prev) => mergeState(prev, { trading_stats: payload }));
      setLastSource("backend");
      setLastRefresh(new Date().toISOString());
    });

    socket.on("module_scores_update", (payload: Record<string, ModuleScore>) => {
      setState((prev) => mergeState(prev, { module_scores: payload }));
      setLastSource("backend");
      setLastRefresh(new Date().toISOString());
    });

    socket.on("trade_added", (payload: Trade) => {
      setState((prev) => {
        const base = prev ?? EMPTY_STATE;
        const trades = [payload, ...base.recent_trades].slice(0, 200);
        return mergeState(base, { recent_trades: trades });
      });
      setLastSource("backend");
      setLastRefresh(new Date().toISOString());
    });

    return () => {
      socket.disconnect();
    };
  }, [wsUrl]);

  const activeState = state ?? EMPTY_STATE;
  const equityChange = activeState.equity - activeState.starting_equity;
  const equityChangePct = activeState.starting_equity > 0 ? equityChange / activeState.starting_equity : 0;
  const pnlTone = equityChange >= 0 ? "positive" : "negative";
  const dailyTone = activeState.daily_pnl >= 0 ? "positive" : "negative";
  const statusName = statusClass(activeState.status);
  const isFallback = lastSource === "fallback" || activeState.status === "offline-fallback";
  const equityPath = buildEquityPath(activeState.equity_history);
  const marketCandles = market?.candles ?? [];
  const latestCandle = marketCandles.at(-1);
  const previousCandle = marketCandles.at(-2);
  const liveMarketTicker = liveTickers[MARKET_SYMBOL];
  const marketPrice = liveMarketTicker?.price ?? latestCandle?.close ?? 0;
  const marketBaseline = previousCandle?.close ?? latestCandle?.open ?? 0;
  const marketChange = marketBaseline ? marketPrice - marketBaseline : 0;
  const marketChangePct = marketBaseline ? marketChange / marketBaseline : 0;
  const marketPath = buildLinePath(marketCandles.map((candle) => candle.close));
  const marketCards = MARKET_SYMBOLS.map((symbol) => {
    const candles = markets[symbol]?.candles ?? [];
    const latest = candles.at(-1);
    const previous = candles.at(-2);
    const ticker = liveTickers[symbol];
    const price = ticker?.price ?? latest?.close ?? 0;
    const baseline = previous?.close ?? latest?.open ?? 0;
    const change = baseline ? price - baseline : 0;
    return {
      symbol,
      price,
      change,
      changePct: baseline ? change / baseline : 0,
      isLive: Boolean(ticker),
    };
  });
  const routeOkCount = routeStatuses.filter((route) => route.ok).length;
  const activeModulesCount = Object.values(activeState.module_scores).filter((score) => score.active).length;
  const latestTrade = activeState.recent_trades[0];
  const latestSignal = activeState.recent_signals[0];
  const signalStats = activeState.signal_stats ?? EMPTY_STATE.signal_stats;
  const activeModuleEntry = Object.entries(activeState.module_scores).find(([, score]) => score.active);
  const appliedStrategyCode = normalizeStrategyCode(
    activeState.open_position ? latestTrade?.strategy_module : latestTrade?.strategy_module || activeModuleEntry?.[0],
  );
  const appliedStrategySource = latestTrade ? "Latest trade" : activeModuleEntry ? "Scorecard" : "Configured";
  const simulationStats = activeState.simulation_stats ?? EMPTY_STATE.simulation_stats;
  const routeFailureCount = routeStatuses.filter((route) => !route.ok).length;
  const marketRouteFailures = routeStatuses.filter((route) => route.name.startsWith("market:") && !route.ok).length;
  const evidenceTimes = [
    ...activeState.recent_signals.map((signal) => new Date(signal.generated_at_utc).getTime()),
    ...activeState.recent_trades.map((trade) => new Date(trade.timestamp).getTime()),
  ].filter(Number.isFinite);
  const evidenceDays = evidenceTimes.length > 1 ? Math.max(0, (Date.now() - Math.min(...evidenceTimes)) / 86400000) : 0;
  const forecastDirectionalCount = (signalStats.forecast_successes ?? 0) + (signalStats.forecast_failures ?? 0);
  const equityValues = (activeState.equity_history ?? []).map((point) => point.equity).filter(Number.isFinite);
  let peakEquity = equityValues[0] ?? activeState.starting_equity;
  const maxDrawdownPct = equityValues.reduce((maxDrawdown, equity) => {
    peakEquity = Math.max(peakEquity || equity, equity);
    if (!peakEquity) {
      return maxDrawdown;
    }
    return Math.max(maxDrawdown, (peakEquity - equity) / peakEquity);
  }, 0);
  const readinessGates = [
    { label: "Evidence Window", passed: evidenceDays >= 30, detail: `${evidenceDays.toFixed(1)} / 30 days` },
    { label: "Signal Sample", passed: signalStats.total_signals >= 100, detail: `${signalStats.total_signals} / 100 signals` },
    {
      label: "Forecast Edge",
      passed: forecastDirectionalCount >= 30 && (signalStats.forecast_success_rate ?? 0) >= 0.5,
      detail: forecastDirectionalCount > 0 ? `${formatPercent(signalStats.forecast_success_rate ?? 0)} across ${forecastDirectionalCount}` : "waiting for resolved forecasts",
    },
    { label: "Paper P&L", passed: simulationStats.net_pnl > 0 || equityChange > 0, detail: formatSignedCurrency(simulationStats.net_pnl || equityChange) },
    { label: "Drawdown Control", passed: maxDrawdownPct <= 0.1, detail: `${formatPercent(maxDrawdownPct)} max drawdown` },
    { label: "Data Health", passed: routeStatuses.length > 0 && routeFailureCount === 0 && !isFallback, detail: routeStatuses.length ? `${routeOkCount}/${routeStatuses.length} routes OK` : "checking routes" },
    { label: "Realism Costs", passed: simulationStats.total_closed_trades === 0 || simulationStats.total_friction > 0, detail: simulationStats.total_closed_trades > 0 ? `${formatCurrency(simulationStats.total_friction)} modeled friction` : "waiting for closed trades" },
  ];
  const readinessPassed = readinessGates.filter((gate) => gate.passed).length;
  const exportLinks = [
    { label: "Signals JSON", href: apiBase ? `${apiBase}/api/signals` : buildApiUrl("/api/bot/signals") },
    { label: "Trades JSON", href: apiBase ? `${apiBase}/api/trades` : buildApiUrl("/api/bot/trades") },
    { label: "State JSON", href: apiBase ? `${apiBase}/api/state` : buildApiUrl("/api/bot/state") },
    { label: "Health JSON", href: apiBase ? `${apiBase}/api/health` : buildApiUrl("/api/bot/health") },
  ];

  const moduleEntries = useMemo(
    () =>
      Object.entries(activeState.module_scores).sort(([, a], [, b]) => {
        if (a.active !== b.active) {
          return a.active ? -1 : 1;
        }
        return b.win_rate - a.win_rate;
      }),
    [activeState.module_scores],
  );

  const tradeSymbols = useMemo(
    () => Array.from(new Set([...MARKET_SYMBOLS, ...activeState.recent_trades.map((trade) => trade.symbol), ...activeState.recent_signals.map((signal) => signal.symbol)])).sort(),
    [activeState.recent_trades, activeState.recent_signals],
  );

  const strategyOptions = useMemo(
    () =>
      Array.from(
        new Set([
          ...CONFIGURED_STRATEGIES,
          ...moduleEntries.map(([moduleName]) => normalizeStrategyCode(moduleName)).filter(Boolean),
          ...activeState.recent_trades.map((trade) => normalizeStrategyCode(trade.strategy_module)).filter(Boolean),
          ...activeState.recent_signals.map((signal) => normalizeStrategyCode(signal.strategy_module)).filter(Boolean),
        ]),
      ),
    [activeState.recent_trades, activeState.recent_signals, moduleEntries],
  );

  const filteredTrades = useMemo(() => {
    const fromTime = dateFrom ? new Date(`${dateFrom}T00:00:00`).getTime() : null;
    const toTime = dateTo ? new Date(`${dateTo}T23:59:59.999`).getTime() : null;
    const allowedSymbols = new Set(MARKET_SYMBOLS);

    return activeState.recent_trades.filter((trade) => {
      // Always filter to allowed symbols
      if (!allowedSymbols.has(trade.symbol)) {
        return false;
      }
      if (tradeFilter === "wins" && trade.pnl < 0) {
        return false;
      }
      if (tradeFilter === "losses" && trade.pnl >= 0) {
        return false;
      }
      if (selectedStrategy !== "all" && normalizeStrategyCode(trade.strategy_module) !== selectedStrategy) {
        return false;
      }
      if (selectedSymbol !== "all" && trade.symbol !== selectedSymbol) {
        return false;
      }

      const tradeTime = new Date(trade.timestamp).getTime();
      if (Number.isNaN(tradeTime)) {
        return fromTime === null && toTime === null;
      }
      if (fromTime !== null && tradeTime < fromTime) {
        return false;
      }
      if (toTime !== null && tradeTime > toTime) {
        return false;
      }
      return true;
    });
  }, [activeState.recent_trades, dateFrom, dateTo, selectedStrategy, selectedSymbol, tradeFilter]);


  const filteredSignals = useMemo(() => {
    const fromTime = dateFrom ? new Date(`${dateFrom}T00:00:00`).getTime() : null;
    const toTime = dateTo ? new Date(`${dateTo}T23:59:59.999`).getTime() : null;
    const allowedSymbols = new Set(MARKET_SYMBOLS);

    return activeState.recent_signals.filter((signal) => {
      if (!allowedSymbols.has(signal.symbol)) {
        return false;
      }
      if (selectedStrategy !== "all" && normalizeStrategyCode(signal.strategy_module) !== selectedStrategy) {
        return false;
      }
      if (selectedSymbol !== "all" && signal.symbol !== selectedSymbol) {
        return false;
      }
      const signalTime = new Date(signal.generated_at_utc).getTime();
      if (Number.isNaN(signalTime)) {
        return fromTime === null && toTime === null;
      }
      if (fromTime !== null && signalTime < fromTime) {
        return false;
      }
      if (toTime !== null && signalTime > toTime) {
        return false;
      }
      return true;
    });
  }, [activeState.recent_signals, dateFrom, dateTo, selectedStrategy, selectedSymbol]);

  const filteredSignalSuccesses = filteredSignals.filter((signal) => signal.status === "SUCCEEDED").length;
  const filteredSignalFailures = filteredSignals.filter((signal) => signal.status === "FAILED").length;
  const selectedStrategyTradeCount =
    selectedStrategy === "all"
      ? activeState.recent_trades.length
      : activeState.recent_trades.filter((trade) => normalizeStrategyCode(trade.strategy_module) === selectedStrategy).length;
  const selectedSymbolTradeCount =
    selectedSymbol === "all"
      ? activeState.recent_trades.length
      : activeState.recent_trades.filter((trade) => trade.symbol === selectedSymbol).length;
  const filteredWins = filteredTrades.filter((trade) => trade.pnl >= 0).length;
  const filteredLosses = filteredTrades.filter((trade) => trade.pnl < 0).length;

  return (
    <main className="dashboard-shell">
      <aside className="side-rail" aria-label="Dashboard navigation">
        <div className="rail-brand">
          <div className="brand-mark">BR</div>
          <div>
            <p>Binance</p>
            <strong>Research Bot</strong>
          </div>
        </div>

        {profileSelector && (
          <div className="rail-profiles">
            {profileSelector}
          </div>
        )}

        <nav className="rail-nav view-nav" aria-label="Dashboard views">
          {DASHBOARD_VIEWS.map((view) => (
            <button
              key={view.id}
              type="button"
              className={activeView === view.id ? "active" : ""}
              onClick={() => setActiveView(view.id)}
            >
              <span>{view.short}</span>
              {view.label}
            </button>
          ))}
        </nav>

        <button className="rail-signal-card" type="button" onClick={() => setActiveView("signals")}>
          <span className="rail-signal-label">Latest Signal</span>
          {latestSignal ? (
            <>
              <strong>{latestSignal.symbol} {latestSignal.direction}</strong>
              <span>{latestSignal.status} / {strategyLabel(latestSignal.strategy_module)}</span>
              <small>Entry {formatCurrency(latestSignal.entry)} / SL {formatCurrency(latestSignal.stop)}</small>
              <small>TP {formatCurrency(latestSignal.target_1)} / {formatCurrency(latestSignal.target_2)}</small>
            </>
          ) : (
            <>
              <strong>No signal yet</strong>
              <span>Waiting for a valid setup</span>
            </>
          )}
        </button>
        <div className="rail-status">
          <span className={`route-dot ${priceStreamConnected ? "ok" : "fail"}`} />
          <div>
            <p>{activeState.environment}</p>
            <strong>{priceStreamConnected ? "Prices live" : realtimeConnected ? "Bot stream" : "HTTP fallback"}</strong>
          </div>
        </div>
      </aside>

      <section className="dashboard-stage">
        <header className="command-bar">
          <div>
            <p className="eyebrow">Trading Command Center</p>
            <h1>Bot Operations Dashboard</h1>
          </div>
          <div className="topbar-actions" aria-label="System status">
            {profileOptions.length > 0 && onProfileChange && (
              <div className="profile-dropdown-wrapper">
                <button
                  type="button"
                  className="profile-dropdown-trigger"
                  onClick={() => setProfileDropdownOpen(!profileDropdownOpen)}
                >
                  <svg className="profile-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                    <circle cx="12" cy="7" r="4" />
                  </svg>
                  <span className="profile-current-label">
                    {profileId === "all" ? "All Profiles" : profileId.charAt(0).toUpperCase() + profileId.slice(1)}
                  </span>
                  <span className="profile-dropdown-caret">▼</span>
                </button>
                {profileDropdownOpen && (
                  <div className="profile-dropdown-menu">
                    {profileOptions.map((opt) => (
                      <button
                        key={opt.id}
                        type="button"
                        className={`profile-dropdown-item ${profileId === opt.id ? "active" : ""} profile-${opt.id}`}
                        onClick={() => {
                          onProfileChange(opt.id);
                          setProfileDropdownOpen(false);
                        }}
                      >
                        <span className={`profile-dropdown-dot ${opt.status === "running" ? "online" : "offline"}`} />
                        <span className="profile-dropdown-label">{opt.label}</span>
                        {opt.equity !== undefined && (
                          <span className="profile-dropdown-equity">${opt.equity.toLocaleString()}</span>
                        )}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
            <button className="theme-toggle" type="button" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
              {theme === "dark" ? "Light" : "Dark"}
            </button>
            <span className={`status-pill status-${statusName}`}>{activeState.status}</span>
            <span className={`wire-pill ${realtimeConnected ? "online" : "offline"}`}>
              {realtimeConnected ? "Bot realtime" : "Bot offline"}
            </span>
            <span className={`wire-pill ${priceStreamConnected ? "online" : "offline"}`}>
              {priceStreamConnected ? "Prices live" : "Prices REST"}
            </span>
          </div>
        </header>

        {error ? <section className="alert alert-danger">API load failed: {error}</section> : null}
        {isFallback ? (
          <section className="alert alert-warning">
            Python backend is offline; showing local snapshots from out/ and data/. Run python run_api.py from the project root to restore live bot data.
          </section>
        ) : connectionIssue ? (
          <section className="alert alert-warning">{connectionIssue}</section>
        ) : priceStreamIssue ? (
          <section className="alert alert-warning">{priceStreamIssue}</section>
        ) : null}

        {activeView === "overview" ? (
          <>
            <section className="metric-grid" aria-label="Portfolio metrics">
              <article className={`metric-card hero-metric metric-${pnlTone}`}>
                <p className="metric-label">Account Equity</p>
                <p className="metric-value">{formatCurrency(activeState.equity)}</p>
                <p className="metric-foot">
                  {formatSignedCurrency(equityChange)} {formatPercent(equityChangePct)} from start
                </p>
              </article>
              <MetricCard
                label="Daily PnL"
                value={formatSignedCurrency(activeState.daily_pnl)}
                foot={`${activeState.trading_stats.winning_trades} wins / ${activeState.trading_stats.losing_trades} losses`}
                tone={dailyTone}
              />
              <MetricCard
                label="Win Rate"
                value={formatPercent(activeState.trading_stats.win_rate)}
                foot={`${activeState.trading_stats.total_trades} closed trades`}
                tone={activeState.trading_stats.win_rate >= 0.5 ? "positive" : "neutral"}
              />
              <MetricCard
                label="Average R"
                value={`${activeState.trading_stats.avg_r_multiple.toFixed(2)}R`}
                foot={`${moduleEntries.length || CONFIGURED_STRATEGIES.length} strategy modules tracked`}
                tone={activeState.trading_stats.avg_r_multiple >= 0 ? "positive" : "negative"}
              />
              <MetricCard
                label="Market"
                value={marketPrice ? formatCurrency(marketPrice) : "-"}
                foot={`${MARKET_SYMBOL} ${formatPercent(marketChangePct)} ${liveMarketTicker ? "live trade" : "latest candle"}`}
                tone={marketChange >= 0 ? "positive" : "negative"}
              />
            </section>

            <section className="overview-grid">
              <article className="panel chart-panel compact-chart-panel">
                <div className="panel-header">
                  <div>
                    <p className="panel-kicker">Performance</p>
                    <h2>Equity Curve</h2>
                  </div>
                  <span className={`delta-chip ${pnlTone}`}>{formatPercent(equityChangePct)}</span>
                </div>
                <div className="equity-chart" aria-label="Equity chart">
                  {equityPath ? (
                    <svg viewBox="0 0 100 42" preserveAspectRatio="none" role="img" aria-label="Equity history line">
                      <path className="grid-line" d="M0 8 H100 M0 21 H100 M0 34 H100" />
                      <path className="equity-line-shadow" d={equityPath} />
                      <path className="equity-line" d={equityPath} />
                    </svg>
                  ) : (
                    <p className="empty-state">Waiting for equity history.</p>
                  )}
                </div>
                <div className="chart-footer">
                  <span>Start {formatCurrency(activeState.starting_equity)}</span>
                  <span>Current {formatCurrency(activeState.equity)}</span>
                </div>
              </article>

              <article className="panel monitor-panel strategy-panel">
                <div className="panel-header">
                  <div>
                    <p className="panel-kicker">Applied Strategy</p>
                    <h2>{strategyLabel(appliedStrategyCode)}</h2>
                  </div>
                  <span>{appliedStrategySource}</span>
                </div>
                <div className="strategy-card">
                  <span>{appliedStrategyCode || "--"}</span>
                  <strong>{strategyDetail(appliedStrategyCode)}</strong>
                  <em>{latestTrade?.regime ?? "No regime recorded"}</em>
                </div>
                <div className="monitor-stack compact-monitor">
                  <div>
                    <span>Status</span>
                    <strong>{activeState.status}</strong>
                  </div>
                  <div>
                    <span>Backend</span>
                    <strong>{health?.ok ? "Online" : isFallback ? "Fallback" : "Checking"}</strong>
                  </div>
                  <div>
                    <span>Refresh</span>
                    <strong>{Math.round((activeState.update_interval_ms ?? FALLBACK_REFRESH_MS) / 1000)}s</strong>
                  </div>
                </div>
              </article>

              <article className="panel risk-panel">
                <div className="panel-header">
                  <div>
                    <p className="panel-kicker">Risk Controls</p>
                    <h2>Exposure</h2>
                  </div>
                  <span>{activeState.open_position ? "exposed" : "flat"}</span>
                </div>
                <div className="guardrail-list">
                  <div>
                    <span>Position State</span>
                    <strong>{activeState.open_position ? "Open" : "Flat"}</strong>
                  </div>
                  <div>
                    <span>Active Modules</span>
                    <strong>{activeModulesCount}/{moduleEntries.length || CONFIGURED_STRATEGIES.length}</strong>
                  </div>
                  <div>
                    <span>Last Trade</span>
                    <strong>{latestTrade ? formatCompactDate(latestTrade.timestamp) : "-"}</strong>
                  </div>
                  <div>
                    <span>Latest Module</span>
                    <strong>{latestTrade?.strategy_module ?? "-"}</strong>
                  </div>
                </div>
                {activeState.open_position ? (
                  <div className="position-ticket compact-ticket">
                    <div>
                      <span>Symbol</span>
                      <strong>{activeState.open_position.symbol}</strong>
                    </div>
                    <div>
                      <span>Direction</span>
                      <strong>{activeState.open_position.direction}</strong>
                    </div>
                    <div>
                      <span>Entry</span>
                      <strong>{formatCurrency(activeState.open_position.entry)}</strong>
                    </div>
                  </div>
                ) : (
                  <div className="flat-state">
                    <strong>No open position</strong>
                    <span>Capital is waiting for the next valid signal.</span>
                  </div>
                )}
              </article>
            </section>

            <section className="market-strategy-grid">
              <article className="panel market-panel">
                <div className="panel-header">
                  <div>
                    <p className="panel-kicker">Market Tape</p>
                    <h2>{MARKET_SYMBOL}</h2>
                  </div>
                  <span className={marketChange >= 0 ? "positive" : "negative"}>{formatPercent(marketChangePct)}</span>
                </div>
                <div className="market-price-row">
                  <strong>{marketPrice ? formatCurrency(marketPrice) : "-"}</strong>
                  <span>{marketPrice ? `${formatSignedCurrency(marketChange)} ${liveMarketTicker ? "live move" : "candle move"}` : "waiting for market data"}</span>
                </div>
                <div className="mini-chart" aria-label="Market close chart">
                  {marketPath ? (
                    <svg viewBox="0 0 100 42" preserveAspectRatio="none" role="img" aria-label="Market close line">
                      <path className="grid-line" d="M0 8 H100 M0 21 H100 M0 34 H100" />
                      <path className="market-line" d={marketPath} />
                    </svg>
                  ) : (
                    <p className="empty-state">No market candles loaded.</p>
                  )}
                </div>
                <div className="market-stats">
                  <div>
                    <span>High</span>
                    <strong>{latestCandle ? formatCurrency(latestCandle.high) : "-"}</strong>
                  </div>
                  <div>
                    <span>Low</span>
                    <strong>{latestCandle ? formatCurrency(latestCandle.low) : "-"}</strong>
                  </div>
                  <div>
                    <span>Volume</span>
                    <strong>{latestCandle ? formatNumber(latestCandle.volume, 0) : "-"}</strong>
                  </div>
                </div>
              </article>

              <article className="panel modules-panel">
                <div className="panel-header">
                  <div>
                    <p className="panel-kicker">Signal Stack</p>
                    <h2>Tool Modules</h2>
                  </div>
                  <span>{activeModulesCount}/{moduleEntries.length || CONFIGURED_STRATEGIES.length} active</span>
                </div>
                <div className="module-list">
                  {(moduleEntries.length > 0
                    ? moduleEntries.slice(0, 6)
                    : CONFIGURED_STRATEGIES.map((moduleName) => [
                        moduleName,
                        { win_rate: 0, total_trades: 0, active: true },
                      ] as [string, ModuleScore])
                  ).map(([moduleName, score]) => (
                    <button key={moduleName} type="button" className={`module-row module-row-button ${selectedStrategy === normalizeStrategyCode(moduleName) ? "selected" : ""}`} onClick={() => { setSelectedStrategy(normalizeStrategyCode(moduleName)); setActiveView("strategies"); }}>
                      <div>
                        <strong>{strategyLabel(moduleName)}</strong>
                        <span>{moduleName} / {score.total_trades} trades</span>
                      </div>
                      <div className="module-meter" aria-label={`${moduleName} win rate`}>
                        <span style={{ width: `${Math.min(100, Math.max(0, score.win_rate * 100))}%` }} />
                      </div>
                      <div className="module-rate">
                        <strong>{moduleEntries.length > 0 ? formatPercent(score.win_rate) : "configured"}</strong>
                        <span>{score.active ? "active" : "paused"}</span>
                      </div>
                    </button>
                  ))}
                </div>
              </article>
            </section>
          </>
        ) : null}

        {activeView === "markets" ? (
          <section className="market-page-grid">
            <article className="panel market-panel market-focus-panel">
              <div className="panel-header">
                <div>
                  <p className="panel-kicker">Market Tape</p>
                  <h2>{MARKET_SYMBOL}</h2>
                </div>
                <span className={marketChange >= 0 ? "positive" : "negative"}>{formatPercent(marketChangePct)}</span>
              </div>
              <div className="market-price-row">
                <strong>{marketPrice ? formatCurrency(marketPrice) : "-"}</strong>
                <span>{marketPrice ? `${formatSignedCurrency(marketChange)} ${liveMarketTicker ? "live move" : "candle move"}` : "waiting for market data"}</span>
              </div>
              <div className="mini-chart market-focus-chart" aria-label="Market close chart">
                {marketPath ? (
                  <svg viewBox="0 0 100 42" preserveAspectRatio="none" role="img" aria-label="Market close line">
                    <path className="grid-line" d="M0 8 H100 M0 21 H100 M0 34 H100" />
                    <path className="market-line" d={marketPath} />
                  </svg>
                ) : (
                  <p className="empty-state">No market candles loaded.</p>
                )}
              </div>
            </article>

            <article className="panel">
              <div className="panel-header">
                <div>
                  <p className="panel-kicker">Live Pairs</p>
                  <h2>Watchlist</h2>
                </div>
                <span>{priceStreamConnected ? "streaming" : "REST"}</span>
              </div>
              <div className="signal-details-wrap market-watchlist-wrap">
                <table className="signal-details-table market-watchlist-table">
                  <thead>
                    <tr>
                      <th>Pair</th>
                      <th>Price</th>
                      <th>Move</th>
                      <th>Source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {marketCards.map((item) => (
                      <tr key={item.symbol}>
                        <td><strong>{item.symbol}</strong></td>
                        <td>{item.price ? formatCurrency(item.price) : "-"}</td>
                        <td className={item.change >= 0 ? "positive" : "negative"}>{formatPercent(item.changePct)}</td>
                        <td>{item.isLive ? "live trade" : "REST candle"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </article>
          </section>
        ) : null}

        {activeView === "signals" ? (
          <section className="signals-page-grid">
            <header className="signals-header">
              <div>
                <p className="panel-kicker">Forecast Journal</p>
                <h2>Trading Signals</h2>
              </div>
              <span>{filteredSignals.length} shown / {activeState.recent_signals.length} total</span>
            </header>

            <section className="signals-metrics-strip">
              <ReportMetricCard
                label="Signals"
                value={signalStats.total_signals.toString()}
                tone="neutral"
                foot={`${signalStats.opened_signals} opened / ${signalStats.blocked_signals} blocked`}
              />
              <ReportMetricCard
                label="Forecast Rate"
                value={(signalStats.forecast_successes ?? 0) + (signalStats.forecast_failures ?? 0) > 0 ? formatPercent(signalStats.forecast_success_rate ?? 0) : "N/A"}
                tone={(signalStats.forecast_success_rate ?? 0) >= 0.5 ? "positive" : "neutral"}
                foot={`${signalStats.forecast_successes ?? 0} TP hits / ${signalStats.forecast_failures ?? 0} stop hits`}
              />
              <ReportMetricCard
                label="TP / Stop"
                value={`${signalStats.tp1_hits ?? 0} / ${signalStats.stop_hits ?? 0}`}
                tone={(signalStats.tp1_hits ?? 0) + (signalStats.tp2_hits ?? 0) >= (signalStats.stop_hits ?? 0) ? "positive" : "negative"}
                foot={`TP2 ${signalStats.tp2_hits ?? 0} / expired ${signalStats.expired_no_hit ?? 0}`}
              />
              <ReportMetricCard
                label="Filtered Result"
                value={`${filteredSignalSuccesses} / ${filteredSignalFailures}`}
                tone={filteredSignalSuccesses >= filteredSignalFailures ? "positive" : "negative"}
                foot="Succeeded / failed in current filter"
              />
            </section>

            <article className="panel signals-panel">
              <div className="panel-header signals-panel-header">
                <div>
                  <p className="panel-kicker">Details</p>
                  <h2>Signal Details</h2>
                </div>
                <span>{latestSignal ? `${latestSignal.symbol} ${latestSignal.status}` : "waiting"}</span>
              </div>

              <div className="trade-filter-bar signal-filter-bar" aria-label="Signal filters">
                <label>
                  <span>Strategy</span>
                  <select value={selectedStrategy} onChange={(event) => setSelectedStrategy(event.target.value)}>
                    <option value="all">All strategies</option>
                    {strategyOptions.map((strategy) => (
                      <option key={strategy} value={strategy}>{strategy} - {strategyLabel(strategy)}</option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Pair</span>
                  <select value={selectedSymbol} onChange={(event) => setSelectedSymbol(event.target.value)}>
                    <option value="all">All pairs</option>
                    {tradeSymbols.map((symbol) => (
                      <option key={symbol} value={symbol}>{symbol}</option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>From</span>
                  <input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
                </label>
                <label>
                  <span>To</span>
                  <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
                </label>
                <button
                  className="text-action reset-action"
                  type="button"
                  onClick={() => {
                    setSelectedStrategy("all");
                    setSelectedSymbol("all");
                    setDateFrom("");
                    setDateTo("");
                  }}
                >
                  Reset
                </button>
              </div>

              {isLoading ? (
                <p className="empty-state">Loading signal journal...</p>
              ) : filteredSignals.length > 0 ? (
                <div className="signal-details-wrap">
                  <table className="signal-details-table">
                    <thead>
                      <tr>
                        <th>Time</th>
                        <th>Pair</th>
                        <th>Market</th>
                        <th>Side</th>
                        <th>Entry</th>
                        <th>Stop</th>
                        <th>TP1</th>
                        <th>TP2</th>
                        <th>R:R</th>
                        <th>Quality</th>
                        <th>Strategy</th>
                        <th>Status</th>
                        <th>Forecast</th>
                        <th>Trade Result</th>
                        <th>Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredSignals.map((signal) => (
                        <tr key={signal.id} className={`signal-row status-${signalStatusClass(signal.status)}`}>
                          <td>{formatDate(signal.generated_at_utc)}</td>
                          <td><strong>{signal.symbol}</strong></td>
                          <td>{signal.market}</td>
                          <td className={signal.direction === "LONG" ? "positive" : "negative"}>{signal.direction}</td>
                          <td>{formatCurrency(signal.entry)}</td>
                          <td>{formatCurrency(signal.stop)}</td>
                          <td>{formatCurrency(signal.target_1)}</td>
                          <td>{formatCurrency(signal.target_2)}</td>
                          <td>{signal.risk_multiple.toFixed(2)}R</td>
                          <td>{signal.quality_label} / {signal.quality_score.toFixed(1)}</td>
                          <td>{normalizeStrategyCode(signal.strategy_module)}</td>
                          <td><span className={`signal-status-pill ${signalTone(signal.status)}`}>{signal.status}</span></td>
                          <td>{formatForecastResult(signal)}</td>
                          <td>{formatSignalResult(signal)}</td>
                          <td className="signal-reason-cell">{signal.forecast_reason || signal.status_reason || signal.strategy_reason || signal.rationale}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="empty-state">No signals match the selected strategy, pair, and date filters.</p>
              )}
            </article>
          </section>
        ) : null}
        {activeView === "strategies" ? (
          <section className="strategy-trades-grid">
            <article className="panel monitor-panel strategy-panel">
              <div className="panel-header">
                <div>
                  <p className="panel-kicker">Applied Strategy</p>
                  <h2>{selectedStrategy === "all" ? strategyLabel(appliedStrategyCode) : strategyLabel(selectedStrategy)}</h2>
                </div>
                <span>{selectedStrategy === "all" ? appliedStrategySource : `${selectedStrategyTradeCount} trades`}</span>
              </div>
              <div className="strategy-card">
                <span>{selectedStrategy === "all" ? appliedStrategyCode || "--" : selectedStrategy}</span>
                <strong>{strategyDetail(selectedStrategy === "all" ? appliedStrategyCode : selectedStrategy)}</strong>
                <em>{selectedSymbol === "all" ? "All symbols" : selectedSymbol}</em>
              </div>
              <div className="trade-summary-grid">
                <div>
                  <span>Filtered Trades</span>
                  <strong>{filteredTrades.length}</strong>
                </div>
                <div>
                  <span>Wins</span>
                  <strong className="positive">{filteredWins}</strong>
                </div>
                <div>
                  <span>Losses</span>
                  <strong className="negative">{filteredLosses}</strong>
                </div>
                <div>
                  <span>Symbol Count</span>
                  <strong>{selectedSymbolTradeCount}</strong>
                </div>
              </div>
            </article>

            <article className="panel modules-panel">
              <div className="panel-header">
                <div>
                  <p className="panel-kicker">Signal Stack</p>
                  <h2>Strategy Modules</h2>
                </div>
                <button className="text-action" type="button" onClick={() => setSelectedStrategy("all")}>All strategies</button>
              </div>
              <div className="module-list">
                {(moduleEntries.length > 0
                  ? moduleEntries
                  : CONFIGURED_STRATEGIES.map((moduleName) => [
                      moduleName,
                      { win_rate: 0, total_trades: 0, active: true },
                    ] as [string, ModuleScore])
                ).map(([moduleName, score]) => {
                  const code = normalizeStrategyCode(moduleName);
                  const moduleTradeCount = activeState.recent_trades.filter((trade) => normalizeStrategyCode(trade.strategy_module) === code).length;
                  return (
                    <button
                      key={moduleName}
                      type="button"
                      className={`module-row module-row-button ${selectedStrategy === code ? "selected" : ""}`}
                      onClick={() => setSelectedStrategy(code)}
                    >
                      <div>
                        <strong>{strategyLabel(moduleName)}</strong>
                        <span>{code} / {moduleTradeCount || score.total_trades} trades</span>
                      </div>
                      <div className="module-meter" aria-label={`${moduleName} win rate`}>
                        <span style={{ width: `${Math.min(100, Math.max(0, score.win_rate * 100))}%` }} />
                      </div>
                      <div className="module-rate">
                        <strong>{moduleEntries.length > 0 ? formatPercent(score.win_rate) : "configured"}</strong>
                        <span>{score.active ? "active" : "paused"}</span>
                      </div>
                    </button>
                  );
                })}
              </div>
            </article>

            <article className="panel trades-panel strategy-trades-panel">
              <div className="panel-header trades-header">
                <div>
                  <p className="panel-kicker">Execution Ledger</p>
                  <h2>Trades by Strategy and Pair</h2>
                </div>
                <span>{filteredTrades.length} shown / {activeState.recent_trades.length} total</span>
              </div>

              <div className="trade-filter-bar" aria-label="Trade filters">
                <label>
                  <span>Strategy</span>
                  <select value={selectedStrategy} onChange={(event) => setSelectedStrategy(event.target.value)}>
                    <option value="all">All strategies</option>
                    {strategyOptions.map((strategy) => (
                      <option key={strategy} value={strategy}>{strategy} - {strategyLabel(strategy)}</option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Pair</span>
                  <select value={selectedSymbol} onChange={(event) => setSelectedSymbol(event.target.value)}>
                    <option value="all">All pairs</option>
                    {tradeSymbols.map((symbol) => (
                      <option key={symbol} value={symbol}>{symbol}</option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>From</span>
                  <input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
                </label>
                <label>
                  <span>To</span>
                  <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
                </label>
                <div className="segmented-control" aria-label="Trade result filter">
                  {(["all", "wins", "losses"] as TradeFilter[]).map((filter) => (
                    <button
                      key={filter}
                      type="button"
                      className={tradeFilter === filter ? "active" : ""}
                      onClick={() => setTradeFilter(filter)}
                    >
                      {filter}
                    </button>
                  ))}
                </div>
                <button
                  className="text-action reset-action"
                  type="button"
                  onClick={() => {
                    setSelectedStrategy("all");
                    setSelectedSymbol("all");
                    setDateFrom("");
                    setDateTo("");
                    setTradeFilter("all");
                  }}
                >
                  Reset
                </button>
              </div>

              {isLoading ? (
                <p className="empty-state">Loading recent trades...</p>
              ) : filteredTrades.length > 0 ? (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Time</th>
                        <th>Profile</th>
                        <th>Symbol</th>
                        <th>Side</th>
                        <th>Entry</th>
                        <th>Exit</th>
                        <th>PnL</th>
                        <th>R</th>
                        <th>Strategy</th>
                        <th>Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredTrades.slice(0, 30).map((trade, index) => (
                        <tr key={`${trade.symbol}-${trade.timestamp}-${index}`}>
                          <td>{formatDate(trade.timestamp)}</td>
                          <td>
                            <span className={`profile-badge profile-${trade.profile_id || profileId}`}>
                              {(trade.profile_id || profileId || "default").charAt(0).toUpperCase()}
                            </span>
                          </td>
                          <td className="symbol-cell">{trade.symbol}</td>
                          <td>{trade.direction}</td>
                          <td>{formatCurrency(trade.entry)}</td>
                          <td>{formatCurrency(trade.exit)}</td>
                          <td className={trade.pnl >= 0 ? "positive" : "negative"}>
                            {formatSignedCurrency(trade.pnl)}
                          </td>
                          <td>{(trade.r_multiple ?? 0).toFixed(2)}R</td>
                          <td>{strategyLabel(trade.strategy_module)}</td>
                          <td>{trade.close_reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="empty-state">No trades match the selected strategy, pair, result, and date filters.</p>
              )}
            </article>
          </section>
        ) : null}

        {activeView === "reports" ? (
          <section className="reports-page-grid">
            <header className="reports-header">
              <div>
                <p className="panel-kicker">Performance Analysis</p>
                <h2>Trading Reports</h2>
              </div>
              <div className="report-period-tabs">
                <button
                  type="button"
                  className={`period-tab ${reportPeriod === "daily" ? "active" : ""}`}
                  onClick={() => setReportPeriod("daily")}
                >
                  Daily
                </button>
                <button
                  type="button"
                  className={`period-tab ${reportPeriod === "weekly" ? "active" : ""}`}
                  onClick={() => setReportPeriod("weekly")}
                >
                  Weekly
                </button>
                <button
                  type="button"
                  className={`period-tab ${reportPeriod === "monthly" ? "active" : ""}`}
                  onClick={() => setReportPeriod("monthly")}
                >
                  Monthly
                </button>
              </div>
            </header>

            <section className="reports-metrics-strip">
              <ReportMetricCard
                label="Period P&L"
                value={formatSignedCurrency(
                  (() => {
                    const tradesToUse = profileId === "all" ? allProfilesTrades : filteredTrades;
                    const now = new Date();
                    const periodStart = reportPeriod === "daily"
                      ? new Date(now.getFullYear(), now.getMonth(), now.getDate())
                      : reportPeriod === "weekly"
                      ? new Date(now.setDate(now.getDate() - now.getDay()))
                      : new Date(now.getFullYear(), now.getMonth(), 1);
                    return tradesToUse
                      .filter((t) => new Date(t.timestamp) >= periodStart)
                      .reduce((sum, t) => sum + t.pnl, 0);
                  })()
                )}
                tone={(() => {
                  const tradesToUse = profileId === "all" ? allProfilesTrades : filteredTrades;
                  const now = new Date();
                  const periodStart = reportPeriod === "daily"
                    ? new Date(now.getFullYear(), now.getMonth(), now.getDate())
                    : reportPeriod === "weekly"
                    ? new Date(now.setDate(now.getDate() - now.getDay()))
                    : new Date(now.getFullYear(), now.getMonth(), 1);
                  const periodPnl = tradesToUse
                    .filter((t) => new Date(t.timestamp) >= periodStart)
                    .reduce((sum, t) => sum + t.pnl, 0);
                  return periodPnl >= 0 ? "positive" : "negative";
                })()}
                foot={`${reportPeriod.charAt(0).toUpperCase() + reportPeriod.slice(1)} performance`}
              />
              <ReportMetricCard
                label="Period Trades"
                value={(() => {
                  const tradesToUse = profileId === "all" ? allProfilesTrades : filteredTrades;
                  const now = new Date();
                  const periodStart = reportPeriod === "daily"
                    ? new Date(now.getFullYear(), now.getMonth(), now.getDate())
                    : reportPeriod === "weekly"
                    ? new Date(now.setDate(now.getDate() - now.getDay()))
                    : new Date(now.getFullYear(), now.getMonth(), 1);
                  return tradesToUse.filter((t) => new Date(t.timestamp) >= periodStart).length.toString();
                })()}
                tone="neutral"
                foot="Total executions"
              />
              <ReportMetricCard
                label="Win Rate"
                value={(() => {
                  const tradesToUse = profileId === "all" ? allProfilesTrades : filteredTrades;
                  const now = new Date();
                  const periodStart = reportPeriod === "daily"
                    ? new Date(now.getFullYear(), now.getMonth(), now.getDate())
                    : reportPeriod === "weekly"
                    ? new Date(now.setDate(now.getDate() - now.getDay()))
                    : new Date(now.getFullYear(), now.getMonth(), 1);
                  const periodTrades = tradesToUse.filter((t) => new Date(t.timestamp) >= periodStart);
                  if (periodTrades.length === 0) return "N/A";
                  const wins = periodTrades.filter((t) => t.pnl > 0).length;
                  return `${((wins / periodTrades.length) * 100).toFixed(1)}%`;
                })()}
                tone="neutral"
                foot="Period win percentage"
              />
              <ReportMetricCard
                label="Avg R-Multiple"
                value={(() => {
                  const tradesToUse = profileId === "all" ? allProfilesTrades : filteredTrades;
                  const now = new Date();
                  const periodStart = reportPeriod === "daily"
                    ? new Date(now.getFullYear(), now.getMonth(), now.getDate())
                    : reportPeriod === "weekly"
                    ? new Date(now.setDate(now.getDate() - now.getDay()))
                    : new Date(now.getFullYear(), now.getMonth(), 1);
                  const periodTrades = tradesToUse.filter((t) => new Date(t.timestamp) >= periodStart);
                  if (periodTrades.length === 0) return "N/A";
                  const avgR = periodTrades.reduce((sum, t) => sum + (t.r_multiple ?? 0), 0) / periodTrades.length;
                  return `${avgR.toFixed(2)}R`;
                })()}
                tone="neutral"
                foot="Risk-adjusted return"
              />
            </section>

            <article className="panel signal-report-panel">
              <div className="panel-header">
                <div>
                  <p className="panel-kicker">Signal Forecasts</p>
                  <h2>Signal Performance by Strategy</h2>
                </div>
                <span>{signalStats.total_signals} total</span>
              </div>
              {signalStats.by_module && Object.keys(signalStats.by_module).length > 0 ? (
                <div className="signal-details-wrap report-signal-table-wrap">
                  <table className="signal-details-table report-signal-table">
                    <thead>
                      <tr>
                        <th>Strategy</th>
                        <th>Signals</th>
                        <th>Opened</th>
                        <th>Blocked</th>
                        <th>TP Hits</th>
                        <th>Stops</th>
                        <th>Forecast Rate</th>
                        <th>Expired</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(signalStats.by_module).map(([moduleName, stats]) => (
                        <tr key={moduleName}>
                          <td><strong>{moduleName}</strong></td>
                          <td>{stats.total_signals}</td>
                          <td>{stats.opened_signals}</td>
                          <td>{stats.blocked_signals}</td>
                          <td>{(stats.tp1_hits ?? 0) + (stats.tp2_hits ?? 0)}</td>
                          <td>{stats.stop_hits ?? 0}</td>
                          <td>{(stats.forecast_successes ?? 0) + (stats.forecast_failures ?? 0) > 0 ? formatPercent(stats.forecast_success_rate ?? 0) : "N/A"}</td>
                          <td>{stats.expired_no_hit ?? 0}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="empty-state">No signal forecast data is available for this profile yet.</p>
              )}
            </article>
            <article className="panel strategy-performance-panel">
              <div className="panel-header">
                <div>
                  <p className="panel-kicker">Strategy Analysis</p>
                  <h2>Performance by Strategy</h2>
                </div>
                <span className={`profile-badge profile-${profileId}`}>
                  {profileId === "all" ? "All" : profileId.charAt(0).toUpperCase() + profileId.slice(1)}
                </span>
              </div>
              <div className="strategy-performance-grid">
                {CONFIGURED_STRATEGIES.map((stratCode) => {
                  const tradesToUse = profileId === "all" ? allProfilesTrades : filteredTrades;
                  const now = new Date();
                  const periodStart = reportPeriod === "daily"
                    ? new Date(now.getFullYear(), now.getMonth(), now.getDate())
                    : reportPeriod === "weekly"
                    ? new Date(now.setDate(now.getDate() - now.getDay()))
                    : new Date(now.getFullYear(), now.getMonth(), 1);
                  const stratTrades = tradesToUse.filter(
                    (t) =>
                      normalizeStrategyCode(t.strategy_module) === stratCode &&
                      new Date(t.timestamp) >= periodStart
                  );
                  const wins = stratTrades.filter((t) => t.pnl > 0).length;
                  const totalPnl = stratTrades.reduce((sum, t) => sum + t.pnl, 0);
                  const avgR = stratTrades.length > 0
                    ? stratTrades.reduce((sum, t) => sum + (t.r_multiple ?? 0), 0) / stratTrades.length
                    : 0;
                  const winRate = stratTrades.length > 0 ? (wins / stratTrades.length) * 100 : 0;

                  return (
                    <div key={stratCode} className="strategy-perf-card">
                      <div className="strategy-perf-header">
                        <strong>{stratCode}</strong>
                        <span className="strategy-perf-name">{STRATEGY_LABELS[stratCode] || stratCode}</span>
                      </div>
                      <div className="strategy-perf-stats">
                        <div className="strategy-stat">
                          <span>Trades</span>
                          <strong>{stratTrades.length}</strong>
                        </div>
                        <div className="strategy-stat">
                          <span>Win Rate</span>
                          <strong>{stratTrades.length > 0 ? `${winRate.toFixed(1)}%` : "N/A"}</strong>
                        </div>
                        <div className="strategy-stat">
                          <span>P&L</span>
                          <strong className={totalPnl >= 0 ? "positive" : "negative"}>
                            {formatSignedCurrency(totalPnl)}
                          </strong>
                        </div>
                        <div className="strategy-stat">
                          <span>Avg R</span>
                          <strong>{stratTrades.length > 0 ? `${avgR.toFixed(2)}R` : "N/A"}</strong>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </article>

            {profileId === "all" && (
              <article className="panel profile-comparison-panel">
                <div className="panel-header">
                  <div>
                    <p className="panel-kicker">Profile Comparison</p>
                    <h2>Conservative vs Aggressive</h2>
                  </div>
                </div>
                <div className="profile-comparison-grid">
                  {["conservative", "aggressive"].map((pid) => {
                    const now = new Date();
                    const periodStart = reportPeriod === "daily"
                      ? new Date(now.getFullYear(), now.getMonth(), now.getDate())
                      : reportPeriod === "weekly"
                      ? new Date(now.setDate(now.getDate() - now.getDay()))
                      : new Date(now.getFullYear(), now.getMonth(), 1);
                    const profileTrades = allProfilesTrades.filter(
                      (t) => t.profile_id === pid && new Date(t.timestamp) >= periodStart
                    );
                    const totalPnl = profileTrades.reduce((sum, t) => sum + t.pnl, 0);
                    const wins = profileTrades.filter((t) => t.pnl > 0).length;
                    const winRate = profileTrades.length > 0 ? (wins / profileTrades.length) * 100 : 0;

                    return (
                      <div key={pid} className={`profile-comparison-card profile-${pid}`}>
                        <div className="profile-comparison-header">
                          <span className={`profile-comparison-dot profile-${pid}`} />
                          <strong>{pid.charAt(0).toUpperCase() + pid.slice(1)}</strong>
                        </div>
                        <div className="profile-comparison-stats">
                          <div className="comparison-stat">
                            <span>Trades</span>
                            <strong>{profileTrades.length}</strong>
                          </div>
                          <div className="comparison-stat">
                            <span>Win Rate</span>
                            <strong>{profileTrades.length > 0 ? `${winRate.toFixed(1)}%` : "N/A"}</strong>
                          </div>
                          <div className="comparison-stat highlight">
                            <span>P&L</span>
                            <strong className={totalPnl >= 0 ? "positive" : "negative"}>
                              {formatSignedCurrency(totalPnl)}
                            </strong>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </article>
            )}

            <article className="panel recent-trades-panel">
              <div className="panel-header">
                <div>
                  <p className="panel-kicker">{reportPeriod.charAt(0).toUpperCase() + reportPeriod.slice(1)} Activity</p>
                  <h2>Recent Trades</h2>
                </div>
              </div>
              <div className="trade-table-wrapper">
                {(() => {
                  const tradesToUse = profileId === "all" ? allProfilesTrades : filteredTrades;
                  const now = new Date();
                  const periodStart = reportPeriod === "daily"
                    ? new Date(now.getFullYear(), now.getMonth(), now.getDate())
                    : reportPeriod === "weekly"
                    ? new Date(now.setDate(now.getDate() - now.getDay()))
                    : new Date(now.getFullYear(), now.getMonth(), 1);
                  const periodTrades = tradesToUse.filter((t) => new Date(t.timestamp) >= periodStart);

                  return periodTrades.length > 0 ? (
                    <table className="trade-table">
                      <thead>
                        <tr>
                          <th>Time</th>
                          <th>Profile</th>
                          <th>Symbol</th>
                          <th>Side</th>
                          <th>P&L</th>
                          <th>R</th>
                          <th>Strategy</th>
                        </tr>
                      </thead>
                      <tbody>
                        {periodTrades.slice(0, 20).map((trade, index) => (
                          <tr key={`${trade.symbol}-${trade.timestamp}-${index}`}>
                            <td>{formatDate(trade.timestamp)}</td>
                            <td>
                              <span className={`profile-badge profile-${trade.profile_id || profileId}`}>
                                {(trade.profile_id || profileId || "default").charAt(0).toUpperCase()}
                              </span>
                            </td>
                            <td>{trade.symbol}</td>
                            <td>{trade.direction}</td>
                            <td className={trade.pnl >= 0 ? "positive" : "negative"}>
                              {formatSignedCurrency(trade.pnl)}
                            </td>
                            <td>{(trade.r_multiple ?? 0).toFixed(2)}R</td>
                            <td>{strategyLabel(trade.strategy_module)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <p className="empty-state">No trades in this {reportPeriod} period.</p>
                  );
                })()}
              </div>
            </article>
          </section>
        ) : null}

        {activeView === "readiness" ? (
          <section className="readiness-page-grid">
            <section className="runtime-strip" aria-label="Readiness summary">
              <div>
                <span>Readiness Gates</span>
                <strong>{readinessPassed}/{readinessGates.length} passed</strong>
              </div>
              <div>
                <span>Evidence Window</span>
                <strong>{evidenceDays.toFixed(1)} days</strong>
              </div>
              <div>
                <span>Signals</span>
                <strong>{signalStats.total_signals}</strong>
              </div>
              <div>
                <span>Forecast Rate</span>
                <strong>{forecastDirectionalCount > 0 ? formatPercent(signalStats.forecast_success_rate ?? 0) : "N/A"}</strong>
              </div>
              <div>
                <span>Max Drawdown</span>
                <strong>{formatPercent(maxDrawdownPct)}</strong>
              </div>
            </section>

            <article className="panel readiness-panel">
              <div className="panel-header">
                <div>
                  <p className="panel-kicker">Live Readiness</p>
                  <h2>Test Gates</h2>
                </div>
                <span>{readinessPassed === readinessGates.length ? "ready" : "not ready"}</span>
              </div>
              <div className="route-list readiness-list">
                {readinessGates.map((gate) => (
                  <div key={gate.label} className="route-row">
                    <span className={gate.passed ? "route-dot ok" : "route-dot fail"} />
                    <strong>{gate.label}</strong>
                    <span>{gate.passed ? "pass" : "wait"}</span>
                    <em>{gate.detail}</em>
                  </div>
                ))}
              </div>
            </article>

            <article className="panel simulation-panel">
              <div className="panel-header">
                <div>
                  <p className="panel-kicker">Paper Realism</p>
                  <h2>Simulation Costs</h2>
                </div>
                <span>{simulationStats.execution_model}</span>
              </div>
              <div className="signal-details-wrap">
                <table className="signal-details-table">
                  <tbody>
                    <tr><td><strong>Closed Trades</strong></td><td>{simulationStats.total_closed_trades}</td></tr>
                    <tr><td><strong>Fees Paid</strong></td><td>{formatCurrency(simulationStats.total_fees)}</td></tr>
                    <tr><td><strong>Funding Paid</strong></td><td>{formatSignedCurrency(simulationStats.total_funding)}</td></tr>
                    <tr><td><strong>Total Friction</strong></td><td>{formatCurrency(simulationStats.total_friction)}</td></tr>
                    <tr><td><strong>Gross Estimated P&L</strong></td><td>{formatSignedCurrency(simulationStats.gross_estimated_pnl)}</td></tr>
                    <tr><td><strong>Net Paper P&L</strong></td><td>{formatSignedCurrency(simulationStats.net_pnl)}</td></tr>
                    <tr><td><strong>Friction Drag</strong></td><td>{formatPercent(simulationStats.friction_drag_pct)}</td></tr>
                    <tr><td><strong>Rejected by Execution</strong></td><td>{simulationStats.blocked_execution}</td></tr>
                    <tr><td><strong>Blocked by Risk</strong></td><td>{simulationStats.blocked_risk}</td></tr>
                    <tr><td><strong>Pending / Expired Orders</strong></td><td>{simulationStats.pending_orders} / {simulationStats.expired_orders}</td></tr>
                  </tbody>
                </table>
              </div>
            </article>

            <article className="panel health-detail-panel">
              <div className="panel-header">
                <div>
                  <p className="panel-kicker">Operational Health</p>
                  <h2>Data Quality</h2>
                </div>
                <span>{routeStatuses.length ? `${routeOkCount}/${routeStatuses.length}` : "checking"}</span>
              </div>
              <div className="guardrail-list">
                <div>
                  <span>Route Failures</span>
                  <strong>{routeFailureCount}</strong>
                </div>
                <div>
                  <span>Market Failures</span>
                  <strong>{marketRouteFailures}</strong>
                </div>
                <div>
                  <span>WebSocket</span>
                  <strong>{priceStreamConnected ? "Streaming" : "Unavailable"}</strong>
                </div>
                <div>
                  <span>Backend</span>
                  <strong>{health?.ok ? "Online" : isFallback ? "Fallback" : "Checking"}</strong>
                </div>
                <div>
                  <span>Last Refresh</span>
                  <strong>{formatCompactDate(lastRefresh)}</strong>
                </div>
                <div>
                  <span>Data Source</span>
                  <strong>{activeState.data_source || lastSource}</strong>
                </div>
              </div>
            </article>

            <article className="panel export-panel">
              <div className="panel-header">
                <div>
                  <p className="panel-kicker">Evidence</p>
                  <h2>Export Data</h2>
                </div>
                <span>JSON</span>
              </div>
              <div className="route-list export-list">
                {exportLinks.map((link) => (
                  <a key={link.label} className="route-row export-row" href={link.href} target="_blank" rel="noreferrer">
                    <span className="route-dot ok" />
                    <strong>{link.label}</strong>
                    <span>open</span>
                    <em>{link.href}</em>
                  </a>
                ))}
              </div>
            </article>
          </section>
        ) : null}

        {activeView === "system" ? (
          <section className="system-page-grid">
            <section className="runtime-strip" aria-label="Runtime summary">
              <div>
                <span>Environment</span>
                <strong>{activeState.environment}</strong>
              </div>
              <div>
                <span>Data Source</span>
                <strong>{activeState.data_source || (isFallback ? "local snapshots" : "backend")}</strong>
              </div>
              <div>
                <span>Last Tick</span>
                <strong>{formatDate(activeState.current_tick || activeState.last_update_utc)}</strong>
              </div>
              <div>
                <span>Routes</span>
                <strong>{routeStatuses.length ? `${routeOkCount}/${routeStatuses.length} OK` : "checking"}</strong>
              </div>
              <div>
                <span>Refresh</span>
                <strong>{formatCompactDate(lastRefresh)}</strong>
              </div>
            </section>

            <article className="panel balance-panel">
              <div className="panel-header">
                <div>
                  <p className="panel-kicker">Paper Trading</p>
                  <h2>Balance Management</h2>
                </div>
                <span className={`profile-badge profile-${profileId}`}>
                  {profileId.charAt(0).toUpperCase()}{profileId.slice(1)}
                </span>
              </div>
              <div className="balance-info">
                <div>
                  <span>Current Balance</span>
                  <strong>{formatCurrency(activeState.equity)}</strong>
                </div>
                <div>
                  <span>Starting Balance</span>
                  <strong>{formatCurrency(activeState.starting_equity)}</strong>
                </div>
                <div>
                  <span>P&L</span>
                  <strong className={equityChange >= 0 ? "positive" : "negative"}>
                    {formatSignedCurrency(equityChange)} ({formatPercent(equityChangePct)})
                  </strong>
                </div>
              </div>
              <div className="balance-adjust">
                <div className="balance-input-row">
                  <label>
                    <span>New Balance</span>
                    <input
                      type="number"
                      min="0"
                      step="100"
                      value={balanceInput}
                      onChange={(e) => setBalanceInput(e.target.value)}
                      placeholder="Enter new balance..."
                    />
                  </label>
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={adjustStarting}
                      onChange={(e) => setAdjustStarting(e.target.checked)}
                    />
                    <span>Also reset starting balance</span>
                  </label>
                </div>
                <button
                  type="button"
                  className="balance-btn"
                  onClick={handleBalanceAdjust}
                  disabled={!balanceInput}
                >
                  Update Balance
                </button>
                {balanceMessage && (
                  <p className={`balance-message ${balanceMessage.startsWith("Error") ? "error" : "success"}`}>
                    {balanceMessage}
                  </p>
                )}
              </div>
            </article>

            <article className="panel pipeline-panel">
              <div className="panel-header">
                <div>
                  <p className="panel-kicker">Data Pipeline</p>
                  <h2>Fetch Status</h2>
                </div>
                <span>{routeStatuses.length ? `${routeOkCount}/${routeStatuses.length}` : "pending"}</span>
              </div>
              <div className="route-list">
                {routeStatuses.length > 0 ? (
                  routeStatuses.map((route) => (
                    <div key={route.name} className="route-row">
                      <span className={route.ok ? "route-dot ok" : "route-dot fail"} />
                      <strong>{route.name}</strong>
                      <span>{route.source}</span>
                      <em>{route.detail}</em>
                    </div>
                  ))
                ) : (
                  <p className="empty-state">Checking routes...</p>
                )}
              </div>
            </article>
          </section>
        ) : null}
      </section>
    </main>
  );
}
