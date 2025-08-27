# plugin/handler loader sistemine uyumlu
# Kullanıcı bazlı + global API Key, alarm ve trade ayarları tek dosyada yönetilebilir.
# /apikey, /set_alarm, /get_alarm, /set_trade, /get_trade komutları ile bütünleşik.



# handlers/apikey_handler.py
from telegram.ext import CommandHandler
from utils.config import CONFIG, update_binance_keys, ENV_PATH
from utils.apikey_utils import (
    add_or_update_apikey, get_apikey,
    set_alarm_settings, get_alarm_settings,
    set_trade_settings, get_trade_settings
)
import os
from dotenv import set_key, load_dotenv
import json

AUTHORIZED_USERS = [123456789]  # Global bot key yetkili kullanıcılar

# --- API Key Komutu ---
async def apikey(update, context):
    user_id = update.effective_user.id

    if len(context.args) != 2:
        await update.message.reply_text("Kullanım: /apikey <API_KEY> <SECRET_KEY>")
        return

    api_key, secret_key = context.args

    # Kullanıcı bazlı kaydet
    add_or_update_apikey(user_id, f"{api_key}:{secret_key}")

    # Global key güncelleme yetkisi
    if user_id in AUTHORIZED_USERS:
        update_binance_keys(api_key, secret_key)
        if os.path.exists(ENV_PATH):
            set_key(ENV_PATH, "BINANCE_API_KEY", api_key)
            set_key(ENV_PATH, "BINANCE_SECRET_KEY", secret_key)
            load_dotenv(ENV_PATH, override=True)
        await update.message.reply_text(
            "Global bot key güncellendi ve DB’ye kaydedildi."
        )
    else:
        await update.message.reply_text("Kullanıcı bazlı API Key DB’ye kaydedildi.")


# --- Alarm Ayarları ---
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


# --- Trade Ayarları ---
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


# --- Handler nesneleri ---
apikey_handler = CommandHandler("apikey", apikey)
set_alarm_handler = CommandHandler("set_alarm", set_alarm)
get_alarm_handler = CommandHandler("get_alarm", get_alarm)
set_trade_handler = CommandHandler("set_trade", set_trade)
get_trade_handler = CommandHandler("get_trade", get_trade)

# --- Loader uyumlu register fonksiyonu ---
def register(application):
    """
    Handler loader ile uyumlu şekilde dispatcher’a ekler
    """
    handlers = [
        apikey_handler,
        set_alarm_handler,
        get_alarm_handler,
        set_trade_handler,
        get_trade_handler
    ]
    for handler in handlers:
        application.add_handler(handler)
