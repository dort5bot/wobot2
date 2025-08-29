# main.py
"""
Production-ready main.py for WorkerA→WorkerB→WorkerC chain + Telegram bot
Adapted for Render / nest_asyncio / python-telegram-bot v20+ environments
Shutdown signal, worker lifecycle ve polling yapısı 3.11/3.13 uyumlu.
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

    async def signal_callback(source: str, symbol: str, side: str, strength: float, payload: dict):
        LOG.info("Signal from %s: %s %s (strength=%.2f)", source, symbol, side, strength)
        await worker_c.send_decision(payload)

    worker_b = WorkerB(queue_raw, signal_callback=signal_callback)
    return worker_a, worker_b, worker_c

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

    token = CONFIG.TELEGRAM.BOT_TOKEN or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        LOG.error("Telegram BOT_TOKEN missing")
        return

    # Telegram app oluştur
    app = ApplicationBuilder().token(token).build()

    # Handler yükle
    load_handlers(app)

    # Workerları başlat
    worker_a, worker_b, worker_c = await setup_workers()
    workers = [(worker_a, "WorkerA"), (worker_b, "WorkerB"), (worker_c, "WorkerC")]

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
