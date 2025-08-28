#main.py
import logging
import asyncio
import signal
from contextlib import suppress

from telegram.ext import ApplicationBuilder

from utils.db import init_db
from utils.monitoring import configure_logging
from utils.handler_loader import load_handlers
from utils.config import CONFIG

from jobs.worker_a import WorkerA
from jobs.worker_b import WorkerB
from jobs.worker_c import WorkerC

LOG = logging.getLogger(__name__)
stop_event = asyncio.Event()

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

async def main():
    configure_logging()
    init_db()

    token = CONFIG["TELEGRAM"]["TOKEN"]
    app = ApplicationBuilder().token(token).build()
    load_handlers(app)

    # Workers
    worker_a = WorkerA()
    worker_b = WorkerB()
    worker_c = WorkerC()
    workers = [(worker_a, "WorkerA"), (worker_b, "WorkerB"), (worker_c, "WorkerC")]

    # Başlat
    for w, n in workers:
        asyncio.create_task(start_worker_async(w, n))

    LOG.info("Bot started & Polling (Render free + UptimeRobot)")
    await app.run_polling(drop_pending_updates=True, allowed_updates=None)
    LOG.info("Bot stopped")

    # Stop workers
    await asyncio.gather(*(stop_worker(w, n) for w, n in workers))
    LOG.info("All workers stopped")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    def _signal_handler(*_):
        LOG.info("Received shutdown signal")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, _signal_handler)

    LOG.info("Booting bot...")
    loop.run_until_complete(main())
