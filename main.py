# main.py — Modern, production-ready entrypoint for PTB v20+ bot (webhook mode)
# - Robust async lifecycle
# - Graceful startup / shutdown
# - Handles environments where an event loop may already exist
# - Clear logging & error handling

import asyncio
import logging
import os
import signal
import sys
from contextlib import suppress
from typing import Optional

from telegram.ext import ApplicationBuilder, Application

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
async def uptime_ping_task(url: str, interval: int = 300, stop_event: asyncio.Event = None):
    """Periodically ping keepalive URL. Stops when stop_event is set."""
    import httpx
    LOG.debug("Uptime ping task started for %s (interval=%s)", url, interval)
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            while (stop_event is None) or (not stop_event.is_set()):
                try:
                    r = await client.get(url)
                    LOG.debug("Pinged Uptime URL, status=%s", r.status_code)
                except Exception as e:
                    LOG.warning("Uptime ping failed: %s", e)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            LOG.info("Uptime ping cancelled")
        except Exception:
            LOG.exception("Uptime ping crashed")

# -------------------------------
async def start_worker_async(worker, name: str):
    """Start worker (supports sync start or async start_async)."""
    try:
        LOG.info("Starting %s...", name)
        if hasattr(worker, "start_async") and asyncio.iscoroutinefunction(worker.start_async):
            await worker.start_async()
        else:
            # run sync start in threadpool
            await asyncio.to_thread(worker.start)
    except Exception:
        LOG.exception("%s failed to start", name)
        raise

async def stop_worker_async(worker, name: str, timeout: float = 5.0):
    """Stop worker (supports stop_async or sync stop)."""
    try:
        LOG.info("Stopping %s...", name)
        if hasattr(worker, "stop_async") and asyncio.iscoroutinefunction(worker.stop_async):
            await worker.stop_async()
        else:
            await asyncio.to_thread(worker.stop)
    except Exception:
        LOG.exception("Error stopping %s", name)

# -------------------------------
async def async_main():
    LOG.info("Boot sequence initiated")
    init_db()

    token = CONFIG.TELEGRAM.BOT_TOKEN
    if not token:
        LOG.error("TELEGRAM_BOT_TOKEN is not set. Exiting.")
        return 1

    # Build PTB application but do not run webhook yet
    app: Application = ApplicationBuilder().token(token).build()

    # Load handlers (plugin loader should register handlers to dispatcher)
    load_handlers(app)

    # Shared queue for workers
    kline_queue = asyncio.Queue()

    # Instantiate workers (non-blocking constructors)
    worker_a = WorkerA(kline_queue)
    worker_c = WorkerC()
    # import signal_handler callback if present; if not, fallback to None
    try:
        from handlers import signal_handler  # type: ignore
        signal_callback = signal_handler.publish_signal
    except Exception:
        LOG.debug("No signal_handler.publish_signal found; continuing without it")
        signal_callback = None

    worker_b = WorkerB(queue=kline_queue, signal_callback=signal_callback)

    # Graceful shutdown coordination
    stop_event = asyncio.Event()

    def _request_shutdown(signame="UNKNOWN"):
        LOG.warning("Signal %s received. Triggering shutdown...", signame)
        # set the event on the loop thread
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(stop_event.set)

    # Register signal handlers (best effort; Windows may not support add_signal_handler)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _request_shutdown, sig.name)
        except Exception:
            try:
                signal.signal(sig, lambda *_: _request_shutdown(sig.name))
            except Exception:
                LOG.debug("Could not register handler for %s", sig)

    # Start workers concurrently (wrap start in tasks)
    startup_tasks = []
    for w, name in ((worker_a, "WorkerA"), (worker_b, "WorkerB"), (worker_c, "WorkerC")):
        t = asyncio.create_task(start_worker_async(w, name), name=f"start-{name}")
        startup_tasks.append(t)

    # Wait for workers to start (catch failures)
    try:
        await asyncio.gather(*startup_tasks)
    except Exception:
        LOG.exception("One or more workers failed to start; initiating shutdown")
        stop_event.set()
        # try to stop any that did start
        await asyncio.gather(
            *(stop_worker_async(w, n) for w, n in ((worker_a, "WorkerA"), (worker_b, "WorkerB"), (worker_c, "WorkerC"))),
            return_exceptions=True
        )
        return 1

    # Webhook setup
    keepalive_url = os.getenv("KEEPALIVE_URL") or getattr(CONFIG, "KEEPALIVE_URL", None)
    if not keepalive_url:
        LOG.error("KEEPALIVE_URL is not set (env or CONFIG). Continuing but webhook ping disabled.")

    port = int(os.getenv("PORT", getattr(CONFIG, "PORT", 8000)))
    webhook_url = f"{keepalive_url.rstrip('/')}/{token}" if keepalive_url else None

    # Initialize and start PTB app properly
    try:
        LOG.info("Initializing PTB application")
        await app.initialize()
        LOG.info("Starting PTB application")
        await app.start()
        if webhook_url:
            await app.bot.set_webhook(webhook_url)
            LOG.info("Webhook set to %s", webhook_url)
    except Exception:
        LOG.exception("Failed to initialize/start PTB application. Initiating shutdown.")
        stop_event.set()

    # Start uptime ping task if keepalive_url present
    ping_task = None
    if keepalive_url:
        ping_task = asyncio.create_task(uptime_ping_task(keepalive_url, interval=300, stop_event=stop_event), name="uptime-pinger")

    # Run webhook server as a task (it's a coroutine)
    webhook_task = None
    if webhook_url:
        try:
            LOG.info("Launching webhook listener on port %s", port)
            webhook_task = asyncio.create_task(
                app.run_webhook(
                    listen="0.0.0.0",
                    port=port,
                    webhook_url=webhook_url,
                    stop_signals=None,  # we manage signals ourselves
                    close_loop=False
                ),
                name="ptb-webhook"
            )
        except Exception:
            LOG.exception("Failed to start webhook task")
            stop_event.set()
    else:
        LOG.info("No webhook_url configured; webhook server not started")

    # Wait until external shutdown is requested
    await stop_event.wait()
    LOG.info("Stop event detected — starting shutdown sequence")

    # Cancel webhook & ping tasks gently
    if ping_task:
        ping_task.cancel()
        with suppress(asyncio.CancelledError):
            await ping_task

    if webhook_task:
        # Ask webhook to stop by calling app.stop() which should make run_webhook return
        try:
            LOG.info("Stopping PTB webhook listener")
            await app.stop()
        except Exception:
            LOG.exception("Error while stopping PTB app")
        with suppress(asyncio.CancelledError):
            await webhook_task

    # Stop workers
    await asyncio.gather(
        stop_worker_async(worker_a, "WorkerA"),
        stop_worker_async(worker_b, "WorkerB"),
        stop_worker_async(worker_c, "WorkerC"),
        return_exceptions=True
    )

    # Final PTB shutdown (ensure shutdown coroutine called)
    try:
        LOG.info("Shutting down PTB application (shutdown() call)")
        await app.shutdown()
    except Exception:
        LOG.exception("Error during app.shutdown()")

    LOG.info("Shutdown complete")
    return 0

# -------------------------------
def main_entry():
    """
    Entry point wrapper which handles environments that might already have a running loop.
    Prefer creating a fresh loop and running async_main; if that's not possible (rare), fallback.
    """
    try:
        # Create isolated event loop for deterministic behavior
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(async_main())
    except RuntimeError as e:
        # Happens if an event loop is already running on this thread (e.g. some managed hosts).
        LOG.warning("RuntimeError when creating new event loop: %s. Falling back to existing loop.", e)
        loop = asyncio.get_event_loop()
        # schedule main and run forever until it completes (it will wait on stop_event)
        future = asyncio.run_coroutine_threadsafe(async_main(), loop)
        try:
            return future.result()
        except Exception:
            LOG.exception("Fallback run failed")
            return 1
    except Exception:
        LOG.exception("Unhandled exception in main_entry")
        return 1

# -------------------------------
if __name__ == "__main__":
    rc = main_entry()
    # ensure proper exit code
    sys.exit(rc or 0)
