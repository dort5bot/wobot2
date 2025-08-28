"""
*keep_alive.py olmadığı için main.py sadece polling çalıştırıyor.
Render URL (ör. https://wobot1.onrender.com/) boş 200 dönecek.
Bu boş cevap UptimeRobot için yeterli.

Render free + UptimeRobot uyumlu
- PTB v20+ → polling-only
- Python 3.11/3.13 uyumlu
- nest_asyncio patch
Production-ready main.py for Render free tier
- Polling only (no webhook, no keep_alive)
- UptimeRobot ping keeps container awake
- WorkerA → WorkerB → WorkerC async lifecycle
"""

import os
import logging
import nest_asyncio
import asyncio
import signal
from contextlib import suppress

from telegram import Update
from telegram.ext import ApplicationBuilder

from utils.db import init_db
from utils.monitoring import configure_logging
from utils.handler_loader import load_handlers
from utils.config import CONFIG

from jobs.worker_a import WorkerA
from jobs.worker_b import WorkerB
from jobs.worker_c import WorkerC


# -----------------------------
# Global vars
# -----------------------------
LOG = logging.getLogger(__name__)
nest_asyncio.apply()
stop_event = asyncio.Event()


# -----------------------------
# Worker lifecycle helpers
# -----------------------------
async def start_worker_async(worker, name: str):
    try:
        LOG.info(f"Starting {name}...")
        await asyncio.to_thread(worker.start)
    except Exception as e:
        LOG.exception(f"{name} crashed: {e}")


async def stop_worker(worker, name: str):
    try:
        LOG.info(f"Stopping {name}...")
        await asyncio.to_thread(worker.stop)
        LOG.info(f"{name} stopped")
    except Exception as e:
        LOG.exception(f"Failed to stop {name}: {e}")


# -----------------------------
# Main app
# -----------------------------
async def main():
    configure_logging()

    # init_db async değil → direkt çağır
    init_db()

    # Load handlers
    token = CONFIG["TELEGRAM"]["TOKEN"]
    app = ApplicationBuilder().token(token).build()
    load_handlers(app)

    #gerekli mi
    LOG.info("Bot started")
    await app.run_polling()

    LOG.info("Bot stopped")

    # Workers
    worker_a = WorkerA()
    worker_b = WorkerB()
    worker_c = WorkerC()
    workers = [
        (worker_a, "WorkerA"),
        (worker_b, "WorkerB"),
        (worker_c, "WorkerC"),
    ]

    # Start workers
    for w, n in workers:
        asyncio.create_task(start_worker_async(w, n))

    # Polling (no webhook!)
    LOG.info("Polling started (Render free + UptimeRobot)")
    await app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
        close_loop=False
    )

    # Wait for shutdown
    await stop_event.wait()
    LOG.info("Shutdown triggered")

    # Stop workers
    await asyncio.gather(*(stop_worker(w, n) for w, n in workers))
    LOG.info("All systems stopped")


# -----------------------------
# Entrypoint
# -----------------------------
if __name__ == "__main__":
    loop = asyncio.get_event_loop()

    # Shutdown signals
    def _signal_handler(*_):
        LOG.info("Received shutdown signal")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, _signal_handler)

    LOG.info("Booting bot...")
    try:
        loop.run_until_complete(main())
    finally:
        LOG.info("Bot stopped")

