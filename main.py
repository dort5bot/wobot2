# main.py
# Render / Heroku / Docker friendly Telegram bot orchestrator (PTB v20+)
# Async lifecycle: asyncio.run(async_main())
# Workers (A & B) run in separate asyncio tasks
# Keep-alive web server (aiohttp) for health checks
# Graceful shutdown on SIGINT/SIGTERM

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
# Logging config
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
# Keep-alive / Health webserver
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
    LOG.info("Web server started on port %d (keep-alive).", port)
    return runner

# -------------------------------
# Workers starter
def start_workers(loop: asyncio.AbstractEventLoop, worker_coros: List[asyncio.coroutines]) -> List[asyncio.Task]:
    tasks = []
    for coro_fn in worker_coros:
        try:
            task = loop.create_task(coro_fn(), name=getattr(coro_fn, "__name__", "worker"))
            tasks.append(task)
            LOG.info("Started worker task: %s", task.get_name())
        except Exception as e:
            LOG.exception("Failed to start worker %s: %s", coro_fn, e)
    return tasks

# -------------------------------
# Graceful shutdown
async def _cancel_and_await(tasks: List[asyncio.Task], timeout: float = 5.0):
    if not tasks:
        return
    LOG.info("Cancelling %d background tasks...", len(tasks))
    for t in tasks:
        t.cancel()
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    if pending:
        LOG.warning("%d tasks did not finish within %.1fs, cancelling forcefully.", len(pending), timeout)
        for p in pending:
            p.cancel()
    LOG.info("Background tasks cancelled/finished.")

async def shutdown(
    *,
    app: Optional[object],
    runner: Optional[web.AppRunner],
    worker_tasks: List[asyncio.Task],
    polling_task: Optional[asyncio.Task]
):
    LOG.info("Shutting down background tasks...")
    await _cancel_and_await(worker_tasks, timeout=6.0)

    if polling_task:
        LOG.info("Cancelling polling task...")
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            LOG.info("Polling task cancelled.")
        except Exception as e:
            LOG.exception("Exception while waiting polling task cancel: %s", e)

    if app:
        try:
            LOG.info("Stopping PTB Application...")
            await app.stop()        # stops handlers
            LOG.info("Shutting down PTB Application...")
            await app.shutdown()    # closes connections, handlers
            LOG.info("PTB Application stopped.")
        except Exception as e:
            LOG.exception("Error during Application stop/shutdown: %s", e)

    if runner:
        try:
            LOG.info("Stopping web server runner...")
            await runner.cleanup()
            LOG.info("Web server stopped.")
        except Exception as e:
            LOG.exception("Error stopping web server: %s", e)

    LOG.info("Shutdown sequence complete.")

# -------------------------------
# Main async entry
async def async_main():
    LOG.info("Booting orchestrator...")

    token = os.getenv(TELEGRAM_TOKEN_ENV)
    if not token:
        LOG.error("%s env not set! Exiting.", TELEGRAM_TOKEN_ENV)
        return

    application = ApplicationBuilder().token(token).build()

    try:
        load_handlers(application)
        LOG.info("Handlers registered successfully.")
    except Exception as e:
        LOG.exception("Error registering handlers: %s", e)

    loop = asyncio.get_running_loop()
    worker_coros = [worker_a_run, worker_b_run]
    worker_tasks = start_workers(loop, worker_coros)
    LOG.info("Worker tasks started: %s", [t.get_name() for t in worker_tasks])

    port = int(os.getenv("PORT", DEFAULT_PORT))
    web_runner = None
    try:
        web_runner = await start_web_server(port)
    except Exception:
        LOG.exception("Failed to start web server.")

    # PTB v20+ async polling
    polling_task = None
    try:
        LOG.info("Initializing PTB Application...")
        await application.initialize()
        LOG.info("Starting PTB Application...")
        await application.start()
        polling_task = loop.create_task(application.run_polling(), name="ptb_polling")
        LOG.info("PTB Application started and polling task created.")
    except Exception:
        LOG.exception("Failed during PTB Application initialize/start/polling.")
        await shutdown(app=application, runner=web_runner, worker_tasks=worker_tasks, polling_task=polling_task)
        return

    stop_event = asyncio.Event()

    def _on_signal(signame):
        LOG.warning("Received signal %s - scheduling shutdown...", signame)
        asyncio.create_task(shutdown(app=application, runner=web_runner, worker_tasks=worker_tasks, polling_task=polling_task))
        async def _set_event_after():
            await asyncio.sleep(0.1)
            stop_event.set()
        asyncio.create_task(_set_event_after())

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, lambda sig=s: _on_signal(sig.name))
        except NotImplementedError:
            signal.signal(s, lambda *_args, sig=s: _on_signal(sig.name))

    LOG.info("Orchestrator is up and running. Waiting for shutdown signal...")
    await stop_event.wait()
    LOG.info("Stop event received — exiting async_main.")

# -------------------------------
# Main sync wrapper
def main():
    try:
        asyncio.run(async_main())
    except Exception:
        LOG.exception("Fatal error in main loop.")

if __name__ == "__main__":
    main()
