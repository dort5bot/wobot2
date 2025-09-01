# utils/apikey_utils.py
# Kullanıcı bazlı API key yönetimi (DB + encryption-only).
# Baştan kurulum için optimize edildi → tüm kayıtlar şifrelenerek saklanır.
# get_apikey() daima decrypt edilmiş (api, secret) tuple döner.

import os
import sqlite3
import json
import logging
import stat
from cryptography.fernet import Fernet

LOG = logging.getLogger("apikey_utils")
LOG.addHandler(logging.NullHandler())

# --- Master key management --- #
_MASTER_KEY_ENV = "API_MASTER_KEY"
_MASTER_KEY_FILE = ".apikey_master_key"


def _ensure_master_key():
    """
    Şifreleme için master key'i getirir.
    - Öncelik: API_MASTER_KEY env var
    - Yoksa: .apikey_master_key dosyasından oku
    - O da yoksa: yeni oluştur ve kaydet
    """
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
    """Verilen değeri şifreler ve string döner."""
    if plain is None:
        return None
    if not isinstance(plain, (str, bytes)):
        plain = str(plain)
    if isinstance(plain, str):
        plain = plain.encode("utf-8")
    token = _FERNET.encrypt(plain)
    return token.decode("utf-8")


def decrypt_value(value: str) -> str:
    """Şifreli değeri çözer ve plaintext döner."""
    if value is None:
        return None
    if isinstance(value, str):
        v = value.encode("utf-8")
    else:
        v = value
    dec = _FERNET.decrypt(v)
    return dec.decode("utf-8")


# --- DB connection --- #
DB_FILE = "wobot.db"  # ihtiyaca göre path güncelleyebilirsin

def get_connection():
    return sqlite3.connect(DB_FILE)


# --- API KEY management --- #
def add_or_update_apikey(user_id: int, api_key: str, secret_key: str):
    """
    Kullanıcıya ait API key'i DB'ye ekler veya günceller.
    - Değerler şifrelenerek saklanır.
    """
    enc_api = encrypt_value(api_key)
    enc_secret = encrypt_value(secret_key)
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS apikeys (
                user_id INTEGER PRIMARY KEY,
                api TEXT,
                secret TEXT
            )
        """)
        conn.execute("""
            INSERT INTO apikeys (user_id, api, secret) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET api=excluded.api, secret=excluded.secret
        """, (user_id, enc_api, enc_secret))
        conn.commit()
    LOG.info("API key updated (user_id=%s)", user_id)


def get_apikey(user_id: int):
    """
    Kullanıcının kayıtlı API key'ini döner.
    - Şifre çözülmüş (api, secret) tuple döner.
    """
    with get_connection() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS apikeys (user_id INTEGER PRIMARY KEY, api TEXT, secret TEXT)")
        cur = conn.execute("SELECT api, secret FROM apikeys WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if not row:
            return None
        api_enc, secret_enc = row
        api = decrypt_value(api_enc) if api_enc else None
        secret = decrypt_value(secret_enc) if secret_enc else None
        return (api, secret)


def delete_apikey(user_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM apikeys WHERE user_id = ?", (user_id,))
        conn.commit()
    LOG.info("API key deleted (user_id=%s)", user_id)


# --- Alarm settings --- #
def set_alarm_settings(user_id: int, settings: dict):
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alarm_settings (
                user_id INTEGER PRIMARY KEY,
                settings TEXT
            )
        """)
        conn.execute("""
            INSERT INTO alarm_settings (user_id, settings) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET settings=excluded.settings
        """, (user_id, json.dumps(settings)))
        conn.commit()


def get_alarm_settings(user_id: int):
    with get_connection() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS alarm_settings (user_id INTEGER PRIMARY KEY, settings TEXT)")
        cur = conn.execute("SELECT settings FROM alarm_settings WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return json.loads(row[0]) if row else None


# --- Trade settings --- #
def set_trade_settings(user_id: int, settings: dict):
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_settings (
                user_id INTEGER PRIMARY KEY,
                settings TEXT
            )
        """)
        conn.execute("""
            INSERT INTO trade_settings (user_id, settings) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET settings=excluded.settings
        """, (user_id, json.dumps(settings)))
        conn.commit()


def get_trade_settings(user_id: int):
    with get_connection() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS trade_settings (user_id INTEGER PRIMARY KEY, settings TEXT)")
        cur = conn.execute("SELECT settings FROM trade_settings WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return json.loads(row[0]) if row else None


# --- Alarm list --- #
def add_alarm(user_id: int, data: dict):
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alarms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                data TEXT
            )
        """)
        conn.execute("INSERT INTO alarms (user_id, data) VALUES (?, ?)", (user_id, json.dumps(data)))
        conn.commit()


def get_alarms(user_id: int):
    with get_connection() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS alarms (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, data TEXT)")
        cur = conn.execute("SELECT id, data FROM alarms WHERE user_id = ?", (user_id,))
        rows = cur.fetchall()
        return [{"id": rid, "data": json.loads(data)} for rid, data in rows]


def delete_alarm(alarm_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM alarms WHERE id = ?", (alarm_id,))
        conn.commit()
