# utils/binance_api.py
''' 
Binance HTTP & WebSocket client (async) - Gelişmiş Sürüm
CONFIG entegre edilmiştir.
Asenkron HTTP istekleri, WebSocket abonelikleri, cache mekanizması, retry + exponential backoff gibi sağlam özellikleri içerir.
Temel ve pro metrikleri hesaplayan fonksiyonlar da eklenmiştir.
'''

import os
import time
import hmac
import hashlib
import json
import asyncio
import logging
import random
import httpx
import websockets
import pandas as pd
from typing import Any, Dict, List, Optional, Tuple, Callable
from urllib.parse import urlencode
from dataclasses import dataclass
from collections import defaultdict

from utils.config import CONFIG

# -------------------------------------------------------------
# Logger
# -------------------------------------------------------------
LOG = logging.getLogger(__name__)
LOG.setLevel(CONFIG.BINANCE.LOG_LEVEL)
LOG.addHandler(logging.NullHandler())

# -------------------------------------------------------------
# Data Classes for Metrics
# -------------------------------------------------------------
@dataclass
class RequestMetrics:
    total_requests: int = 0
    failed_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    rate_limited_requests: int = 0

@dataclass
class WSMetrics:
    total_connections: int = 0
    failed_connections: int = 0
    messages_received: int = 0
    reconnections: int = 0

# -------------------------------------------------------------
# Circuit Breaker Pattern
# -------------------------------------------------------------
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, reset_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
    
    async def execute(self, func, *args, **kwargs):
        if self.state == "OPEN":
            if time.time() - self.last_failure_time > self.reset_timeout:
                self.state = "HALF_OPEN"
            else:
                raise Exception("Circuit breaker is OPEN")
        
        try:
            result = await func(*args, **kwargs)
            if self.state == "HALF_OPEN":
                self.state = "CLOSED"
                self.failure_count = 0
            return result
        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            if self.failure_count >= self.failure_threshold:
                self.state = "OPEN"
            
            raise e

# Global circuit breaker instance
binance_circuit_breaker = CircuitBreaker(
    failure_threshold=CONFIG.BINANCE.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    reset_timeout=CONFIG.BINANCE.CIRCUIT_BREAKER_RESET_TIMEOUT
)

# -------------------------------------------------------------
# HTTP Katmanı: Retry + Exponential Backoff + TTL Cache
# -------------------------------------------------------------
class BinanceHTTPClient:
    def __init__(self, api_key: Optional[str] = None, secret_key: Optional[str] = None):
        self.api_key = api_key or CONFIG.BINANCE.API_KEY
        self.secret_key = secret_key or CONFIG.BINANCE.SECRET_KEY
        self.client = httpx.AsyncClient(
            base_url=CONFIG.BINANCE.BASE_URL, 
            timeout=CONFIG.BINANCE.REQUEST_TIMEOUT
        )
        self.sem = asyncio.Semaphore(CONFIG.BINANCE.CONCURRENCY)
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._last_cache_cleanup = time.time()
        self.last_request_time = 0
        self.min_request_interval = 1.0 / CONFIG.BINANCE.MAX_REQUESTS_PER_SECOND
        self.metrics = RequestMetrics()

    def _cleanup_cache(self):
        """Süresi dolmuş cache entry'lerini temizler"""
        current_time = time.time()
        expired_keys = [
            key for key, (ts, _) in self._cache.items()
            if current_time - ts > CONFIG.BINANCE.BINANCE_TICKER_TTL
        ]
        for key in expired_keys:
            del self._cache[key]

    async def _request(self, method: str, path: str, params: Optional[dict] = None,
                       signed: bool = False, futures: bool = False, max_retries: int = None) -> Any:
        if max_retries is None:
            max_retries = CONFIG.BINANCE.DEFAULT_RETRY_ATTEMPTS

        # Rate limiting
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.min_request_interval:
            await asyncio.sleep(self.min_request_interval - time_since_last)
        
        self.last_request_time = time.time()
        self.metrics.total_requests += 1

        base_url = CONFIG.BINANCE.FAPI_URL if futures else CONFIG.BINANCE.BASE_URL
        headers = {}
        params = params or {}

        if signed:
            ts = int(time.time() * 1000)
            params["timestamp"] = ts
            query = urlencode(params)
            signature = hmac.new(self.secret_key.encode(),
                                 query.encode(), hashlib.sha256).hexdigest()
            params["signature"] = signature
            headers["X-MBX-APIKEY"] = self.api_key

        # Cache temizleme (periodik)
        current_time_cleanup = time.time()
        if current_time_cleanup - self._last_cache_cleanup > CONFIG.BINANCE.CACHE_CLEANUP_INTERVAL:
            self._cleanup_cache()
            self._last_cache_cleanup = current_time_cleanup

        cache_key = f"{method}:{base_url}{path}:{json.dumps(params, sort_keys=True) if params else ''}"
        ttl = CONFIG.BINANCE.BINANCE_TICKER_TTL
        
        if ttl > 0 and cache_key in self._cache:
            ts_cache, data = self._cache[cache_key]
            if time.time() - ts_cache < ttl:
                self.metrics.cache_hits += 1
                return data
            self.metrics.cache_misses += 1

        attempt = 0
        last_exception = None
        
        while attempt < max_retries:
            attempt += 1
            try:
                async with self.sem:
                    r = await self.client.request(method, base_url + path, params=params, headers=headers)
                
                if r.status_code == 200:
                    data = r.json()
                    if ttl > 0:
                        self._cache[cache_key] = (time.time(), data)
                    return data
                
                if r.status_code == 429:
                    self.metrics.rate_limited_requests += 1
                    retry_after = int(r.headers.get("Retry-After", 1))
                    delay = min(2 ** attempt, 60) + retry_after
                    LOG.warning("Rate limited. Sleeping %ss", delay)
                    await asyncio.sleep(delay)
                    continue
                
                r.raise_for_status()
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500:
                    # Server error - retry
                    delay = min(2 ** attempt, 30)
                    LOG.warning("Server error %s, retrying in %s", e.response.status_code, delay)
                    await asyncio.sleep(delay)
                    continue
                else:
                    # Client error - don't retry
                    self.metrics.failed_requests += 1
                    raise
                    
            except (httpx.RequestError, asyncio.TimeoutError) as e:
                last_exception = e
                self.metrics.failed_requests += 1
                delay = min(2 ** attempt, 60)
                LOG.error("Request error %s, retrying in %s", e, delay)
                await asyncio.sleep(delay)
        
        raise last_exception or Exception(f"Max retries ({max_retries}) exceeded")

    async def get_server_time(self) -> Dict[str, Any]:
        return await binance_circuit_breaker.execute(self._request, "GET", "/api/v3/time")

    async def get_exchange_info(self) -> Dict[str, Any]:
        return await binance_circuit_breaker.execute(self._request, "GET", "/api/v3/exchangeInfo")

    async def get_symbol_price(self, symbol: str) -> Dict[str, Any]:
        return await binance_circuit_breaker.execute(self._request, "GET", "/api/v3/ticker/price", {"symbol": symbol.upper()})

    def get_metrics(self) -> RequestMetrics:
        return self.metrics

    def reset_metrics(self):
        self.metrics = RequestMetrics()

    async def close(self):
        await self.client.aclose()

# -------------------------------------------------------------
# WebSocket Manager
# -------------------------------------------------------------
class BinanceWebSocketManager:
    def __init__(self):
        self.connections: Dict[str, websockets.WebSocketClientProtocol] = {}
        self.callbacks: Dict[str, List[Callable]] = defaultdict(list)
        self.metrics = WSMetrics()
        self._running = True

    async def _listen(self, stream_url: str, callback: Callable[[Dict[str, Any]], Any]):
        """
        WS connection + reconnect loop.
        """
        while self._running:
            try:
                async with websockets.connect(stream_url, ping_interval=20, ping_timeout=10) as ws:
                    LOG.info(f"Connected to {stream_url}")
                    self.metrics.total_connections += 1
                    async for msg in ws:
                        try:
                            self.metrics.messages_received += 1
                            data = json.loads(msg)
                            asyncio.create_task(callback(data))
                        except Exception as cb_err:
                            LOG.error(f"Callback error: {cb_err}")
            except Exception as e:
                self.metrics.failed_connections += 1
                LOG.warning(f"WS error: {e}, reconnecting in {CONFIG.BINANCE.WS_RECONNECT_DELAY}s")
                await asyncio.sleep(CONFIG.BINANCE.WS_RECONNECT_DELAY)

    async def subscribe(self, stream_name: str, callback: Callable):
        """Subscribe to a websocket stream"""
        if stream_name not in self.connections:
            await self._create_connection(stream_name)
        
        self.callbacks[stream_name].append(callback)

    async def _create_connection(self, stream_name: str):
        """Create a new websocket connection"""
        url = f"wss://stream.binance.com:9443/ws/{stream_name}"
        
        try:
            ws = await websockets.connect(url, ping_interval=20, ping_timeout=10)
            self.connections[stream_name] = ws
            self.metrics.total_connections += 1
            
            # Start listening to this stream
            asyncio.create_task(self._listen_stream(stream_name))
            
        except Exception as e:
            self.metrics.failed_connections += 1
            LOG.error("Failed to create WS connection for %s: %s", stream_name, e)
            raise

    async def _listen_stream(self, stream_name: str):
        """Listen to messages from a websocket stream"""
        while self._running and stream_name in self.connections:
            try:
                ws = self.connections[stream_name]
                msg = await ws.recv()
                self.metrics.messages_received += 1
                
                data = json.loads(msg)
                
                # Call all registered callbacks
                for callback in self.callbacks[stream_name]:
                    try:
                        await callback(data)
                    except Exception as e:
                        LOG.error("Callback error for %s: %s", stream_name, e)
                        
            except websockets.ConnectionClosed:
                LOG.warning("Connection closed for %s, reconnecting...", stream_name)
                await self._reconnect(stream_name)
            except Exception as e:
                LOG.error("Error in stream %s: %s", stream_name, e)
                await self._reconnect(stream_name)

    async def _reconnect(self, stream_name: str):
        """Reconnect to a websocket stream"""
        if stream_name in self.connections:
            try:
                await self.connections[stream_name].close()
            except:
                pass
            del self.connections[stream_name]
        
        self.metrics.reconnections += 1
        await asyncio.sleep(CONFIG.BINANCE.WS_RECONNECT_DELAY)
        
        if self._running:
            try:
                await self._create_connection(stream_name)
            except Exception as e:
                LOG.error("Failed to reconnect %s: %s", stream_name, e)

    def start_symbol_ticker(self, symbol: str, callback: Callable[[Dict[str, Any]], Any]):
        """Start ticker stream (alternative method)"""
        stream_name = f"{symbol.lower()}@ticker"
        asyncio.create_task(self.subscribe(stream_name, callback))

    def start_kline_stream(self, symbol: str, interval: str, callback: Callable[[Dict[str, Any]], Any]):
        """Start kline stream (alternative method)"""
        stream_name = f"{symbol.lower()}@kline_{interval}"
        asyncio.create_task(self.subscribe(stream_name, callback))

    def start_order_book(self, symbol: str, depth: int, callback: Callable[[Dict[str, Any]], Any]):
        """Start order book stream (alternative method)"""
        if depth not in [5, 10, 20]:
            raise ValueError("Depth must be one of [5, 10, 20]")
        stream_name = f"{symbol.lower()}@depth{depth}"
        asyncio.create_task(self.subscribe(stream_name, callback))

    async def close_all(self):
        """Close all websocket connections"""
        self._running = False
        for stream_name, ws in self.connections.items():
            try:
                await ws.close()
            except:
                pass
        self.connections.clear()
        self.callbacks.clear()

    def get_metrics(self) -> WSMetrics:
        return self.metrics

    def reset_metrics(self):
        self.metrics = WSMetrics()

# -------------------------------------------------------------
# Veri Formatı Dönüşüm Fonksiyonları
# -------------------------------------------------------------
def klines_to_dataframe(klines: List[List[Any]]) -> pd.DataFrame:
    """Binance klines verisini pandas DataFrame'e dönüştürür."""
    df = pd.DataFrame(klines, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ])
    
    # Numeric columns conversion
    numeric_cols = ['open', 'high', 'low', 'close', 'volume']
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    # Time conversion
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')
    
    df.set_index('open_time', inplace=True)
    return df[['open', 'high', 'low', 'close', 'volume']]

# -------------------------------------------------------------
# BinanceClient Wrapper
# -------------------------------------------------------------
class BinanceClient:
    """
    Binance REST + WS wrapper
    Temel + Pro metrikler içerir.
    - Public endpointler: API key gerekmez
    - Private endpointler: API key + secret gerekir
    """

    def __init__(self, api_key: Optional[str] = None, secret_key: Optional[str] = None):
        self.api_key = api_key or CONFIG.BINANCE.API_KEY
        self.secret_key = secret_key or CONFIG.BINANCE.SECRET_KEY
        self.http = BinanceHTTPClient(self.api_key, self.secret_key)
        self.ws_manager = BinanceWebSocketManager()

        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

    # ---------------------------------------------------------
    # ✅ PUBLIC (API key gerekmez)
    # --- REST Methods ---
    # ---------------------------------------------------------
    async def get_server_time(self) -> Dict[str, Any]:
        return await self.http.get_server_time()

    async def get_exchange_info(self) -> Dict[str, Any]:
        return await self.http.get_exchange_info()

    async def get_symbol_price(self, symbol: str) -> Dict[str, Any]:
        return await self.http.get_symbol_price(symbol)

    async def get_order_book(self, symbol: str, limit: int = 100) -> Dict[str, Any]:
        return await binance_circuit_breaker.execute(
            self.http._request, "GET", "/api/v3/depth",
            {"symbol": symbol.upper(), "limit": limit}
        )

    async def get_recent_trades(self, symbol: str, limit: int = 500) -> List[Dict[str, Any]]:
        return await binance_circuit_breaker.execute(
            self.http._request, "GET", "/api/v3/trades",
            {"symbol": symbol.upper(), "limit": limit}
        )

    async def get_agg_trades(self, symbol: str, limit: int = 500) -> List[Dict[str, Any]]:
        return await binance_circuit_breaker.execute(
            self.http._request, "GET", "/api/v3/aggTrades",
            {"symbol": symbol.upper(), "limit": limit}
        )

    async def get_klines(self, symbol: str, interval: str = "1m", limit: int = 500) -> List[List[Any]]:
        return await binance_circuit_breaker.execute(
            self.http._request, "GET", "/api/v3/klines",
            {"symbol": symbol.upper(), "interval": interval, "limit": limit}
        )

    async def get_klines_dataframe(self, symbol: str, interval: str = "1m", limit: int = 500) -> pd.DataFrame:
        """Klines verisini DataFrame formatında döndürür"""
        klines = await self.get_klines(symbol, interval, limit)
        return klines_to_dataframe(klines)

    async def get_24h_ticker(self, symbol: str) -> Dict[str, Any]:
        return await binance_circuit_breaker.execute(
            self.http._request, "GET", "/api/v3/ticker/24hr",
            {"symbol": symbol.upper()}
        )

    async def get_all_24h_tickers(self) -> List[Dict[str, Any]]:
        return await binance_circuit_breaker.execute(
            self.http._request, "GET", "/api/v3/ticker/24hr"
        )

    async def get_all_symbols(self) -> List[str]:
        data = await binance_circuit_breaker.execute(
            self.http._request, "GET", "/api/v3/exchangeInfo"
        )
        return [s["symbol"] for s in data["symbols"]]

    async def exchange_info_details(self) -> Dict[str, Any]:
        return await binance_circuit_breaker.execute(
            self.http._request, "GET", "/api/v3/exchangeInfo"
        )

    # ---------------------------------------------------------
    # ✅ PRIVATE (API key + secret zorunlu)
    # --- Signed Spot & Futures ---
    # ---------------------------------------------------------
    async def _require_keys(self):
        if not self.api_key or not self.secret_key:
            raise ValueError("Bu endpoint için API key + secret gerekli")

    async def get_account_info(self) -> Dict[str, Any]:
        await self._require_keys()
        return await binance_circuit_breaker.execute(
            self.http._request, "GET", "/api/v3/account", signed=True
        )

    async def place_order(self, symbol: str, side: str, type_: str,
                          quantity: float, price: Optional[float] = None) -> Dict[str, Any]:
        await self._require_keys()
        params = {"symbol": symbol.upper(), "side": side, "type": type_, "quantity": quantity}
        if price:
            params["price"] = price
        return await binance_circuit_breaker.execute(
            self.http._request, "POST", "/api/v3/order", params=params, signed=True
        )

    async def futures_position_info(self) -> List[Dict[str, Any]]:
        await self._require_keys()
        return await binance_circuit_breaker.execute(
            self.http._request, "GET", "/fapi/v2/positionRisk", signed=True, futures=True
        )

    async def get_funding_rate(self, symbol: str, limit: int = 1) -> List[Dict[str, Any]]:
        await self._require_keys()
        params = {"symbol": symbol.upper(), "limit": limit}
        return await binance_circuit_breaker.execute(
            self.http._request, "GET", "/fapi/v1/fundingRate", params=params, futures=True
        )

    # -----------
    # --- WebSocket Methods ---
    # -----------
    async def ws_ticker(self, symbol: str, callback: Callable):
        """Subscribe to ticker stream"""
        stream_name = f"{symbol.lower()}@ticker"
        await self.ws_manager.subscribe(stream_name, callback)

    async def ws_trades(self, symbol: str, callback: Callable):
        """Subscribe to trades stream"""
        stream_name = f"{symbol.lower()}@trade"
        await self.ws_manager.subscribe(stream_name, callback)

    async def ws_order_book(self, symbol: str, depth: int, callback: Callable):
        """Subscribe to order book stream"""
        if depth not in [5, 10, 20]:
            raise ValueError("Depth must be one of [5, 10, 20]")
        stream_name = f"{symbol.lower()}@depth{depth}"
        await self.ws_manager.subscribe(stream_name, callback)

    async def ws_kline(self, symbol: str, interval: str, callback: Callable):
        """Subscribe to kline stream"""
        stream_name = f"{symbol.lower()}@kline_{interval}"
        await self.ws_manager.subscribe(stream_name, callback)

    async def ws_multiplex(self, streams: List[str], callback: Callable):
        """Subscribe to multiple streams using multiplex connection"""
        combined_streams = "/".join(streams)
        stream_name = f"streams={combined_streams}"
        await self.ws_manager.subscribe(stream_name, callback)

    def start_symbol_ticker(self, symbol: str, callback: Callable[[Dict[str, Any]], Any]):
        """Start ticker stream (alternative method)"""
        self.ws_manager.start_symbol_ticker(symbol, callback)

    def start_kline_stream(self, symbol: str, interval: str, callback: Callable[[Dict[str, Any]], Any]):
        """Start kline stream (alternative method)"""
        self.ws_manager.start_kline_stream(symbol, interval, callback)

    def start_order_book(self, symbol: str, depth: int, callback: Callable[[Dict[str, Any]], Any]):
        """Start order book stream (alternative method)"""
        self.ws_manager.start_order_book(symbol, depth, callback)

    # --- Temel Metrikler ---
    async def order_book_imbalance(self, symbol: str, limit: int = 50) -> float:
        ob = await self.get_order_book(symbol, limit)
        bids = sum(float(b[1]) for b in ob["bids"])
        asks = sum(float(a[1]) for a in ob["asks"])
        return (bids - asks) / max(bids + asks, 1)

    async def whale_trades(self, symbol: str, usd_threshold: float = CONFIG.BINANCE.WHALE_USD_THRESHOLD) -> int:
        trades = await self.get_recent_trades(symbol)
        return sum(1 for t in trades if float(t["price"]) * float(t["qty"]) > usd_threshold)

    async def taker_buy_sell_ratio(self, symbol: str) -> float:
        trades = await self.get_agg_trades(symbol)
        buy = sum(float(t["q"]) for t in trades if not t["m"])
        sell = sum(float(t["q"]) for t in trades if t["m"])
        return (buy - sell) / max(buy + sell, 1)

    async def volume_delta(self, symbol: str) -> float:
        trades = await self.get_agg_trades(symbol)
        buy = sum(float(t["q"]) for t in trades if not t["m"])
        sell = sum(float(t["q"]) for t in trades if t["m"])
        return buy - sell

    # --- Pro Metrikler ---
    async def spread(self, symbol: str) -> float:
        ob = await self.get_order_book(symbol, 5)
        best_bid = float(ob["bids"][0][0])
        best_ask = float(ob["asks"][0][0])
        return (best_ask - best_bid) / ((best_ask + best_bid) / 2)

    async def vwap_depth_impact(self, symbol: str, depth: float = 0.01) -> float:
        ob = await self.get_order_book(symbol, 100)
        mid = (float(ob["bids"][0][0]) + float(ob["asks"][0][0])) / 2
        target = mid * (1 + depth)
        cum_qty = 0
        cum_notional = 0
        for ask in ob["asks"]:
            p, q = float(ask[0]), float(ask[1])
            if p > target:
                break
            cum_qty += q
            cum_notional += p * q
        vwap = cum_notional / max(cum_qty, 1)
        return (vwap - mid) / mid

    async def liquidity_score(self, symbol: str, levels: int = 20) -> float:
        ob = await self.get_order_book(symbol, levels)
        bid_vol = sum(float(b[1]) for b in ob["bids"])
        ask_vol = sum(float(a[1]) for a in ob["asks"])
        return 100 * min(bid_vol, ask_vol) / max(bid_vol, ask_vol)

    async def trade_size_distribution(self, symbol: str) -> Dict[str, int]:
        trades = await self.get_recent_trades(symbol)
        buckets = {"small": 0, "medium": 0, "large": 0}
        for t in trades:
            notional = float(t["price"]) * float(t["qty"])
            if notional < 1000:
                buckets["small"] += 1
            elif notional < 10000:
                buckets["medium"] += 1
            else:
                buckets["large"] += 1
        return buckets

    async def short_term_momentum(self, symbol: str, window: int = 10) -> float:
        kl = await self.get_klines(symbol, "1m", limit=window)
        closes = [float(k[4]) for k in kl]
        return (closes[-1] - closes[0]) / closes[0]

    async def market_order_price_impact(self, symbol: str, qty: float) -> float:
        ob = await self.get_order_book(symbol, 100)
        cum_qty = 0
        for ask in ob["asks"]:
            p, q = float(ask[0]), float(ask[1])
            cum_qty += q
            if cum_qty >= qty:
                return (p - float(ob["bids"][0][0])) / float(ob["bids"][0][0])
        return 0.0

    # --- Gelişmiş Pro Metrikler ---
    async def whale_momentum(self, symbol: str, lookback: int = 50, usd_threshold: float = CONFIG.BINANCE.WHALE_USD_THRESHOLD) -> float:
        trades = await self.get_recent_trades(symbol, limit=lookback)
        net = 0
        for t in trades:
            notional = float(t["price"]) * float(t["qty"])
            if notional >= usd_threshold:
                net += -1 if t["m"] else 1
        return net / max(len(trades), 1)

    async def taker_ratio_score(self, symbol: str, lookback: int = 500) -> float:
        trades = await self.get_agg_trades(symbol, limit=lookback)
        buy = sum(float(t["q"]) for t in trades if not t["m"])
        sell = sum(float(t["q"]) for t in trades if t["m"])
        total = buy + sell
        return (buy - sell) / max(total, 1)

    async def vwap_depth_score(self, symbol: str, depth: float = 0.01) -> float:
        ob = await self.get_order_book(symbol, limit=100)
        mid = (float(ob["bids"][0][0]) + float(ob["asks"][0][0])) / 2
        target = mid * (1 + depth)
        cum_qty = 0
        cum_notional = 0
        for ask in ob["asks"]:
            p, q = float(ask[0]), float(ask[1])
            if p > target:
                break
            cum_qty += q
            cum_notional += p * q
        vwap = cum_notional / max(cum_qty, 1)
        return (vwap - mid) / mid

    async def liquidity_imbalance_score(self, symbol: str, levels: int = 20) -> float:
        ob = await self.get_order_book(symbol, limit=levels)
        bid_vol = sum(float(b[1]) for b in ob["bids"])
        ask_vol = sum(float(a[1]) for a in ob["asks"])
        imbalance = (bid_vol - ask_vol) / max(bid_vol + ask_vol, 1)
        liquidity = 100 * min(bid_vol, ask_vol) / max(bid_vol, ask_vol)
        return liquidity * (1 + imbalance)

    async def pro_metrics_aggregator(self, symbol: str) -> Dict[str, Any]:
        """Tüm pro metrikleri tek bir çağrıda toplar"""
        results = await asyncio.gather(
            self.spread(symbol),
            self.liquidity_score(symbol),
            self.whale_momentum(symbol),
            self.taker_ratio_score(symbol),
            self.vwap_depth_score(symbol),
            self.liquidity_imbalance_score(symbol),
            return_exceptions=True
        )
        
        # Handle exceptions
        metrics = {}
        metric_names = ["spread", "liquidity", "whale_momentum", "taker_ratio", "vwap_depth_score", "liquidity_imbalance"]
        
        for name, result in zip(metric_names, results):
            if isinstance(result, Exception):
                LOG.error("Error calculating %s for %s: %s", name, symbol, result)
                metrics[name] = None
            else:
                metrics[name] = result
        
        return metrics

    # --- Health Check ve Monitoring ---
    async def health_check(self) -> Dict[str, Any]:
        """Sistem sağlık durumunu kontrol eder."""
        return {
            "http_metrics": self.get_http_metrics().__dict__,
            "ws_metrics": self.get_ws_metrics().__dict__,
            "cache_status": {
                "size": len(self.http._cache),
                "hits": self.http.metrics.cache_hits,
                "misses": self.http.metrics.cache_misses
            },
            "circuit_breaker": {
                "state": binance_circuit_breaker.state,
                "failure_count": binance_circuit_breaker.failure_count,
                "last_failure_time": binance_circuit_breaker.last_failure_time
            },
            "timestamp": time.time()
        }

    # --- Utils ---
    async def fetch_many(self, func, symbols: List[str], *args, **kwargs) -> Dict[str, Any]:
        tasks = [func(sym, *args, **kwargs) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {s: r for s, r in zip(symbols, results)}

    async def close(self):
        """Cleanup resources"""
        await self.ws_manager.close_all()
        await self.http.close()

    def get_http_metrics(self) -> RequestMetrics:
        return self.http.get_metrics()

    def get_ws_metrics(self) -> WSMetrics:
        return self.ws_manager.get_metrics()

    def reset_metrics(self):
        self.http.reset_metrics()
        self.ws_manager.reset_metrics()

# -------------------------------------------------------------
# Global Health Check Fonksiyonu
# -------------------------------------------------------------
async def health_check() -> Dict[str, Any]:
    """Sistem sağlık durumunu kontrol eder."""
    client = get_binance_api()
    return await client.health_check()

# -------------------------------------------------------------
# Singleton instance güvenli oluşturma
# -------------------------------------------------------------
binance_api: BinanceClient | None = None

def get_binance_api() -> BinanceClient:
    """
    Singleton erişim. Eğer loop yoksa burada yaratılır.
    """
    global binance_api
    if binance_api is None:
        binance_api = BinanceClient()
    return binance_api

async def cleanup_binance_api():
    """Cleanup resources on application shutdown"""
    global binance_api
    if binance_api:
        await binance_api.close()
        binance_api = None