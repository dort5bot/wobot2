# handlers/ap_handler.py
import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from utils.ap_utils import build_ap_report_lines_pro
from utils.binance_api import BinanceClient
from utils.config import CONFIG

LOG = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())

# /ap [symbols...] -> Altcoin short skorları
async def ap_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("AP skor raporu hazırlanıyor... ⏳")
    try:
        # Kullanıcı komutundan coin listesi al
        symbols = context.args if context.args else ["BTCUSDT","ETHUSDT","SOLUSDT"]

        # BinanceClient singleton
        client = BinanceClient(CONFIG.BINANCE_API_KEY, CONFIG.BINANCE_SECRET_KEY)

        # Skorları hesapla
        lines = await build_ap_report_lines_pro(client=client, symbols=symbols)

        # Mesajı güncelle
        text = "\n".join(lines) if lines else "⚠️ Skor bulunamadı."
        await msg.edit_text(text)
    except Exception as e:
        LOG.exception("AP handler error:")
        await msg.edit_text(f"❌ Hata oluştu: {e}")

# Handler kaydı
def register(application):
    application.add_handler(CommandHandler("ap", ap_handler))
