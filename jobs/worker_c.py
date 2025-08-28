# jobs/worker_c.py
# trade yönetici. ordermanager wrapper
#

import asyncio
import logging
from utils.order_manager import OrderManager
from utils.config import CONFIG

LOG = logging.getLogger("worker_c")

class WorkerC:
    def __init__(self):
        self.order_manager = OrderManager(paper_mode=CONFIG.BOT.PAPER_MODE)
        self.queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._running = False

    async def start_async(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="worker_c")
        LOG.info("WorkerC started")

    async def stop_async(self):
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        LOG.info("WorkerC stopped")

    async def send_decision(self, decision: dict):
        await self.queue.put(decision)

    async def _run(self):
        try:
            while self._running:
                decision = await self.queue.get()
                try:
                    await self.order_manager.process_decision(decision)
                    LOG.info("Order executed: %s", decision)
                except Exception:
                    LOG.exception("WorkerC process_decision failed")
                finally:
                    self.queue.task_done()
        except asyncio.CancelledError:
            LOG.info("WorkerC cancelled")

