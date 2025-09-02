# utils/monitoring.py-901-2224
# utils/monitoring.py - Güncellenmiş ve Ta_utils ile Uyumlu
import logging
from logging.handlers import RotatingFileHandler
import os
import traceback
import httpx
import asyncio
import time
import pandas as pd
from typing import Dict, Any
from dataclasses import dataclass

try:
    from utils.config import CONFIG
except ImportError:
    # Fallback config
    @dataclass
    class SafeConfig:
        class TELEGRAM:
            BOT_TOKEN = None
            ALERT_CHAT_ID = None
            ENABLED = False
            NOTIFICATION_LEVEL = "INFO"

    CONFIG = SafeConfig()

try:
    from utils.ta_utils import calculate_all_ta_hybrid, alpha_ta, health_check
except ImportError:
    # Fallback functions
    def calculate_all_ta_hybrid(*args, **kwargs):
        return {}

    def alpha_ta(*args, **kwargs):
        return {}

    def health_check():
        return {"status": "unknown", "error": "ta_utils not available"}

try:
    from utils.binance_api import get_binance_api
except ImportError:
    # Fallback binance API
    async def get_binance_api():
        class DummyClient:
            async def pro_metrics_aggregator(self, symbol):
                return {}

        return DummyClient()


# --- Log dosyası ayarları ---
LOG_DIR = os.getenv("LOG_DIR", "wobot1/logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "bot.log")


def configure_logging(level=logging.INFO):
    """
    Console ve rotating file log ayarları.
    Aynı handler tekrar eklenmez.
    """
    logger = logging.getLogger()
    logger.setLevel(level)
    if not logger.handlers:  # Tekrarlı handler eklenmesini engelle
        # Formatter
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

        # Console handler
        ch = logging.StreamHandler()
        ch.setLevel(level)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

        # Rotating file handler
        fh = RotatingFileHandler(LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5)
        fh.setLevel(level)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger


async def _send_telegram(token: str, chat_id: str, message: str):
    """
    Async Telegram mesaj gönderici (fire & forget).
    """
    try:
        message = message[:4096]  # Telegram mesaj limiti
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": f"[ALERT] {message}"}
            )
    except Exception as e:
        logging.error(f"Telegram mesajı gönderilirken hata oluştu: {e}")
        traceback.print_exc()


def telegram_alert(message: str):
    """
    Telegram alert gönderir, token/chat_id CONFIG üzerinden alınır.
    Eğer tanımlı değilse uyarı loglar, sessizce geçer.
    """
    try:
        token = CONFIG.TELEGRAM.BOT_TOKEN
        chat_id = CONFIG.TELEGRAM.ALERT_CHAT_ID
        if not token or not chat_id:
            logging.warning("Telegram token/chat_id tanımlı değil. Alert gönderilemiyor.")
            return

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_send_telegram(token, chat_id, message))
        except RuntimeError:
            # Eğer event loop yoksa blocking olarak çalıştır
            asyncio.run(_send_telegram(token, chat_id, message))
    except Exception as e:
        logging.error(f"Telegram alert sırasında hata: {e}")


async def generate_ta_dashboard(symbol: str, df: pd.DataFrame) -> Dict[str, Any]:
    """
    TA metrikleri, Binance metrikleri ve sistem sağlığı içeren bir dashboard oluşturur.
    """
    try:
        # Teknik analiz hesaplamaları
        ta_results = calculate_all_ta_hybrid(df, symbol)
        alpha_result = alpha_ta(df)

        # Binance metrikleri
        client = await get_binance_api()
        binance_metrics = await client.pro_metrics_aggregator(symbol)

        # Dashboard çıktısı
        return {
            "symbol": symbol,
            "timestamp": time.time(),
            "price": float(df['close'].iloc[-1]) if len(df) > 0 else 0.0,
            "ta_metrics": {
                k: float(v.iloc[-1]) if hasattr(v, 'iloc') and len(v) > 0 else v
                for k, v in ta_results.items() if v is not None
            },
            "alpha_metrics": alpha_result,
            "binance_metrics": binance_metrics,
            "system_health": health_check()
        }
    except Exception as e:
        logging.error(f"TA dashboard oluşturulurken hata: {e}")
        return {
            "symbol": symbol,
            "timestamp": time.time(),
            "error": str(e),
            "system_health": health_check()
        }


class PerformanceMonitor:
    """Performans izleme ve metrik toplama sınıfı"""

    def __init__(self):
        self.metrics = {
            "ta_calculations": 0,
            "api_calls": 0,
            "errors": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "start_time": time.time()
        }
        self._lock = asyncio.Lock()

    async def increment(self, metric: str, value: int = 1):
        """Metrik değerini artırır"""
        async with self._lock:
            if metric in self.metrics:
                self.metrics[metric] += value

    async def get_metrics(self) -> Dict[str, Any]:
        """Tüm metrikleri döndürür"""
        async with self._lock:
            uptime = time.time() - self.metrics["start_time"]
            metrics = self.metrics.copy()
            metrics["uptime_seconds"] = uptime
            metrics["uptime_hours"] = uptime / 3600
            metrics["requests_per_minute"] = metrics["api_calls"] / (uptime / 60) if uptime > 0 else 0
            return metrics

    async def reset(self):
        """Metrikleri sıfırlar"""
        async with self._lock:
            self.metrics = {
                "ta_calculations": 0,
                "api_calls": 0,
                "errors": 0,
                "cache_hits": 0,
                "cache_misses": 0,
                "start_time": time.time()
            }


# Global performance monitor instance
performance_monitor = PerformanceMonitor()


async def log_performance_metrics(interval: int = 300):
    """Belirli aralıklarla performans metriklerini loglar"""
    while True:
        try:
            metrics = await performance_monitor.get_metrics()
            logging.info(f"Performance Metrics: {metrics}")
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"Performance logging error: {e}")
            await asyncio.sleep(interval)


# Logger'ı başlat
configure_logging()


if __name__ == "__main__":
    # Test code
    import asyncio

    async def test():
        # Test data
        data = {
            'open': [100, 101, 102, 103, 104, 105, 106, 107, 108, 109],
            'high': [105, 106, 107, 108, 109, 110, 111, 112, 113, 114],
            'low': [95, 96, 97, 98, 99, 100, 101, 102, 103, 104],
            'close': [102, 103, 104, 105, 106, 107, 108, 109, 110, 111],
            'volume': [1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000]
        }
        df = pd.DataFrame(data)

        # Test dashboard
        dashboard = await generate_ta_dashboard("BTCUSDT", df)
        print("Dashboard:", dashboard)

        # Test performance monitor
        await performance_monitor.increment("ta_calculations")
        await performance_monitor.increment("api_calls", 3)
        metrics = await performance_monitor.get_metrics()
        print("Metrics:", metrics)

    asyncio.run(test())

# EOF

