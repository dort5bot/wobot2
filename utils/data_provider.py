# utils/data_provider.py
# Minimal veri sağlayıcı
# Önce cache’den oku, yoksa stub (boş) response dön

from utils import cache

def get_price(symbol):
    tick = cache.get_latest("ticker") or {}
    return tick.get(symbol)

def get_tickers(symbols):
    tick = cache.get_latest("ticker") or {}
    return {s: tick.get(s) for s in symbols}

def get_funding(symbols):
    # funding verisi cache’den çekiliyor
    return cache.get_latest("funding") or {}
