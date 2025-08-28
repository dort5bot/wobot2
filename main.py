# main.py
# Async + Worker tabanlı bot giriş noktası
# - Python 3.11, python-telegram-bot v20+
# - WorkerA (Binance stream + funding) → WorkerB (sinyal üretimi) → WorkerC (işlem yöneticisi)
# - Free Render uyumlu: Aiohttp keep-alive endpoint + opsiyonel self-ping
# - Otomatik handler yükleme: handler_loader.load_handlers(application)

import asyncio
import logging
import os
import signal
from contextlib import suppress
from typing import Any, Dict

from aiohttp import web
from telegram.ext import ApplicationBuilder

from utils.monitoring import configure_logging, telegram_alert
from utils.db import init_db, log_signal, log_decision, log_paper_trade
from utils.config import CONFIG
from handler_loader import load_handlers

from jobs.worker_a import WorkerA
from jobs.worker_b import WorkerB
from jobs.worker_c import WorkerC


# ---------------------------
# Aiohttp Keep-Alive Web App
# ---------------------------

async def handle_root(request: web.Request):
    return web.Response(text="ok")

async def handle_health(request: web.Request):
    return web.json_response({"status": "ok", "service": "bot", "paper_mode": CONFIG.BOT.PAPER_MODE})

def build_web_app() -> web.Application:
    app = web.Application()
    app.add_routes([
        web.get("/", handle_root),
        web.get("/health", handle_health),
    ])
    return app

async def start_web_server(loop: asyncio.AbstractEventLoop) -> tuple[web.AppRunner, web.TCPSite]:
    """Render Free için PORT ortam değişkenini kullanır."""
    app = build_web_app()
    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logging.getLogger("main").info("Keep-alive server started on :%s", port)
    return runner, site

async def stop_web_server(runner: web.AppRunner):
    with suppress(Exception):
        await runner.cleanup()


# ---------------------------
# Opsiyonel Self-Ping Görevi
# ---------------------------

async def self_ping_task(url: str, interval_sec: int = 240):
    """
    Servisin uykuya geçmesini önlemek için kendi URL'ini pingler.
    - URL: SELF_PING_URL env ile ver (örn: https://your-render.onrender.com/health)
    - UptimeRobot kullanıyorsan bu görevi devre dışı bırak (env set etme), sadece endpoint yeterli.
    """
    import aiohttp
    LOG = logging.getLogger("self_ping")
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            try:
                async with session.get(url) as resp:
                    LOG.debug("Ping %s -> %s", url, resp.status)
            except Exception as e:
                LOG.warning("Ping failed: %s", e)
            await asyncio.sleep(interval_sec)


# ---------------------------
# Worker → Decision Köprüsü
# ---------------------------

class Pipeline:
    """WorkerA→WorkerB→WorkerC boru hattını ve Telegram botu yönetir."""
    def __init__(self):
        self.LOG = logging.getLogger("pipeline")
        self.loop = asyncio.get_event_loop()

        # A→B kuyruğu
        self.q_ab: asyncio.Queue = asyncio.Queue(maxsize=10_000)

        self.worker_a = WorkerA(queue=self.q_ab, loop=self.loop)

        # WorkerB sinyal callback'i bu sınıfın methoduna bağlanır
        self.worker_b = WorkerB(queue=self.q_ab, signal_callback=self.on_signal)

        self.worker_c = WorkerC()

        # Telegram Application (polling)
        bot_token = CONFIG.TELEGRAM.BOT_TOKEN
        if not bot_token:
            self.LOG.warning("TELEGRAM_BOT_TOKEN tanımlı değil; bot başlatılmayacak.")
        self.application = ApplicationBuilder().token(bot_token).build() if bot_token else None

        # Durum
        self._started = False

    # --- WorkerB → Signal Callback ---
    async def on_signal(self, source: str, symbol: str, side: str, strength: float, payload: Dict[str, Any]):
        """
        WorkerB'den gelen sinyali:
        - DB'ye loglar
        - Decision oluşturur (örnek basit karar)
        - WorkerC'ye iletir
        """
        # 1) Sinyali DB'ye yaz
        try:
            log_signal(symbol, signal_type=side, strength=strength, payload=str(payload), source=source)
        except Exception:
            self.LOG.exception("log_signal failed")

        # 2) Basit decision örneği (gerçek strateji OrderManager içinde/diğer utils'lerde detaylandırılabilir)
        decision = {
            "symbol": symbol,
            "side": side,                 # BUY / SELL
            "qty": payload.get("qty", 0), # gerçek qty hesaplamasını kendi stratejine göre koy
            "price": payload.get("price"),
            "meta": payload,              # tüm ek verileri meta'da taşı
        }

        # 3) Decision DB + WorkerC
        try:
            log_decision(symbol, decision=side, strength=strength, reason=f"source={source}")
        except Exception:
            self.LOG.exception("log_decision failed")

        try:
            await self.worker_c.send_decision(decision)
        except Exception:
            self.LOG.exception("send_decision failed")

        # 4) Paper trade modunda örnek kayıt (OrderManager zaten yapıyorsa bu kısım opsiyoneldir)
        if CONFIG.BOT.PAPER_MODE:
            try:
                price = payload.get("price") or 0.0
                qty = payload.get("qty") or 0.0
                log_paper_trade(symbol, side, qty, price, source=source)
            except Exception:
                self.LOG.exception("log_paper_trade failed")

        # 5) Opsiyonel Telegram alarm
        try:
            telegram_alert(f"{symbol} {side} strength={strength:.3f}")
        except Exception:
            self.LOG.exception("telegram_alert failed")

    # --- Telegram Bot Handlers ---
    def register_bot_handlers(self):
        """
        Harici handlers/ klasöründeki modülleri otomatik yükler.
        Her modülün register(application) fonksiyonuna çağrı yapılır.
        """
        if not self.application:
            return
        load_handlers(self.application)

        # Örnek /status komutu (bu dosyada da ufak bir handler veriyoruz)
        from telegram import Update
        from telegram.ext import CommandHandler, ContextTypes

        async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
            qsize = self.q_ab.qsize()
            msg = (
                f"🟢 Pipeline Status\n"
                f"- A→B queue: {qsize}\n"
                f"- PAPER_MODE: {CONFIG.BOT.PAPER_MODE}\n"
                f"- Symbols: {', '.join(CONFIG.BINANCE.TOP_SYMBOLS_FOR_IO)}\n"
            )
            await update.message.reply_text(msg)

        self.application.add_handler(CommandHandler("status", status_cmd))

    # --- Lifecycle ---
    async def start(self):
        if self._started:
            return
        self._started = True

        # DB + logging
        init_db()
        configure_logging(logging.INFO)
        self.LOG.info("Booting bot...")

        # Workers
        await self.worker_c.start_async()
        await self.worker_b.start_async()
        await self.worker_a.start_async()

        # Telegram bot
        if self.application:
            self.register_bot_handlers()
            await self.application.initialize()
            await self.application.start()

            # V20+ polling: updater mevcutsa başlat
            if self.application.updater:
                await self.application.updater.start_polling()
            else:
                # Webhook kurulumu yoksa, fallback kısa döngü (nadir durum)
                self.LOG.warning("No updater available; polling not started.")

        self.LOG.info("Bot started.")

    async def stop(self):
        if not self._started:
            return
        self._started = False
        self.LOG.info("Shutting down...")

        # Telegram kapat
        if self.application:
            with suppress(Exception):
                if self.application.updater:
                    await self.application.updater.stop()
            with suppress(Exception):
                await self.application.stop()
            with suppress(Exception):
                await self.application.shutdown()

        # Workers kapat
        await self.worker_a.stop_async()
        await self.worker_b.stop_async()
        await self.worker_c.stop_async()

        self.LOG.info("Shutdown complete.")


# ---------------------------
# Main Entrypoint
# ---------------------------

async def main():
    # Keep-alive web server
    web_runner, _web_site = await start_web_server(asyncio.get_event_loop())

    # Opsiyonel self-ping (UptimeRobot kullanıyorsan SELF_PING_URL ayarlama)
    self_ping_url = os.getenv("SELF_PING_URL", "").strip()
    ping_task = None
    if self_ping_url:
        ping_task = asyncio.create_task(self_ping_task(self_ping_url), name="self_ping")

    # Pipeline başlat
    pipe = Pipeline()
    await pipe.start()

    # Graceful shutdown sinyalleri
    stop_event = asyncio.Event()

    def _handle_signal(sig_name: str):
        logging.getLogger("main").info("Signal received: %s", sig_name)
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, _handle_signal, sig.name)

    # Bekle
    await stop_event.wait()

    # Kapatma
    if ping_task:
        ping_task.cancel()
        with suppress(asyncio.CancelledError):
            await ping_task
    await pipe.stop()
    await stop_web_server(web_runner)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
