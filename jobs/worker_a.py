# jobs/worker_a.py
"""
WorkerA: Binance'den veri toplayıcı
- Kline streamlerini queue'ya aktarır
- Funding rate verilerini periyodik olarak alır ve queue'ya koyar
- Funding / open interest gibi genel veriler her zaman .env içindeki global API key ile alınır
- Kişisel API key sadece trade/alarmlar için kullanılacak (WorkerA’da devre dışı)
"""

import asyncio
import logging
from utils.config import CONFIG
from utils.binance_api import BinanceClient
from utils.apikey_utils import get_apikey  # kullanıcı key erişimi

LOG = logging.getLogger("worker_a")


def get_user_api_keys(user_id: str) -> dict:
    """
    Veritabanından user_id'ye karşılık gelen Binance API key ve secret'ı çeker.
    Dönüş: {"api_key": "xxx", "secret_key": "yyy"} veya boş dict
    """
    api_key, secret_key = get_apikey(int(user_id))
    if api_key and secret_key:
        return {"api_key": api_key, "secret_key": secret_key}
    return {}


class WorkerA:
    def __init__(self, queue: asyncio.Queue, loop=None, user_id: str = None):
        self.queue = queue
        self.loop = loop or asyncio.get_event_loop()
        self._running = False
        self._tasks: list[asyncio.Task] = []

        # 🔹 Funding gibi ortak işler için global client
        self.client = self._init_global_client()

        # (Not: İleride user_id gerekirse trade/alarmlarda kullanılacak)
        self.user_id = user_id

    def _init_global_client(self) -> BinanceClient:
        """
        Funding / open interest gibi genel endpointler için
        her zaman .env içindeki global API key kullanılır.
        """
        if CONFIG.BINANCE.API_KEY and CONFIG.BINANCE.SECRET_KEY:
            LOG.info("WorkerA: Global API keys (env) kullanılacak.")
            return BinanceClient(CONFIG.BINANCE.API_KEY, CONFIG.BINANCE.SECRET_KEY)
        else:
            LOG.warning("WorkerA: API key bulunamadı → sadece public endpoint kullanılabilir!")
            return BinanceClient()  # public-only

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
                        # 🔹 Global API key ile funding alınır
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
