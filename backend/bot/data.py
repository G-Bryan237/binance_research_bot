from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

from .config import ApiConfig
from .types import Candle, MarketSnapshot, MarketType, OrderBookDepth, OrderBookLevel

LOG = logging.getLogger(__name__)
MAINNET_SPOT_BASE = "https://api.binance.com"
MAINNET_FUTURES_BASE = "https://fapi.binance.com"


@dataclass
class SymbolRules:
    tick_size: float
    step_size: float
    min_notional: float


class BinanceDataClient:
    def __init__(self, cfg: ApiConfig) -> None:
        self.cfg = cfg
        self.timeout = cfg.request_timeout_seconds
        self.retries = max(cfg.request_retries, 0)
        self.retry_backoff = max(cfg.request_retry_backoff_seconds, 0.0)
        self.session = requests.Session()
        self._fallback_logged: set[tuple[str, str]] = set()

    def _bases_for_market(self, market: MarketType) -> list[str]:
        if market == MarketType.SPOT:
            bases = [self.cfg.spot_base_url]
            if self.cfg.use_mainnet_data_fallback and self.cfg.spot_base_url != MAINNET_SPOT_BASE:
                bases.append(MAINNET_SPOT_BASE)
            return bases
        bases = [self.cfg.futures_base_url]
        if self.cfg.use_mainnet_data_fallback and self.cfg.futures_base_url != MAINNET_FUTURES_BASE:
            bases.append(MAINNET_FUTURES_BASE)
        return bases

    def _get(self, bases: list[str], path: str, params: dict[str, Any], market: MarketType) -> Any:
        errors: list[str] = []
        for b_idx, base in enumerate(bases):
            url = f"{base}{path}"
            for attempt in range(self.retries + 1):
                try:
                    resp = self.session.get(url, params=params, timeout=self.timeout)
                    resp.raise_for_status()
                    if b_idx > 0:
                        key = (base, path)
                        if key not in self._fallback_logged:
                            LOG.warning(
                                "using fallback market-data endpoint for %s: %s",
                                market.value,
                                base,
                            )
                            self._fallback_logged.add(key)
                    return resp.json()
                except requests.RequestException as exc:
                    errors.append(f"{url} attempt {attempt + 1}: {exc}")
                    if attempt < self.retries:
                        time.sleep(self.retry_backoff * (attempt + 1))
                    else:
                        break
        raise RuntimeError(errors[-1] if errors else f"request failed for {path}")

    def _get_optional(self, bases: list[str], path: str, params: dict[str, Any], market: MarketType) -> Any | None:
        try:
            return self._get(bases, path, params, market)
        except Exception as exc:  # noqa: BLE001
            LOG.info(
                "optional market-data fetch skipped for %s %s %s: %s",
                params.get("symbol", ""),
                market.value,
                path,
                exc,
            )
            return None

    def _parse_klines(self, raw_klines: list[list[Any]]) -> list[Candle]:
        out: list[Candle] = []
        for row in raw_klines:
            out.append(
                Candle(
                    open_time=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        return out

    def fetch_snapshot(self, symbol: str, market: MarketType, timeframe: str, limit: int = 500) -> MarketSnapshot | None:
        try:
            bases = self._bases_for_market(market)
            if market == MarketType.SPOT:
                kl = self._get(
                    bases,
                    "/api/v3/klines",
                    {"symbol": symbol, "interval": timeframe, "limit": min(limit, 1000)},
                    market,
                )
                bt = self._get(
                    bases,
                    "/api/v3/ticker/bookTicker",
                    {"symbol": symbol},
                    market,
                )
                tk = self._get(
                    bases,
                    "/api/v3/ticker/24hr",
                    {"symbol": symbol},
                    market,
                )
                depth = self._get_optional(
                    bases,
                    "/api/v3/depth",
                    {"symbol": symbol, "limit": 20},
                    market,
                )
                depth_obj = None
                if depth is not None:
                    bid_levels = [OrderBookLevel(price=float(b[0]), quantity=float(b[1])) for b in depth.get("bids", [])]
                    ask_levels = [OrderBookLevel(price=float(a[0]), quantity=float(a[1])) for a in depth.get("asks", [])]
                    depth_obj = OrderBookDepth(
                        bid_levels=bid_levels,
                        ask_levels=ask_levels,
                        timestamp_ms=depth.get("T", int(time.time() * 1000)),
                    )
                return MarketSnapshot(
                    symbol=symbol,
                    market=market,
                    timeframe=timeframe,
                    candles=self._parse_klines(kl),
                    bid=float(bt["bidPrice"]),
                    ask=float(bt["askPrice"]),
                    quote_volume_24h=float(tk.get("quoteVolume", 0.0)),
                    depth=depth_obj,
                )

            kl = self._get(
                bases,
                "/fapi/v1/klines",
                {"symbol": symbol, "interval": timeframe, "limit": min(limit, 1500)},
                market,
            )
            bt = self._get(
                bases,
                "/fapi/v1/ticker/bookTicker",
                {"symbol": symbol},
                market,
            )
            tk = self._get(
                bases,
                "/fapi/v1/ticker/24hr",
                {"symbol": symbol},
                market,
            )
            fr = self._get_optional(
                bases,
                "/fapi/v1/fundingRate",
                {"symbol": symbol, "limit": 1},
                market,
            )
            oi = self._get_optional(
                bases,
                "/fapi/v1/openInterest",
                {"symbol": symbol},
                market,
            )
            depth = self._get_optional(
                bases,
                "/fapi/v1/depth",
                {"symbol": symbol, "limit": 20},
                market,
            )
            funding = None
            if isinstance(fr, list) and fr:
                funding = float(fr[-1].get("fundingRate", 0.0))
            oi_value = None
            if isinstance(oi, dict):
                try:
                    oi_value = float(oi.get("openInterest", 0.0))
                except (TypeError, ValueError):
                    oi_value = None
            depth_obj = None
            if depth is not None:
                bid_levels = [OrderBookLevel(price=float(b[0]), quantity=float(b[1])) for b in depth.get("bids", [])]
                ask_levels = [OrderBookLevel(price=float(a[0]), quantity=float(a[1])) for a in depth.get("asks", [])]
                depth_obj = OrderBookDepth(
                    bid_levels=bid_levels,
                    ask_levels=ask_levels,
                    timestamp_ms=depth.get("T", int(time.time() * 1000)),
                )
            return MarketSnapshot(
                symbol=symbol,
                market=market,
                timeframe=timeframe,
                candles=self._parse_klines(kl),
                bid=float(bt["bidPrice"]),
                ask=float(bt["askPrice"]),
                quote_volume_24h=float(tk.get("quoteVolume", 0.0)),
                funding_rate=funding,
                open_interest=oi_value,
                depth=depth_obj,
            )
        except Exception as exc:  # noqa: BLE001
            LOG.warning("snapshot fetch failed for %s %s: %s", symbol, market, exc)
            return None

    def fetch_symbol_rules(self, symbol: str, market: MarketType) -> SymbolRules | None:
        try:
            bases = self._bases_for_market(market)
            if market == MarketType.SPOT:
                raw = self._get(bases, "/api/v3/exchangeInfo", {"symbol": symbol}, market)
            else:
                raw = self._get(bases, "/fapi/v1/exchangeInfo", {"symbol": symbol}, market)
            symbols = raw.get("symbols", [])
            if not symbols:
                return None
            info = symbols[0]
            tick_size = 0.0
            step_size = 0.0
            min_notional = 5.0
            for f in info.get("filters", []):
                ft = f.get("filterType")
                if ft == "PRICE_FILTER":
                    tick_size = float(f.get("tickSize", 0.0))
                elif ft == "LOT_SIZE":
                    step_size = float(f.get("stepSize", 0.0))
                elif ft in {"MIN_NOTIONAL", "NOTIONAL"}:
                    min_notional = float(f.get("minNotional", min_notional))
            if step_size == 0:
                step_size = 0.000001
            if tick_size == 0:
                tick_size = 0.000001
            return SymbolRules(tick_size=tick_size, step_size=step_size, min_notional=min_notional)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("symbol rules fetch failed for %s %s: %s", symbol, market, exc)
            return None

    def fetch_orderbook_depth(self, symbol: str, market: MarketType, limit: int = 20) -> OrderBookDepth | None:
        """Fetch orderbook depth from Binance."""
        try:
            bases = self._bases_for_market(market)
            if market == MarketType.SPOT:
                raw = self._get_optional(bases, "/api/v3/depth", {"symbol": symbol, "limit": limit}, market)
            else:
                raw = self._get_optional(bases, "/fapi/v1/depth", {"symbol": symbol, "limit": limit}, market)
            
            if raw is None:
                return None
            
            bid_levels = []
            for entry in raw.get("bids", []):
                try:
                    bid_levels.append(OrderBookLevel(price=float(entry[0]), quantity=float(entry[1])))
                except (TypeError, ValueError, IndexError):
                    continue
            
            ask_levels = []
            for entry in raw.get("asks", []):
                try:
                    ask_levels.append(OrderBookLevel(price=float(entry[0]), quantity=float(entry[1])))
                except (TypeError, ValueError, IndexError):
                    continue
            
            timestamp_ms = raw.get("T", int(time.time() * 1000))
            
            return OrderBookDepth(
                bid_levels=bid_levels,
                ask_levels=ask_levels,
                timestamp_ms=timestamp_ms,
            )
        except Exception as exc:  # noqa: BLE001
            LOG.debug("orderbook depth fetch failed for %s %s: %s", symbol, market, exc)
            return None
