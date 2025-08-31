# jobs/worker_a.py
"""
WorkerA: Binance'den veri toplayıcı - YENİ MİMARİ
- Sadece .env'deki GLOBAL API'yi kullanır
- Kişisel API'lerle hiçbir işlem yapmaz
- Sadece market verisi toplar
"""

import asyncio
import logging
from utils.config import CONFIG
from utils.binance_api import BinanceClient

LOG = logging.getLogger("worker_a")

class WorkerA:
    def __init__(self, queue: asyncio.Queue):
        self.queue = queue
        self._running = False
        self._tasks = []
        
        # 🔹 SADECE global API client'ı (kişisel API yok)
        self.client = BinanceClient(
            CONFIG.BINANCE.API_KEY, 
            CONFIG.BINANCE.SECRET_KEY
        )
        LOG.info("WorkerA initialized with global API")

    async def start_async(self):
        if self._running:
            return
        self._running = True

        # Mevcut event loop'u kullan
        loop = asyncio.get_event_loop()

        # Kline stream'leri başlat
        for symbol in CONFIG.BINANCE.TOP_SYMBOLS_FOR_IO:
            task = loop.create_task(
                self.client.ws_kline(symbol.lower(), CONFIG.BINANCE.STREAM_INTERVAL, self.bridge),
                name=f"ws_kline_{symbol}"
            )
            self._tasks.append(task)

        # Funding poller
        task = loop.create_task(self._funding_loop(), name="funding_poller")
        self._tasks.append(task)

        LOG.info("WorkerA started with symbols: %s", CONFIG.BINANCE.TOP_SYMBOLS_FOR_IO)

    async def _funding_loop(self):
        while self._running:
            try:
                data = {}
                for symbol in CONFIG.BINANCE.TOP_SYMBOLS_FOR_IO:
                    try:
                        # Global API ile funding rate al
                        fr = await self.client.get_funding_rate(symbol)
                        data[symbol] = fr
                    except Exception as e:
                        LOG.warning(f"Funding rate alınamadı ({symbol}): {e}")
                await self.queue.put({"funding": data})
            except asyncio.CancelledError:
                break  # Clean exit
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

        # Tüm task'leri iptal et
        for t in self._tasks:
            if not t.done():
                t.cancel()
        
        # Tüm task'leri bekle
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        
        LOG.info("WorkerA stopped")
