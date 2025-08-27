# utils/db.py
import sqlite3
import os
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "data/paper_trades.db")

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS paper_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        side TEXT,
        qty REAL,
        price REAL,
        source TEXT,
        executed BOOLEAN DEFAULT 0,
        ts DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        signal_type TEXT,
        strength REAL,
        payload TEXT,
        source TEXT,
        ts DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        decision TEXT,
        strength REAL,
        reason TEXT,
        executed BOOLEAN DEFAULT 0,
        ts DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()

def log_paper_trade(symbol, side, qty, price, source="signal"):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO paper_trades (symbol, side, qty, price, source, executed)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (symbol, side, qty, price, source, True))
    conn.commit()
    conn.close()

def log_signal(symbol, signal_type, strength, payload: str, source="strategy"):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO signals (symbol, signal_type, strength, payload, source)
        VALUES (?, ?, ?, ?, ?)
    """, (symbol, signal_type, strength, payload, source))
    conn.commit()
    conn.close()

def log_decision(symbol, decision, strength, reason=""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO decisions (symbol, decision, strength, reason, executed)
        VALUES (?, ?, ?, ?, ?)
    """, (symbol, decision, strength, reason, True))
    conn.commit()
    conn.close()
  
