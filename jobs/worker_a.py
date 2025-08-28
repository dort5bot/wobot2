# jobs/worker_a.py
# veri toplayıcı Queue
# Worker A: ticker ve funding verilerini cache’e koyar (async)
# Config üzerinden interval ve sembol listesi kontrolü
import asyncio
import logging
from utils.config import CONFIG
from utils.stream_manager import StreamManager
from utils.binance_api import BinanceClient

LOG = logging.getLogger("worker_a")

class WorkerA:
    def __init__(self, queue: asyncio.Queue, loop=None):
        self.queue = queue
        self.loop = loop or asyncio.get_event_loop()
        self.client = BinanceClient()
        self.stream_mgr = StreamManager(self.client, loop=self.loop)
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start_async(self):
        if self._running:
            return
        self._running = True

        streams = [f"{s.lower()}@kline_{CONFIG.BINANCE.STREAM_INTERVAL}" 
                   for s in CONFIG.BINANCE.TOP_SYMBOLS_FOR_IO]

        # Stream başlat
        self.stream_mgr.start_combined_groups(streams, self.bridge)
        LOG.info("WorkerA started: %s", streams)

        # Funding poller
        async def funding_loop():
            while self._running:
                try:
                    data = await self.client.get_funding_rates(CONFIG.BINANCE.TOP_SYMBOLS_FOR_IO)
                    await self.queue.put({"funding": data})
                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOG.exception("WorkerA funding poll error")
                await asyncio.sleep(60)

        task = asyncio.create_task(funding_loop(), name="worker_a_funding")
        self._tasks.append(task)

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
        self.stream_mgr.cancel_all()
        for t in self._tasks:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        LOG.info("WorkerA stopped")
