#utils
import os
import requests

BASE_URL = "https://open-api-v4.coinglass.com"
API_KEY = os.getenv("COINGLASS_API_KEY")
HEADERS = {"coinglassSecret": API_KEY}

def _get(path: str, params: dict = None):
    """API isteği yapan genel fonksiyon."""
    url = f"{BASE_URL}{path}"
    resp = requests.get(url, headers=HEADERS, params=params)
    resp.raise_for_status()
    return resp.json()

# --- FUTURES ---
def futures_supported_coins(): return _get("/api/futures/supported-coins")
def futures_supported_exchange_pairs(): return _get("/api/futures/supported-exchange-pairs")
def futures_price_history(pair: str, interval: str = "h1"): return _get("/api/price/ohlc-history", {"symbol": pair, "interval": interval})
def futures_liquidation_history(pair: str, interval: str = "h4"): return _get("/api/futures/liquidation/history", {"pair": pair, "interval": interval})

# --- SPOT ---
def spot_supported_coins(): return _get("/api/spot/supported-coins")
def spot_price_history(symbol: str, interval: str = "h1"): return _get("/api/spot/price/history", {"symbol": symbol, "interval": interval})

# --- OPTIONS ---
def option_info(): return _get("/api/option/info")

# --- ETF ---
def etf_btc_list(): return _get("/api/etf/bitcoin/list")
def etf_btc_flows_history(): return _get("/api/etf/bitcoin/flow-history")
def etf_eth_list(): return _get("/api/etf/ethereum/list")
def etf_eth_flows_history(): return _get("/api/etf/ethereum/flow-history")

# --- Örnek Diğer Endpoint ---
def open_interest_exchange_list(symbol: str): return _get("/api/futures/openInterest/exchange-list", {"symbol": symbol})

# ... ihtiyaca göre diğer endpointler benzer şekilde eklenebilir ...
