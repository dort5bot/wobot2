# utils/init_db.py
import sqlite3
import logging
from typing import Dict, List

LOG = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())

DB_PATH = "bot.db"

# Beklenen tablo şemaları - Foreign key ve index eklendi
SCHEMA = {
    "apikeys": {
        "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
        "user_id": "INTEGER NOT NULL",
        "api_key": "TEXT NOT NULL",
        "api_secret": "TEXT NOT NULL",
        "exchange": "TEXT DEFAULT 'binance'",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "is_active": "INTEGER DEFAULT 1"
    },
    "alarm_settings": {
        "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
        "user_id": "INTEGER NOT NULL",
        "symbol": "TEXT NOT NULL",
        "condition": "TEXT NOT NULL",
        "target_price": "REAL NOT NULL",
        "is_active": "INTEGER DEFAULT 1",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
    },
    "trade_settings": {
        "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
        "user_id": "INTEGER NOT NULL",
        "symbol": "TEXT NOT NULL",
        "leverage": "INTEGER DEFAULT 1",
        "position_size": "REAL DEFAULT 0",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
    },
    "alarms": {
        "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
        "user_id": "INTEGER NOT NULL",
        "symbol": "TEXT NOT NULL",
        "triggered_at": "TEXT NOT NULL",
        "note": "TEXT",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
    },
}

# Index tanımları
INDEXES = {
    "apikeys": [
        "CREATE INDEX IF NOT EXISTS idx_apikeys_user_id ON apikeys(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_apikeys_exchange ON apikeys(exchange)"
    ],
    "alarm_settings": [
        "CREATE INDEX IF NOT EXISTS idx_alarm_settings_user_id ON alarm_settings(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_alarm_settings_symbol ON alarm_settings(symbol)"
    ],
    "trade_settings": [
        "CREATE INDEX IF NOT EXISTS idx_trade_settings_user_id ON trade_settings(user_id)"
    ],
    "alarms": [
        "CREATE INDEX IF NOT EXISTS idx_alarms_user_id ON alarms(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_alarms_triggered_at ON alarms(triggered_at)"
    ]
}

def get_existing_columns(cur, table: str) -> List[str]:
    """Tablodaki mevcut kolonları getirir."""
    try:
        cur.execute(f"PRAGMA table_info({table})")
        return [row[1] for row in cur.fetchall()]
    except sqlite3.Error as e:
        LOG.error(f"[DB] {table} tablosu için kolon bilgisi alınamadı: {e}")
        return []

def create_indexes(cur, table: str):
    """Tablo için indexleri oluşturur."""
    if table in INDEXES:
        for index_sql in INDEXES[table]:
            try:
                cur.execute(index_sql)
                LOG.info(f"[DB] Index oluşturuldu: {index_sql}")
            except sqlite3.Error as e:
                LOG.error(f"[DB] Index oluşturulamadı: {index_sql}, Hata: {e}")

def init_db():
    """Tabloları oluşturur ve eksik kolonları otomatik ekler."""
    try:
        conn = sqlite3.connect(DB_PATH)
        # Foreign key desteğini aç
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.cursor()

        for table, columns in SCHEMA.items():
            # 1) Tabloyu oluştur
            cols_def = ", ".join([f"{col} {ctype}" for col, ctype in columns.items()])
            cur.execute(f"CREATE TABLE IF NOT EXISTS {table} ({cols_def})")
            LOG.info(f"[DB] {table} tablosu kontrol edildi/oluşturuldu")

            # 2) Mevcut kolonları kontrol et
            existing_cols = get_existing_columns(cur, table)
            
            if not existing_cols:
                LOG.warning(f"[DB] {table} tablosu bulunamadı veya okunamadı")
                continue

            # 3) Eksik kolonları ekle
            for col, ctype in columns.items():
                if col not in existing_cols:
                    try:
                        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ctype}")
                        LOG.warning(f"[DB] {table} tablosuna eksik kolon eklendi: {col}")
                    except sqlite3.Error as e:
                        LOG.error(f"[DB] {table} tablosuna {col} eklenirken hata: {e}")

            # 4) Indexleri oluştur
            create_indexes(cur, table)

        conn.commit()
        LOG.info("[DB] Migration tamamlandı (init_db).")
        
    except sqlite3.Error as e:
        LOG.error(f"[DB] Veritabanı bağlantı hatası: {e}")
        raise
    finally:
        if conn:
            conn.close()

def check_db_integrity():
    """Veritabanı bütünlüğünü kontrol eder."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check")
        result = cur.fetchone()
        LOG.info(f"[DB] Integrity check: {result[0]}")
        return result[0] == "ok"
    except sqlite3.Error as e:
        LOG.error(f"[DB] Integrity check hatası: {e}")
        return False
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    # Loglama ayarı
    logging.basicConfig(level=logging.INFO)
    
    init_db()
    if check_db_integrity():
        print("DB hazır ve sağlam ✅")
    else:
        print("DB hazır ancak integrity check başarısız ⚠️")
