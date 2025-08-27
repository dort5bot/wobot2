# utils/cmc_api.py
#coinmarkercap

import aiohttp
import asyncio
import logging
from typing import Dict, Any, List, Optional

LOG = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())

CMC_API_KEY = "729c1052-b2d5-4011-85ff-6694f32259a3"
BASE_URL = "https://pro-api.coinmarketcap.com/v1"

HEADERS = {
    "Accepts": "application/json",
    "X-CMC_PRO_API_KEY": CMC_API_KEY
}

# -------------------------
# Async HTTP GET request
# -------------------------
async def _get(endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(f"{BASE_URL}{endpoint}", params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    LOG.error(f"CMC API Error {resp.status}: {await resp.text()}")
    except Exception as e:
        LOG.exception(f"CMC API Exception: {e}")
    return None

# -------------------------
# Latest Listings (Top Coins)
# -------------------------
async def get_latest_listings(limit: int = 50, start: int = 1) -> Optional[List[Dict[str, Any]]]:
    params = {
        "start": start,
        "limit": limit,
        "convert": "USD"
    }
    data = await _get("/cryptocurrency/listings/latest", params)
    if data and "data" in data:
        return data["data"]
    return None

# -------------------------
# Coin Quote by Symbol or ID
# -------------------------
async def get_coin_quote(symbol: Optional[str] = None, coin_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    params = {
        "convert": "USD"
    }
    if symbol:
        params["symbol"] = symbol.upper()
    elif coin_id:
        params["id"] = str(coin_id)
    else:
        return None
    data = await _get("/cryptocurrency/quotes/latest", params)
    if data and "data" in data:
        return data["data"]
    return None

# -------------------------
# Global Market Metrics
# -------------------------
async def get_global_metrics() -> Optional[Dict[str, Any]]:
    data = await _get("/global-metrics/quotes/latest")
    if data and "data" in data:
        return data["data"]
    return None

# -------------------------
# Coin Info
# -------------------------
async def get_coin_info(symbol: Optional[str] = None, coin_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    params = {}
    if symbol:
        params["symbol"] = symbol.upper()
    elif coin_id:
        params["id"] = str(coin_id)
    else:
        return None
    data = await _get("/cryptocurrency/info", params)
    if data and "data" in data:
        return data["data"]
    return None

# -------------------------
# Örnek: Bot handler ile kullanım
# -------------------------
async def example_usage():
    listings = await get_latest_listings(limit=10)
    print("Top 10 Coins:", listings)

    btc_quote = await get_coin_quote(symbol="BTC")
    print("BTC Quote:", btc_quote)

    metrics = await get_global_metrics()
    print("Global Metrics:", metrics)

    eth_info = await get_coin_info(symbol="ETH")
    print("ETH Info:", eth_info)

# Test
if __name__ == "__main__":
    asyncio.run(example_usage())
