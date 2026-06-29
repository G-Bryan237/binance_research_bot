import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";

// Profile-specific backend URLs
const PROFILE_BACKENDS: Record<string, string> = {
  conservative: process.env.CONSERVATIVE_API_URL || "http://127.0.0.1:5001",
  aggressive: process.env.AGGRESSIVE_API_URL || "http://127.0.0.1:5002",
  default: process.env.PYTHON_BACKEND_BASE_URL || "http://127.0.0.1:5000",
};

// Public URLs for client-side access
const PUBLIC_PROFILE_BACKENDS: Record<string, string> = {
  conservative: process.env.NEXT_PUBLIC_CONSERVATIVE_API_URL || "http://127.0.0.1:5001",
  aggressive: process.env.NEXT_PUBLIC_AGGRESSIVE_API_URL || "http://127.0.0.1:5002",
  default: process.env.NEXT_PUBLIC_BACKEND_URL || "http://127.0.0.1:5000",
};

export type ProfileId = "conservative" | "aggressive" | "default";

export interface ProfileInfo {
  profile_id: string;
  profile_name: string;
  status: string;
  starting_equity: number;
  current_equity: number;
  daily_pnl: number;
  port: number;
}

export interface ReportSummary {
  filename: string;
  period_start: string;
  period_end: string;
  total_pnl: number;
  pnl_pct: number;
  total_trades: number;
  win_rate: number;
}

export interface StrategyBreakdown {
  module: string;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  total_pnl: number;
  avg_r_multiple: number;
  best_trade_pnl: number;
  worst_trade_pnl: number;
}

export interface Report {
  profile_id: string;
  profile_name: string;
  period_type: string;
  period_start: string;
  period_end: string;
  starting_equity: number;
  ending_equity: number;
  total_pnl: number;
  pnl_pct: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  avg_r_multiple: number;
  max_drawdown_pct: number;
  strategy_breakdown: StrategyBreakdown[];
  generated_at: string;
}

type SummaryFile = {
  initial_equity?: number;
  final_equity?: number;
  total_trades?: number;
  wins?: number;
  losses?: number;
  win_rate_pct?: number;
  avg_r_multiple?: number;
  by_module?: Record<
    string,
    {
      trades?: number;
      win_rate_pct?: number;
    }
  >;
};

type TradeRow = {
  symbol: string;
  direction: string;
  entry: number;
  exit: number;
  pnl: number;
  strategy_module: string;
  close_reason: string;
  timestamp: string;
  partial_exit: boolean;
  regime: string;
  r_multiple: number;
};

type MarketCandle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

type MarketPayload = {
  symbol: string;
  candles: MarketCandle[];
};

const MARKET_STALE_AFTER_MS = 30 * 60 * 1000;
const BINANCE_KLINE_ENDPOINTS = [
  "https://api.binance.com/api/v3/klines",
  "https://api1.binance.com/api/v3/klines",
  "https://api2.binance.com/api/v3/klines",
  "https://api3.binance.com/api/v3/klines",
  "https://data-api.binance.vision/api/v3/klines",
];

function getDefaultBackendBaseUrl(): string {
  const raw = process.env.PYTHON_BACKEND_BASE_URL || "http://127.0.0.1:5000";
  return raw.replace(/\/+$/, "");
}

function buildBackendUrl(pathWithQuery: string): string {
  const normalizedPath = pathWithQuery.startsWith("/")
    ? pathWithQuery
    : `/${pathWithQuery}`;
  return `${getDefaultBackendBaseUrl()}${normalizedPath}`;
}

function getProjectRoot(): string {
  return path.basename(process.cwd()) === "frontend" ? path.resolve(process.cwd(), "..") : process.cwd();
}

async function getNewestFile(directory: string, prefix: string, extension: string): Promise<string | null> {
  const files = await readdir(directory).catch(() => []);
  const candidates = await Promise.all(
    files
      .filter((file) => file.startsWith(prefix) && file.endsWith(extension))
      .map(async (file) => {
        const fullPath = path.join(directory, file);
        const details = await stat(fullPath);
        return { fullPath, mtimeMs: details.mtimeMs };
      }),
  );

  candidates.sort((a, b) => b.mtimeMs - a.mtimeMs);
  return candidates[0]?.fullPath ?? null;
}

async function readSummary(): Promise<SummaryFile | null> {
  const outDir = path.join(getProjectRoot(), "out");
  const summaryPath = await getNewestFile(outDir, "summary", ".json");
  if (!summaryPath) {
    return null;
  }

  return JSON.parse(await readFile(summaryPath, "utf-8")) as SummaryFile;
}

function parseCsvLine(line: string): string[] {
  const cells: string[] = [];
  let current = "";
  let inQuotes = false;

  for (const char of line) {
    if (char === '"') {
      inQuotes = !inQuotes;
    } else if (char === "," && !inQuotes) {
      cells.push(current);
      current = "";
    } else {
      current += char;
    }
  }

  cells.push(current);
  return cells.map((cell) => cell.trim());
}

function rowsFromCsv(raw: string): Record<string, string>[] {
  const [headerLine, ...rows] = raw.split(/\r?\n/).filter(Boolean);
  if (!headerLine) {
    return [];
  }

  const headers = parseCsvLine(headerLine);
  return rows.map((line) => {
    const values = parseCsvLine(line);
    return Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ""]));
  });
}

async function readTrades(limit = 200): Promise<TradeRow[]> {
  const outDir = path.join(getProjectRoot(), "out");
  const tradesPath = await getNewestFile(outDir, "trades", ".csv");
  if (!tradesPath) {
    return [];
  }

  return rowsFromCsv(await readFile(tradesPath, "utf-8"))
    .slice(-limit)
    .reverse()
    .map((row) => ({
      symbol: row.symbol || "BTCUSDT",
      direction: row.direction || "-",
      entry: Number(row.entry || 0),
      exit: Number(row.exit || 0),
      pnl: Number(row.pnl || 0),
      strategy_module: row.strategy_module || "-",
      close_reason: row.close_reason || "-",
      timestamp: row.closed_at_utc || row.opened_at_utc || "",
      partial_exit: false,
      regime: row.regime || "-",
      r_multiple: Number(row.r_multiple || 0),
    }));
}


function isMarketRoute(pathWithQuery: string): boolean {
  return pathWithQuery.split("?")[0] === "/api/market";
}

function parseMarketParams(pathWithQuery: string): { symbol: string; limit: number } {
  const [, rawQuery = ""] = pathWithQuery.split("?");
  const params = new URLSearchParams(rawQuery);
  return {
    symbol: (params.get("symbol") || "BTCUSDT").toUpperCase(),
    limit: Math.max(1, Math.min(Number(params.get("limit") || 120), 1000)),
  };
}

function latestCandleTime(candles: MarketCandle[]): number {
  return Math.max(...candles.map((candle) => Number(candle.time || 0)).filter(Number.isFinite), 0);
}

function marketPayloadIsFresh(payload: unknown): payload is MarketPayload {
  const candles = (payload as MarketPayload | null)?.candles ?? [];
  const latest = latestCandleTime(candles);
  return latest > 0 && Date.now() - latest <= MARKET_STALE_AFTER_MS;
}

function normalizeBinanceKline(row: unknown): MarketCandle | null {
  if (!Array.isArray(row) || row.length < 6) {
    return null;
  }

  const candle = {
    time: Number(row[0]),
    open: Number(row[1]),
    high: Number(row[2]),
    low: Number(row[3]),
    close: Number(row[4]),
    volume: Number(row[5]),
  };

  return Number.isFinite(candle.time) && Number.isFinite(candle.close) && candle.close > 0 ? candle : null;
}

async function fetchLiveMarketPayload(symbol: string, limit = 120): Promise<MarketPayload | null> {
  for (const endpoint of BINANCE_KLINE_ENDPOINTS) {
    const url = new URL(endpoint);
    url.searchParams.set("symbol", symbol.toUpperCase());
    url.searchParams.set("interval", "5m");
    url.searchParams.set("limit", String(Math.max(1, Math.min(limit, 1000))));

    try {
      const response = await fetch(url, {
        headers: { Accept: "application/json" },
        cache: "no-store",
        next: { revalidate: 0 },
      });

      if (!response.ok) {
        continue;
      }

      const raw = (await response.json()) as unknown[];
      const candles = raw.map(normalizeBinanceKline).filter((candle): candle is MarketCandle => candle !== null);
      if (candles.length > 0) {
        return { symbol: symbol.toUpperCase(), candles };
      }
    } catch {
      // Try the next public market-data endpoint.
    }
  }

  return null;
}

async function getFreshMarketPayload(pathWithQuery: string): Promise<MarketPayload | null> {
  const { symbol, limit } = parseMarketParams(pathWithQuery);
  return fetchLiveMarketPayload(symbol, limit);
}

async function readMarketCandles(symbol: string, limit = 120): Promise<MarketCandle[]> {
  const projectRoot = getProjectRoot();
  const dataDirs = [path.join(projectRoot, "data"), path.join(projectRoot, "backend", "data")];
  const normalizedSymbol = symbol.toUpperCase();
  const filenames = [`${normalizedSymbol}_SPOT_5m.csv`, `${normalizedSymbol}_SPOT_1m.csv`, `${normalizedSymbol}.csv`];
  const candidates = dataDirs.flatMap((dataDir) => filenames.map((filename) => path.join(dataDir, filename)));
  let found: string | null = null;
  for (const candidate of candidates) {
    try {
      const details = await stat(candidate);
      if (details.isFile()) {
        found = candidate;
        break;
      }
    } catch {
      // Try the next candidate.
    }
  }

  if (!found) {
    return [];
  }

  return rowsFromCsv(await readFile(found, "utf-8"))
    .slice(-Math.max(1, Math.min(limit, 1000)))
    .map((row) => ({
      time: Number(row.open_time || row.time || 0),
      open: Number(row.open || 0),
      high: Number(row.high || 0),
      low: Number(row.low || 0),
      close: Number(row.close || 0),
      volume: Number(row.volume || row.qty || 0),
    }))
    .filter((row) => Number.isFinite(row.close) && row.close > 0);
}

function statsFromSummary(summary: SummaryFile | null) {
  return {
    total_trades: Number(summary?.total_trades ?? 0),
    winning_trades: Number(summary?.wins ?? 0),
    losing_trades: Number(summary?.losses ?? 0),
    win_rate: Number(summary?.win_rate_pct ?? 0) / 100,
    avg_r_multiple: Number(summary?.avg_r_multiple ?? 0),
  };
}

function moduleScoresFromSummary(summary: SummaryFile | null) {
  return Object.fromEntries(
    Object.entries(summary?.by_module ?? {}).map(([moduleName, score]) => [
      moduleName,
      {
        win_rate: Number(score.win_rate_pct ?? 0) / 100,
        total_trades: Number(score.trades ?? 0),
        active: true,
      },
    ]),
  );
}

async function buildFallbackPayload(pathWithQuery: string) {
  const [routePath, rawQuery = ""] = pathWithQuery.split("?");
  const summary = await readSummary();

  if (routePath === "/api/stats") {
    return statsFromSummary(summary);
  }

  if (routePath === "/api/trades") {
    return { trades: await readTrades() };
  }

  if (routePath === "/api/market") {
    const params = new URLSearchParams(rawQuery);
    const symbol = (params.get("symbol") || "BTCUSDT").toUpperCase();
    const limit = Number(params.get("limit") || 120);
    const livePayload = await fetchLiveMarketPayload(symbol, limit);
    if (livePayload) {
      return livePayload;
    }
    return { symbol, candles: await readMarketCandles(symbol, limit) };
  }

  if (routePath === "/api/state" || routePath === "/api/health") {
    const trades = await readTrades();
    const stats = statsFromSummary(summary);
    const equity = Number(summary?.final_equity ?? 0);
    const startingEquity = Number(summary?.initial_equity ?? 0);
    const lastTradeAt = trades[0]?.timestamp || null;

    if (routePath === "/api/health") {
      return {
        ok: false,
        service: "binance-research-bot-api",
        status: "offline-fallback",
        last_update_utc: lastTradeAt,
        db_path: null,
      };
    }

    return {
      status: "offline-fallback",
      current_tick: null,
      last_update_utc: lastTradeAt,
      environment: "paper",
      data_source: "local out snapshots",
      update_interval_ms: 30000,
      equity,
      starting_equity: startingEquity,
      daily_pnl: equity - startingEquity,
      open_position: null,
      recent_trades: trades,
      equity_history: [
        { time: "start", equity: startingEquity },
        { time: lastTradeAt || "latest", equity },
      ],
      module_scores: moduleScoresFromSummary(summary),
      trading_stats: stats,
    };
  }

  return null;
}

function jsonResponse(payload: unknown, status: number, source: "backend" | "fallback" | "binance-live"): Response {
  return Response.json(payload, {
    status,
    headers: {
      "cache-control": "no-store",
      "x-bot-data-source": source,
    },
  });
}

export async function proxyToBackend(pathWithQuery: string): Promise<Response> {
  try {
    const upstream = await fetch(buildBackendUrl(pathWithQuery), {
      method: "GET",
      headers: {
        Accept: "application/json",
      },
      cache: "no-store",
    });

    const body = await upstream.text();

    if (isMarketRoute(pathWithQuery)) {
      if (upstream.ok) {
        try {
          const payload = JSON.parse(body) as unknown;
          if (marketPayloadIsFresh(payload)) {
            const headers = new Headers();
            headers.set(
              "content-type",
              upstream.headers.get("content-type") || "application/json; charset=utf-8",
            );
            headers.set("cache-control", "no-store");
            headers.set("x-bot-data-source", "backend");

            return new Response(body, {
              status: upstream.status,
              headers,
            });
          }
        } catch {
          // Fall through to live market data.
        }
      }

      const livePayload = await getFreshMarketPayload(pathWithQuery);
      if (livePayload) {
        return jsonResponse(livePayload, 200, "binance-live");
      }

      const { symbol, limit } = parseMarketParams(pathWithQuery);
      const fallbackCandles = await readMarketCandles(symbol, limit);
      if (fallbackCandles.length > 0) {
        return jsonResponse({ symbol, candles: fallbackCandles }, 200, "fallback");
      }
    }

    const headers = new Headers();
    headers.set(
      "content-type",
      upstream.headers.get("content-type") || "application/json; charset=utf-8",
    );
    headers.set("cache-control", "no-store");
    headers.set("x-bot-data-source", "backend");

    return new Response(body, {
      status: upstream.status,
      headers,
    });
  } catch (error) {
    const fallbackPayload = await buildFallbackPayload(pathWithQuery);
    if (fallbackPayload) {
      return jsonResponse(fallbackPayload, 200, "fallback");
    }

    return Response.json(
      {
        error: "Unable to reach Python backend.",
        detail: error instanceof Error ? error.message : "Unknown error",
      },
      { status: 502 },
    );
  }
}

// ============================================================================
// Profile-specific API functions
// ============================================================================

function getBackendBaseUrl(profile: ProfileId = "default"): string {
  const raw = PROFILE_BACKENDS[profile] || PROFILE_BACKENDS.default;
  return raw.replace(/\/+$/, "");
}

function getPublicBackendUrl(profile: ProfileId = "default"): string {
  const raw = PUBLIC_PROFILE_BACKENDS[profile] || PUBLIC_PROFILE_BACKENDS.default;
  return raw.replace(/\/+$/, "");
}

function buildProfileUrl(profile: ProfileId, pathWithQuery: string): string {
  const normalizedPath = pathWithQuery.startsWith("/") ? pathWithQuery : `/${pathWithQuery}`;
  return `${getBackendBaseUrl(profile)}${normalizedPath}`;
}

export function getAvailableProfiles(): ProfileId[] {
  return ["conservative", "aggressive"];
}

export async function fetchProfileInfo(profile: ProfileId): Promise<ProfileInfo | null> {
  try {
    const response = await fetch(buildProfileUrl(profile, "/api/profile"), {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

export async function fetchAllProfiles(): Promise<Record<ProfileId, ProfileInfo | null>> {
  const profiles = getAvailableProfiles();
  const results = await Promise.all(profiles.map(p => fetchProfileInfo(p)));
  return Object.fromEntries(profiles.map((p, i) => [p, results[i]])) as Record<ProfileId, ProfileInfo | null>;
}

export async function fetchReportsList(profile: ProfileId): Promise<{ daily: ReportSummary[]; weekly: ReportSummary[] } | null> {
  try {
    const response = await fetch(buildProfileUrl(profile, "/api/reports"), {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

export async function fetchDailyReport(profile: ProfileId, date?: string): Promise<Report | null> {
  try {
    const path = date ? `/api/reports/daily/${date}` : "/api/reports/daily";
    const response = await fetch(buildProfileUrl(profile, path), {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

export async function fetchWeeklyReport(profile: ProfileId, date?: string): Promise<Report | null> {
  try {
    const path = date ? `/api/reports/weekly/${date}` : "/api/reports/weekly";
    const response = await fetch(buildProfileUrl(profile, path), {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

export async function proxyToProfileBackend(profile: ProfileId, pathWithQuery: string): Promise<Response> {
  try {
    const upstream = await fetch(buildProfileUrl(profile, pathWithQuery), {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });

    const body = await upstream.text();
    const headers = new Headers();
    headers.set("content-type", upstream.headers.get("content-type") || "application/json; charset=utf-8");
    headers.set("cache-control", "no-store");
    headers.set("x-bot-profile", profile);
    headers.set("x-bot-data-source", "backend");

    return new Response(body, { status: upstream.status, headers });
  } catch (error) {
    return Response.json(
      {
        error: `Unable to reach ${profile} backend.`,
        detail: error instanceof Error ? error.message : "Unknown error",
      },
      { status: 502 },
    );
  }
}

export async function postToProfileBackend(
  profile: ProfileId,
  pathWithQuery: string,
  body: unknown
): Promise<Response> {
  try {
    const upstream = await fetch(buildProfileUrl(profile, pathWithQuery), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(body),
    });

    const responseBody = await upstream.text();
    const headers = new Headers();
    headers.set("content-type", upstream.headers.get("content-type") || "application/json; charset=utf-8");
    headers.set("cache-control", "no-store");
    headers.set("x-bot-profile", profile);

    return new Response(responseBody, { status: upstream.status, headers });
  } catch (error) {
    return Response.json(
      {
        error: `Unable to reach ${profile} backend.`,
        detail: error instanceof Error ? error.message : "Unknown error",
      },
      { status: 502 },
    );
  }
}

export { getPublicBackendUrl };
