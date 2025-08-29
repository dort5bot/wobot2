# utils/config.py
# Binance bot için konfigürasyon dosyası.
# Tüm parametreler merkezi olarak burada tutulur ve runtime sırasında güncellenebilir.

'''
# - .env üzerinden tüm değerleri yükler
# - CONFIG nesnesi altında gruplanmış halde kullanılabilir
# - Binance, Bot, TA, System, IO, Telegram, Database modülleri ayrı dataclass ile yönetilir
Ek faydalı configler ekleme > Binance connection management
    RATE_LIMIT_BUFFER: Rate limit için buffer süresi
    LOG_LEVEL ve DEBUG_MODE: Sistem log seviyeleri
    Telegram için ENABLED, RETRY_ATTEMPTS, TIMEOUT
    Database için BACKUP_INTERVAL ve MAX_BACKUP_FILES
    Type annotations eklendi: Tüm yeni değişkenlere uygun type annotations eklendi.
Bu eklemeler sistemin daha robust ve configurable olmasını sağlayacaktır.
'''

import os
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from dotenv import load_dotenv

ENV_PATH = ".env"
load_dotenv(ENV_PATH, override=True)

# ✅=== Binance Config ===✅
@dataclass
class BinanceConfig:
    # --- API Bağlantıları ---
    BASE_URL: str = "https://api.binance.com"       # Spot API
    FAPI_URL: str = "https://fapi.binance.com"      # Futures API
    VAPI_URL: str = "https://vapi.binance.com"      # Options API

    # --- API Anahtarları ---
    API_KEY: Optional[str] = os.getenv("BINANCE_API_KEY")
    SECRET_KEY: Optional[str] = os.getenv("BINANCE_SECRET_KEY")

    # --- İstek Ayarları ---
    REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", 10))
    DEFAULT_RETRY_ATTEMPTS: int = int(os.getenv("DEFAULT_RETRY_ATTEMPTS", 3))
    RATE_LIMIT_BUFFER: float = float(os.getenv("RATE_LIMIT_BUFFER", 0.1))
    MAX_REQUESTS_PER_SECOND: int = int(os.getenv("MAX_REQUESTS_PER_SECOND", 10))

    # --- Circuit Breaker Ayarları ---
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = int(os.getenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", 5))
    CIRCUIT_BREAKER_RESET_TIMEOUT: int = int(os.getenv("CIRCUIT_BREAKER_RESET_TIMEOUT", 60))

    # --- WebSocket Ayarları ---
    WS_RECONNECT_DELAY: int = int(os.getenv("WS_RECONNECT_DELAY", 5))
    MAX_WS_CONNECTIONS: int = int(os.getenv("MAX_WS_CONNECTIONS", 5))
    CACHE_CLEANUP_INTERVAL: int = int(os.getenv("CACHE_CLEANUP_INTERVAL", 60))

    # --- Cache Ayarları ---
    BINANCE_TICKER_TTL: int = int(os.getenv("BINANCE_TICKER_TTL", 5))
    CONCURRENCY: int = int(os.getenv("BINANCE_CONCURRENCY", 8))

    # --- Stream Varsayılanları ---
    STREAM_INTERVAL: str = os.getenv("STREAM_INTERVAL", "1m")

    # --- Ticaret Ayarları ---
    TRADES_LIMIT: int = int(os.getenv("TRADES_LIMIT", 500))
    WHALE_USD_THRESHOLD: float = float(os.getenv("WHALE_USD_THRESHOLD", 50000))
    FUNDING_POLL_INTERVAL: int = int(os.getenv("FUNDING_POLL_INTERVAL", 5))

    # --- Logging / Debugging ---
    LOG_LEVEL: int = getattr(logging, os.getenv("LOG_LEVEL", "INFO"))  # String'i logging level'a çevir
    DEBUG_MODE: bool = os.getenv("DEBUG_MODE", "false").lower() == "true"

    # --- Sembol Listeleri ---
    TOP_SYMBOLS_FOR_IO: List[str] = field(
        default_factory=lambda: os.getenv("TOP_SYMBOLS_FOR_IO", "BTCUSDT,ETHUSDT").split(",")
    )
    SCAN_SYMBOLS: List[str] = field(
        default_factory=lambda: os.getenv(
            "SCAN_SYMBOLS", 
            "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,TRXUSDT,CAKEUSDT,SUIUSDT,PEPEUSDT,ARPAUSDT,TURBOUSDT"
        ).split(",")
    )

# ✅=== Bot Config ===✅
@dataclass
class BotConfig:
    PAPER_MODE: bool = os.getenv("PAPER_MODE", "true").lower() == "true"
    EVALUATOR_WINDOW: int = int(os.getenv("EVALUATOR_WINDOW", 60))
    EVALUATOR_THRESHOLD: float = float(os.getenv("EVALUATOR_THRESHOLD", 0.5))

# ✅=== TA Config ===✅
@dataclass
class TAConfig:
    EMA_PERIODS: List[int] = field(
        default_factory=lambda: [int(x) for x in os.getenv("EMA_PERIODS", "20,50,200").split(",")]
    )
    EMA_PERIOD: int = int(os.getenv("EMA_PERIOD", 20))
    MACD_FAST: int = int(os.getenv("MACD_FAST", 12))
    MACD_SLOW: int = int(os.getenv("MACD_SLOW", 26))
    MACD_SIGNAL: int = int(os.getenv("MACD_SIGNAL", 9))
    ADX_PERIOD: int = int(os.getenv("ADX_PERIOD", 14))
    RSI_PERIOD: int = int(os.getenv("RSI_PERIOD", 14))
    STOCH_K: int = int(os.getenv("STOCH_K", 14))
    STOCH_D: int = int(os.getenv("STOCH_D", 3))
    ATR_PERIOD: int = int(os.getenv("ATR_PERIOD", 14))
    BB_PERIOD: int = int(os.getenv("BB_PERIOD", 20))
    BB_STDDEV: float = float(os.getenv("BB_STDDEV", 2))
    SHARPE_RISK_FREE_RATE: float = float(os.getenv("SHARPE_RISK_FREE_RATE", 0.02))
    SHARPE_PERIOD: int = int(os.getenv("SHARPE_PERIOD", 252))
    OBV_ENABLED: bool = os.getenv("OBV_ENABLED", "true").lower() == "true"
    OBI_DEPTH: int = int(os.getenv("OBI_DEPTH", 20))
    OPEN_INTEREST_ENABLED: bool = os.getenv("OPEN_INTEREST_ENABLED", "true").lower() == "true"
    FUNDING_RATE_ENABLED: bool = os.getenv("FUNDING_RATE_ENABLED", "true").lower() == "true"
    SOCIAL_SENTIMENT_ENABLED: bool = os.getenv("SOCIAL_SENTIMENT_ENABLED", "false").lower() == "true"
    PIPELINE_INTERVAL = 60  # seconds    worker_d.py
    MIN_DATA_POINTS = 20    # worker_d.py
    CACHE_TTL = 300        # worker_d.py
    SYMBOLS = ["BTCUSDT", "ETHUSDT"]  # İzlenecek semboller worker_d.py

    # Advanced alpha_ta & analysis params
    ALPHA_LONG_THRESHOLD: float = float(os.getenv("ALPHA_LONG_THRESHOLD", 0.6))
    ALPHA_SHORT_THRESHOLD: float = float(os.getenv("ALPHA_SHORT_THRESHOLD", -0.6))

    KALMAN_Q: float = float(os.getenv("KALMAN_Q", 1e-5))
    KALMAN_R: float = float(os.getenv("KALMAN_R", 1e-2))

    REGIME_WINDOW: int = int(os.getenv("REGIME_WINDOW", 80))
    ENTROPY_M: int = int(os.getenv("ENTROPY_M", 3))
    ENTROPY_R_FACTOR: float = float(os.getenv("ENTROPY_R_FACTOR", 0.2))
    LEADLAG_MAX_LAG: int = int(os.getenv("LEADLAG_MAX_LAG", 10))

    # alpha_ta ağırlıkları
    W_KALMAN: float = float(os.getenv("W_KALMAN", 0.20))
    W_HILBERT: float = float(os.getenv("W_HILBERT", 0.20))
    W_ENTROPY: float = float(os.getenv("W_ENTROPY", 0.20))
    W_REGIME: float = float(os.getenv("W_REGIME", 0.20))
    W_LEADLAG: float = float(os.getenv("W_LEADLAG", 0.20))

# ✅=== System Config ===✅
@dataclass
class SystemConfig:
    MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", 2))

# ✅=== IO Config ===✅
@dataclass
class IOConfig:
    ENABLED: bool = os.getenv("IO_ENABLED", "true").lower() == "true"
    WINDOW: int = int(os.getenv("IO_WINDOW", 15))
    MIN_NOTIONAL: float = float(os.getenv("IO_MIN_NOTIONAL", 10000))
    MOMENTUM_LOOKBACK: int = int(os.getenv("IO_MOMENTUM_LOOKBACK", 5))
    DEPTH_LEVELS: int = int(os.getenv("IO_DEPTH_LEVELS", 20))
    CACHE_TTL: int = int(os.getenv("IO_CACHE_TTL", 10))
    CASHFLOW_TIMEFRAMES: Dict[str, int] = field(
        default_factory=lambda: {
            "15m": 15,
            "1h": 60,
            "4h": 240,
            "12h": 720,
            "1d": 1440,
        }
    )
    RSI_PERIOD: int = int(os.getenv("IO_RSI_PERIOD", 14))
    OBI_DEPTH: int = int(os.getenv("IO_OBI_DEPTH", 20))
    FUNDING_AVG: float = float(os.getenv("IO_FUNDING_AVG", 0.0))
    FUNDING_STD: float = float(os.getenv("IO_FUNDING_STD", 0.0005))
    OI_BASELINE: float = float(os.getenv("IO_OI_BASELINE", 1.0))
    LIQUIDATION_BASELINE: float = float(os.getenv("IO_LIQUIDATION_BASELINE", 1.0))
    TOP_N_MIGRATION: int = int(os.getenv("IO_TOP_N_MIGRATION", 10))
    MAX_SYMBOLS_MARKET: int = int(os.getenv("IO_MAX_SYMBOLS_MARKET", 30))
    QUOTE_ASSET: str = os.getenv("IO_QUOTE_ASSET", "USDT")
    IO_CONCURRENCY: int = int(os.getenv("IO_CONCURRENCY", 5))

# ✅=== Telegram Config ===
@dataclass
class TelegramConfig:
    BOT_TOKEN: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
    ALERT_CHAT_ID: Optional[str] = os.getenv("ALERT_CHAT_ID")

# ✅=== Database Config ===
@dataclass
class DatabaseConfig:
    DB_PATH: str = os.getenv("DB_PATH", "data/bot.db")

# ✅=== Master Config ===
@dataclass
class AppConfig:
    SYSTEM: SystemConfig = field(default_factory=SystemConfig)
    BINANCE: BinanceConfig = field(default_factory=BinanceConfig)
    BOT: BotConfig = field(default_factory=BotConfig)
    TA: TAConfig = field(default_factory=TAConfig)
    IO: IOConfig = field(default_factory=IOConfig)
    TELEGRAM: TelegramConfig = field(default_factory=TelegramConfig)
    DATABASE: DatabaseConfig = field(default_factory=DatabaseConfig)

CONFIG = AppConfig()

# ✅--- Runtime Config Güncelleme Fonksiyonları ---

def update_binance_keys(api_key: str, secret_key: str):
    """
    Sadece Binance API Key ve Secret'ını runtime'da günceller.
    """
    CONFIG.BINANCE.API_KEY = api_key
    CONFIG.BINANCE.SECRET_KEY = secret_key

def update_binance_config(**kwargs):
    """
    BinanceConfig içerisindeki herhangi bir parametreyi runtime'da günceller.
    Örnek kullanım:
        update_binance_config(REQUEST_TIMEOUT=20, LOG_LEVEL=logging.DEBUG)
    """
    for k, v in kwargs.items():
        if hasattr(CONFIG.BINANCE, k):
            setattr(CONFIG.BINANCE, k, v)
        else:
            raise AttributeError(f"BinanceConfig parametresi bulunamadı: {k}")

