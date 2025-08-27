# utils/signal_evaluator.py
import asyncio
from typing import Dict, Any, Optional
from utils import db
import time
import json

class Signal:
    def __init__(self, source: str, symbol: str, type_: str, strength: float = 0.5, payload: Optional[Dict] = None):
        self.source = source
        self.symbol = symbol.upper()
        self.type = type_.upper()
        self.strength = float(strength)
        self.payload = payload or {}
        self.ts = time.time()

    def to_dict(self):
        return {
            "source": self.source,
            "symbol": self.symbol,
            "type": self.type,
            "strength": self.strength,
            "payload": self.payload,
            "ts": self.ts
        }

class SignalEvaluator:
    def __init__(self, decision_callback=None, loop=None, window_seconds: int = 10, threshold: float = 0.3):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.loop = loop or asyncio.get_event_loop()
        self.window_seconds = window_seconds
        self.threshold = threshold
        self.decision_callback = decision_callback
        self.running = False
        self.buf = {}

    async def publish(self, signal: Signal):
        try:
            db.log_signal(signal.symbol, signal.type, signal.strength, json.dumps(signal.payload), source=signal.source)
        except Exception:
            pass
        await self.queue.put(signal)

    async def _process_loop(self):
        self.running = True
        while self.running:
            try:
                sig = await self.queue.get()
                self._buffer_signal(sig)
                decision = self._aggregate_and_decide(sig.symbol)
                if decision:
                    try:
                        db.log_decision(decision["symbol"], decision["decision"], decision["strength"], decision.get("reason", ""))
                    except Exception:
                        pass
                    if self.decision_callback:
                        res = self.decision_callback(decision)
                        if asyncio.iscoroutine(res):
                            await res
                self.queue.task_done()
            except Exception as e:
                print("SignalEvaluator loop error:", e)

    def _buffer_signal(self, sig: Signal):
        lst = self.buf.setdefault(sig.symbol, [])
        lst.append( (sig.ts, sig) )
        cutoff = time.time() - self.window_seconds
        self.buf[sig.symbol] = [ (t,s) for (t,s) in lst if t >= cutoff ]

    def _aggregate_and_decide(self, symbol: str) -> Optional[Dict[str, Any]]:
        items = self.buf.get(symbol, [])
        if not items:
            return None
        buy = 0.0
        sell = 0.0
        signals_list = []
        for _, s in items:
            signals_list.append(s.to_dict())
            if s.type == "BUY":
                buy += s.strength
            elif s.type == "SELL":
                sell += s.strength
        count = max(1, len(items))
        buy /= count
        sell /= count
        diff = buy - sell
        decision = "HOLD"
        strength = abs(diff)
        reason = f"agg_buy={buy:.3f}, agg_sell={sell:.3f}, diff={diff:.3f}"
        if diff >= self.threshold:
            decision = "BUY"
        elif diff <= -self.threshold:
            decision = "SELL"
        else:
            decision = "HOLD"
        return {
            "symbol": symbol,
            "decision": decision,
            "strength": strength,
            "reason": reason,
            "signals": signals_list,
            "ts": time.time()
        }

    def start(self):
        self.loop.create_task(self._process_loop())

    def stop(self):
        self.running = False
