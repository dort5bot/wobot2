# jobs/worker_a.py
'''
WorkerA class'ında kullanılacak BinanceClient nesnesi, duruma göre:
✅ user_id verilirse: ilgili kullanıcının API key + secret bilgisi veritabanından çekilsin.
✅ user_id verilmezse:.env'de tanımlı CONFIG.BINANCE.API_KEY varsa onu kullan.
Yoksa → sadece public endpoint'ler desteklensin (örneğin WebSocket veya funding rate gibi public erişimli endpoint’ler çalışsın, auth isteyenler çalışmasın veya loglansın).
'''
import asyncio
import logging
from utils.config import CONFIG
from utils.binance_api import get_binance_api
from utils.db import get_user_api_keys  # <- user_id ile key çekmek için (örnek fonksiyon)

LOG = logging.getLogger("worker_a")


class WorkerA:
    """
    Worker A: Binance'den veri toplayıcı
    - Kline streamlerini queue'ya aktarır
    - Funding rate verilerini periyodik olarak alır ve queue'ya koyar
    """
    def __init__(self, queue: asyncio.Queue, loop=None, user_id: str = None):
        self.queue = queue
        self.loop = loop or asyncio.get_event_loop()
        self._running = False
        self._tasks: list[asyncio.Task] = []

        # Akıllı API client oluşturma
        self.client = self._init_binance_client(user_id)

    def _init_binance_client(self, user_id: str = None):
        """
        Kullanıcı bazlı veya default Binance API client'ı oluşturur.
        """
        if user_id:
            user_keys = get_user_api_keys(user_id)
            if user_keys and user_keys.get("api_key") and user_keys.get("secret_key"):
                LOG.info(f"WorkerA: User-specific API keys loaded for user_id={user_id}")
                return get_binance_api(
                    api_key=user_keys["api_key"],
                    api_secret=user_keys["secret_key"]
                )
            else:
                LOG.warning(f"WorkerA: user_id={user_id} için API key bulunamadı, fallback olarak default key kullanılacak.")

        if CONFIG.BINANCE.API_KEY and CONFIG.BINANCE.SECRET_KEY:
            LOG.info("WorkerA: Default API keys from .env kullanılıyor.")
            return get_binance_api(
                api_key=CONFIG.BINANCE.API_KEY,
                api_secret=CONFIG.BINANCE.SECRET_KEY
            )

        LOG.info("WorkerA: Public-only Binance client oluşturuldu.")
        return get_binance_api()  # Public erişim (API key yok)

    async def start_async(self):
        if self._running:
            return
        self._running = True

        # Kline stream'leri başlat
        for symbol in CONFIG.BINANCE.TOP_SYMBOLS_FOR_IO:
            task = self.loop.create_task(
                self.client.ws_kline(symbol.lower(), CONFIG.BINANCE.STREAM_INTERVAL, self.bridge),
                name=f"ws_kline_{symbol}"
            )
            self._tasks.append(task)

        # Funding poller
        task = self.loop.create_task(self._funding_loop(), name="funding_poller")
        self._tasks.append(task)

        LOG.info("WorkerA started with symbols: %s", CONFIG.BINANCE.TOP_SYMBOLS_FOR_IO)

    async def _funding_loop(self):
        while self._running:
            try:
                data = {}
                for symbol in CONFIG.BINANCE.TOP_SYMBOLS_FOR_IO:
                    try:
                        fr = await self.client.get_funding_rate(symbol)
                        data[symbol] = fr
                    except ValueError as ve:
                        LOG.warning(f"Funding rate alınamadı ({symbol}): {ve}")
                await self.queue.put({"funding": data})
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("WorkerA funding poll error")
            await asyncio.sleep(CONFIG.BINANCE.FUNDING_POLL_INTERVAL)

    async def bridge(self, msg):
        """Stream mesajlarını queue'ya aktarır"""
        try:
            await self.queue.put(msg)
        except Exception:
            LOG.exception("WorkerA bridge error")

    async def stop_async(self):
        if not self._running:
            return
        self._running = False

        for t in self._tasks:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        LOG.info("WorkerA stopped")
