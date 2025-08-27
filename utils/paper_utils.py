##paper_utils.py
import sqlite3
import os
from datetime import datetime

DB_PATH = os.getenv("SQLITE_DB_PATH", "data/paper_log.db")

os.makedirs("data", exist_ok=True)

with sqlite3.connect(DB_PATH) as conn:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            symbol TEXT,
            quantity REAL,
            price REAL,
            timestamp TEXT
        )
    """)

def log_paper_trade(user_id, action, symbol, quantity, price):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO paper_trades (user_id, action, symbol, quantity, price, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, action, symbol, quantity, price, datetime.utcnow().isoformat()))
        conn.commit()

def get_paper_trades(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute("""
            SELECT action, symbol, quantity, price, timestamp
            FROM paper_trades
            WHERE user_id = ?
            ORDER BY timestamp DESC
        """, (user_id,)).fetchall()
