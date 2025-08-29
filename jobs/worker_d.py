# jobs/worker_d.py
'''
Trading Pipeline Entegrasyonu içindir
ta_utils.py ile ilişkilidir
'''
import asyncio
import logging
from utils.binance_api import get_binance_api
from utils.ta_utils import calculate_all_ta_hybrid, generate_signals, klines_to_dataframe

LOG = logging.getLogger("worker_d")

class WorkerD:
    def __init__(self, signal_callback=None):
        self.signal_callback = signal_callback
        self._running = False
        self._task = None

    async def start_async(self):
        self._running = True
        self._task = asyncio.create_task(self._trading_loop())
        LOG.info("WorkerD started")

    async def stop_async(self):
        self._running = False
        if self._task:
            self._task.cancel()
            with asyncio.CancelledError():
                await self._task
        LOG.info("WorkerD stopped")

    async def _trading_loop(self, symbol: str = "BTCUSDT", interval: str = "1m"):
        """Gerçek zamanlı trading pipeline"""
        client = get_binance_api()
        
        while self._running:
            try:
                # 1. Veriyi çek
                klines = await client.get_klines(symbol, interval, limit=100)
                df = klines_to_dataframe(klines)
                
                # 2. TA hesapla
                ta_results = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: calculate_all_ta_hybrid(df, symbol)
                )
                
                # 3. Sinyal üret
                signal = generate_signals(df)
                
                # 4. Callback ile sinyali gönder
                if self.signal_callback and signal['signal'] != 0:
                    await self.signal_callback({
                        'symbol': symbol,
                        'signal': signal['signal'],
                        'score': signal['score'],
                        'alpha_score': signal['alpha_ta']['score'],
                        'timestamp': asyncio.get_event_loop().time()
                    })
                
                # 5. Interval kadar bekle
                await asyncio.sleep(CONFIG.TA.PIPELINE_INTERVAL or 60)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                LOG.error(f"Trading pipeline error: {e}")
                await asyncio.sleep(30)
