# jobs/worker_a.py
# veri toplayıcı Queue
# Worker A: ticker ve funding verilerini cache’e koyar (async)
# Config üzerinden interval ve sembol listesi kontrolü

# jobs/worker_a.py
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

    def start(self):
        self._running = True
        streams = [f"{s.lower()}@kline_{CONFIG.BINANCE.STREAM_INTERVAL}" for s in CONFIG.BINANCE.TOP_SYMBOLS_FOR_IO]
        self.stream_mgr.start_combined_groups(streams, self.bridge)
        LOG.info("WorkerA started: %s", streams)

        self.stream_mgr.start_periodic_funding_poll(
            CONFIG.BINANCE.TOP_SYMBOLS_FOR_IO,
            interval_sec=60,
            callback=lambda data: asyncio.create_task(self.queue.put({"funding": data}))
        )

    async def bridge(self, msg):
        """Stream mesajlarını queue'ya aktarır"""
        await self.queue.put(msg)

    def stop(self):
        self._running = False
        self.stream_mgr.cancel_all()
        LOG.info("WorkerA stopped")
