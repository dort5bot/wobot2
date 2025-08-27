# handlers/ap_handler
#+debug lu
import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from utils.ap_utils import build_ap_report_lines_pro
from utils.apikey_utils import get_apikey
from utils.binance_api import BinanceClient

LOG = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())

# --- /ap [symbols...] -> Altcoin short skorları (Debug + API Key entegre) ---
async def ap_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("AP skor raporu hazırlanıyor... ⏳")
    try:
        user_id = update.effective_user.id

        # Kullanıcıdan coin listesi al
        symbols = context.args if context.args else ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

        # Kullanıcının şifreli API key’i DB’den al
        user_key = get_apikey(user_id)
        if not user_key:
            await msg.edit_text("❌ API key bulunamadı. Lütfen /apikey ile girin.")
            return

        # API key ve secret ayrıştır
        try:
            api_key, secret_key = user_key.split(":")
        except ValueError:
            await msg.edit_text("❌ API key format hatası. Lütfen yeniden girin (/apikey).")
            return

        LOG.debug(f"User {user_id} API key alındı.")

        # BinanceClient oluştur
        client = BinanceClient(api_key, secret_key)

        # Skorları hesapla
        try:
            results = await build_ap_report_lines_pro(client=client, symbols=symbols)
            LOG.debug(f"build_ap_report_lines_pro sonuçları: {results}")
        except Exception as e:
            LOG.exception("AP raporu oluşturulurken hata oluştu")
            await msg.edit_text(f"❌ Skor hesaplanamadı: {e}")
            return

        if not results:
            await msg.edit_text(
                "⚠️ Skorlar boş döndü. API key doğru mu? Binance verisi erişilebilir mi?"
            )
            return

        # Mesajı güncelle
        text = "\n".join(results)
        await msg.edit_text(text)

    except Exception as e:
        LOG.exception("AP handler genel hata:")
        await msg.edit_text(f"❌ Hata oluştu: {e}")


# --- Handler register ---
def register(application):
    application.add_handler(CommandHandler("ap", ap_handler))
