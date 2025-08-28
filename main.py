# main.py — PTB v20+ Trading Bot Entrypoint (Worker A/B/C, Render Webhook Mode)
import os
import asyncio
import signal
import logging
from telegram.ext import ApplicationBuilder, CommandHandler

from keep_alive import keep_alive
from utils.db import init_db
from utils.monitoring import configure_logging
from utils.config import CONFIG
from utils.handler_loader import load_handlers

from jobs.worker_a import WorkerA
from jobs.worker_b import WorkerB
from jobs.worker_c import WorkerC

# -------------------------------
configure_logging(logging.INFO)
LOG = logging.getLogger("main")

# -------------------------------
async def start(update, context):
    await update.message.reply_text("Bot çalışıyor 🚀")

# -------------------------------
async def async_main():
    LOG.info("Booting bot...")
    init_db()

    token = CONFIG.TELEGRAM.BOT_TOKEN or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        LOG.error("TELEGRAM_BOT_TOKEN is not set. Exiting.")
        return

    app = ApplicationBuilder().token(token).build()
    keep_alive()
    load_handlers(app)
    app.add_handler(CommandHandler("start", start))

    loop = asyncio.get_running_loop()
    kline_queue = asyncio.Queue()

    worker_a = WorkerA(kline_queue, loop=loop)
    worker_c = WorkerC()
    from handlers import signal_handler
    worker_b = WorkerB(queue=kline_queue, signal_callback=signal_handler.publish_signal)

    worker_a.start()
    worker_b.start()
    worker_c.start()

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
    # Webhook setup (Render uyumlu)
    # -------------------------------
    await app.initialize()
    await app.start()
    webhook_url = os.getenv("WEBHOOK_URL", f"https://your-default-url.com/{token}")
    port = int(os.getenv("PORT", 8080))
    await app.bot.set_webhook(webhook_url)
    LOG.info("Webhook set to %s", webhook_url)

    # run_webhook yerine app.updater.start_webhook kullan
    await app.updater.start_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=token,
        webhook_url=webhook_url,
        stop_signals=None
    )

    await stop_event.wait()

    LOG.info("Shutting down...")
    await app.updater.stop()
    await app.stop()
    await app.shutdown()

    worker_a.stop()
    worker_b.stop()
    worker_c.stop()
    await asyncio.sleep(0.5)

    LOG.info("Shutdown complete.")

# -------------------------------
if __name__ == "__main__":
    # Mevcut event loop varsa kullan
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(async_main())
    except RuntimeError:  # loop zaten çalışıyorsa
        asyncio.create_task(async_main())
