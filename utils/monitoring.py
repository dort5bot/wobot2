# utils/monitoring.py
# ♦️ Loglama + opsiyonel Telegram alert + TA dashboard

import logging
from logging.handlers import RotatingFileHandler
import os
import traceback
import httpx
import asyncio
import time
import pandas as pd

from utils.config import CONFIG
from utils.ta_utils import compute_alpha_ta
from utils.health import health_check


# --- Log dosyası ayarları ---
LOG_DIR = os.getenv("LOG_DIR", "logs")
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
        async with httpx.AsyncClient(timeout=3) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": f"[ALERT] {message}"}
            )
    except Exception:
        logging.error("Telegram mesajı gönderilirken hata oluştu:")
        traceback.print_exc()


def telegram_alert(message: str):
    """
    Telegram alert gönderir, token/chat_id CONFIG üzerinden alınır.
    Eğer tanımlı değilse uyarı loglar, sessizce geçer.
    """
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


async def generate_ta_dashboard(symbol: str, df: pd.DataFrame):
    """
    TA metrikleri, Binance metrikleri ve sistem sağlığı içeren bir dashboard oluşturur.
    """
    from utils.ta_utils import calculate_all_ta_hybrid
    from utils.binance_api import get_binance_api

    # Teknik analiz hesaplamaları
    ta_results = calculate_all_ta_hybrid(df)
    alpha_result = compute_alpha_ta(df)

    # Binance metrikleri
    client = get_binance_api()
    binance_metrics = await client.pro_metrics_aggregator(symbol)

    # Dashboard çıktısı
    return {
        "symbol": symbol,
        "timestamp": time.time(),
        "price": float(df['close'].iloc[-1]),
        "ta_metrics": {
            k: float(v.iloc[-1]) if hasattr(v, 'iloc') else v
            for k, v in ta_results.items() if v is not None
        },
        "alpha_metrics": alpha_result,
        "binance_metrics": binance_metrics,
        "system_health": await health_check()
    }
