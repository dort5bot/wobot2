# main.py
"""
Production-ready main.py for WorkerA→WorkerB→WorkerC→WorkerD chain + Telegram bot
Adapted for Render / nest_asyncio / python-telegram-bot v20+ environments
Shutdown signal, worker lifecycle ve polling yapısı 3.11/3.13 uyumlu.
keep_alive.py eklendi ve main.py içinde asyncio.create_task(start_keepalive()) çağrıldı.
    Render seni web service olarak görecek → UptimeRobot GET / ping attığında bot hep uyanık kalacak.
"""

import os
import logging
import nest_asyncio
import asyncio
from contextlib import suppress
import signal

from telegram.ext import ApplicationBuilder

from utils.db import init_db
from utils.monitoring import configure_logging
from utils.handler_loader import load_handlers
from utils.config import CONFIG

from jobs.worker_a import WorkerA
from jobs.worker_b import WorkerB
from jobs.worker_c import WorkerC
from jobs.worker_d import WorkerD   # 🔹 WorkerD eklendi

# 🔹 Keep-alive import
from keep_alive import start_keepalive

# -----------------------------
# Patch mevcut event loop
# -----------------------------
nest_asyncio.apply()

# -----------------------------
# Logging
# -----------------------------
configure_logging(logging.INFO)
LOG = logging.getLogger("main")

# -----------------------------
# Worker setup
# -----------------------------
async def setup_workers():
    queue_raw = asyncio.Queue()
    worker_a = WorkerA(queue_raw)
    worker_c = WorkerC()

    # WorkerB callback
    async def signal_callback(source: str, symbol: str, side: str, strength: float, payload: dict):
        LOG.info("Signal from %s: %s %s (strength=%.2f)", source, symbol, side, strength)
        await worker_c.send_decision(payload)

    # WorkerD → trading signal callback
    async def trading_signal_callback(signal_data: dict):
        LOG.info("Trading signal: %s", signal_data)
        payload = {
            'type': 'trading_signal',
            'data': signal_data,
            'source': 'worker_d'
        }
        await worker_c.send_decision(payload)

    worker_b = WorkerB(queue_raw, signal_callback=signal_callback)
    worker_d = WorkerD(signal_callback=trading_signal_callback)

    return worker_a, worker_b, worker_c, worker_d

# -----------------------------
# Worker lifecycle helpers
# -----------------------------
async def start_worker(worker, name: str):
    LOG.info("Starting %s...", name)
    try:
        await worker.start_async()
    except Exception:
        LOG.exception("Failed to start %s", name)

async def stop_worker(worker, name: str):
    LOG.info("Stopping %s...", name)
    try:
        await worker.stop_async()
    except Exception:
        LOG.exception("Error stopping %s", name)

# -----------------------------
# Async Main
# -----------------------------
async def main():
    LOG.info("Boot sequence started")
    init_db()

    # 🔹 HTTP keep-alive başlat
    asyncio.create_task(start_keepalive())
    LOG.info("Keep-alive server started")

    token = CONFIG.TELEGRAM.BOT_TOKEN or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        LOG.error("Telegram BOT_TOKEN missing")
        return

    # Telegram app oluştur
    app = ApplicationBuilder().token(token).build()

    # Handler yükle
    load_handlers(app)

    # Workerları başlat
    worker_a, worker_b, worker_c, worker_d = await setup_workers()
    workers = [
        (worker_a, "WorkerA"),
        (worker_b, "WorkerB"),
        (worker_c, "WorkerC"),
        (worker_d, "WorkerD")
    ]

    stop_event = asyncio.Event()

    # -----------------------------
    # Signal callback (Linux/Windows uyumlu)
    # -----------------------------
    def _shutdown(sig=None):
        LOG.warning("Shutdown signal %s received", getattr(sig, 'name', str(sig)))
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _shutdown, sig)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _shutdown(sig))

    # Start workers
    await asyncio.gather(*(start_worker(w, n) for w, n in workers))
    LOG.info("All workers started")

    # -----------------------------
    # Start Telegram polling (tek satır)
    # -----------------------------
    polling_task = asyncio.create_task(app.run_polling(close_loop=False))
    LOG.info("Polling started")

    # Wait for shutdown signal
    await stop_event.wait()
    LOG.info("Shutdown triggered")

    # Cancel polling
    polling_task.cancel()
    with suppress(asyncio.CancelledError):
        await polling_task

    # Stop workers
    await asyncio.gather(*(stop_worker(w, n) for w, n in workers))

    LOG.info("All systems stopped")

# -----------------------------
# Entry point
# -----------------------------
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
