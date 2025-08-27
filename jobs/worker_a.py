# jobs/worker_a.py
# Worker A: ticker ve funding verilerini cache’e koyar (async)
# Config üzerinden interval ve sembol listesi kontrolü

import asyncio
import time
import logging
from utils.binance_api import BinanceClient
from utils import coinglass_utils, cache, config_worker

LOG = logging.getLogger("worker_a")
api = BinanceClient()  # Singleton instance

async def fetch_tickers():
    """Tüm 24h ticker bilgilerini alır ve cache'e koyar."""
    try:
        tickers = await api.get_all_24h_tickers()
        cache.put("ticker", tickers, ttl=config_worker.CACHE_TTL_SECONDS.get("ticker", 20))
        LOG.info("Ticker cache güncellendi (%d symbols).", len(tickers))
    except Exception as e:
        LOG.error("Worker A ticker error", exc_info=True)

async def fetch_funding():
    """Funding verilerini alır ve cache'e koyar."""
    try:
        if hasattr(coinglass_utils, "get_funding_rates"):
            fund = await coinglass_utils.get_funding_rates(symbols=config_worker.SYMBOLS)
            cache.put("funding", fund, ttl=config_worker.CACHE_TTL_SECONDS.get("funding", 8*3600))
            LOG.info("Funding cache güncellendi (%d symbols).", len(fund))
    except Exception as e:
        LOG.error("Worker A funding error", exc_info=True)

async def run_forever():
    """Worker A main loop."""
    last_run = {task["name"]: 0 for task in config_worker.WORKER_A_TASKS}
    min_interval = min(task.get("interval", 10) for task in config_worker.WORKER_A_TASKS)
    sleep_duration = max(0.5, min_interval / 5)

    LOG.info("Worker A başlatıldı.")
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
            LOG.error("Worker A main loop error", exc_info=True)

        await asyncio.sleep(sleep_duration)
