"""
Production-ready main.py for Render free tier
- Polling only (no webhook, no keep_alive)
- UptimeRobot ping keeps container awake
- WorkerA → WorkerB → WorkerC async lifecycle
- CONFIG dataclass yapısına uyumlu
"""

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
        await worker.start_async()
    except Exception as e:
        LOG.exception(f"{name} crashed: {e}")


async def stop_worker_async(worker, name: str):
    try:
        LOG.info(f"Stopping {name}...")
        await worker.stop_async()
        LOG.info(f"{name} stopped")
    except Exception as e:
        LOG.exception(f"Failed to stop {name}: {e}")


# -----------------------------
# Main app
# -----------------------------
async def main():
    configure_logging()
    init_db()

    # Telegram bot
    token = CONFIG.TELEGRAM.BOT_TOKEN
    if not token:
        LOG.error("TELEGRAM_BOT_TOKEN not set in .env")
        return

    app = ApplicationBuilder().token(token).build()
    load_handlers(app)

    # -----------------------------
    # Shared queue
    # -----------------------------
    queue_a_b = asyncio.Queue()
    queue_b_c = asyncio.Queue()

    # -----------------------------
    # WorkerC (Trade executor)
    # -----------------------------
    worker_c = WorkerC()
    # WorkerB sinyal callback → WorkerC queue'ya gönderir
    async def signal_callback(source, symbol, side, strength=0.0, payload=None):
        await worker_c.send_decision(payload)

    # -----------------------------
    # WorkerB (TA / Signal generator)
    # -----------------------------
    worker_b = WorkerB(queue=queue_a_b, signal_callback=signal_callback)

    # -----------------------------
    # WorkerA (Binance data collector)
    # -----------------------------
    worker_a = WorkerA(queue=queue_a_b)

    workers = [
        (worker_a, "WorkerA"),
        (worker_b, "WorkerB"),
        (worker_c, "WorkerC"),
    ]

    # Start workers
    for w, n in workers:
        asyncio.create_task(start_worker_async(w, n))

    # Telegram polling
    LOG.info("Polling started (Render free + UptimeRobot)")
    await app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
        close_loop=False
    )

    # Wait for shutdown signal
    await stop_event.wait()
    LOG.info("Shutdown triggered")

    # Stop workers
    await asyncio.gather(*(stop_worker_async(w, n) for w, n in workers))
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
