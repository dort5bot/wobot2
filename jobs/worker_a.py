# jobs/worker_a.py
#not: zaman ayarlari confige.work üzerinde 
# asyn uyumlu

import asyncio
import time
from utils.binance_api import BinanceClient
from utils import coinglass_utils, cache, config_worker

api = BinanceClient()  # Singleton instance

async def fetch_tickers():
    """
    Tüm 24h ticker bilgilerini alır ve cache'e koyar.
    """
    try:
        tickers = await api.get_all_24h_tickers()
        cache.put(
            "ticker",
            tickers,
            ttl=config_worker.CACHE_TTL_SECONDS.get("ticker", 20),
        )
    except Exception as e:
        print("worker_a ticker error", e)


async def fetch_funding():
    """
    Funding verilerini alır ve cache'e koyar.
    """
    try:
        if hasattr(coinglass_utils, "get_funding_rates"):
            fund = await coinglass_utils.get_funding_rates(symbols=config_worker.SYMBOLS)
            cache.put(
                "funding",
                fund,
                ttl=config_worker.CACHE_TTL_SECONDS.get("funding", 8*3600),
            )
    except Exception as e:
        print("worker_a funding error", e)


async def run_forever():
    """
    Worker A: ticker ve funding verilerini cache’e koyar.
    """
    last_run = {task["name"]: 0 for task in config_worker.WORKER_A_TASKS}
    min_interval = min(task.get("interval", 10) for task in config_worker.WORKER_A_TASKS)
    sleep_duration = max(0.5, min_interval / 5)

    while True:
        now = time.time()
        try:
            for task in config_worker.WORKER_A_TASKS:
                name = task["name"]
                interval = task.get("interval", 10)

                if now - last_run[name] >= interval:
                    if name == "ticker":
                        await fetch_tickers()
                    elif name == "funding":
                        await fetch_funding()
                    last_run[name] = now
        except Exception as e:
            print("worker_a error", e)

        await asyncio.sleep(sleep_duration)
