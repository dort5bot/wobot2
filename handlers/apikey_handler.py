# plugin/handler loader sistemine uyumlu
# Kullanıcı bazlı + global API Key, alarm ve trade ayarları tek dosyada yönetilebilir.
# /apikey, /set_alarm, /get_alarm, /set_trade, /get_trade komutları ile bütünleşik.
# alarm+trade+mesaj silme

from telegram.ext import CommandHandler
from utils.apikey_utils import (
    add_or_update_apikey, get_apikey,
    set_alarm_settings, get_alarm_settings,
    set_trade_settings, get_trade_settings
)
from utils.config import CONFIG, update_binance_keys, ENV_PATH
from dotenv import set_key, load_dotenv
import os
import json

AUTHORIZED_USERS = [123456789]

# --- API Key ---
async def apikey(update, context):
    user_id = update.effective_user.id
    if len(context.args) != 2:
        await update.message.reply_text("Kullanım: /apikey <API_KEY> <SECRET_KEY>")
        return

    api_key, secret_key = context.args
    try: await update.message.delete()  # mesaj sil

    except: pass
    add_or_update_apikey(user_id, f"{api_key}:{secret_key}")

    if user_id in AUTHORIZED_USERS:
        update_binance_keys(api_key, secret_key)
        if os.path.exists(ENV_PATH):
            set_key(ENV_PATH, "BINANCE_API_KEY", api_key)
            set_key(ENV_PATH, "BINANCE_SECRET_KEY", secret_key)
            load_dotenv(ENV_PATH, override=True)
        await update.message.reply_text("Global bot key güncellendi ve DB’ye kaydedildi.")
    else:
        await update.message.reply_text("Kullanıcı bazlı API Key DB’ye kaydedildi.")

# --- Alarm ---
async def set_alarm(update, context):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Kullanım: /set_alarm <JSON>")
        return
    try:
        settings = json.loads(" ".join(context.args))
        set_alarm_settings(user_id, settings)
        await update.message.reply_text("Alarm ayarları kaydedildi.")
    except json.JSONDecodeError:
        await update.message.reply_text("Geçersiz JSON formatı.")

async def get_alarm(update, context):
    user_id = update.effective_user.id
    settings = get_alarm_settings(user_id)
    if settings:
        await update.message.reply_text(f"Alarm ayarları:\n{json.dumps(settings, indent=2)}")
    else:
        await update.message.reply_text("Hiç alarm ayarı bulunamadı.")

# --- Trade ---
async def set_trade(update, context):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Kullanım: /set_trade <JSON>")
        return
    try:
        settings = json.loads(" ".join(context.args))
        set_trade_settings(user_id, settings)
        await update.message.reply_text("Trade ayarları kaydedildi.")
    except json.JSONDecodeError:
        await update.message.reply_text("Geçersiz JSON formatı.")

async def get_trade(update, context):
    user_id = update.effective_user.id
    settings = get_trade_settings(user_id)
    if settings:
        await update.message.reply_text(f"Trade ayarları:\n{json.dumps(settings, indent=2)}")
    else:
        await update.message.reply_text("Hiç trade ayarı bulunamadı.")

# --- Handler register ---
def register(application):
    handlers = [
        CommandHandler("apikey", apikey),
        CommandHandler("set_alarm", set_alarm),
        CommandHandler("get_alarm", get_alarm),
        CommandHandler("set_trade", set_trade),
        CommandHandler("get_trade", get_trade)
    ]
    for h in handlers:
        application.add_handler(h)
