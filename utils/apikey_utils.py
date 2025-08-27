##utils/apikey_utils.py
##
## utils/apikey_utils.py
import sqlite3
import os
import json
from datetime import datetime, timedelta

DB_PATH = "data/apikeys.db"
os.makedirs("data", exist_ok=True)


# --- DB bağlantı fonksiyonu ---
def get_connection():
    return sqlite3.connect(DB_PATH)


# --- TABLO OLUŞTURMA ---
with sqlite3.connect(DB_PATH) as conn:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS apikeys (
            user_id INTEGER PRIMARY KEY,
            api_key TEXT,
            alarm_settings TEXT,
            trade_settings TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alarms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            alarm_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


# ----------------- API KEY -----------------
def add_or_update_apikey(user_id: int, api_key: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO apikeys (user_id, api_key)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET api_key=excluded.api_key
        """, (user_id, api_key))
        conn.commit()


def get_apikey(user_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT api_key FROM apikeys WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row[0] if row else None


# ----------------- ALARM AYARLARI -----------------
def set_alarm_settings(user_id: int, settings: dict):
    """Alarm ayarlarını JSON formatında kaydeder"""
    settings_json = json.dumps(settings)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO apikeys (user_id, alarm_settings)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET alarm_settings=excluded.alarm_settings
        """, (user_id, settings_json))
        conn.commit()


def get_alarm_settings(user_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT alarm_settings FROM apikeys WHERE user_id = ?", (user_id,)
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else None


# ----------------- ALARM İŞLEMLERİ -----------------
def add_alarm(user_id: int, alarm_data: dict):
    """Yeni alarm ekler"""
    alarm_json = json.dumps(alarm_data)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO alarms (user_id, alarm_data) VALUES (?, ?)",
            (user_id, alarm_json)
        )
        conn.commit()


def get_alarms(user_id: int):
    """JSON formatında alarm listesi döner"""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, alarm_data FROM alarms WHERE user_id = ?", (user_id,)
        ).fetchall()
        return [{"id": r[0], "data": json.loads(r[1])} for r in rows]


def get_user_alarms(user_id: int):
    """
    Eski handler uyumu için tuple versiyonu:
    (id, alarm_type, value, created_at) döner.
    created_at -> YYYY-MM-DD HH:MM formatına çevrilir.
    """
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT id, alarm_data, created_at
            FROM alarms
            WHERE user_id = ?
            ORDER BY created_at DESC
        """, (user_id,)).fetchall()

    result = []
    for alarm_id, alarm_json, created_at in rows:
        try:
            data = json.loads(alarm_json)
            alarm_type = data.get("type")
            value = data.get("value")
        except (ValueError, AttributeError):
            alarm_type = None
            value = None
        # Tarihi okunabilir formata çevir
        if isinstance(created_at, str):
            try:
                created_at_fmt = datetime.fromisoformat(created_at).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                created_at_fmt = created_at
        else:
            created_at_fmt = created_at
        result.append((alarm_id, alarm_type, value, created_at_fmt))
    return result


def delete_alarm(alarm_id: int):
    """Alarmı manuel veya tetiklendikten sonra siler"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM alarms WHERE id = ?", (alarm_id,))
        conn.commit()


# ----------------- TRADE AYARLARI -----------------
def set_trade_settings(user_id: int, settings: dict):
    settings_json = json.dumps(settings)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO apikeys (user_id, trade_settings)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET trade_settings=excluded.trade_settings
        """, (user_id, settings_json))
        conn.commit()


def get_trade_settings(user_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT trade_settings FROM apikeys WHERE user_id = ?", (user_id,)
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else None


# ----------------- TEMİZLEME -----------------
def cleanup_old_alarms(days: int = 60):
    """Belirtilen günden eski alarmları siler"""
    cutoff_date = datetime.now() - timedelta(days=days)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM alarms WHERE created_at < ?", (cutoff_date,)
        )
        conn.commit()


def cleanup_old_apikeys(days: int = 365):
    """1 yıldan eski API kayıtlarını temizler"""
    cutoff_date = datetime.now() - timedelta(days=days)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM apikeys WHERE created_at < ?", (cutoff_date,)
        )
        conn.commit()
