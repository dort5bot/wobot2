# jobs/worker_a.py
import asyncio
import logging
from utils.config import CONFIG
from utils.binance_api import get_binance_api

LOG = logging.getLogger("worker_a")


class WorkerA:
    """
    Worker A: Binance'den veri toplayıcı
    - Kline streamlerini queue'ya aktarır
    - Funding rate verilerini periyodik olarak alır ve queue'ya koyar
    """
    def __init__(self, queue: asyncio.Queue, loop=None):
        self.queue = queue
        self.loop = loop or asyncio.get_event_loop()
        self.client = get_binance_api()
        self._running = False
        self._tasks: list[asyncio.Task] = []

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
                    fr = await self.client.get_funding_rate(symbol)
                    data[symbol] = fr
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


# -------------------------------------------------------------
# BinanceClient içine eklenecek WebSocket helper
# -------------------------------------------------------------
async def ws_kline(self, symbol: str, interval: str, callback):
    """
    Kline stream'i başlatır ve gelen verileri callback ile iletir
    """
    url = f"wss://stream.binance.com:9443/ws/{symbol}@kline_{interval}"
    await self.ws_subscribe(url, callback)


