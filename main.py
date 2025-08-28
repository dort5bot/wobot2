# main.py — PTB v20+ Trading Bot Entrypoint (Worker A/B/C, Render Webhook Mode)
# CONFIG’e dokunmadan .env üzerinden hem port hem keepalive URL alir
# flasksiz, sadece webhook ve keepalive kullanılıyor.

import asyncio
import signal
import logging
import os
from telegram.ext import ApplicationBuilder

from keep_alive import keep_alive
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
async def async_main():
    LOG.info("Booting bot...")
    init_db()

    token = CONFIG.TELEGRAM.BOT_TOKEN
    if not token:
        LOG.error("TELEGRAM_BOT_TOKEN is not set. Exiting.")
        return

    # PTB app
    app = ApplicationBuilder().token(token).build()

    # Keep-alive webserver (Render ping)
    keep_alive()

    # Load handlers
    load_handlers(app)

    # Queue shared for WorkerA -> WorkerB
    kline_queue = asyncio.Queue()

    # Workers
    worker_a = WorkerA(kline_queue)
    worker_c = WorkerC()

    from handlers import signal_handler
    worker_b = WorkerB(queue=kline_queue, signal_callback=signal_handler.publish_signal)

    # Start workers
    LOG.info("Starting WorkerA...")
    worker_a.start()
    LOG.info("Starting WorkerB...")
    worker_b.start()
    LOG.info("Starting WorkerC...")
    worker_c.start()

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
    # Webhook setup
    keepalive_url = os.getenv("KEEPALIVE_URL")
    if not keepalive_url:
        LOG.error("KEEPALIVE_URL is not set in .env")
        return

    port = int(os.getenv("PORT", 8000))
    webhook_url = f"{keepalive_url}/{token}"

    # PTB async başlatma
    LOG.info("Initializing PTB app...")
    await app.initialize()
    await app.start()
    await app.bot.set_webhook(webhook_url)
    LOG.info("Webhook set to %s", webhook_url)

    # run_webhook, Render uyumlu close_loop=False
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

    # Stop-event bekleme
    await stop_event.wait()
    LOG.info("Stop event triggered. Shutting down...")

    # Workers stop
    LOG.info("Stopping WorkerA...")
    worker_a.stop()
    LOG.info("Stopping WorkerB...")
    worker_b.stop()
    LOG.info("Stopping WorkerC...")
    worker_c.stop()
    await asyncio.sleep(0.5)  # workers cancel gracefully

    # PTB stop/shutdown
    LOG.info("Stopping PTB app...")
    await app.stop()
    await app.shutdown()

    # Cancel webhook task
    webhook_task.cancel()
    try:
        await webhook_task
    except asyncio.CancelledError:
        LOG.info("Webhook task cancelled.")

    LOG.info("Shutdown complete.")

# -------------------------------
if __name__ == "__main__":
    try:
        asyncio.run(async_main())
    except RuntimeError as e:
        logging.getLogger("main").error("Event loop error: %s", e)
        
