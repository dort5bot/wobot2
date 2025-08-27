# main.py — Render Free WebService uyumlu Telegram Bot + Worker orchestrator
# - Modern asyncio lifecycle (asyncio.run)
# - Worker A & B paralel çalışır
# - PTB Application loader uyumlu (DummyApp ile placeholder)
# - Render Free için zorunlu keep-alive web server eklenmiş (aiohttp)

import asyncio
import logging
import signal
from aiohttp import web

from utils.handler_loader import register_handlers
from jobs.worker_a import run_forever as worker_a_run
from jobs.worker_b import run_forever as worker_b_run

# -------------------------------
# Minimal PTB-like skeleton (gerçek Application yerine Dummy)
class DummyApp:
    def add_handler(self, h):
        pass

app = DummyApp()

# -------------------------------
# Logging
logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("main")

# -------------------------------
# Keep-alive web service (Render port binding için)
async def handle_root(request):
    return web.Response(text="Bot is alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_root)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 10000)  # Render dinlediği port
    await site.start()
    LOG.info("Web server started on port 10000 (Render keep-alive).")

# -------------------------------
# Main async entry
async def async_main():
    LOG.info("Booting orchestrator...")

    # Handlers
    register_handlers(app)

    # Workers
    loop = asyncio.get_running_loop()
    loop.create_task(worker_a_run(), name="worker_a")
    loop.create_task(worker_b_run(), name="worker_b")

    # Keep-alive web server
    await start_web_server()

    # Graceful shutdown için event
    stop_event = asyncio.Event()

    def _request_shutdown(signame: str):
        LOG.warning("Signal received: %s — shutting down...", signame)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_shutdown, sig.name)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _request_shutdown(sig.name))

    await stop_event.wait()
    LOG.info("Shutdown complete. Bye.")

# -------------------------------
if __name__ == "__main__":
    asyncio.run(async_main())
