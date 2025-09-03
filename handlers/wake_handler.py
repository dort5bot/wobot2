#wake_handler.py
import os
import requests
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler

# Çevresel değişkenler: bunları .env veya Render Dashboard üzerinden ayarlayabilirsin
RENDER_URL = os.getenv("RENDER_URL", "https://senin-bot.onrender.com/")
RENDER_API_KEY = os.getenv("RENDER_API_KEY", "")
RENDER_SERVICE_ID = os.getenv("RENDER_SERVICE_ID", "")

# Render API endpoint şablonları
DEPLOY_URL = f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/deploys"

HEADERS = {
    "Authorization": f"Bearer {RENDER_API_KEY}",
    "Content-Type": "application/json",
}

def wake(update: Update, context: CallbackContext):
    """Ping atarak botu uyandırır."""
    try:
        resp = requests.get(RENDER_URL, timeout=10)
        if resp.status_code == 200:
            update.message.reply_text("✅ Bot başarıyla uyandırıldı!")
        else:
            update.message.reply_text(f"⚠️ Servis yanıt verdi ama status: {resp.status_code}")
    except Exception as e:
        update.message.reply_text(f"❌ Hata (uyandırma): {e}")

def deploy(update: Update, context: CallbackContext, clear_cache=False):
    """Deploy başlatır; clear_cache=True ise cache temizlenir."""
    data = {}
    if clear_cache:
        data["clearCache"] = True

    try:
        resp = requests.post(DEPLOY_URL, json=data, headers=HEADERS, timeout=10)
        if resp.status_code in (200, 201):
            text = "cache temizlenip deploy başlatıldı." if clear_cache else "deploy başarıyla başlatıldı."
            update.message.reply_text(f"✅ {text}")
        else:
            update.message.reply_text(f"⚠️ Deploy isteği gönderildi ama status: {resp.status_code}")
    except Exception as e:
        update.message.reply_text(f"❌ Hata (deploy): {e}")

# -- plugin loader ---
def register(app):
    app.add_handler(CommandHandler("wake", wake))
    app.add_handler(CommandHandler("wake deploy", lambda u, c: deploy(u, c, clear_cache=False)))
    app.add_handler(CommandHandler("wake clear", lambda u, c: deploy(u, c, clear_cache=True)))
