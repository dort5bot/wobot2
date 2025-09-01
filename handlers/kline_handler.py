# handlers/kline_handler.py
# Kline worker: TA göstergeleri kullanılarak sinyal üretir.
# Değişiklik: merkezi olması için utils.ta_utils modülünü `import as indicators` şeklinde kullandım.
# Böylece kod içinde indicators.rsi / indicators.macd kullanılacaktır.

import asyncio
from collections import deque
import logging

from utils import ta_utils as indicators
from handlers.signal_handler import publish_signal

LOG = logging.getLogger("kline_handler")
LOG.addHandler(logging.NullHandler())

async def kline_worker(queue: asyncio.Queue, symbol: str, interval: str = "1m", lookback: int = 500):
    """
    queue: asyncio.Queue içinde Binance websocket kline event'leri (packed dict) beklenir
    symbol: örn "BTCUSDT"
    interval: "1m", "5m", ...
    lookback: kaç close saklanacak
    """
    closes = deque(maxlen=lookback)

    while True:
        data = await queue.get()
        try:
            # Event yapısı tipik olarak { "s": symbol, "k": {...} }
            if not isinstance(data, dict):
                queue.task_done()
                continue

            if data.get("s") and data.get("s") != symbol:
                queue.task_done()
                continue

            k = data.get("k", {})
            # yalnızca kapanmış mumları işleyelim
            if not k.get("x", False):
                queue.task_done()
                continue

            # close price
            close = None
            try:
                close = float(k.get("c"))
            except Exception:
                # hatalı veri gelirse atla
                queue.task_done()
                continue

            closes.append(close)
            arr = list(closes)

            # indicators modulü üzerinden hesaplar
            # rsi: dizi, period default 14 -> ta_utils.rsi(arr, period=14) benzeri
            try:
                rsi_list = indicators.rsi(arr, period=14)
            except Exception as e:
                LOG.debug("RSI hesaplanamadı: %s", e)
                rsi_list = []

            rsi_val = rsi_list[-1] if rsi_list else None

            try:
                macd_line, macd_signal, macd_hist = indicators.macd(arr)
            except Exception as e:
                LOG.debug("MACD hesaplanamadı: %s", e)
                macd_line, macd_signal, macd_hist = (None, None, None)

            macd_h = macd_hist[-1] if macd_hist else None

            # Basit al/sat mantığı (örnek)
            if rsi_val is not None and macd_h is not None:
                # Güçlü BUY sinyali
                if rsi_val < 30 and macd_h > 0:
                    await publish_signal("kline_rsi_macd", {"symbol": symbol, "action": "BUY", "price": close, "rsi": rsi_val, "macd_h": macd_h})
                # Güçlü SELL sinyali
                elif rsi_val > 70 and macd_h < 0:
                    await publish_signal("kline_rsi_macd", {"symbol": symbol, "action": "SELL", "price": close, "rsi": rsi_val, "macd_h": macd_h})
                else:
                    # opsiyonel: nötr veya zayıf sinyal publish edilebilir
                    pass

        except Exception as e:
            LOG.exception("kline_worker hata: %s", e)
        finally:
            queue.task_done()
