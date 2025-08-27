# main.py
# PTB v20+ | Asyncio | Docker/Render friendly | Workers + Keep-alive server

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
# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
LOG = logging.getLogger("main")

# -------------------------------
# Constants
DEFAULT_PORT = 8080
TELEGRAM_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"

# -------------------------------
# Keep-alive / Health server
async def handle_root(request):
    return web.Response(text="Bot is alive!")

async def handle_ready(request):
    return web.Response(text="ready")

async def start_web_server(port: int) -> web.AppRunner:
    web_app = web.Application()
    web_app.router.add_get("/", handle_root)
    web_app.router.add_get("/ready", handle_ready)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    LOG.info("Web server started on port %d", port)
    return runner

# -------------------------------
# Workers starter
def start_workers(worker_coros: List[asyncio.coroutines]) -> List[asyncio.Task]:
    tasks = []
    for coro_fn in worker_coros:
        task = asyncio.create_task(coro_fn(), name=getattr(coro_fn, "__name__", "worker"))
        tasks.append(task)
        LOG.info("Started worker: %s", task.get_name())
    return tasks

# -------------------------------
# Graceful shutdown
async def shutdown(
    *,
    app: Optional[object],
    runner: Optional[web.AppRunner],
    worker_tasks: List[asyncio.Task],
    polling_task: Optional[asyncio.Task]
):
    LOG.info("Shutting down background tasks...")
    for t in worker_tasks:
        t.cancel()
    await asyncio.gather(*worker_tasks, return_exceptions=True)

    if polling_task:
        LOG.info("Cancelling PTB polling task...")
        polling_task.cancel()
        await asyncio.gather(polling_task, return_exceptions=True)

    if app:
        LOG.info("Stopping PTB application...")
        await app.stop()
        await app.shutdown()
        LOG.info("PTB application stopped.")

    if runner:
        LOG.info("Stopping web server runner...")
        await runner.cleanup()
        LOG.info("Web server stopped.")

    LOG.info("Shutdown complete.")

# -------------------------------
# Main async entry
async def main_async():
    LOG.info("Booting bot...")

    token = os.getenv(TELEGRAM_TOKEN_ENV)
    if not token:
        LOG.error("%s env not set! Exiting.", TELEGRAM_TOKEN_ENV)
        return

    app = ApplicationBuilder().token(token).build()
    try:
        load_handlers(app)
        LOG.info("Handlers registered.")
    except Exception:
        LOG.exception("Handler loading failed!")

    # Start workers
    worker_tasks = start_workers([worker_a_run, worker_b_run])

    # Start web server
    port = int(os.getenv("PORT", DEFAULT_PORT))
    web_runner = None
    try:
        web_runner = await start_web_server(port)
    except Exception:
        LOG.exception("Web server failed!")

    # Start PTB polling on existing loop
    try:
        await app.initialize()
        await app.start()
        polling_task = asyncio.create_task(app.run_polling(), name="ptb_polling")
        LOG.info("PTB polling started.")
    except Exception:
        LOG.exception("PTB start failed!")
        await shutdown(app=app, runner=web_runner, worker_tasks=worker_tasks, polling_task=None)
        return

    # Setup shutdown signals
    stop_event = asyncio.Event()

    def _signal_handler(signame):
        LOG.warning("Received signal %s", signame)
        asyncio.create_task(shutdown(app=app, runner=web_runner, worker_tasks=worker_tasks, polling_task=polling_task))
        stop_event.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(s, lambda sig=s: _signal_handler(sig.name))
        except NotImplementedError:
            signal.signal(s, lambda *_args, sig=s: _signal_handler(sig.name))

    LOG.info("Bot is running. Press Ctrl+C to stop.")
    await stop_event.wait()
    LOG.info("Stop event received. Exiting main loop.")

# -------------------------------
# Sync wrapper
def main():
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main_async())
    except Exception:
        LOG.exception("Fatal error in main loop.")

if __name__ == "__main__":
    main()
