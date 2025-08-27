# handlers/alerts_handler.py
##♦️merkezi telegram alert wrapper
#alerts_handler.py → Telegram’a mesaj atmak gibi genel amaçlı uyarılar içindir


import os
from utils.monitoring import telegram_alert, configure_logging
import logging

LOG = logging.getLogger("alerts_handler")

def alert_info(msg: str):
    LOG.info("ALERT: %s", msg)
    telegram_alert(msg)

def alert_error(msg: str):
    LOG.error("ALERT: %s", msg)
    telegram_alert(msg)
