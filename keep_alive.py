# keep_alive.py — Basit Flask ping server (Render uyumlu)
from flask import Flask
import threading

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is alive ✅"

@app.route("/ping")
def ping():
    return "pong"

def run():
    # Render kendi PORT environment variable’ını set ediyor
    import os
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = threading.Thread(target=run)
    t.daemon = True
    t.start()
