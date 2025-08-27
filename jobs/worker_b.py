# jobs/worker_b.py
# async uyumlu, PTB v20+ uyumlu 
# 1. config_worker.WORKER_B_INTERVAL üzerinden interval ayarlanabilir
# 2. Döngü sleep süresi CPU dostu, interval’in 1/5’i kadar bekler
# Worker B: Sinyalleri değerlendirir, risk kontrolü yapar ve order gönderir.

import asyncio
import time
import logging
from utils import cache, config_worker, signal_evaluator, risk_manager, data_provider as dp
from utils.binance_api import BinanceClient
from utils.order_manager import OrderManager

LOG = logging.getLogger("worker_b")
api = BinanceClient()  # Singleton
sevaluator = signal_evaluator.SignalEvaluator()
order_manager = OrderManager()  # async uyumlu

sevaluator.start()  # background loop başlat

async def evaluate_and_place_orders():
    try:
        # Worker A cache kullanımı → API çağrısını azalt
        tickers = cache.get_latest("ticker")
        if not tickers:
            LOG.warning("Ticker cache boş, API çağrısı yapılıyor.")
            tickers = await api.get_all_24h_tickers()

        funding = cache.get_latest("funding") or dp.get_funding(config_worker.SYMBOLS)
        ctx = {"tickers": tickers, "funding": funding}

        # Sinyal hesaplama (async)
        signals = await sevaluator.evaluate(ctx)

        # Cache’e yaz
        cache.put("signals", signals, ttl=config_worker.CACHE_TTL_SECONDS.get("signals", 10))
        LOG.info("Signals cache güncellendi (%d symbols).", len(signals))

        # Risk kontrolü ve order gönderimi
        for symbol, sig in signals.items():
            if sig.get("decision") in ("BUY", "SELL") and risk_manager.check(sig, ctx):
                await order_manager.process_decision(sig)
                LOG.info("Order işleme: %s %s", sig.get("decision"), symbol)

    except Exception as e:
        LOG.error("Worker B evaluate_and_place_orders error:", exc_info=True)

async def run_forever():
    interval = getattr(config_worker, "WORKER_B_INTERVAL", 5)
    last_run = 0
    sleep_duration = max(0.5, interval / 5)

    LOG.info("Worker B başlatıldı.")
    while True:
        now = time.time()
        try:
            if now - last_run >= interval:
                await evaluate_and_place_orders()
                last_run = now
        except Exception as e:
            LOG.error("Worker B main loop error:", exc_info=True)

        await asyncio.sleep(sleep_duration)
