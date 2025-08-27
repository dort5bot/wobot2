# utils/config_worker.py
# worker’a özel, trade ve işlemle ilgili ayarlar (risk limitleri, pozisyon boyutu, trade frekansı vb.)
# 	1. WORKER_A_TASKS → worker_a.py’nin loop’unda interval kontrolü için kullanılıyor.
# 	2. WORKER_B_INTERVAL → worker_b.py’nin loop’unda kullanılıyor.
# 	3. CACHE_TTL_SECONDS → hafizada tutma süresi. her task’in cache süresini ayrı ayrı tutuyor.
# 	4. SYMBOLS → hem worker_a hem worker_b tarafından kullanılıyor.
# RISK_MAX_DAILY_LOSS = 100  # günlük max kayıp örnek
# DB_PATH = "db/trades.sqlite"  # worker için DB path

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
# Worker B pipeline ayarları (opsiyonel; yoksa WorkerB varsayılanları kullanır)
WORKER_B_WORKERS = 3            # paralel worker sayısı
WORKER_B_PROC_MAXSIZE = 2000    # işlem kuyruğu kapasitesi


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



# utla/risk_manage.py
RISK_MAX_DAILY_LOSS = 100  # günlük max kayıp örnek
DB_PATH = "db/trades.sqlite"  # worker için DB path
