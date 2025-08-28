# main.py
import asyncio
import logging
import os
import signal
from contextlib import suppress

from aiohttp import web
from telegram.ext import ApplicationBuilder
from utils.monitoring import configure_logging
from utils.db import init_db
from utils.config import CONFIG
from handler_loader import load_handlers

from jobs.worker_a import WorkerA
from jobs.worker_b import WorkerB
from jobs.worker_c import WorkerC

LOG = logging.getLogger(__name__)

# --- Keep-alive ---
async def handle_root(request): return web.Response(text="ok")
async def handle_health(request): return web.json_response({"status": "ok"})

async def start_web():
    app = web.Application()
    app.add_routes([web.get("/", handle_root), web.get("/health", handle_health)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    LOG.info("Keep-alive running on :%s", port)
    return runner


# --- Pipeline ---
class Pipeline:
    def __init__(self):
        self.q_ab = asyncio.Queue(maxsize=5000)
        self.worker_a = WorkerA(queue=self.q_ab)
        self.worker_b = WorkerB(queue=self.q_ab)
        self.worker_c = WorkerC()
        self.application = None

    async def start(self):
        init_db()
        configure_logging(logging.INFO)

        if CONFIG.TELEGRAM.BOT_TOKEN:
            self.application = ApplicationBuilder().token(CONFIG.TELEGRAM.BOT_TOKEN).build()
            load_handlers(self.application)
        else:
            LOG.warning("No TELEGRAM_BOT_TOKEN defined")

        await self.worker_c.start_async()
        await self.worker_b.start_async()
        await self.worker_a.start_async()

        if self.application:
            LOG.info("Polling started (Render free + UptimeRobot)")
            # run_polling bloklayıcı → async loop’u bozmasın diye executor’da çalıştırıyoruz
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, self.application.run_polling, {"close_loop": False})

    async def stop(self):
        await self.worker_a.stop_async()
        await self.worker_b.stop_async()
        await self.worker_c.stop_async()
        if self.application:
            with suppress(Exception):
                await self.application.shutdown()
        LOG.info("Bot stopped")


# --- Main ---
async def main():
    runner = await start_web()
    pipe = Pipeline()
    await pipe.start()

    stop_event = asyncio.Event()

    def _signal(sig):
        LOG.info("Signal %s", sig)
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, _signal, sig.name)

    await stop_event.wait()
    await pipe.stop()
    await runner.cleanup()


if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
