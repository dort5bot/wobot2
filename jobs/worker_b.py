# jobs/worker_b.py
# Pipeline: consumer (ingest) + worker pool (process)
# - CPU dostu: wait_for timeout + kısa uyku
# - Hafif storage: deque(maxlen)
# - Backpressure: işlem kuyruğu maxsize ile; dolarsa paket düşür
# - Per-symbol lock: aynı sembolde eşzamanlı yazma/okuma güvenli
# - Sinyal üretici: utils.ta_utils.generate_signals(df)

import asyncio
import logging
import time
from collections import deque, defaultdict
from typing import Dict, Tuple, Optional
import pandas as pd

from utils import ta_utils
from utils import config_worker as CWORKER  # WORKER_B_INTERVAL, (opsiyonel) WORKER_B_WORKERS/PROC_MAXSIZE
from utils.config import CONFIG

LOG = logging.getLogger("worker_b")

KlinePack = Tuple[str, pd.Timestamp, float, float, float, float, float]  # (symbol, ts, o, h, l, c, v)

class WorkerB:
    def __init__(self, queue: asyncio.Queue, signal_callback=None):
        self.in_q = queue                       # WS/WorkerA'dan gelen ham mesaj kuyruğu
        self.signal_callback = signal_callback

        # Storage: sembol -> deque( (ts, o, h, l, c, v) )
        self._candles: Dict[str, deque] = {}

        # Cooldown takibi
        self._last_signal_ts: Dict[str, float] = {}

        # Per-symbol locks (deque & hesap güvenliği)
        self._locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        # Çalışma durumu / görevler
        self._running = False
        self._consumer_task: Optional[asyncio.Task] = None
        self._worker_tasks: list[asyncio.Task] = []

        # Config
        self.cooldown = getattr(CONFIG.BOT, "SIGNAL_COOLDOWN", 60)
        self.history_len = getattr(CONFIG.TA, "HISTORY_WINDOW", 500)
        self.min_candles = getattr(CONFIG.TA, "MIN_CANDLES_FOR_SIGNALS", 50)
        self.interval = getattr(CWORKER, "WORKER_B_INTERVAL", 5)

        # Pipeline: işlem kuyruğu ve havuz büyüklüğü
        self.num_workers = getattr(CWORKER, "WORKER_B_WORKERS", 3)
        self.proc_maxsize = getattr(CWORKER, "WORKER_B_PROC_MAXSIZE", 2000)
        self.proc_q: asyncio.Queue[KlinePack] = asyncio.Queue(maxsize=self.proc_maxsize)

    def start(self):
        if self._running:
            return
        self._running = True
        self._consumer_task = asyncio.create_task(self._consumer(), name="worker_b_consumer")
        self._worker_tasks = [
            asyncio.create_task(self._worker_loop(i), name=f"worker_b_worker_{i}") for i in range(self.num_workers)
        ]
        LOG.info("WorkerB started (workers=%s, proc_maxsize=%s)", self.num_workers, self.proc_maxsize)

    def stop(self):
        self._running = False
        # Görevleri iptal et
        if self._consumer_task:
            self._consumer_task.cancel()
        for t in self._worker_tasks:
            t.cancel()

    # ---------------------------
    # Consumer: ham mesaj -> kapalı mum paketine dönüştürüp işlem kuyruğuna at
    # ---------------------------
    async def _consumer(self):
        try:
            while self._running:
                try:
                    msg = await asyncio.wait_for(self.in_q.get(), timeout=self.interval)
                except asyncio.TimeoutError:
                    await asyncio.sleep(self.interval / 5)
                    continue

                try:
                    data = msg.get("k") if isinstance(msg, dict) else None
                    # Sadece kapanan mumlar
                    if not data or not data.get("x", False):
                        continue

                    symbol = data.get("s")
                    if not symbol:
                        continue

                    pack: KlinePack = (
                        symbol,
                        pd.to_datetime(int(data["t"]), unit="ms"),
                        float(data["o"]),
                        float(data["h"]),
                        float(data["l"]),
                        float(data["c"]),
                        float(data["v"]),
                    )

                    # Backpressure: doluysa düşür (en azından logla)
                    try:
                        self.proc_q.put_nowait(pack)
                    except asyncio.QueueFull:
                        LOG.warning("WorkerB proc_q full; dropping kline %s @ %s", pack[0], pack[1])

                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOG.exception("WorkerB consumer error")
                finally:
                    # Ham girdi kuyruğunu tamamla
                    try:
                        self.in_q.task_done()
                    except Exception:
                        pass

                # Kısa uyku → CPU dostu
                await asyncio.sleep(self.interval / 5)

        except asyncio.CancelledError:
            LOG.info("WorkerB consumer cancelled")

    # ---------------------------
    # Worker: işlem kuyruğundaki paketleri işler, sinyal üretir
    # ---------------------------
    async def _worker_loop(self, wid: int):
        try:
            while self._running:
                try:
                    pack = await asyncio.wait_for(self.proc_q.get(), timeout=self.interval)
                except asyncio.TimeoutError:
                    await asyncio.sleep(self.interval / 5)
                    continue

                try:
                    symbol, ts, o, h, l, c, v = pack

                    # Sembol özelinde tekil erişim
                    lock = self._locks[symbol]
                    async with lock:
                        dq = self._candles.get(symbol)
                        if dq is None:
                            dq = deque(maxlen=self.history_len)
                            self._candles[symbol] = dq
                        dq.append((ts, o, h, l, c, v))

                        if len(dq) < self.min_candles:
                            continue

                        # df sadece gerektiğinde oluşturulur (deque → DataFrame)
                        df = pd.DataFrame(list(dq), columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")

                        # TA sinyali
                        sig_res = ta_utils.generate_signals(df)
                        signal_val = sig_res.get("signal", 0)
                        alpha_score = sig_res.get("alpha_ta", {}).get("score", 0.0)
                        score = float(sig_res.get("score", 0.0))

                        now = time.time()
                        if signal_val != 0 and (now - self._last_signal_ts.get(symbol, 0)) >= self.cooldown:
                            side = "BUY" if signal_val == 1 else "SELL"
                            decision = {
                                "symbol": symbol,
                                "side": side,
                                "score": score,
                                "alpha_score": alpha_score,
                                "indicators": sig_res.get("indicators", {}),
                            }
                            if self.signal_callback:
                                await self.signal_callback("ta_utils", symbol, side, strength=abs(score), payload=decision)
                            self._last_signal_ts[symbol] = now
                            LOG.info("[W%s] Signal %s %s score=%.3f alpha=%.3f", wid, symbol, side, score, alpha_score)

                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOG.exception("WorkerB worker error (id=%s)", wid)
                finally:
                    try:
                        self.proc_q.task_done()
                    except Exception:
                        pass

                # Kısa uyku → CPU dostu
                await asyncio.sleep(self.interval / 5)

        except asyncio.CancelledError:
            LOG.info("WorkerB worker cancelled (id=%s)", wid)
