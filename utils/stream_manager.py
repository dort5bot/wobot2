# utils/stream_manager.py
##♦️ grouped combined streams, http fallback scheduler

import asyncio
import logging
import time
from typing import List, Callable
from utils.config import CONFIG
from utils.binance_api import BinanceClient

LOG = logging.getLogger("stream_manager")


class StreamManager:
    """
    Builds grouped combined streams from a symbol list (to respect websocket URL length).
    Provides a simple HTTP fallback scheduler for endpoints missing in WS (e.g. futures funding).
    """

    def __init__(self, client: BinanceClient, loop=None):
        self.client = client
        self.loop = loop or asyncio.get_event_loop()
        self.tasks: List[asyncio.Task] = []

    # ---------------------------------------------------------
    # Stream gruplama (URL uzunluğu limitine karşı)
    # ---------------------------------------------------------
    def group_streams(self, streams: List[str]) -> List[List[str]]:
        group_size = CONFIG.BINANCE.IO_CONCURRENCY  # eski STREAM_GROUP_SIZE yerine
        return [streams[i:i + group_size] for i in range(0, len(streams), group_size)]

    # ---------------------------------------------------------
    # Combined stream başlat (REST fallback yok)
    # ---------------------------------------------------------
    def start_combined_groups(self, streams: List[str], message_handler: Callable):
        groups = self.group_streams(streams)
        LOG.info("Starting %s combined stream groups", len(groups))

        for grp in groups:
            url = f"wss://stream.binance.com:9443/stream?streams={'/'.join(grp)}"

            async def runner():
                await self.client.ws_subscribe(url, message_handler)

            task = self.loop.create_task(runner())
            self.tasks.append(task)

    # ---------------------------------------------------------
    # Funding verisi (REST fallback — çünkü funding WS kullanılmıyor)
    # ---------------------------------------------------------
    def start_periodic_funding_poll(self, symbols: List[str], interval_sec: int, callback: Callable):
        """
        Periodically poll REST funding endpoint (fapi) for symbols since WS funding stream not used here.
        callback: async fn(entry)
        """

        async def runner():
            while True:
                try:
                    for sym in symbols:
                        # funding_rate endpoint
                        res = await self.client.http._request(
                            "GET",
                            "/fapi/v1/fundingRate",
                            {"symbol": sym.upper(), "limit": 1},
                            futures=True,
                        )
                        if res:
                            entry = res[0] if isinstance(res, list) and len(res) else res
                            await callback(entry)
                    await asyncio.sleep(interval_sec)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    LOG.exception("funding poll error: %s", e)
                    await asyncio.sleep(5)

        task = self.loop.create_task(runner())
        self.tasks.append(task)

    # ---------------------------------------------------------
    # Cancel all tasks
    # ---------------------------------------------------------
    def cancel_all(self):
        for t in self.tasks:
            t.cancel()
        self.tasks = []
