
# main.py — Render Free WebService uyumlu Telegram Bot + Worker orchestrator
# - Modern asyncio lifecycle (asyncio.run)
# - Worker A & B paralel çalışır
# - PTB Application loader uyumlu (DummyApp ile placeholder)
# - Render Free için keep-alive web server eklenmiş (aiohttp) web server (port env üzerinden)
# 	1. Logging iyileştirildi: timestamp + level + message format.
# 	2. Workers ayrı task listesinde → shutdown sırasında hepsi güvenli cancel ediliyor.
# 	3. Graceful shutdown → Ctrl+C veya SIGTERM ile tüm workers güvenli şekilde duruyor.
# 	4. Exception handling → handler yükleme veya main loop hataları log’lanıyor.
# 	5. Keep-alive server → Render Free web service için port 8080 sabit ve bind edilebilir.
# 	6. Kolay geliştirilebilir: Yeni worker eklemek için start_workers listesine eklemek yeterli.
# 	7. CPU dostu: Workers kendi içlerinde interval ve sleep mantığına göre çalışıyor.
# 	8. Async güvenli: loop.create_task + asyncio.run uyumlu, KeyboardInterrupt veya Render stop signal ile uyumlu.
#

import asyncio
import logging
import os
import signal
from aiohttp import web

from utils.handler_loader import load_handlers
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
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
LOG = logging.getLogger("main")

# -------------------------------
# Keep-alive web server
async def handle_root(request):
    return web.Response(text="Bot is alive!")

async def start_web_server():
    port = int(os.getenv("PORT", 8080))  # .env üzerinden al, default 8080
    web_app = web.Application()
    web_app.router.add_get("/", handle_root)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    LOG.info("Web server started on port %d (Render keep-alive).", port)

# -------------------------------
# Worker task starter
def start_workers(loop: asyncio.AbstractEventLoop):
    tasks = [
        loop.create_task(worker_a_run(), name="worker_a"),
        loop.create_task(worker_b_run(), name="worker_b"),
    ]
    return tasks

# -------------------------------
# Graceful shutdown helper
async def shutdown(loop, stop_event: asyncio.Event, tasks):
    LOG.info("Shutting down background tasks...")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    LOG.info("All background tasks stopped.")
    stop_event.set()
    loop.stop()

# -------------------------------
# Async main fonksiyonu
async def async_main():
    LOG.info("Booting orchestrator...")

    # Handlers yükle
    try:
        load_handlers(app)
        LOG.info("Handlers registered successfully.")
    except Exception as e:
        LOG.exception("Error registering handlers: %s", e)

    # Event loop
    loop = asyncio.get_running_loop()

    # Workers başlat
    tasks = start_workers(loop)
    LOG.info("Worker A & B tasks started.")

    # Keep-alive web server
    await start_web_server()

    # Graceful shutdown için event
    stop_event = asyncio.Event()

    # Signal handler
    def _request_shutdown(signame: str):
        LOG.warning("Signal received: %s — initiating shutdown...", signame)
        asyncio.create_task(shutdown(loop, stop_event, tasks))

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_shutdown, sig.name)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _request_shutdown(sig.name))

    await stop_event.wait()
    LOG.info("Shutdown complete. Bye.")

# -------------------------------
# Main entry
def main():
    try:
        asyncio.run(async_main())
    except Exception:
        LOG.exception("Fatal error in main loop.")

# -------------------------------
if __name__ == "__main__":
    main()
