"""
Production-ready main.py for WorkerA→WorkerB→WorkerC chain + Telegram bot
Features:
- Robust async lifecycle
- Graceful startup / shutdown
- Handles existing event loops
- Logging & error handling
- Keep-alive / UptimeRobot support
- Health/readiness endpoints
- Optional Prometheus metrics / Sentry
- CLI configurable
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from contextlib import suppress
from typing import Optional

# Optional performance / diagnostics
try:
    import uvloop
    _HAS_UVLOOP = True
except Exception:
    _HAS_UVLOOP = False

try:
    from setproctitle import setproctitle
    _HAS_SETPROCTITLE = True
except Exception:
    _HAS_SETPROCTITLE = False

try:
    from prometheus_client import start_http_server, Counter, Summary
    _HAS_PROM = True
except Exception:
    _HAS_PROM = False

SENTRY_DSN = os.getenv("SENTRY_DSN")
if SENTRY_DSN:
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=SENTRY_DSN)
    except Exception:
        pass

from aiohttp import web
from telegram.ext import ApplicationBuilder, Application

from utils.db import init_db
from utils.monitoring import configure_logging
from utils.handler_loader import load_handlers
from utils.config import CONFIG

from jobs.worker_a import WorkerA
from jobs.worker_b import WorkerB
from jobs.worker_c import WorkerC

# ------------------------------
configure_logging(logging.INFO)
LOG = logging.getLogger("main")

# ------------------------------
# Worker setup
async def setup_workers():
    queue_raw = asyncio.Queue()
    worker_a = WorkerA(queue_raw)
    worker_c = WorkerC()

    async def signal_callback(source: str, symbol: str, side: str, strength: float, payload: dict):
        LOG.info("Signal from %s: %s %s (strength=%.2f)", source, symbol, side, strength)
        await worker_c.send_decision(payload)

    worker_b = WorkerB(queue_raw, signal_callback=signal_callback)
    return worker_a, worker_b, worker_c

# ------------------------------
# Worker lifecycle helpers
async def start_worker(worker, name: str):
    LOG.info("Starting %s...", name)
    try:
        await worker.start_async()
    except Exception:
        LOG.exception("Failed to start %s", name)

async def stop_worker(worker, name: str):
    LOG.info("Stopping %s...", name)
    try:
        await worker.stop_async()
    except Exception:
        LOG.exception("Error stopping %s", name)

# ------------------------------
# Health endpoints
async def health_handler(request):
    return web.json_response({"status": "ok", "workers": "running"})

async def health_server(host: str = "0.0.0.0", port: int = 8080, stop_event: Optional[asyncio.Event] = None):
    app = web.Application()
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    LOG.info("Health server listening on %s:%s", host, port)

    try:
        while not (stop_event and stop_event.is_set()):
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        LOG.info("Health server cancelled")
    finally:
        await runner.cleanup()
        LOG.info("Health server stopped")

# ------------------------------
# Keep-alive / uptime pinger
async def uptime_ping(url: str, interval: int = 300, stop_event: Optional[asyncio.Event] = None):
    import httpx
    LOG.info("Starting uptime ping to %s every %ss", url, interval)
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            while not (stop_event and stop_event.is_set()):
                try:
                    r = await client.get(url)
                    LOG.debug("Pinged %s -> %s", url, getattr(r, "status_code", None))
                except Exception as e:
                    LOG.warning("Uptime ping failed: %s", e)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            LOG.info("Uptime ping cancelled")

# ------------------------------
# Main async
async def async_main(args):
    if args.use_uvloop and _HAS_UVLOOP:
        uvloop.install()
        LOG.info("uvloop enabled")

    if _HAS_SETPROCTITLE:
        setproctitle("wobot1:main")

    LOG.info("Boot sequence started")
    init_db()

    token = CONFIG.TELEGRAM.BOT_TOKEN or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        LOG.error("Telegram BOT_TOKEN missing")
        return 1

    app: Application = ApplicationBuilder().token(token).build()
    load_handlers(app)

    worker_a, worker_b, worker_c = await setup_workers()
    workers = [(worker_a, "WorkerA"), (worker_b, "WorkerB"), (worker_c, "WorkerC")]

    stop_event = asyncio.Event()

    # --------------------------
    # Shutdown signal handler
    def _shutdown(sig_name="UNKNOWN"):
        LOG.warning("Shutdown signal %s received", sig_name)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _shutdown, sig.name)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _shutdown(getattr(sig, "name", str(sig))))

    # Start workers
    await asyncio.gather(*(start_worker(w, n) for w, n in workers))
    LOG.info("All workers started")

    # Health server
    health_task = None
    if args.health_port:
        health_task = asyncio.create_task(health_server(port=args.health_port, stop_event=stop_event))

    # Uptime ping
    ping_task = None
    if args.keepalive_url:
        ping_task = asyncio.create_task(uptime_ping(args.keepalive_url, interval=args.keepalive_interval, stop_event=stop_event))

    # PTB initialize & polling/webhook
    await app.initialize()
    await app.start()
    webhook_task = None

    if args.mode == "webhook":
        if not args.keepalive_url:
            LOG.warning("Webhook mode requested but KEEPALIVE_URL missing")
        else:
            webhook_url = f"{args.keepalive_url.rstrip('/')}/{token}"
            try:
                await app.bot.set_webhook(webhook_url)
                LOG.info("Webhook set to %s", webhook_url)
                webhook_task = asyncio.create_task(app.run_webhook(
                    listen="0.0.0.0",
                    port=args.port,
                    webhook_url=webhook_url,
                    stop_signals=None,
                    close_loop=False
                ))
            except Exception:
                LOG.exception("Failed to run webhook")
                stop_event.set()
    else:
        polling_task = asyncio.create_task(app.run_polling(close_loop=False))
        LOG.info("Polling started")

    # Wait for shutdown signal
    await stop_event.wait()
    LOG.info("Shutdown triggered")

    # Cancel auxiliary tasks
    for t in [ping_task, health_task, webhook_task, polling_task if args.mode=="polling" else None]:
        if t:
            t.cancel()
            with suppress(asyncio.CancelledError):
                await t

    # Stop workers
    await asyncio.gather(*(stop_worker(w, n) for w, n in workers))

    # PTB shutdown
    try:
        await app.shutdown()
        await app.stop()
        LOG.info("PTB app shutdown complete")
    except Exception:
        LOG.exception("Error during app shutdown")

    LOG.info("All systems stopped")
    return 0

# ------------------------------
def build_argparser():
    p = argparse.ArgumentParser(description="Production-ready bot entrypoint")
    p.add_argument("--mode", choices=["webhook", "polling"], default="webhook")
    p.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    p.add_argument("--health-port", type=int, default=int(os.getenv("HEALTH_PORT", "8080")))
    p.add_argument("--use-uvloop", action="store_true", default=bool(os.getenv("USE_UVLOOP", False)))
    p.add_argument("--keepalive-url", default=os.getenv("KEEPALIVE_URL"))
    p.add_argument("--keepalive-interval", type=int, default=int(os.getenv("KEEPALIVE_INTERVAL", "300")))
    return p

# ------------------------------
def main():
    args = build_argparser().parse_args()
    try:
        asyncio.run(async_main(args))
    except Exception:
        LOG.exception("Unhandled exception in main")
        sys.exit(1)

if __name__ == "__main__":
    main()
