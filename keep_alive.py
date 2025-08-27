# keep_alive.py
#self ping yok eklenebilir 
from flask import Flask
import threading
import logging

LOG = logging.getLogger("keep_alive")
LOG.addHandler(logging.NullHandler())

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive", 200

def run():
    LOG.info("Keep-alive web server started on port 8080")
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    """
    Basit bir Flask server'ını ayrı bir thread'de çalıştırır.
    asyncio ile uyumlu olması için parametre almaz.
    """
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
