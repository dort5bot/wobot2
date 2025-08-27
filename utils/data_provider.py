#utils/data_provider.py

from utils import cache
# minimal provider - first read cache, else stub responses
def get_price(symbol):
    tick = cache.get_latest("ticker") or {}
    return tick.get(symbol)
def get_tickers(symbols):
    tick = cache.get_latest("ticker") or {}
    return {s: tick.get(s) for s in symbols}
def get_funding(symbols):
    return cache.get_latest("funding") or {}
