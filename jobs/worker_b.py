# jobs/worker_b.py
#1. config_worker.WORKER_B_INTERVAL üzerinden interval ayarlanabiliyor.
#2. Döngü sleep süresi otomatik → CPU dostu, interval’in 1/5’i kadar bekliyor.
    

import asyncio
import time
from utils import data_provider as dp, risk_manager, order_manager, signal_evaluator, config_worker

async def run_forever():
    """
    Worker B: Sinyalleri değerlendirir ve uygun order’ları gönderir.
    - Interval config üzerinden alınır
    - Döngü sleep süresi CPU dostu
    """

    # ♦️ Worker B interval (config)
    interval = getattr(config_worker, "WORKER_B_INTERVAL", 5)  # varsayılan 5s
    last_run = 0

    # Döngü sleep süresi: interval / 5, minimum 0.5s
    sleep_duration = max(0.5, interval / 5)

    while True:
        now = time.time()
        try:
            if now - last_run >= interval:
                # ♦️ Context oluştur
                ctx = {
                    "ticker": dp.get_tickers(config_worker.SYMBOLS),
                    "funding": dp.get_funding(config_worker.SYMBOLS),
                }

                # ♦️ Sinyal değerlendirme
                sig = signal_evaluator.evaluate(ctx)

                # ♦️ Risk kontrolü ve order
                if sig.get("action") in ("BUY", "SELL") and risk_manager.check(sig, ctx):
                    order_manager.place_order(sig)

                last_run = now

        except Exception as e:
            print("worker_b error", e)

        # ♦️ Döngü sleep, CPU dostu
        await asyncio.sleep(sleep_duration)
        
