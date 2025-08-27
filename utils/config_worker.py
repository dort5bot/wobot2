##utils/config_worker.py
WORKER_A_TASKS = [
    {"name": "ticker", "interval": 10},        # Her 10 saniyede bir
    {"name": "funding", "interval": 8*3600},  # Her 8 saatte bir
]

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]

CACHE_TTL_SECONDS = {
    "ticker": 20,       # 20 saniye cache
    "funding": 8*3600,  # 8 saat cache
}

#
WORKER_A_TASKS = [
    {"name": "ticker", "interval": 10},
    {"name": "funding", "interval": 60},
]
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
WORKER_B_INTERVAL = 5
CACHE_TTL_SECONDS = {"ticker":20, "funding":180}
CACHE_MAX_ROWS_PER_KEY = 100
