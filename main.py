# main.py — PTB v20+ Trading Bot Entrypoint (Worker A/B/C, Render Webhook Mode)
# CONFIG’e dokunmadan .env üzerinden hem port hem keepalive URL alir

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
    load_handlers(app)

    loop = asyncio.get_running_loop()
    kline_queue = asyncio.Queue()

    # Workers
    worker_a = WorkerA(kline_queue, loop=loop)
    worker_c = WorkerC()

    from handlers import signal_handler
    worker_b = WorkerB(
        queue=kline_queue,
        signal_callback=signal_handler.publish_signal
    )

    # Start workers
    worker_a.start()
    worker_b.start()
    worker_c.start()

    # Graceful shutdown event
    stop_event = asyncio.Event()

    def _request_shutdown(signame: str):
        LOG.warning("Signal %s received, shutting down...", signame)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_shutdown, sig.name)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _request_shutdown(sig.name))

    # -------------------------------
    # Webhook setup (instead of polling)
    # ⚡ .env’den keepalive URL ve port alıyoruz
    # -------------------------------
    await app.initialize()
    await app.start()

    keepalive_url = os.getenv("KEEPALIVE_URL")
    if not keepalive_url:
        LOG.error("KEEPALIVE_URL is not set in .env")
        return

    port = int(os.getenv("PORT", 8000))  # ⚡ .env’den port al, default 8000

    webhook_url = f"{keepalive_url}/{token}"
    await app.bot.set_webhook(webhook_url)
    LOG.info("Webhook set to %s", webhook_url)

    await app.run_webhook(
        listen="0.0.0.0",
        port=port,
        webhook_url=webhook_url,
        stop_signals=None,  # biz kendimiz stop_event ile kontrol ediyoruz
    )

    # -------------------------------
    await stop_event.wait()

    LOG.info("Shutting down...")

    await app.stop()
    await app.shutdown()

    worker_a.stop()
    worker_b.stop()
    worker_c.stop()
    await asyncio.sleep(0.5)  # workers cancel gracefully

    LOG.info("Shutdown complete.")

# -------------------------------
if __name__ == "__main__":
    asyncio.run(async_main())
