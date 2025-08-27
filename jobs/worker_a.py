import asyncio, time
from utils import binance_api, coinglass_utils, cache, config_worker
async def run_forever():
    while True:
        try:
            tick = binance_api.get_tickers(symbols=config_worker.SYMBOLS)
            cache.put("ticker", tick, ttl=config_worker.CACHE_TTL_SECONDS.get("ticker",20))
            fund = coinglass_utils.get_funding_rates(symbols=config_worker.SYMBOLS) if hasattr(coinglass_utils, "get_funding_rates") else {}
            cache.put("funding", fund, ttl=config_worker.CACHE_TTL_SECONDS.get("funding",180))
        except Exception as e:
            print("worker_a error", e)
        await asyncio.sleep(1)