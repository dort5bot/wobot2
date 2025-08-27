# jobs/worker_b.py
# async uyumlu
# Worker B: Sinyalleri değerlendirir, risk kontrolü yapar ve order gönderir.
# 1. config_worker.WORKER_B_INTERVAL üzerinden interval ayarlanabilir
# 2. Döngü sleep süresi CPU dostu, interval’in 1/5’i kadar bekler

import asyncio
import time
from utils import cache, config_worker
from utils.binance_api import BinanceClient
from utils import signal_evaluator, risk_manager, order_manager, data_provider as dp

api = BinanceClient()  # Singleton instance

async def evaluate_and_place_orders():
    """
    Tüm semboller için:
    - ticker ve funding verilerini alır
    - signal_evaluator ile sinyal oluşturur
    - risk kontrolünden geçirir
    - uygun order'ı gönderir
    """
    try:
        # Async veri çekme
        tickers = await api.get_all_24h_tickers()
        funding = await dp.get_funding(config_worker.SYMBOLS)  # async fonksiyon olmalı

        ctx = {
            "tickers": tickers,
            "funding": funding,
        }

        # Sinyal hesaplama (async ise await ekle)
        signals = await signal_evaluator.evaluate(ctx)

        # Cache'e koy
        cache.put("signals", signals, ttl=config_worker.CACHE_TTL_SECONDS.get("signals", 10))

        # Risk kontrolü ve order
        for symbol, sig in signals.items():
            if sig.get("action") in ("BUY", "SELL") and risk_manager.check(sig, ctx):
                await order_manager.place_order(sig)  # async ise await, değilse kaldır
    except Exception as e:
        print("worker_b evaluate_and_place_orders error:", e)


async def run_forever():
    """
    Worker B main loop
    """
    interval = getattr(config_worker, "WORKER_B_INTERVAL", 5)  # default 5s
    last_run = 0
    sleep_duration = max(0.5, interval / 5)

    while True:
        now = time.time()
        try:
            if now - last_run >= interval:
                await evaluate_and_place_orders()
                last_run = now
        except Exception as e:
            print("worker_b main loop error:", e)

        await asyncio.sleep(sleep_duration)


if __name__ == "__main__":
    asyncio.run(run_forever())
