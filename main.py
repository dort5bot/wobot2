# main.py
import asyncio
import logging
import os
import signal
from typing import List, Optional

from aiohttp import web
from telegram.ext import ApplicationBuilder

from utils.handler_loader import load_handlers
from jobs.worker_a import run_forever as worker_a_run
from jobs.worker_b import run_forever as worker_b_run

# -------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
LOG = logging.getLogger("main")

DEFAULT_PORT = 8080
TELEGRAM_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"

# -------------------------------
# Keep-alive / Health webserver
async def handle_root(request):
    return web.Response(text="Bot is alive!")

async def handle_ready(request):
    return web.Response(text="ready")

async def start_web_server(port: int) -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/ready", handle_ready)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    LOG.info("Web server started on port %d", port)
    return runner

# -------------------------------
async def shutdown(
    app: Optional[object],
    runner: Optional[web.AppRunner],
    tasks: List[asyncio.Task]
):
    LOG.info("Shutting down...")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    if app:
        try:
            await app.stop()
            await app.shutdown()
        except Exception as e:
            LOG.exception("PTB shutdown error: %s", e)

    if runner:
        try:
            await runner.cleanup()
        except Exception as e:
            LOG.exception("Webserver cleanup error: %s", e)

    LOG.info("Shutdown complete.")

# -------------------------------
async def async_main():
    LOG.info("Starting orchestrator...")

    token = os.getenv(TELEGRAM_TOKEN_ENV)
    if not token:
        LOG.error("%s env not set! Exiting.", TELEGRAM_TOKEN_ENV)
        return

    application = ApplicationBuilder().token(token).build()
    try:
        load_handlers(application)
    except Exception as e:
        LOG.exception("Handler load error: %s", e)

    worker_tasks = [
        asyncio.create_task(worker_a_run(), name="worker_a"),
        asyncio.create_task(worker_b_run(), name="worker_b")
    ]

    port = int(os.getenv("PORT", DEFAULT_PORT))
    web_runner = await start_web_server(port)

    stop_event = asyncio.Event()

    def _signal_handler(signame):
        LOG.warning("Signal %s received", signame)
        stop_event.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(s, lambda sig=s: _signal_handler(sig.name))
        except NotImplementedError:
            signal.signal(s, lambda *_args, sig=s: _signal_handler(sig.name))

    # PTB async polling context manager (blocking-free)
    async with application:
        polling_task = asyncio.create_task(application.run_polling(), name="polling_task")
        LOG.info("Bot started, waiting for stop signal...")
        await stop_event.wait()
        LOG.info("Stop signal received, shutting down...")
        await shutdown(application, web_runner, worker_tasks + [polling_task])

# -------------------------------
def main():
    try:
        asyncio.run(async_main())
    except Exception:
        LOG.exception("Fatal error in main")

if __name__ == "__main__":
    main()
