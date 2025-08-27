##signal_evaluator.py
#gelismis async

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
    """
    Async uyumlu sinyal toplayıcı ve aggregator.
    Worker B'de kullanıma hazır.
    """
    def __init__(self, decision_callback=None, loop=None, window_seconds: int = 10, threshold: float = 0.3):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.loop = loop or asyncio.get_event_loop()
        self.window_seconds = window_seconds
        self.threshold = threshold
        self.decision_callback = decision_callback
        self.running = False
        self.buf: Dict[str, list] = {}

    async def publish(self, signal: Signal):
        try:
            db.log_signal(signal.symbol, signal.type, signal.strength, json.dumps(signal.payload), source=signal.source)
        except Exception:
            pass
        await self.queue.put(signal)

    async def evaluate(self, ctx: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """
        Async sinyal toplama ve karar döndürme.
        ctx: {"tickers": ..., "funding": ...}
        """
        # Burada örnek olarak semboller üzerinden işlem yapıyoruz
        signals = {}
        for symbol, ticker in ctx.get("tickers", {}).items():
            # async sinyal oluşturma simülasyonu
            sig = Signal(source="worker_b", symbol=symbol, type_="BUY", strength=0.5)
            await self.publish(sig)
            decision = self._aggregate_and_decide(symbol)
            signals[symbol] = decision or {"decision": "HOLD"}
        return signals

    async def _process_loop(self):
        self.running = True
        while self.running:
            try:
                sig: Signal = await self.queue.get()
                self._buffer_signal(sig)
                decision = self._aggregate_and_decide(sig.symbol)
                if decision and self.decision_callback:
                    res = self.decision_callback(decision)
                    if asyncio.iscoroutine(res):
                        await res
                self.queue.task_done()
            except Exception as e:
                print("SignalEvaluator loop error:", e)

    def _buffer_signal(self, sig: Signal):
        lst = self.buf.setdefault(sig.symbol, [])
        lst.append((sig.ts, sig))
        cutoff = time.time() - self.window_seconds
        self.buf[sig.symbol] = [(t,s) for t,s in lst if t >= cutoff]

    def _aggregate_and_decide(self, symbol: str) -> Optional[Dict[str, Any]]:
        items = self.buf.get(symbol, [])
        if not items:
            return {"decision": "HOLD"}
        buy = sum(s.strength for _, s in items if s.type == "BUY") / max(1, len(items))
        sell = sum(s.strength for _, s in items if s.type == "SELL") / max(1, len(items))
        diff = buy - sell
        decision = "HOLD"
        if diff >= self.threshold:
            decision = "BUY"
        elif diff <= -self.threshold:
            decision = "SELL"
        return {
            "symbol": symbol,
            "decision": decision,
            "strength": abs(diff),
            "reason": f"agg_buy={buy:.3f}, agg_sell={sell:.3f}, diff={diff:.3f}",
            "signals": [s.to_dict() for _, s in items],
            "ts": time.time()
        }

    def start(self):
        self.loop.create_task(self._process_loop())

    def stop(self):
        self.running = False
