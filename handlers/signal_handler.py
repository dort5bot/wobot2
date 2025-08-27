# handlers/signal_handler.py
from utils.signal_evaluator import Signal
from typing import Dict, Any

evaluator = None

def set_evaluator(ev):
    global evaluator
    evaluator = ev

async def publish_signal(source: str, symbol: str, type_: str, strength: float = 0.5, payload: Dict = None):
    if evaluator is None:
        print("SignalEvaluator not set; dropping signal", source, symbol, type_)
        return
    sig = Signal(source=source, symbol=symbol, type_=type_, strength=strength, payload=payload)
    await evaluator.publish(sig)
