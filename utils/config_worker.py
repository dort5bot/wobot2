# utils/config_worker.py
# 	1. WORKER_A_TASKS → worker_a.py’nin loop’unda interval kontrolü için kullanılıyor.
# 	2. WORKER_B_INTERVAL → worker_b.py’nin loop’unda kullanılıyor.
# 	3. CACHE_TTL_SECONDS → her task’in cache süresini ayrı ayrı tutuyor.
# 	4. SYMBOLS → hem worker_a hem worker_b tarafından kullanılıyor.

# -------------------------------
# Worker A: Ticker ve Funding task’leri
# - interval: her task’in kaç saniyede bir çalışacağını belirler
# - funding 8 saatte bir, ticker sık (örn. 10s)
WORKER_A_TASKS = [
    {"name": "ticker", "interval": 10},        # Her 10 saniyede bir ticker çek
    {"name": "funding", "interval": 8*3600},  # Her 8 saatte bir funding çek
]

# -------------------------------
# Worker B: Sinyal değerlendirme interval’i
WORKER_B_INTERVAL = 5  # saniye cinsinden, dinamik sleep ile kullanılacak

# -------------------------------
# Takip edilecek semboller
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]

# -------------------------------
# Cache ayarları
CACHE_TTL_SECONDS = {
    "ticker": 20,       # ticker cache 20 saniye
    "funding": 8*3600,  # funding cache 8 saat
}

CACHE_MAX_ROWS_PER_KEY = 100  # cache boyutu limit

# -------------------------------
# Opsiyonel: ileride ek task veya parametreler
# WORKER_A_TASKS.append({"name": "open_interest", "interval": 1800})
