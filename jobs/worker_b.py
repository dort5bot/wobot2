# jobs/worker_b.py
# async uyumlu, PTB v20+ uyumlu 
# Worker B: Sinyalleri değerlendirir, risk kontrolü yapar ve order gönderir.
# 1. config_worker.WORKER_B_INTERVAL üzerinden interval ayarlanabilir
# 2. Döngü sleep süresi CPU dostu, interval’in 1/5’i kadar bekler

import asyncio
import time
from utils import cache, config_worker
from utils.binance_api import BinanceClient
from utils import signal_evaluator, risk_manager, data_provider as dp
from utils.order_manager import OrderManager

api = BinanceClient()  # Singleton instance
sevaluator = signal_evaluator.SignalEvaluator()
sevaluator.start()  # background loop
order_manager = OrderManager()  # async uyumlu OrderManager instance

async def evaluate_and_place_orders():
    try:
        # Binance verileri
        tickers = await api.get_all_24h_tickers()
        # funding bilgisi (senkron, await gerekmez)
        funding = dp.get_funding(config_worker.SYMBOLS)

        ctx = {"tickers": tickers, "funding": funding}

        # Sinyal hesaplama (async)
        signals = await sevaluator.evaluate(ctx)

        # Cache’e yaz
        cache.put("signals", signals, ttl=config_worker.CACHE_TTL_SECONDS.get("signals", 10))

        # Risk kontrolü ve order gönderimi
        for symbol, sig in signals.items():
            if sig.get("decision") in ("BUY", "SELL") and risk_manager.check(sig, ctx):
                await order_manager.process_decision(sig)

    except Exception as e:
        print("worker_b evaluate_and_place_orders error:", e)

async def run_forever():
    interval = getattr(config_worker, "WORKER_B_INTERVAL", 5)
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
