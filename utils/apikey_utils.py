#apikey_utils.py
# alarm+trade+şifreleme 
# encryption_utils.py ile şifreleme 


import sqlite3
import os
import json
from datetime import datetime, timedelta
from utils.encryption_utils import encrypt_text, decrypt_text

DB_PATH = "data/apikeys.db"
os.makedirs("data", exist_ok=True)

def get_connection():
    return sqlite3.connect(DB_PATH)

with get_connection() as conn:
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
    encrypted = encrypt_text(api_key)
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO apikeys (user_id, api_key)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET api_key=excluded.api_key
        """, (user_id, encrypted))
        conn.commit()

def get_apikey(user_id: int):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT api_key FROM apikeys WHERE user_id = ?", (user_id,)
        ).fetchone()
        return decrypt_text(row[0]) if row and row[0] else None

# ----------------- ALARM AYARLARI -----------------
def set_alarm_settings(user_id: int, settings: dict):
    settings_json = json.dumps(settings)
    encrypted = encrypt_text(settings_json)
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO apikeys (user_id, alarm_settings)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET alarm_settings=excluded.alarm_settings
        """, (user_id, encrypted))
        conn.commit()

def get_alarm_settings(user_id: int):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT alarm_settings FROM apikeys WHERE user_id = ?", (user_id,)
        ).fetchone()
        return json.loads(decrypt_text(row[0])) if row and row[0] else None

# ----------------- TRADE AYARLARI -----------------
def set_trade_settings(user_id: int, settings: dict):
    settings_json = json.dumps(settings)
    encrypted = encrypt_text(settings_json)
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO apikeys (user_id, trade_settings)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET trade_settings=excluded.trade_settings
        """, (user_id, encrypted))
        conn.commit()

def get_trade_settings(user_id: int):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT trade_settings FROM apikeys WHERE user_id = ?", (user_id,)
        ).fetchone()
        return json.loads(decrypt_text(row[0])) if row and row[0] else None

# ----------------- ALARM İŞLEMLERİ -----------------
def add_alarm(user_id: int, alarm_data: dict):
    encrypted = encrypt_text(json.dumps(alarm_data))
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO alarms (user_id, alarm_data) VALUES (?, ?)",
            (user_id, encrypted)
        )
        conn.commit()

def get_alarms(user_id: int):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, alarm_data FROM alarms WHERE user_id = ?", (user_id,)
        ).fetchall()
        return [{"id": r[0], "data": json.loads(decrypt_text(r[1]))} for r in rows]

def delete_alarm(alarm_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM alarms WHERE id = ?", (alarm_id,))
        conn.commit()

# ----------------- TEMİZLEME -----------------
def cleanup_old_alarms(days: int = 60):
    cutoff_date = datetime.now() - timedelta(days=days)
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM alarms WHERE created_at < ?", (cutoff_date,)
        )
        conn.commit()

def cleanup_old_apikeys(days: int = 365):
    cutoff_date = datetime.now() - timedelta(days=days)
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM apikeys WHERE created_at < ?", (cutoff_date,)
        )
        conn.commit()
