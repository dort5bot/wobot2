# handlers/apikey_handler.py
# Kullanıcı bazlı API Key yönetimi.
# - add_or_update_apikey çağrısına önce encrypt edilmiş api/secret gönderir.
# - get_apikey kullanıldığında decrypt denemesi yapar; plaintext fallback kabul edilir.
# - Güvenlik: master key env veya dosyaya yazılır (600 izinleri)

import os
import json
import logging
import stat
from telegram.ext import CommandHandler
from telegram import Update
from telegram.ext import ContextTypes

# DB helpers (mevcut utils fonksiyonlarını kullanıyoruz)
from utils.apikey_utils import (
    add_or_update_apikey,
    get_apikey,
    set_alarm_settings,
    get_alarm_settings,
    set_trade_settings,
    get_trade_settings,
    get_alarms,
    delete_alarm,
    add_alarm
)

# local personal_trader kullanımı (lock, clients) - mevcut yapı ile uyumlu
from utils.personal_trader import personal_trader

# encryption
try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception as e:
    raise ImportError("cryptography kütüphanesi bulunamadı. `pip install cryptography` ile kurun.") from e

LOG = logging.getLogger("apikey_handler")
LOG.addHandler(logging.NullHandler())

_MASTER_KEY_ENV = "API_MASTER_KEY"
_MASTER_KEY_FILE = ".apikey_master_key"


def _ensure_master_key():
    k = os.getenv(_MASTER_KEY_ENV)
    if k:
        return k.encode() if isinstance(k, str) else k
    if os.path.exists(_MASTER_KEY_FILE):
        with open(_MASTER_KEY_FILE, "rb") as f:
            return f.read().strip()
    key = Fernet.generate_key()
    with open(_MASTER_KEY_FILE, "wb") as f:
        f.write(key)
    try:
        os.chmod(_MASTER_KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    return key


_MASTER_KEY = _ensure_master_key()
_FERNET = Fernet(_MASTER_KEY)


def encrypt_value(plain: str) -> str:
    """Encrypt and return bytes-decoded string"""
    if plain is None:
        return None
    if not isinstance(plain, (str, bytes)):
        plain = str(plain)
    if isinstance(plain, str):
        plain = plain.encode("utf-8")
    token = _FERNET.encrypt(plain)
    return token.decode("utf-8")


def try_decrypt(value: str) -> str:
    """Try decrypt; if fails, return original (for plaintext compatibility)."""
    if value is None:
        return None
    if isinstance(value, str):
        v = value.encode("utf-8")
    else:
        v = value
    try:
        dec = _FERNET.decrypt(v)
        return dec.decode("utf-8")
    except Exception:
        # not encrypted -> assume plaintext
        try:
            return v.decode("utf-8")
        except Exception:
            return str(value)


# --- Handlers --- #

async def api_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/api komutu: yardım mesajı"""
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
        "💡 *Not:* API key'ler şifrelenerek saklanır."
    )
    await update.message.reply_text(message, parse_mode="Markdown")


async def apikey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /apikey <API_KEY> <SECRET_KEY>
    - Kayıt öncesi değerleri encrypt eder ve DB'ye kaydeder.
    - add_or_update_apikey(user_id, api, secret) fonksiyonunun mevcut imzasını korur.
    """
    user_id = update.effective_user.id
    if len(context.args) != 2:
        await update.message.reply_text("Kullanım: /apikey <API_KEY> <SECRET_KEY>")
        return

    api_key_raw, secret_key_raw = context.args
    try:
        # delete incoming message to avoid leaking
        try:
            await update.message.delete()
        except Exception:
            pass

        # encrypt separately
        enc_api = encrypt_value(api_key_raw)
        enc_secret = encrypt_value(secret_key_raw)

        # store encrypted values (DB tarafında API ve SECRET sütunlarına enk. yazılır)
        add_or_update_apikey(user_id, enc_api, enc_secret)

        # clear cached client for this user (personal_trader pattern)
        try:
            async with personal_trader.lock:
                personal_trader.clients.pop(user_id, None)
        except Exception:
            # if personal_trader.lock is not async context manager, try pop safely
            try:
                personal_trader.clients.pop(user_id, None)
            except Exception:
                pass

        await update.message.reply_text(
            "✅ API Key kaydedildi! (şifrelenmiş olarak saklandı)\n\n"
            "• *Veri sorgulama:* Global API\n"
            "• *Alarm/Trade:* Kişisel API",
            parse_mode="Markdown"
        )
    except Exception as e:
        LOG.exception("apikey ekleme hatası: %s", e)
        await update.message.reply_text(f"❌ Hata: {e}")


async def apimy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /apimy -> Kayıtlı API bilgilerini gösterir.
    - Kayıtlı değer şifreliyse çözer, plaintext ise direkt gösterir (maskelenmiş).
    """
    user_id = update.effective_user.id
    stored = get_apikey(user_id)
    if not stored:
        await update.message.reply_text("📭 Kayıtlı bir API Key bulunamadı. /apikey ile ekleyin.")
        return

    # stored can be tuple/list/dict/string - normalize
    api_enc = None
    secret_enc = None
    if isinstance(stored, (tuple, list)) and len(stored) >= 2:
        api_enc, secret_enc = stored[0], stored[1]
    elif isinstance(stored, dict):
        api_enc = stored.get("api") or stored.get("api_key") or stored.get("key")
        secret_enc = stored.get("secret") or stored.get("secret_key")
    elif isinstance(stored, str):
        if ":" in stored:
            a, b = stored.split(":", 1)
            api_enc, secret_enc = a, b
        else:
            api_enc = stored
            secret_enc = None
    else:
        api_enc = str(stored)
        secret_enc = None

    api = try_decrypt(api_enc) if api_enc else None
    secret = try_decrypt(secret_enc) if secret_enc else None

    if api and secret:
        masked_api = f"{api[:4]}...{api[-4:]}" if len(api) > 8 else api
        masked_secret = f"{secret[:4]}...{secret[-4:]}" if len(secret) > 8 else secret
        await update.message.reply_text(
            f"🔐 *Kayıtlı API Key*\n\nAPI: `{masked_api}`\nSECRET: `{masked_secret}`",
            parse_mode="Markdown"
        )
    else:
        # if incomplete data
        await update.message.reply_text("📭 Kayıtlı (ancak eksik veya okunamayan) API verisi bulundu. Lütfen yeniden kaydedin.")


async def apidel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /apidel -> DB'den apikey siler (mevcut utils fonksiyonunu kullanan basit SQL silme)
    """
    user_id = update.effective_user.id
    from utils.apikey_utils import get_connection
    try:
        with get_connection() as conn:
            conn.execute("DELETE FROM apikeys WHERE user_id = ?", (user_id,))
            conn.commit()
    except Exception as e:
        LOG.exception("apidel hata: %s", e)
    try:
        async with personal_trader.lock:
            personal_trader.clients.pop(user_id, None)
    except Exception:
        try:
            personal_trader.clients.pop(user_id, None)
        except Exception:
            pass
    await update.message.reply_text("🗑 API Key silindi.")


# --- Alarm & Trade helpers (aynı şekilde) --- #
# Bu bölümü önceki handler'dan aldım; olduğu gibi bıraktım.
async def set_alarm(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


async def get_alarm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    settings = get_alarm_settings(user_id)
    if settings:
        await update.message.reply_text(f"⏰ Alarm ayarları:\n```json\n{json.dumps(settings, indent=2)}\n```", parse_mode="Markdown")
    else:
        await update.message.reply_text("ℹ️ Hiç alarm ayarı bulunamadı.")


async def myalarms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    alarms = get_alarms(user_id)
    if not alarms:
        await update.message.reply_text("📭 Hiç aktif alarmınız yok.")
        return
    message = "⏰ *Aktif Alarmlarınız*\n\n"
    for alarm in alarms:
        message += f"🆔 {alarm['id']}: {json.dumps(alarm['data'], ensure_ascii=False)}\n"
    await update.message.reply_text(message, parse_mode="Markdown")


async def delalarm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Kullanım: /delalarm <alarm_id>\nÖrnek: /delalarm 1")
        return
    alarm_id = int(context.args[0])
    delete_alarm(alarm_id)
    await update.message.reply_text(f"🗑 Alarm #{alarm_id} silindi.")


async def set_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


async def get_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    settings = get_trade_settings(user_id)
    if settings:
        await update.message.reply_text(f"📊 Trade ayarları:\n```json\n{json.dumps(settings, indent=2)}\n```", parse_mode="Markdown")
    else:
        await update.message.reply_text("ℹ️ Hiç trade ayarı bulunamadı.")


# --- Register fonksiyonu (plugin loader uyumlu) --- #
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
    LOG.info("apikey_handler registered.")
