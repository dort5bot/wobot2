''' 
Binance HTTP & WebSocket client (async) - Gelişmiş Sürüm
YENİ MİMARİ: user_id parametresi KALDIRILDI
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
        self.state = "CLOSED"

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

    def get_status(self) -> Dict[str, Any]:
        """Circuit breaker durumunu döndür"""
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "last_failure_time": self.last_failure_time,
            "failure_threshold": self.failure_threshold,
            "reset_timeout": self.reset_timeout
        }

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
        # 🔹 user_id parametresi KALDIRILDI
        self.api_key = api_key
        self.secret_key = secret_key
        
        LOG.info(f"HTTP Client initialized, has_keys: {bool(self.api_key and self.secret_key)}")
        
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

        # Connection pool metrics
        self.connection_pool = {
            "total_connections": 0,
            "active_connections": 0,
            "max_concurrent": 0
        }

        # Priority queues
        self.high_priority_sem = asyncio.Semaphore(max(1, CONFIG.BINANCE.CONCURRENCY // 2))
        self.normal_priority_sem = asyncio.Semaphore(CONFIG.BINANCE.CONCURRENCY)
        self.low_priority_sem = asyncio.Semaphore(CONFIG.BINANCE.CONCURRENCY * 2)

        # Advanced cache with different TTLs
        self._cache_strategies = {
            "ticker": 5,           # 5 seconds
            "klines": 30,          # 30 seconds
            "orderbook": 2,        # 2 seconds
            "exchange_info": 300,  # 5 minutes
            "default": 10          # 10 seconds
        }

        # Dynamic rate limiting
        self.rate_limits = {
            "requests": {
                "limit": CONFIG.BINANCE.MAX_REQUESTS_PER_SECOND,
                "remaining": CONFIG.BINANCE.MAX_REQUESTS_PER_SECOND,
                "reset_time": time.time() + 1
            }
        }

    def _cleanup_cache(self):
        current_time = time.time()
        expired_keys = [
            key for key, (ts, _) in self._cache.items()
            if current_time - ts > CONFIG.BINANCE.BINANCE_TICKER_TTL
        ]
        for key in expired_keys:
            del self._cache[key]

    def _get_cache_ttl(self, path: str) -> int:
        """Path'e göre uygun TTL belirle"""
        if "ticker" in path:
            return self._cache_strategies["ticker"]
        elif "klines" in path:
            return self._cache_strategies["klines"]
        elif "depth" in path:
            return self._cache_strategies["orderbook"]
        elif "exchangeInfo" in path:
            return self._cache_strategies["exchange_info"]
        else:
            return self._cache_strategies["default"]

    async def _check_rate_limit(self):
        """Dinamik rate limit kontrolü"""
        current_time = time.time()
        
        # Reset periodu dolduysa limitleri sıfırla
        if current_time >= self.rate_limits["requests"]["reset_time"]:
            self.rate_limits["requests"]["remaining"] = self.rate_limits["requests"]["limit"]
            self.rate_limits["requests"]["reset_time"] = current_time + 1
        
        # Limit dolduysa bekle
        if self.rate_limits["requests"]["remaining"] <= 0:
            sleep_time = self.rate_limits["requests"]["reset_time"] - current_time
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
                self.rate_limits["requests"]["remaining"] = self.rate_limits["requests"]["limit"]
                self.rate_limits["requests"]["reset_time"] = time.time() + 1
        
        self.rate_limits["requests"]["remaining"] -= 1

    async def _request(self, method: str, path: str, params: Optional[dict] = None,
                       signed: bool = False, futures: bool = False, max_retries: int = None,
                       priority: str = "normal") -> Any:
        if max_retries is None:
            max_retries = CONFIG.BINANCE.DEFAULT_RETRY_ATTEMPTS

        # Rate limiting
        await self._check_rate_limit()
        
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
            if not self.api_key or not self.secret_key:
                raise ValueError("Bu endpoint için API key + secret gerekli")
                
            ts = int(time.time() * 1000)
            params["timestamp"] = ts
            query = urlencode(params)
            signature = hmac.new(self.secret_key.encode(),
                                 query.encode(), hashlib.sha256).hexdigest()
            params["signature"] = signature
            headers["X-MBX-APIKEY"] = self.api_key

        # Cache temizleme
        current_time_cleanup = time.time()
        if current_time_cleanup - self._last_cache_cleanup > CONFIG.BINANCE.CACHE_CLEANUP_INTERVAL:
            self._cleanup_cache()
            self._last_cache_cleanup = current_time_cleanup

        cache_key = f"{method}:{base_url}{path}:{json.dumps(params, sort_keys=True) if params else ''}"
        ttl = self._get_cache_ttl(path)
        
        if ttl > 0 and cache_key in self._cache:
            ts_cache, data = self._cache[cache_key]
            if time.time() - ts_cache < ttl:
                self.metrics.cache_hits += 1
                return data
            self.metrics.cache_misses += 1

        # Priority-based semaphore selection
        if priority == "high":
            sem = self.high_priority_sem
        elif priority == "low":
            sem = self.low_priority_sem
        else:
            sem = self.normal_priority_sem

        attempt = 0
        last_exception = None
        
        # Akıllı retry stratejisi
        retry_strategy = {
            "429": {  # Rate limit
                "base_delay": 1,
                "max_delay": 60,
                "backoff_factor": 2
            },
            "5xx": {  # Server errors
                "base_delay": 2,
                "max_delay": 30,
                "backoff_factor": 1.5
            },
            "default": {  # Other errors
                "base_delay": 1,
                "max_delay": 10,
                "backoff_factor": 1.2
            }
        }
        
        while attempt < max_retries:
            attempt += 1
            
            async with sem:
                # Connection tracking
                self.connection_pool["active_connections"] += 1
                self.connection_pool["total_connections"] += 1
                self.connection_pool["max_concurrent"] = max(
                    self.connection_pool["max_concurrent"], 
                    self.connection_pool["active_connections"]
                )
                
                try:
                    r = await self.client.request(method, base_url + path, params=params, headers=headers)
                    
                    if r.status_code == 200:
                        data = r.json()
                        if ttl > 0:
                            self._cache[cache_key] = (time.time(), data)
                        return data
                    
                    # Status code'a göre retry stratejisi seç
                    status_category = "default"
                    if r.status_code == 429:
                        status_category = "429"
                    elif str(r.status_code).startswith('5'):
                        status_category = "5xx"
                    
                    strategy = retry_strategy[status_category]
                    delay = min(strategy["base_delay"] * (strategy["backoff_factor"] ** attempt), 
                               strategy["max_delay"])
                    
                    if r.status_code == 429:
                        self.metrics.rate_limited_requests += 1
                        retry_after = int(r.headers.get("Retry-After", 1))
                        delay += retry_after
                        LOG.warning("Rate limited. Sleeping %ss", delay)
                    
                    await asyncio.sleep(delay)
                    continue
                    
                except httpx.HTTPStatusError as e:
                    last_exception = e
                    self.metrics.failed_requests += 1
                    
                    # Daha detaylı hata loglama
                    error_info = {
                        "method": method,
                        "path": path,
                        "params": params,
                        "signed": signed,
                        "futures": futures,
                        "error": str(e),
                        "status_code": e.response.status_code if hasattr(e, 'response') else None,
                        "attempt": attempt
                    }
                    LOG.error(f"HTTP error: {json.dumps(error_info)}")
                    
                    if e.response.status_code >= 500:
                        strategy = retry_strategy["5xx"]
                        delay = min(strategy["base_delay"] * (strategy["backoff_factor"] ** attempt), 
                                   strategy["max_delay"])
                        LOG.warning("Server error %s, retrying in %s", e.response.status_code, delay)
                        await asyncio.sleep(delay)
                        continue
                    else:
                        raise
                        
                except (httpx.RequestError, asyncio.TimeoutError) as e:
                    last_exception = e
                    self.metrics.failed_requests += 1
                    
                    error_info = {
                        "method": method,
                        "path": path,
                        "params": params,
                        "signed": signed,
                        "futures": futures,
                        "error": str(e),
                        "attempt": attempt
                    }
                    LOG.error(f"Request error: {json.dumps(error_info)}")
                    
                    strategy = retry_strategy["default"]
                    delay = min(strategy["base_delay"] * (strategy["backoff_factor"] ** attempt), 
                               strategy["max_delay"])
                    await asyncio.sleep(delay)
                
                finally:
                    self.connection_pool["active_connections"] -= 1
        
        raise last_exception or Exception(f"Max retries ({max_retries}) exceeded")

    async def get_server_time(self) -> Dict[str, Any]:
        return await binance_circuit_breaker.execute(self._request, "GET", "/api/v3/time")

    async def get_exchange_info(self) -> Dict[str, Any]:
        return await binance_circuit_breaker.execute(self._request, "GET", "/api/v3/exchangeInfo")

    async def get_symbol_price(self, symbol: str) -> Dict[str, Any]:
        return await binance_circuit_breaker.execute(self._request, "GET", "/api/v3/ticker/price", {"symbol": symbol.upper()})

    def get_metrics(self) -> RequestMetrics:
        return self.metrics

    def get_connection_pool_stats(self) -> Dict[str, Any]:
        """Connection pool istatistiklerini döndür"""
        return self.connection_pool.copy()

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
        reconnect_attempts = 0
        max_reconnect_attempts = 10
        
        while self._running and reconnect_attempts < max_reconnect_attempts:
            try:
                async with websockets.connect(stream_url, ping_interval=20, ping_timeout=10) as ws:
                    LOG.info(f"Connected to {stream_url}")
                    self.metrics.total_connections += 1
                    reconnect_attempts = 0  # Reset on successful connection
                    
                    async for msg in ws:
                        try:
                            self.metrics.messages_received += 1
                            data = json.loads(msg)
                            asyncio.create_task(callback(data))
                        except Exception as cb_err:
                            LOG.error(f"Callback error: {cb_err}")
                            
            except Exception as e:
                reconnect_attempts += 1
                self.metrics.failed_connections += 1
                delay = min(2 ** reconnect_attempts, 60)  # Exponential backoff
                LOG.warning(f"WS error: {e}, reconnecting in {delay}s (attempt {reconnect_attempts}/{max_reconnect_attempts})")
                await asyncio.sleep(delay)

    async def subscribe(self, stream_name: str, callback: Callable):
        if stream_name not in self.connections:
            await self._create_connection(stream_name)
        self.callbacks[stream_name].append(callback)

    async def _create_connection(self, stream_name: str):
        url = f"wss://stream.binance.com:9443/ws/{stream_name}"
        try:
            ws = await websockets.connect(url, ping_interval=20, ping_timeout=10)
            self.connections[stream_name] = ws
            self.metrics.total_connections += 1
            asyncio.create_task(self._listen_stream(stream_name))
        except Exception as e:
            self.metrics.failed_connections += 1
            LOG.error("Failed to create WS connection for %s: %s", stream_name, e)
            raise

    async def _listen_stream(self, stream_name: str):
        while self._running and stream_name in self.connections:
            try:
                ws = self.connections[stream_name]
                msg = await ws.recv()
                self.metrics.messages_received += 1
                data = json.loads(msg)
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
        stream_name = f"{symbol.lower()}@ticker"
        asyncio.create_task(self.subscribe(stream_name, callback))

    def start_kline_stream(self, symbol: str, interval: str, callback: Callable[[Dict[str, Any]], Any]):
        stream_name = f"{symbol.lower()}@kline_{interval}"
        asyncio.create_task(self.subscribe(stream_name, callback))

    def start_order_book(self, symbol: str, depth: int, callback: Callable[[Dict[str, Any]], Any]):
        if depth not in [5, 10, 20]:
            raise ValueError("Depth must be one of [5, 10, 20]")
        stream_name = f"{symbol.lower()}@depth{depth}"
        asyncio.create_task(self.subscribe(stream_name, callback))

    async def close_all(self):
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
    df = pd.DataFrame(klines, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ])
    
    numeric_cols = ['open', 'high', 'low', 'close', 'volume']
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')
    df.set_index('open_time', inplace=True)
    return df[['open', 'high', 'low', 'close', 'volume']]

# -------------------------------------------------------------
# BinanceClient Wrapper - YENİ MİMARİ
# -------------------------------------------------------------
class BinanceClient:
    def __init__(self, api_key: Optional[str] = None, secret_key: Optional[str] = None):
        # 🔹 user_id parametresi TAMAMEN KALDIRILDI
        self.api_key = api_key
        self.secret_key = secret_key
        self.http = BinanceHTTPClient(self.api_key, self.secret_key)
        self.ws_manager = BinanceWebSocketManager()

        # Event loop handling iyileştirme
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

    def test_connection(self):
        has_keys = bool(self.api_key and self.secret_key)
        LOG.info(f"Binance client initialized, has_keys: {has_keys}")
        return True

    # ---------------------------------------------------------
    # ✅ PUBLIC (API key gerekmez)
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
    # ---------------------------------------------------------
    async def _require_keys(self):
        if not self.http.api_key or not self.http.secret_key:
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
        stream_name = f"{symbol.lower()}@ticker"
        await self.ws_manager.subscribe(stream_name, callback)

    async def ws_trades(self, symbol: str, callback: Callable):
        stream_name = f"{symbol.lower()}@trade"
        await self.ws_manager.subscribe(stream_name, callback)

    async def ws_order_book(self, symbol: str, depth: int, callback: Callable):
        if depth not in [5, 10, 20]:
            raise ValueError("Depth must be one of [5, 10, 20]")
        stream_name = f"{symbol.lower()}@depth{depth}"
        await self.ws_manager.subscribe(stream_name, callback)

    async def ws_kline(self, symbol: str, interval: str, callback: Callable):
        stream_name = f"{symbol.lower()}@kline_{interval}"
        await self.ws_manager.subscribe(stream_name, callback)

    async def ws_multiplex(self, streams: List[str], callback: Callable):
        combined_streams = "/".join(streams)
        stream_name = f"streams={combined_streams}"
        await self.ws_manager.subscribe(stream_name, callback)

    def start_symbol_ticker(self, symbol: str, callback: Callable[[Dict[str, Any]], Any]):
        self.ws_manager.start_symbol_ticker(symbol, callback)

    def start_kline_stream(self, symbol: str, interval: str, callback: Callable[[Dict[str, Any]], Any]):
        self.ws_manager.start_kline_stream(symbol, interval, callback)

    def start_order_book(self, symbol: str, depth: int, callback: Callable[[Dict[str, Any]], Any]):
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
        results = await asyncio.gather(
            self.spread(symbol),
            self.liquidity_score(symbol),
            self.whale_momentum(symbol),
            self.taker_ratio_score(symbol),
            self.vwap_depth_score(symbol),
            self.liquidity_imbalance_score(symbol),
            return_exceptions=True
        )
        
        metrics = {}
        metric_names = ["spread", "liquidity", "whale_momentum", "taker_ratio", "vwap_depth_score", "liquidity_imbalance"]
        
        for name, result in zip(metric_names, results):
            if isinstance(result, Exception):
                LOG.error("Error calculating %s for %s: %s", name, symbol, result)
                metrics[name] = None
            else:
                metrics[name] = result
        
        return metrics

    # --- YENİ: Batch Request Support ---
    async def batch_request(self, requests: List[Dict[str, Any]]) -> List[Any]:
        """Çoklu isteği paralelde yürüt"""
        async def _execute_single_request(req):
            try:
                method = req.get("method", "GET")
                endpoint = req.get("endpoint")
                params = req.get("params", {})
                signed = req.get("signed", False)
                futures = req.get("futures", False)
                priority = req.get("priority", "normal")
                
                return await self.http._request(method, endpoint, params, signed, futures, priority=priority)
            except Exception as e:
                return {"error": str(e), "request": req}
        
        # Tüm istekleri paralelde çalıştır
        results = await asyncio.gather(
            *[_execute_single_request(req) for req in requests],
            return_exceptions=True
        )
        
        return results

    # --- YENİ: Detaylı Health Check ---
    async def get_detailed_metrics(self) -> Dict[str, Any]:
        """Detaylı performans metrikleri"""
        http_metrics = self.get_http_metrics()
        ws_metrics = self.get_ws_metrics()
        conn_pool = self.http.get_connection_pool_stats()
        
        return {
            "http": {
                "total_requests": http_metrics.total_requests,
                "failed_requests": http_metrics.failed_requests,
                "success_rate": (http_metrics.total_requests - http_metrics.failed_requests) / max(http_metrics.total_requests, 1) * 100,
                "cache_hits": http_metrics.cache_hits,
                "cache_misses": http_metrics.cache_misses,
                "cache_hit_rate": http_metrics.cache_hits / max(http_metrics.cache_hits + http_metrics.cache_misses, 1) * 100,
                "rate_limited_requests": http_metrics.rate_limited_requests
            },
            "websocket": {
                "total_connections": ws_metrics.total_connections,
                "failed_connections": ws_metrics.failed_connections,
                "messages_received": ws_metrics.messages_received,
                "reconnections": ws_metrics.reconnections,
                "active_connections": len(self.ws_manager.connections)
            },
            "connection_pool": conn_pool,
            "circuit_breaker": binance_circuit_breaker.get_status(),
            "timestamp": time.time()
        }

    # --- Health Check ve Monitoring ---
    async def health_check(self) -> Dict[str, Any]:
        return {
            "http_metrics": self.get_http_metrics().__dict__,
            "ws_metrics": self.get_ws_metrics().__dict__,
            "cache_status": {
                "size": len(self.http._cache),
                "hits": self.http.metrics.cache_hits,
                "misses": self.http.metrics.cache_misses
            },
            "circuit_breaker": binance_circuit_breaker.get_status(),
            "timestamp": time.time(),
            "has_api_keys": bool(self.http.api_key and self.http.secret_key)
        }

    # --- Utils ---
    async def fetch_many(self, func, symbols: List[str], *args, **kwargs) -> Dict[str, Any]:
        tasks = [func(sym, *args, **kwargs) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {s: r for s, r in zip(symbols, results)}

    async def close(self, graceful: bool = True):
        """Graceful shutdown"""
        if graceful:
            # Önce WebSocket bağlantılarını kapat
            await self.ws_manager.close_all()
            
            # HTTP client'ı kapat
            await self.http.close()
            
            # Cache'i temizle
            self.http._cache.clear()
        else:
            # Forceful shutdown
            tasks = []
            if hasattr(self.ws_manager, 'close_all'):
                tasks.append(self.ws_manager.close_all())
            if hasattr(self.http, 'close'):
                tasks.append(self.http.close())
            
            await asyncio.gather(*tasks, return_exceptions=True)

    def get_http_metrics(self) -> RequestMetrics:
        return self.http.get_metrics()

    def get_ws_metrics(self) -> WSMetrics:
        return self.ws_manager.get_metrics()

    def reset_metrics(self):
        self.http.reset_metrics()
        self.ws_manager.reset_metrics()

# -------------------------------------------------------------
# Kullanıcı bazlı / Global fallback - YENİ MİMARİ
# -------------------------------------------------------------
def get_binance_client(api_key: Optional[str] = None, secret_key: Optional[str] = None) -> BinanceClient:
    # 🔹 user_id parametresi KALDIRILDI, direkt api_key/secret_key alıyor
    if not api_key or not secret_key:
        api_key = CONFIG.BINANCE.API_KEY
        secret_key = CONFIG.BINANCE.SECRET_KEY
    
    return BinanceClient(api_key, secret_key)

# -------------------------------------------------------------
# Singleton instance - YENİ MİMARİ
# -------------------------------------------------------------
binance_api: BinanceClient | None = None

def get_binance_api() -> BinanceClient:
    global binance_api
    if binance_api is None:
        binance_api = get_binance_client()
    return binance_api

async def cleanup_binance_api():
    global binance_api
    if binance_api:
        await binance_api.close()
        binance_api = None
