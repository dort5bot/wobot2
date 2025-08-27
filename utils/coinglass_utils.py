#coinglass_utils.py
##
#
#tamamen async, rate-limit ve timeout + retry yönetimli

import os
import asyncio
import httpx
from typing import Optional

BASE_URL = "https://open-api-v4.coinglass.com"
API_KEY = os.getenv("COINGLASS_API_KEY")
HEADERS = {"coinglassSecret": API_KEY}

# Rate-limit: saniyede max 5 istek (örnek)
RATE_LIMIT = 5
_semaphore = asyncio.Semaphore(RATE_LIMIT)

# Timeout ve retry ayarları
TIMEOUT = 10  # saniye
MAX_RETRIES = 3

async def _get(path: str, params: Optional[dict] = None):
    """Async API isteği yapan genel fonksiyon, timeout ve retry ile güvenli."""
    url = f"{BASE_URL}{path}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with _semaphore:
                async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                    resp = await client.get(url, headers=HEADERS, params=params)
                    resp.raise_for_status()
                    return resp.json()
        except httpx.RequestError as e:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(1)  # retry öncesi bekle
                continue
            raise RuntimeError(f"API request failed after {MAX_RETRIES} attempts: {e}")
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"HTTP error {e.response.status_code}: {e.response.text}")

# --- FUTURES ---
async def futures_supported_coins(): return await _get("/api/futures/supported-coins")
async def futures_supported_exchange_pairs(): return await _get("/api/futures/supported-exchange-pairs")
async def futures_price_history(pair: str, interval: str = "h1"): return await _get("/api/price/ohlc-history", {"symbol": pair, "interval": interval})
async def futures_liquidation_history(pair: str, interval: str = "h4"): return await _get("/api/futures/liquidation/history", {"pair": pair, "interval": interval})

# --- SPOT ---
async def spot_supported_coins(): return await _get("/api/spot/supported-coins")
async def spot_price_history(symbol: str, interval: str = "h1"): return await _get("/api/spot/price/history", {"symbol": symbol, "interval": interval})

# --- OPTIONS ---
async def option_info(): return await _get("/api/option/info")

# --- ETF ---
async def etf_btc_list(): return await _get("/api/etf/bitcoin/list")
async def etf_btc_flows_history(): return await _get("/api/etf/bitcoin/flow-history")
async def etf_eth_list(): return await _get("/api/etf/ethereum/list")
async def etf_eth_flows_history(): return await _get("/api/etf/ethereum/flow-history")

# --- Örnek Diğer Endpoint ---
async def open_interest_exchange_list(symbol: str): return await _get("/api/futures/openInterest/exchange-list", {"symbol": symbol})
