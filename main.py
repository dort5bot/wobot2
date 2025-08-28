# main.py — PTB v20+ Trading Bot Entrypoint (Worker A/B/C, Render Webhook Mode)
# main.py — PTB v20+ Trading Bot Entrypoint (Prod-ready, Worker A/B/C, Webhook Mode)
# CONFIG’e dokunmadan .env üzerinden port ve keepalive URL alır
# Flask gerekmiyor, sadece webhook ve UptimeRobot ping ile keep-alive

import asyncio
import logging
import os
import signal
from telegram.ext import ApplicationBuilder

from utils.db import init_db
from utils.monitoring import configure_logging
from utils.handler_loader import load_handlers
from utils.config import CONFIG
from jobs.worker_a import WorkerA
from jobs.worker_b import WorkerB
from jobs.worker_c import WorkerC

# -------------------------------
configure_logging(logging.INFO)
LOG = logging.getLogger("main")

# -------------------------------
async def uptime_ping_task(url: str, interval: int = 300):
    """
    UptimeRobot ping simülasyonu için async task.
    interval: saniye
    """
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            try:
                r = await client.get(url)
                LOG.debug("Pinged Uptime URL, status=%s", r.status_code)
            except Exception as e:
                LOG.warning("Uptime ping failed: %s", e)
            await asyncio.sleep(interval)

# -------------------------------
async def async_main():
    LOG.info("Booting bot...")
    init_db()

    token = CONFIG.TELEGRAM.BOT_TOKEN
    if not token:
        LOG.error("TELEGRAM_BOT_TOKEN is not set. Exiting.")
        return

    # PTB app
    app = ApplicationBuilder().token(token).build()

    # Load handlers
    load_handlers(app)

    # Queue shared for WorkerA -> WorkerB
    kline_queue = asyncio.Queue()

    # Workers
    worker_a = WorkerA(kline_queue)
    worker_c = WorkerC()
    from handlers import signal_handler
    worker_b = WorkerB(queue=kline_queue, signal_callback=signal_handler.publish_signal)

    # -------------------------------
    # Graceful shutdown event
    stop_event = asyncio.Event()

    def _request_shutdown(signame: str):
        LOG.warning("Signal %s received, shutting down...", signame)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _request_shutdown, sig.name)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _request_shutdown(sig.name))

    # -------------------------------
    # Start workers as tasks
    async def start_worker(worker, name):
        try:
            LOG.info("Starting %s...", name)
            await worker.start_async()
        except Exception as e:
            LOG.exception("%s failed to start: %s", name, e)

    worker_tasks = [
        asyncio.create_task(start_worker(worker_a, "WorkerA")),
        asyncio.create_task(start_worker(worker_b, "WorkerB")),
        asyncio.create_task(start_worker(worker_c, "WorkerC")),
    ]

    # -------------------------------
    # Webhook setup
    keepalive_url = os.getenv("KEEPALIVE_URL")
    if not keepalive_url:
        LOG.error("KEEPALIVE_URL is not set in .env")
        return

    port = int(os.getenv("PORT", 8000))
    webhook_url = f"{keepalive_url}/{token}"

    LOG.info("Initializing PTB app...")
    await app.initialize()
    await app.start()
    await app.bot.set_webhook(webhook_url)
    LOG.info("Webhook set to %s", webhook_url)

    # -------------------------------
    # Start Uptime ping task (async)
    ping_task = asyncio.create_task(uptime_ping_task(keepalive_url, interval=300))
    LOG.info("Uptime ping task started (interval=300s)")

    # -------------------------------
    # Run PTB webhook
    LOG.info("Running webhook...")
    webhook_task = asyncio.create_task(
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            webhook_url=webhook_url,
            stop_signals=None,
            close_loop=False
        )
    )

    # Wait for shutdown signal
    await stop_event.wait()
    LOG.info("Stop event triggered. Shutting down...")

    # -------------------------------
    # Cancel workers
    for worker in (worker_a, worker_b, worker_c):
        try:
            LOG.info("Stopping %s...", worker.__class__.__name__)
            await worker.stop_async()
        except Exception as e:
            LOG.warning("Error stopping %s: %s", worker.__class__.__name__, e)

    for task in worker_tasks:
        task.cancel()

    # -------------------------------
    # Cancel ping & webhook tasks
    ping_task.cancel()
    webhook_task.cancel()
    try:
        await ping_task
    except asyncio.CancelledError:
        LOG.info("Ping task cancelled.")
    try:
        await webhook_task
    except asyncio.CancelledError:
        LOG.info("Webhook task cancelled.")

    # PTB shutdown
    LOG.info("Stopping PTB app...")
    await app.stop()
    await app.shutdown()

    LOG.info("Shutdown complete.")

# -------------------------------
if __name__ == "__main__":
    try:
        asyncio.run(async_main())
    except RuntimeError as e:
        LOG.error("Event loop error: %s", e)

