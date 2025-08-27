# jobs/worker_a.py
import asyncio
import time
from utils import binance_api, coinglass_utils, cache, config_worker

async def run_forever():
    # last_run zamanlarını tut
    last_run = {task["name"]: 0 for task in config_worker.WORKER_A_TASKS}

    while True:
        now = time.time()
        try:
            # Her task için interval kontrolü
            for task in config_worker.WORKER_A_TASKS:
                name = task["name"]
                interval = task.get("interval", 10)

                # interval süresi dolmuş mu?
                if now - last_run[name] >= interval:
                    if name == "ticker":
                        tick = binance_api.get_tickers(symbols=config_worker.SYMBOLS)
                        cache.put(
                            "ticker",
                            tick,
                            ttl=config_worker.CACHE_TTL_SECONDS.get("ticker", 20),
                        )

                    elif name == "funding":
                        fund = (
                            coinglass_utils.get_funding_rates(symbols=config_worker.SYMBOLS)
                            if hasattr(coinglass_utils, "get_funding_rates")
                            else {}
                        )
                        cache.put(
                            "funding",
                            fund,
                            ttl=config_worker.CACHE_TTL_SECONDS.get("funding", 28800),
                        )

                    last_run[name] = now

        except Exception as e:
            print("worker_a error", e)

        # Döngü frekansı (çok kısa tutma, CPU yakar)
        await asyncio.sleep(1)
