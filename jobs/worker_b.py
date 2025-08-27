# jobs/worker_b.py
# async uyumlu, PTB v20+ uyumlu 
# 1. config_worker.WORKER_B_INTERVAL üzerinden interval ayarlanabilir
# 2. Döngü sleep süresi CPU dostu, interval’in 1/5’i kadar bekler
# 3. DataFrame append optimizasyonu (concat yerine df.loc[ts] = row)
# 4. Sinyal üretici ta_utils.py ile

import asyncio
import logging
import time
import pandas as pd
from typing import Dict, Optional
from utils import ta_utils
from utils import config_worker as CWORKER
from utils.config import CONFIG

LOG = logging.getLogger("worker_b")

class WorkerB:
    def __init__(self, queue: asyncio.Queue, signal_callback=None):
        self.queue = queue
        self.signal_callback = signal_callback
        self._dfs: Dict[str, pd.DataFrame] = {}
        self._last_signal_ts: Dict[str, float] = {}
        self._task = None
        self._running = False
        self.cooldown = getattr(CONFIG.BOT, "SIGNAL_COOLDOWN", 60)
        self.history_len = getattr(CONFIG.TA, "HISTORY_WINDOW", 500)

    def start(self):
        self._running = True
        self._task = asyncio.create_task(self._run(), name="worker_b")
        LOG.info("WorkerB started")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _run(self):
        try:
            while self._running:
                try:
                    # queue’dan mesaj çek (timeout ile CPU dostu)
                    msg = await asyncio.wait_for(self.queue.get(), timeout=CWORKER.WORKER_B_INTERVAL)
                except asyncio.TimeoutError:
                    # hiç mesaj gelmezse biraz uyku (CPU dostu)
                    await asyncio.sleep(CWORKER.WORKER_B_INTERVAL / 5)
                    continue

                try:
                    data = msg.get("k") if isinstance(msg, dict) else None
                    if not data or not data.get("x", False):  # sadece kapanan mumlar
                        self.queue.task_done()
                        continue

                    symbol = data.get("s")
                    if not symbol:
                        self.queue.task_done()
                        continue

                    ts = pd.to_datetime(int(data["t"]), unit="ms")
                    row = {
                        "open": float(data["o"]),
                        "high": float(data["h"]),
                        "low": float(data["l"]),
                        "close": float(data["c"]),
                        "volume": float(data["v"]),
                    }

                    # DataFrame oluştur/append (concat yerine loc ataması)
                    df = self._dfs.get(symbol)
                    if df is None or df.empty:
                        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
                    df.loc[ts] = row

                    # history limit uygula
                    if len(df) > self.history_len:
                        df = df.iloc[-self.history_len :]
                    self._dfs[symbol] = df

                    # minimum mum sayısı kontrolü
                    if len(df) < getattr(CONFIG.TA, "MIN_CANDLES_FOR_SIGNALS", 50):
                        self.queue.task_done()
                        continue

                    # TA sinyali üret
                    sig_res = ta_utils.generate_signals(df)
                    signal_val = sig_res.get("signal", 0)
                    alpha = sig_res.get("alpha_ta", {})
                    alpha_score = alpha.get("score", 0.0)

                    now = time.time()
                    if signal_val != 0 and (now - self._last_signal_ts.get(symbol, 0)) >= self.cooldown:
                        side = "BUY" if signal_val == 1 else "SELL"
                        decision = {
                            "symbol": symbol,
                            "side": side,
                            "score": sig_res.get("score", 0.0),
                            "alpha_score": alpha_score,
                            "indicators": sig_res.get("indicators", {}),
                        }
                        if self.signal_callback:
                            await self.signal_callback(
                                "ta_utils", symbol, side, strength=abs(decision["score"]), payload=decision
                            )
                        self._last_signal_ts[symbol] = now
                        LOG.info("Signal %s %s score=%.3f alpha=%.3f", symbol, side, decision["score"], alpha_score)

                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOG.exception("WorkerB error")
                finally:
                    self.queue.task_done()

                # Döngüde interval’in 1/5’i kadar uyku → CPU dostu
                await asyncio.sleep(CWORKER.WORKER_B_INTERVAL / 5)

        except asyncio.CancelledError:
            LOG.info("WorkerB cancelled")
