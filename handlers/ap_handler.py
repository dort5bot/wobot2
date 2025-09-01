# handlers/ap_handler.py
# AP (Altların Güç Endeksi) handler - hem public hem private (API key ile) modu destekler.
# Düzeltilmiş: eksik importlar eklendi, apikey format/tip tutarsızlıkları giderildi,
# ve local decrypt helper ile uyumlu hale getirildi.

import os
import logging
from telegram import Update
from telegram.ext import ContextTypes
from utils.apikey_utils import get_apikey
from utils.binance_api import BinanceClient, get_binance_api
from utils.ap_utils import build_ap_report_lines_pro

# Encryption helper: duplicate minimal helper to decrypt stored api/secret (works with Fernet)
# (Aynı logic apikey_handler.py ile uyumlu olmalı)
try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception as e:
    raise ImportError("cryptography kütüphanesi bulunamadı. `pip install cryptography` ile kurun.") from e

import base64
import stat

LOG = logging.getLogger("ap_handler")
LOG.addHandler(logging.NullHandler())

_MASTER_KEY_ENV = "API_MASTER_KEY"
_MASTER_KEY_FILE = ".apikey_master_key"


def _ensure_master_key():
    """Get or create a persistent master key. Prefer env var, else read/create file."""
    k = os.getenv(_MASTER_KEY_ENV)
    if k:
        # if user provided raw 32-byte key or base64 key - assume valid Fernet key (b64 urlsafe)
        return k.encode() if isinstance(k, str) else k
    # try read file
    if os.path.exists(_MASTER_KEY_FILE):
        with open(_MASTER_KEY_FILE, "rb") as f:
            return f.read().strip()
    # generate
    key = Fernet.generate_key()
    with open(_MASTER_KEY_FILE, "wb") as f:
        f.write(key)
    # set strict permissions
    try:
        os.chmod(_MASTER_KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    return key


_MASTER_KEY = _ensure_master_key()
_FERNET = Fernet(_MASTER_KEY)


def try_decrypt(value: str) -> str:
    """
    Try to decrypt value. If decryption fails, return original value (assume plaintext).
    value: str (utf-8)
    """
    if value is None:
        return None
    if not isinstance(value, (bytes, str)):
        return value
    if isinstance(value, str):
        v = value.encode()
    else:
        v = value
    try:
        dec = _FERNET.decrypt(v)
        return dec.decode("utf-8")
    except Exception:
        # not encrypted (or wrong key) -> return original str
        try:
            return v.decode("utf-8")
        except Exception:
            return str(value)


# ---------------- MAIN HANDLER ---------------- #
async def ap_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /ap komutu: AP skor raporu hazırlar.
    - Eğer kullanıcıya ait API key varsa private modda (daha hassas) çalışır.
    - Aksi halde public (anon) erişim kullanılır.
    """
    msg = await update.message.reply_text("AP skor raporu hazırlanıyor... ⏳")
    try:
        user_id = update.effective_user.id
        # default symbols if none provided
        symbols = context.args if context.args else ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

        # get stored apikey from utils.apikey_utils
        stored = get_apikey(user_id)

        api_key = None
        secret_key = None
        mode = "public"

        if stored:
            # handle different possible return shapes: tuple/list, dict, or string "api:secret"
            if isinstance(stored, (tuple, list)) and len(stored) >= 2:
                api_key_enc, secret_key_enc = stored[0], stored[1]
            elif isinstance(stored, dict):
                api_key_enc = stored.get("api") or stored.get("api_key") or stored.get("key")
                secret_key_enc = stored.get("secret") or stored.get("secret_key")
            elif isinstance(stored, str):
                # might be "api:secret" or encrypted-api / encrypted-secret separated by colon
                if ":" in stored:
                    a, b = stored.split(":", 1)
                    api_key_enc, secret_key_enc = a, b
                else:
                    # unexpected single string -> treat as api only (invalid)
                    api_key_enc, secret_key_enc = stored, None
            else:
                # unknown format
                api_key_enc, secret_key_enc = None, None

            # try decrypt both; if not encrypted, try_decrypt returns original
            api_key = try_decrypt(api_key_enc) if api_key_enc else None
            secret_key = try_decrypt(secret_key_enc) if secret_key_enc else None

            if api_key and secret_key:
                mode = "private"
            else:
                # fallback: treat as public if private credentials incomplete
                mode = "public"

        if mode == "private":
            # create client with user keys
            try:
                client = BinanceClient(api_key, secret_key)
            except Exception as e:
                LOG.exception("BinanceClient oluşturulamadı (private mode): %s", e)
                await msg.edit_text("❌ API ile bağlanırken hata oluştu. Public mod ile devam ediliyor.")
                client = BinanceClient()  # fallback public
                mode = "public"
        else:
            client = BinanceClient()  # public access

        # build report using provided utils function (keeps same interface)
        results = await build_ap_report_lines_pro(client=client, symbols=symbols)
        if isinstance(results, (list, tuple)):
            text = "\n".join(results)
        else:
            text = str(results)

        if mode == "private":
            text = "🔐 (Private Mode)\n\n" + text

        await msg.edit_text(text)
    except Exception as e:
        LOG.exception("ap_handler hata: %s", e)
        await msg.edit_text(f"❌ Hata oluştu: {e}")
