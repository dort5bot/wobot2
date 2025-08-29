# handlers/apikey_handler.py
# Kullanıcı bazlı + global API Key, alarm ve trade ayarları
# /apikey, /set_alarm, /get_alarm, /set_trade, /get_trade


from telegram.ext import CommandHandler
from utils.apikey_utils import (
    add_or_update_apikey, get_apikey,
    set_alarm_settings, get_alarm_settings,
    set_trade_settings, get_trade_settings,
    get_alarms, delete_alarm
)
from utils.config import CONFIG, update_binance_keys, ENV_PATH
from dotenv import set_key, load_dotenv
import os
import json

AUTHORIZED_USERS = [123456789]  # global key değiştirebilecek adminler

# --- Yardım: /api ---
async def api_info(update, context):
    message = (
        "🔧 *API Komutları*\n\n"
        "🔑 `/apikey <API_KEY> <SECRET_KEY>` → Yeni API key ekle\n"
        "📋 `/apimy` → Kendi kayıtlı API key bilgilerini görüntüle\n"
        "❌ `/apidel <numara>` → Belirtilen sıradaki API kaydını sil (örnek: /apidel 2)\n\n"
        "⏰ `/set_alarm <JSON>` → Alarm ayarlarını belirle\n"
        "📥 `/get_alarm` → Mevcut alarm ayarlarını görüntüle\n\n"
        "📊 `/set_trade <JSON>` → Trade ayarlarını belirle\n"
        "📤 `/get_trade` → Mevcut trade ayarlarını görüntüle"
    )
    await update.message.reply_text(message, parse_mode="Markdown")

# --- API Key Ekleme: /apikey ---
async def apikey(update, context):
    user_id = update.effective_user.id
    if len(context.args) != 2:
        await update.message.reply_text("Kullanım: /apikey <API_KEY> <SECRET_KEY>")
        return

    api_key, secret_key = context.args

    try:
        await update.message.delete()
    except:
        pass

    add_or_update_apikey(user_id, api_key, secret_key)

    if user_id in AUTHORIZED_USERS:
        update_binance_keys(api_key, secret_key)
        if os.path.exists(ENV_PATH):
            set_key(ENV_PATH, "BINANCE_API_KEY", api_key)
            set_key(ENV_PATH, "BINANCE_SECRET_KEY", secret_key)
            load_dotenv(ENV_PATH, override=True)
        await update.message.reply_text("✅ Global API Key güncellendi ve DB’ye kaydedildi.")
    else:
        await update.message.reply_text("🔑 API Key & Secret kullanıcı bazlı DB’ye kaydedildi.")

# --- Kayıtlı API Key Bilgileri: /apimy ---
async def apimy(update, context):
    user_id = update.effective_user.id
    api_key, secret_key = get_apikey(user_id)
    if api_key and secret_key:
        masked_api = f"{api_key[:4]}...{api_key[-4:]}"
        masked_secret = f"{secret_key[:4]}...{secret_key[-4:]}"
        await update.message.reply_text(
            f"🔐 *Kayıtlı API Key*\nAPI: `{masked_api}`\nSECRET: `{masked_secret}`",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("📭 Kayıtlı bir API Key bulunamadı.")

# --- API Key Silme: /apidel <numara> ---
async def apidel(update, context):
    user_id = update.effective_user.id
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Kullanım: /apidel <sıra_numarası> (örnek: /apidel 1)")
        return

    # Kullanıcının kayıtlı alarm listesi getirilir (çünkü çoklu alarm desteklenmiş ama apikey tekli)
    api_key, secret_key = get_apikey(user_id)
    if not api_key or not secret_key:
        await update.message.reply_text("🗂 Hiç kayıtlı API Key bulunamadı.")
        return

    index = int(context.args[0])
    if index != 1:
        await update.message.reply_text("❌ Sadece 1 adet API key kaydı bulunuyor. Sıra numarası 1 olmalıdır.")
        return

    from utils.apikey_utils import get_connection
    with get_connection() as conn:
        conn.execute("DELETE FROM apikeys WHERE user_id = ?", (user_id,))
        conn.commit()
    await update.message.reply_text("🗑 API Key silindi.")

# --- Alarm Ayarları ---
async def set_alarm(update, context):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Kullanım: /set_alarm <JSON>")
        return
    try:
        settings = json.loads(" ".join(context.args))
        set_alarm_settings(user_id, settings)
        await update.message.reply_text("⏰ Alarm ayarları kaydedildi.")
    except json.JSONDecodeError:
        await update.message.reply_text("❌ Geçersiz JSON formatı.")

async def get_alarm(update, context):
    user_id = update.effective_user.id
    settings = get_alarm_settings(user_id)
    if settings:
        await update.message.reply_text(f"⏰ Alarm ayarları:\n{json.dumps(settings, indent=2)}")
    else:
        await update.message.reply_text("ℹ️ Hiç alarm ayarı bulunamadı.")

# --- Trade Ayarları ---
async def set_trade(update, context):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Kullanım: /set_trade <JSON>")
        return
    try:
        settings = json.loads(" ".join(context.args))
        set_trade_settings(user_id, settings)
        await update.message.reply_text("📊 Trade ayarları kaydedildi.")
    except json.JSONDecodeError:
        await update.message.reply_text("❌ Geçersiz JSON formatı.")

async def get_trade(update, context):
    user_id = update.effective_user.id
    settings = get_trade_settings(user_id)
    if settings:
        await update.message.reply_text(f"📊 Trade ayarları:\n{json.dumps(settings, indent=2)}")
    else:
        await update.message.reply_text("ℹ️ Hiç trade ayarı bulunamadı.")

# --- Handler Kayıt ---
def register(application):
    handlers = [
        CommandHandler("api", api_info),
        CommandHandler("apikey", apikey),
        CommandHandler("apimy", apimy),
        CommandHandler("apidel", apidel),
        CommandHandler("set_alarm", set_alarm),
        CommandHandler("get_alarm", get_alarm),
        CommandHandler("set_trade", set_trade),
        CommandHandler("get_trade", get_trade)
    ]
    for h in handlers:
        application.add_handler(h)
