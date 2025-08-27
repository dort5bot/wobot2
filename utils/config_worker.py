WORKER_A_TASKS = [
    {"name": "ticker", "interval": 10},
    {"name": "funding", "interval": 60},
]
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
WORKER_B_INTERVAL = 5
CACHE_TTL_SECONDS = {"ticker":20, "funding":180}
CACHE_MAX_ROWS_PER_KEY = 100