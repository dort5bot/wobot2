
# utils/monitoring.py
# ♦️ Loglama + opsiyonel Telegram alert

import logging
from logging.handlers import RotatingFileHandler
import os
import traceback
import httpx
import asyncio

from utils.config import CONFIG

# --- Log dosyası ---
LOG_DIR = os.getenv("LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "bot.log")


def configure_logging(level=logging.INFO):
    """
    Console ve rotating file log ayarları
    """
    logger = logging.getLogger()
    logger.setLevel(level)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
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
    Async Telegram mesaj gönderici (fire & forget)
    """
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": f"[ALERT] {message}"}
            )
    except Exception:
        traceback.print_exc()


def telegram_alert(message: str):
    """
    Telegram alert gönderir, token/chat_id CONFIG üzerinden alınır.
    Eğer tanımlı değilse silent geçer.
    """
    token = CONFIG.TELEGRAM.BOT_TOKEN
    chat_id = CONFIG.TELEGRAM.ALERT_CHAT_ID

    if not token or not chat_id:
        return

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_send_telegram(token, chat_id, message))
    except RuntimeError:
        # Eğer event loop yoksa blocking çalıştır
        asyncio.run(_send_telegram(token, chat_id, message))
