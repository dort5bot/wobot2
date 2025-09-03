# wake_handler.py
"""
Kullanım Örnekleri
/wake → Botu ping atar, uyandırır
/wake d → Deploy tetikler
/wake c → Cache temizler + deploy tetikler
/wake q → Kalan compute + build quota bilgisi
"""

import os
import json
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Çevresel değişkenler (Render dashboard'dan ekle)
RENDER_URL = os.getenv("RENDER_URL", "https://wobot2.onrender.com")
RENDER_API_KEY = os.getenv("RENDER_API_KEY", "")
RENDER_SERVICE_ID = os.getenv("RENDER_SERVICE_ID", "")

# API endpointler
DEPLOY_URL = f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/deploys"
QUOTA_URL = "https://api.render.com/v1/quota"

HEADERS = {
    "Authorization": f"Bearer {RENDER_API_KEY}",
    "Content-Type": "application/json",
}

# --- Helper fonksiyon ---
async def send_message(update: Update, text: str):
    """update.message yoksa loga yaz, varsa mesaj gönder"""
    if update.message:
        await update.message.reply_text(text)
    else:
        print("Bot mesaj gönderemedi, update.message yok. Text:", text)

# --- İşlevler ---
async def do_ping(update: Update):
    """Botu uyandırmak için ping atar"""
    try:
        resp = requests.get(RENDER_URL, timeout=10)
        if resp.status_code == 200:
            await send_message(update, "✅ Bot başarıyla uyandırıldı (ping OK).")
        else:
            await send_message(update, f"⚠️ Servis yanıtladı ama status {resp.status_code}")
    except Exception as e:
        await send_message(update, f"❌ Hata (uyandırma): {e}")

async def do_deploy(update: Update, clear_cache=False):
    """Deploy tetikler, cache isteğe bağlı temizlenir"""
    data = {"clearCache": True} if clear_cache else {}
    try:
        resp = requests.post(DEPLOY_URL, headers=HEADERS, json=data, timeout=15)
        if resp.status_code in (200, 201):
            msg = "✅ Cache temizlenip deploy başlatıldı." if clear_cache else "✅ Deploy başarıyla başlatıldı."
            await send_message(update, msg)
        else:
            await send_message(update, f"⚠️ Deploy isteği başarısız (status {resp.status_code})")
    except Exception as e:
        await send_message(update, f"❌ Hata (deploy): {e}")

async def do_quota(update: Update):
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
            await send_message(update, text)
        else:
            await send_message(update, f"⚠️ Quota isteği başarısız (status {resp.status_code})")
    except Exception as e:
        await send_message(update, f"❌ Hata (quota): {e}")

# --- Ana /wake komutu ---
async def wake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ana /wake komutu, alt parametreleri işler"""
    args = context.args
    if not args:
        await do_ping(update)
        return

    cmd = args[0].lower()
    if cmd == "d":
        await do_deploy(update, clear_cache=False)
    elif cmd == "c":
        await do_deploy(update, clear_cache=True)
    elif cmd == "q":
        await do_quota(update)
    else:
        text = (
            "❌ Geçersiz parametre. Kullanım:\n"
            "/wake → Ping\n"
            "/wake d → Deploy\n"
            "/wake c → Clear cache + deploy\n"
            "/wake q → Quota bilgisi"
        )
        await send_message(update, text)

# --- Plugin loader ---
def register(app: Application):
    """Bot main dosyasında register(app) ile ekle"""
    app.add_handler(CommandHandler("wake", wake))
