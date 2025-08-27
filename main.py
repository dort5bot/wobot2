# main.py
# Render / Heroku / Docker friendly Telegram bot orchestrator (PTB v20+)
# - Async lifecycle: asyncio.run(async_main())
# - PTB Application properly initialized & started before polling
# - Workers (A & B) run in separate asyncio tasks and are cleanly cancelled on shutdown
# - Keep-alive web server (aiohttp) for platform health checks
# - Robust graceful shutdown on SIGINT/SIGTERM (and Windows fallback)
# - Improved logging + defensive error handling
#
# Nasıl çalışır (özeti):
# 1. Application oluşturulur (ApplicationBuilder).
# 2. Handlers yüklenir (utils.handler_loader.load_handlers).
# 3. Workers başlatılır (jobs.worker_a.run_forever, jobs.worker_b.run_forever).
# 4. Web server (aiohttp) başlatılır.
# 5. PTB lifecycle: initialize() -> start() -> polling görevini ayrı Task olarak çalıştır.
# 6. SIGINT/SIGTERM alınca: workers ve polling task güvenli şekilde iptal edilir, ardından app.stop/shutdown/cleanup çalıştırılır.
#
# Notlar / Önemli:
# * PTB v20+ ile "start" çağrılmadan önce initialize() çağrılmazsa yukarıdaki RuntimeError alınır.
# * Polling uzun süreli (blocking) coroutine olabilir; bu yüzden ayrı task olarak çalıştırıyoruz.
# * `shutdown()` içinde loop.stop() çağırmıyoruz — asyncio.run flow'una bırakıyoruz.
# * Worker'ların kendi içinde exception handle etmesi önerilir; burada genel cancel & gather yapıyoruz.
#
# Geliştirmek için:
# - Yeni worker eklemek için start_worker_coros listesine run_coroutine ekle.
# - metrics / prometheus endpoint ekle (web server'e).
# - health/readiness endpointleri genişlet.

import asyncio
import logging
import os
import signal
from typing import List, Optional

from aiohttp import web
from telegram.ext import Application, ApplicationBuilder

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
    """Basic health endpoint"""
    return web.Response(text="Bot is alive!")

async def handle_ready(request):
    """Readiness endpoint — daha sonra DB/3rd-party health kontrolü eklenebilir"""
    return web.Response(text="ready")

async def start_web_server(port: int) -> web.AppRunner:
    """Start aiohttp web server and return its runner (so caller can cleanup)."""
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
    """
    Başlatılacak worker coroutine'lerini al ve Task listesi döndür.
    worker_coros: list of coroutine functions (callables returning coroutine) — örn: worker_a_run
    """
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
    # wait for tasks to finish (or timeout)
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    if pending:
        LOG.warning("%d tasks did not finish within %.1fs, cancelling forcefully.", len(pending), timeout)
        for p in pending:
            p.cancel()
    LOG.info("Background tasks cancelled/finished.")

async def shutdown(
    *,
    app: Optional[Application],
    runner: Optional[web.AppRunner],
    worker_tasks: List[asyncio.Task],
    polling_task: Optional[asyncio.Task]
):
    """Tüm parçaları güvenli şekilde durdurur."""
    LOG.info("Shutting down background tasks...")
    # 1) Cancel worker tasks
    await _cancel_and_await(worker_tasks, timeout=6.0)

    # 2) Cancel polling task (PTB)
    if polling_task:
        LOG.info("Cancelling polling task...")
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            LOG.info("Polling task cancelled.")
        except Exception as e:
            LOG.exception("Exception while waiting polling task cancel: %s", e)

    # 3) Stop & shutdown application
    if app:
        try:
            LOG.info("Stopping PTB Application...")
            await app.stop()        # stops the running pieces (handlers)
            LOG.info("Shutting down PTB Application...")
            await app.shutdown()    # close connections, handlers etc
            LOG.info("Cleaning up PTB Application...")
            await app.cleanup()     # final cleanup
            LOG.info("PTB Application stopped.")
        except Exception as e:
            LOG.exception("Error during Application stop/shutdown/cleanup: %s", e)

    # 4) Stop web server
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

    # TELEGRAM token kontrolü
    token = os.getenv(TELEGRAM_TOKEN_ENV)
    if not token:
        LOG.error("%s env not set! Exiting.", TELEGRAM_TOKEN_ENV)
        return

    # Uygulama oluştur
    application = ApplicationBuilder().token(token).build()

    # Handlers yükle (sorun olursa yakala)
    try:
        load_handlers(application)
        LOG.info("Handlers registered successfully.")
    except Exception as e:
        LOG.exception("Error registering handlers: %s", e)
        # Kritik bir hata değilse devam edebilir; istersen return ile çık.
        # return

    loop = asyncio.get_running_loop()

    # Workers başlat
    worker_coros = [worker_a_run, worker_b_run]  # yeni worker eklemek için buraya ekle
    worker_tasks = start_workers(loop, worker_coros)
    LOG.info("Worker tasks started: %s", [t.get_name() for t in worker_tasks])

    # Web server start
    port = int(os.getenv("PORT", DEFAULT_PORT))
    web_runner = None
    try:
        web_runner = await start_web_server(port)
    except Exception:
        LOG.exception("Failed to start web server.")

    # PTB application lifecycle:
    # 1) initialize
    # 2) start
    # 3) start_polling() as a background task (so main() can continue)
    polling_task = None
    try:
        LOG.info("Initializing PTB Application...")
        await application.initialize()
        LOG.info("Starting PTB Application...")
        await application.start()
        # start_polling returns a coroutine that runs until stopped; run it as a task
        polling_task = loop.create_task(application.start_polling(), name="ptb_polling")
        LOG.info("PTB Application started and polling task created.")
    except Exception:
        LOG.exception("Failed during PTB Application initialize/start/polling.")
        # If PTB failed to start, we still want to run shutdown sequence
        await shutdown(app=application, runner=web_runner, worker_tasks=worker_tasks, polling_task=polling_task)
        return

    # Graceful shutdown trigger
    stop_event = asyncio.Event()

    def _on_signal(signame):
        LOG.warning("Received signal %s - scheduling shutdown...", signame)
        # schedule shutdown without blocking the signal handler
        asyncio.create_task(shutdown(app=application, runner=web_runner, worker_tasks=worker_tasks, polling_task=polling_task))
        # ensure main waits a short moment and then sets stop_event
        # set stop_event after a tiny delay to let shutdown begin
        async def _set_event_after():
            await asyncio.sleep(0.1)
            stop_event.set()
        asyncio.create_task(_set_event_after())

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, lambda sig=s: _on_signal(sig.name))
        except NotImplementedError:
            # Windows fallback (or environments without add_signal_handler)
            signal.signal(s, lambda *_args, sig=s: _on_signal(sig.name))

    LOG.info("Orchestrator is up and running. Waiting for shutdown signal...")
    # wait until stop_event is set by signal handler
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
