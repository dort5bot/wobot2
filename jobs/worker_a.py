# jobs/worker_a.py
#not: zaman ayarlari confige.work üzerinde 

import asyncio
import time
from utils import binance_api, coinglass_utils, cache, config_worker

async def run_forever():
    """
    Worker A: ticker ve funding verilerini cache’e koyar.
    - ticker: sık güncellenir (örn. 10s)
    - funding: 8 saatte bir güncellenir
    Döngü sleep süresi otomatik ayarlanır (en kısa interval / 5)
    """

    # Her task’in en son çalıştığı zamanı tut
    last_run = {task["name"]: 0 for task in config_worker.WORKER_A_TASKS}

    # Döngü sleep süresi: en kısa interval / 5, minimum 0.5s
    min_interval = min(task.get("interval", 10) for task in config_worker.WORKER_A_TASKS)
    sleep_duration = max(0.5, min_interval / 5)

    while True:
        now = time.time()
        try:
            for task in config_worker.WORKER_A_TASKS:
                name = task["name"]
                interval = task.get("interval", 10)  # ♦️ Task’in kendi interval değeri (config)

                # ♦️ Interval süresi dolduysa task’i çalıştır
                if now - last_run[name] >= interval:

                    if name == "ticker":
                        tick = binance_api.get_tickers(symbols=config_worker.SYMBOLS)
                        cache.put(
                            "ticker",
                            tick,
                            ttl=config_worker.CACHE_TTL_SECONDS.get("ticker", 20),  # ♦️ Cache TTL ticker
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
                            ttl=config_worker.CACHE_TTL_SECONDS.get("funding", 8*3600),  # ♦️ Cache TTL funding: 8 saat
                        )

                    last_run[name] = now

        except Exception as e:
            print("worker_a error", e)

        # ♦️ Döngü sleep süresi otomatik, CPU verimli
        await asyncio.sleep(sleep_duration)
        
