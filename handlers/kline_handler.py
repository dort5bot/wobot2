# handlers/kline_handler.py
#al sat sinyali oluşturur 
#ta_utils’teki tüm göstergeleri kullanan ve gelişmiş al/sat sinyali üreten güncellenmiş bir kline_handler.py örneği hazırlat


import asyncio
from collections import deque
from utils.ta_utils import rsi, macd
from handlers.signal_handler import publish_signal

async def kline_worker(queue: asyncio.Queue, symbol: str, interval: str = "1m", lookback: int = 500):
    closes = deque(maxlen=lookback)
    while True:
        data = await queue.get()
        try:
            if data.get("s") != symbol:
                queue.task_done()
                continue
            k = data.get("k", {})
            if not k.get("x", False):
                queue.task_done()
                continue
            close = float(k.get("c"))
            closes.append(close)
            arr = list(closes)
            rsi_list = indicators.rsi(arr, period=14)
            rsi_val = rsi_list[-1] if rsi_list else None
            macd_line, macd_signal, macd_hist = indicators.macd(arr)
            macd_h = macd_hist[-1] if macd_hist else None
            if rsi_val is not None and macd_h is not None:
                if rsi_val < 30 and macd_h > 0:
                    await publish_signal("kline_rsi_macd", symbol, "BUY", strength=0.6, payload={"price": close, "rsi": rsi_val, "macd_h": macd_h})
                elif rsi_val > 70 and macd_h < 0:
                    await publish_signal("kline_rsi_macd", symbol, "SELL", strength=0.6, payload={"price": close, "rsi": rsi_val, "macd_h": macd_h})
        except Exception as e:
            print("kline_worker error", e)
        finally:
            queue.task_done()
