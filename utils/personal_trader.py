# handlers/apikey_handler.py
# Kullanıcı bazlı API Key, alarm ve trade ayarları
# YENİ MİMARİ: Global API (.env) + Kişisel API (DB)

from telegram.ext import CommandHandler
from utils.apikey_utils import (
    add_or_update_apikey, get_apikey,
    set_alarm_settings, get_alarm_settings,
    set_trade_settings, get_trade_settings,
    get_alarms, delete_alarm,
    add_alarm
)
from utils.personal_trader import personal_trader
import json
import logging

LOG = logging.getLogger("apikey_handler")

# --- Yardım: /api ---
async def api_info(update, context):
    message = (
        "🔧 *API Komutları*\n\n"
        "🔑 `/apikey <API_KEY> <SECRET_KEY>` → Kişisel API key ekle\n"
        "📋 `/apimy` → Kayıtlı API key bilgilerini görüntüle\n"
        "❌ `/apidel` → API key'i sil\n\n"
        "⏰ `/set_alarm <JSON>` → Alarm ayarlarını belirle\n"
        "📥 `/get_alarm` → Alarm ayarlarını görüntüle\n"
        "📋 `/myalarms` → Aktif alarmları listele\n"
        "🗑 `/delalarm <id>` → Alarm sil\n\n"
        "📊 `/set_trade <JSON>` → Trade ayarlarını belirle\n"
        "📤 `/get_trade` → Trade ayarlarını görüntüle\n\n"
        "💡 *Not:* Veri sorgulama için global API, kişisel işlemler için kişisel API kullanılır."
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

    # DB'ye kaydet
    add_or_update_apikey(user_id, api_key, secret_key)
    
    # Cache'i temizle (yeni key ile yeniden oluşturulsun)
    async with personal_trader.lock:
        personal_trader.clients.pop(user_id, None)
    
    await update.message.reply_text(
        "✅ API Key kaydedildi!\n\n"
        "• *Veri sorgulama:* Global API\n"  
        "• *Alarm/Trade:* Kişisel API\n\n"
        "Artık kişisel işlemlerinizde kullanılacak.",
        parse_mode="Markdown"
    )

# --- Kayıtlı API Key Bilgileri: /apimy ---
async def apimy(update, context):
    user_id = update.effective_user.id
    api_key, secret_key = get_apikey(user_id)
    if api_key and secret_key:
        masked_api = f"{api_key[:4]}...{api_key[-4:]}"
        masked_secret = f"{secret_key[:4]}...{secret_key[-4:]}"
        await update.message.reply_text(
            f"🔐 *Kayıtlı API Key*\n\nAPI: `{masked_api}`\nSECRET: `{masked_secret}`",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("📭 Kayıtlı bir API Key bulunamadı. /apikey ile ekleyin.")

# --- API Key Silme: /apidel ---
async def apidel(update, context):
    user_id = update.effective_user.id
    
    from utils.apikey_utils import get_connection
    with get_connection() as conn:
        conn.execute("DELETE FROM apikeys WHERE user_id = ?", (user_id,))
        conn.commit()
    
    # Cache'ten de sil
    async with personal_trader.lock:
        personal_trader.clients.pop(user_id, None)
    
    await update.message.reply_text("🗑 API Key silindi.")

# --- Alarm Ayarları ---
async def set_alarm(update, context):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Kullanım: /set_alarm <JSON>\nÖrnek: /set_alarm {\"symbol\": \"BTCUSDT\", \"price\": 50000}")
        return
    
    try:
        settings = json.loads(" ".join(context.args))
        set_alarm_settings(user_id, settings)
        await update.message.reply_text("⏰ Alarm ayarları kaydedildi.")
    except json.JSONDecodeError:
        await update.message.reply_text("❌ Geçersiz JSON formatı.")
    except Exception as e:
        await update.message.reply_text(f"❌ Hata: {str(e)}")

async def get_alarm(update, context):
    user_id = update.effective_user.id
    settings = get_alarm_settings(user_id)
    if settings:
        await update.message.reply_text(f"⏰ Alarm ayarları:\n```json\n{json.dumps(settings, indent=2)}\n```", parse_mode="Markdown")
    else:
        await update.message.reply_text("ℹ️ Hiç alarm ayarı bulunamadı.")

# --- Alarm Listeleme: /myalarms ---
async def myalarms(update, context):
    user_id = update.effective_user.id
    alarms = get_alarms(user_id)
    
    if not alarms:
        await update.message.reply_text("📭 Hiç aktif alarmınız yok.")
        return
    
    message = "⏰ *Aktif Alarmlarınız*\n\n"
    for alarm in alarms:
        message += f"🆔 {alarm['id']}: {json.dumps(alarm['data'], ensure_ascii=False)}\n"
    
    await update.message.reply_text(message, parse_mode="Markdown")

# --- Alarm Silme: /delalarm <id> ---
async def delalarm(update, context):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Kullanım: /delalarm <alarm_id>\nÖrnek: /delalarm 1")
        return
    
    alarm_id = int(context.args[0])
    delete_alarm(alarm_id)
    await update.message.reply_text(f"🗑 Alarm #{alarm_id} silindi.")

# --- Trade Ayarları ---
async def set_trade(update, context):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Kullanım: /set_trade <JSON>\nÖrnek: /set_trade {\"max_amount\": 1000, \"risk_level\": \"medium\"}")
        return
    
    try:
        settings = json.loads(" ".join(context.args))
        set_trade_settings(user_id, settings)
        await update.message.reply_text("📊 Trade ayarları kaydedildi.")
    except json.JSONDecodeError:
        await update.message.reply_text("❌ Geçersiz JSON formatı.")
    except Exception as e:
        await update.message.reply_text(f"❌ Hata: {str(e)}")

async def get_trade(update, context):
    user_id = update.effective_user.id
    settings = get_trade_settings(user_id)
    if settings:
        await update.message.reply_text(f"📊 Trade ayarları:\n```json\n{json.dumps(settings, indent=2)}\n```", parse_mode="Markdown")
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
        CommandHandler("myalarms", myalarms),
        CommandHandler("delalarm", delalarm),
        CommandHandler("set_trade", set_trade),
        CommandHandler("get_trade", get_trade)
    ]
    for h in handlers:
        application.add_handler(h)
