# main.py (revize)
# Python 3.11 + python-telegram-bot v20+
# WorkerA→WorkerB→WorkerC async
# Free Render + UptimeRobot keep-alive uyumlu

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


# ---------------------------
# Keep-alive endpoint
# ---------------------------
async def handle_root(request):
    return web.Response(text="ok")

async def handle_health(request):
    return web.json_response({"status": "ok", "service": "bot"})

async def start_web(loop):
    app = web.Application()
    app.add_routes([
        web.get("/", handle_root),
        web.get("/health", handle_health),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    LOG.info("Keep-alive running on :%s", port)
    return runner


# ---------------------------
# Pipeline
# ---------------------------
class Pipeline:
    def __init__(self):
        self.q_ab = asyncio.Queue(maxsize=5000)
        self.worker_a = WorkerA(queue=self.q_ab)
        self.worker_b = WorkerB(queue=self.q_ab)
        self.worker_c = WorkerC()
        self.application = None
        self._started = False

    async def start(self):
        if self._started:
            return
        self._started = True

        init_db()
        configure_logging(logging.INFO)
        LOG.info("Booting bot...")

        # Telegram bot
        if CONFIG.TELEGRAM.BOT_TOKEN:
            self.application = ApplicationBuilder().token(CONFIG.TELEGRAM.BOT_TOKEN).build()
            load_handlers(self.application)
        else:
            LOG.warning("No TELEGRAM_BOT_TOKEN defined")

        # Workers
        await self.worker_c.start_async()
        await self.worker_b.start_async()
        await self.worker_a.start_async()

        # Telegram polling (non-async)
        if self.application:
            LOG.info("Polling started (Render free + UptimeRobot)")
            # run_polling bloklayıcıdır, o yüzden ayrı thread çalıştırılır
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, self.application.run_polling, {"close_loop": False})

    async def stop(self):
        if not self._started:
            return
        self._started = False
        LOG.info("Stopping...")

        await self.worker_a.stop_async()
        await self.worker_b.stop_async()
        await self.worker_c.stop_async()

        if self.application:
            with suppress(Exception):
                await self.application.shutdown()
        LOG.info("Bot stopped")


# ---------------------------
# Main
# ---------------------------
async def main():
    runner = await start_web(asyncio.get_running_loop())
    pipe = Pipeline()
    await pipe.start()

    stop_event = asyncio.Event()

    def _signal(sig):
        LOG.info("Signal received: %s", sig)
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
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
