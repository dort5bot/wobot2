#wake_handler.py
'''
Kullanım Örnekleri
/wake → Botu ping atar, uyandırır
/wake d → Deploy tetikler
/wake c → Cache temizler + deploy tetikler
/wake q → Kalan compute + build quota bilgisi
'''

import os
import requests
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler

# Çevresel değişkenler (Render dashboard'dan ekle)
RENDER_URL = os.getenv("RENDER_URL", "https://senin-bot.onrender.com/")
RENDER_API_KEY = os.getenv("RENDER_API_KEY", "")
RENDER_SERVICE_ID = os.getenv("RENDER_SERVICE_ID", "")

# API endpointler
DEPLOY_URL = f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/deploys"
QUOTA_URL = "https://api.render.com/v1/quota"

HEADERS = {
    "Authorization": f"Bearer {RENDER_API_KEY}",
    "Content-Type": "application/json",
}

def do_ping(update: Update):
    """Botu uyandırmak için ping atar"""
    try:
        resp = requests.get(RENDER_URL, timeout=10)
        if resp.status_code == 200:
            update.message.reply_text("✅ Bot başarıyla uyandırıldı (ping OK).")
        else:
            update.message.reply_text(f"⚠️ Servis yanıtladı ama status {resp.status_code}")
    except Exception as e:
        update.message.reply_text(f"❌ Hata (uyandırma): {e}")

def do_deploy(update: Update, clear_cache=False):
    """Deploy tetikler, cache isteğe bağlı temizlenir"""
    data = {}
    if clear_cache:
        data["clearCache"] = True

    try:
        resp = requests.post(DEPLOY_URL, json=data, headers=HEADERS, timeout=15)
        if resp.status_code in (200, 201):
            msg = "✅ Cache temizlenip deploy başlatıldı." if clear_cache else "✅ Deploy başarıyla başlatıldı."
            update.message.reply_text(msg)
        else:
            update.message.reply_text(f"⚠️ Deploy isteği başarısız (status {resp.status_code})")
    except Exception as e:
        update.message.reply_text(f"❌ Hata (deploy): {e}")

def do_quota(update: Update):
    """Kalan compute ve build quota bilgisi çeker"""
    try:
        resp = requests.get(QUOTA_URL, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            compute = data.get("computeQuota", {})
            build = data.get("buildQuota", {})

            compute_used = compute.get("used", 0)
            compute_limit = compute.get("limit", 0)
            build_used = build.get("used", 0)
            build_limit = build.get("limit", 0)

            text = (
                "📊 Render Quota:\n\n"
                f"🖥 Compute: {compute_used}/{compute_limit} saat kullanıldı\n"
                f"🔨 Build: {build_used}/{build_limit} dk kullanıldı"
            )
            update.message.reply_text(text)
        else:
            update.message.reply_text(f"⚠️ Quota isteği başarısız (status {resp.status_code})")
    except Exception as e:
        update.message.reply_text(f"❌ Hata (quota): {e}")

def wake(update: Update, context: CallbackContext):
    """Ana /wake komutu, alt parametreleri işler"""
    args = context.args

    if not args:
        do_ping(update)
        return

    cmd = args[0].lower()

    if cmd == "d":
        do_deploy(update, clear_cache=False)
    elif cmd == "c":
        do_deploy(update, clear_cache=True)
    elif cmd == "q":
        do_quota(update)
    else:
        update.message.reply_text("❌ Geçersiz parametre. Kullanım:\n"
                                  "/wake → Ping\n"
                                  "/wake d → Deploy\n"
                                  "/wake c → Clear cache + deploy\n"
                                  "/wake q → Quota bilgisi")

# -- plugin loader ---
def register(app):
    app.add_handler(CommandHandler("wake", wake))
