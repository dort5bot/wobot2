# utils/binance_api.py

import os
import time
import hmac
import hashlib
import json
import asyncio
import logging
import httpx
import websockets
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from utils.config import CONFIG

# -------------------------------------------------------------
# Logger
# -------------------------------------------------------------
LOG = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())


# -------------------------------------------------------------
# HTTP Katmanı: Retry + Exponential Backoff + TTL Cache
# -------------------------------------------------------------
class BinanceHTTPClient:
    def __init__(self):
        self.client = httpx.AsyncClient(base_url=CONFIG.BINANCE.BASE_URL, timeout=15)
        self.sem = asyncio.Semaphore(CONFIG.BINANCE.CONCURRENCY)
        self._cache: Dict[str, Tuple[float, Any]] = {}

    async def _request(self, method: str, path: str, params: Optional[dict] = None,
                       signed: bool = False, futures: bool = False) -> Any:
        base_url = CONFIG.BINANCE.FAPI_URL if futures else CONFIG.BINANCE.BASE_URL
        headers = {}
        params = params or {}

        if signed:
            ts = int(time.time() * 1000)
            params["timestamp"] = ts
            query = urlencode(params)
            signature = hmac.new(CONFIG.BINANCE.SECRET_KEY.encode(),
                                 query.encode(), hashlib.sha256).hexdigest()
            params["signature"] = signature
            headers["X-MBX-APIKEY"] = CONFIG.BINANCE.API_KEY

        cache_key = f"{method}:{base_url}{path}:{json.dumps(params, sort_keys=True) if params else ''}"
        ttl = CONFIG.BINANCE.BINANCE_TICKER_TTL
        if ttl > 0 and cache_key in self._cache:
            ts_cache, data = self._cache[cache_key]
            if time.time() - ts_cache < ttl:
                return data

        attempt = 0
        while True:
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
                    retry_after = int(r.headers.get("Retry-After", 1))
                    delay = min(2 ** attempt, 60) + retry_after
                    LOG.warning("Rate limited. Sleeping %ss", delay)
                    await asyncio.sleep(delay)
                    continue
                r.raise_for_status()
            except Exception as e:
                delay = min(2 ** attempt, 60)
                LOG.error("Request error %s, retrying in %s", e, delay)
                await asyncio.sleep(delay)

http = BinanceHTTPClient()

# -------------------------------------------------------------
# BinanceClient Wrapper
# -------------------------------------------------------------
class BinanceClient:
    """
    REST + WS wrapper. Temel + Pro metrikler içerir.
    """
    def __init__(self):
        self.http = http
        # Async loop yoksa yarat
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
        self.ws_tasks: List[asyncio.Task] = []

    # --- REST ---
    async def get_order_book(self, symbol: str, limit: int = 100) -> Dict[str, Any]:
        return await self.http._request("GET", "/api/v3/depth", {"symbol": symbol.upper(), "limit": limit})

    async def get_recent_trades(self, symbol: str, limit: int = 500) -> List[Dict[str, Any]]:
        return await self.http._request("GET", "/api/v3/trades", {"symbol": symbol.upper(), "limit": limit})

    async def get_agg_trades(self, symbol: str, limit: int = 500) -> List[Dict[str, Any]]:
        return await self.http._request("GET", "/api/v3/aggTrades", {"symbol": symbol.upper(), "limit": limit})

    async def get_klines(self, symbol: str, interval: str = "1m", limit: int = 500) -> List[List[Any]]:
        return await self.http._request("GET", "/api/v3/klines", {"symbol": symbol.upper(), "interval": interval, "limit": limit})

    async def get_24h_ticker(self, symbol: str) -> Dict[str, Any]:
        return await self.http._request("GET", "/api/v3/ticker/24hr", {"symbol": symbol.upper()})

    async def get_all_24h_tickers(self) -> List[Dict[str, Any]]:
        return await self.http._request("GET", "/api/v3/ticker/24hr")

    async def get_all_symbols(self) -> List[str]:
        data = await self.http._request("GET", "/api/v3/exchangeInfo")
        return [s["symbol"] for s in data["symbols"]]

    async def exchange_info_details(self) -> Dict[str, Any]:
        return await self.http._request("GET", "/api/v3/exchangeInfo")

    # --- Signed Spot & Futures ---
    async def get_account_info(self) -> Dict[str, Any]:
        return await self.http._request("GET", "/api/v3/account", signed=True)

    async def place_order(self, symbol: str, side: str, type_: str, quantity: float, price: Optional[float] = None) -> Dict[str, Any]:
        params = {"symbol": symbol.upper(), "side": side, "type": type_, "quantity": quantity}
        if price:
            params["price"] = price
        return await self.http._request("POST", "/api/v3/order", params=params, signed=True)

    async def futures_position_info(self) -> List[Dict[str, Any]]:
        return await self.http._request("GET", "/fapi/v2/positionRisk", signed=True, futures=True)

    async def get_funding_rate(self, symbol: str, limit: int = 1) -> List[Dict[str, Any]]:
        params = {"symbol": symbol.upper(), "limit": limit}
        return await self.http._request("GET", "/fapi/v1/fundingRate", params=params, futures=True)

    # --- WebSocket ---
    async def ws_subscribe(self, url: str, callback):
        while True:
            try:
                async with websockets.connect(url) as ws:
                    async for msg in ws:
                        data = json.loads(msg)
                        await callback(data)
            except Exception as e:
                LOG.error("WS error: %s", e)
                await asyncio.sleep(5)
                continue

    async def ws_ticker(self, symbol: str, callback):
        url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@ticker"
        await self.ws_subscribe(url, callback)

    async def ws_trades(self, symbol: str, callback):
        url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@trade"
        await self.ws_subscribe(url, callback)

    async def ws_order_book(self, symbol: str, depth: int, callback):
        url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@depth{depth}@100ms"
        await self.ws_subscribe(url, callback)

    # -------------------------------------------------------------
    # Temel + Pro Metrikler ve diğer metotlar...
    # -------------------------------------------------------------

    # -------------------------------------------------------------
    # Temel Metrikler
    # -------------------------------------------------------------
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

    # -------------------------------------------------------------
    # Pro Metrikler
    # -------------------------------------------------------------
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

    # -------------------------------------------------------------
    # Gelişmiş Pro Metrikler
    # -------------------------------------------------------------
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
        spread_val = await self.spread(symbol)
        liquidity = await self.liquidity_score(symbol)
        whale_mom = await self.whale_momentum(symbol)
        taker_score = await self.taker_ratio_score(symbol)
        depth_score = await self.vwap_depth_score(symbol)
        imbalance_score = await self.liquidity_imbalance_score(symbol)
        return {
            "spread": spread_val,
            "liquidity": liquidity,
            "whale_momentum": whale_mom,
            "taker_ratio": taker_score,
            "vwap_depth_score": depth_score,
            "liquidity_imbalance": imbalance_score
        }

    # -------------------------------------------------------------
    # Utils
    # -------------------------------------------------------------
    async def fetch_many(self, func, symbols: List[str], *args, **kwargs) -> Dict[str, Any]:
        tasks = [func(sym, *args, **kwargs) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {s: r for s, r in zip(symbols, results)}


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
