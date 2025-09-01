#utils/config.py
import os
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from dotenv import load_dotenv
from functools import lru_cache

ENV_PATH = ".env"
load_dotenv(ENV_PATH, override=True)

# ✅=== Environment Variable Helper ===✅
def get_env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ('true', '1', 'yes', 'y')

def get_env_list(key: str, default: str = "", separator: str = ",") -> List[str]:
    return [item.strip() for item in os.getenv(key, default).split(separator) if item.strip()]

def get_env_int_list(key: str, default: str = "", separator: str = ",") -> List[int]:
    return [int(item.strip()) for item in os.getenv(key, default).split(separator) if item.strip()]

# ✅=== Binance Config ===✅
@dataclass
class BinanceConfig:
    BASE_URL: str = "https://api.binance.com"
    FAPI_URL: str = "https://fapi.binance.com"
    VAPI_URL: str = "https://vapi.binance.com"
    TESTNET_BASE_URL: str = "https://testnet.binancefuture.com"

    API_KEY: Optional[str] = os.getenv("BINANCE_API_KEY")
    SECRET_KEY: Optional[str] = os.getenv("BINANCE_SECRET_KEY")

    REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "10"))
    DEFAULT_RETRY_ATTEMPTS: int = int(os.getenv("DEFAULT_RETRY_ATTEMPTS", "3"))
    RATE_LIMIT_BUFFER: float = float(os.getenv("RATE_LIMIT_BUFFER", "0.1"))
    MAX_REQUESTS_PER_SECOND: int = int(os.getenv("MAX_REQUESTS_PER_SECOND", "10"))

    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = int(os.getenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5"))
    CIRCUIT_BREAKER_RESET_TIMEOUT: int = int(os.getenv("CIRCUIT_BREAKER_RESET_TIMEOUT", "60"))

    WS_RECONNECT_DELAY: int = int(os.getenv("WS_RECONNECT_DELAY", "5"))
    MAX_WS_CONNECTIONS: int = int(os.getenv("MAX_WS_CONNECTIONS", "5"))
    CACHE_CLEANUP_INTERVAL: int = int(os.getenv("CACHE_CLEANUP_INTERVAL", "60"))

    BINANCE_TICKER_TTL: int = int(os.getenv("BINANCE_TICKER_TTL", "5"))
    CONCURRENCY: int = int(os.getenv("BINANCE_CONCURRENCY", "8"))

    STREAM_INTERVAL: str = os.getenv("STREAM_INTERVAL", "1m")

    TRADES_LIMIT: int = int(os.getenv("TRADES_LIMIT", "500"))
    WHALE_USD_THRESHOLD: float = float(os.getenv("WHALE_USD_THRESHOLD", "50000"))
    FUNDING_POLL_INTERVAL: int = int(os.getenv("FUNDING_POLL_INTERVAL", "5"))

    LOG_LEVEL: int = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    DEBUG_MODE: bool = get_env_bool("DEBUG_MODE", False)

    TOP_SYMBOLS_FOR_IO: List[str] = field(
        default_factory=lambda: get_env_list("TOP_SYMBOLS_FOR_IO", "BTCUSDT,ETHUSDT")
    )
    SCAN_SYMBOLS: List[str] = field(
        default_factory=lambda: get_env_list(
            "SCAN_SYMBOLS", 
            "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,TRXUSDT,CAKEUSDT,SUIUSDT,PEPEUSDT,ARPAUSDT,TURBOUSDT"
        )
    )

# ✅=== Bot Config ===✅
@dataclass
class BotConfig:
    PAPER_MODE: bool = get_env_bool("PAPER_MODE", True)
    EVALUATOR_WINDOW: int = int(os.getenv("EVALUATOR_WINDOW", "60"))
    EVALUATOR_THRESHOLD: float = float(os.getenv("EVALUATOR_THRESHOLD", "0.5"))
    SIGNAL_COOLDOWN: int = int(os.getenv("SIGNAL_COOLDOWN", "60"))

# ✅=== TA Config ===✅
@dataclass
class TAConfig:
    EMA_PERIODS: List[int] = field(
        default_factory=lambda: get_env_int_list("EMA_PERIODS", "20,50,200")
    )
    EMA_PERIOD: int = int(os.getenv("EMA_PERIOD", "20"))
    MACD_FAST: int = int(os.getenv("MACD_FAST", "12"))
    MACD_SLOW: int = int(os.getenv("MACD_SLOW", "26"))
    MACD_SIGNAL: int = int(os.getenv("MACD_SIGNAL", "9"))
    ADX_PERIOD: int = int(os.getenv("ADX_PERIOD", "14"))
    RSI_PERIOD: int = int(os.getenv("RSI_PERIOD", "14"))
    STOCH_K: int = int(os.getenv("STOCH_K", "14"))
    STOCH_D: int = int(os.getenv("STOCH_D", "3"))
    ATR_PERIOD: int = int(os.getenv("ATR_PERIOD", "14"))
    BB_PERIOD: int = int(os.getenv("BB_PERIOD", "20"))
    BB_STDDEV: float = float(os.getenv("BB_STDDEV", "2"))
    SHARPE_RISK_FREE_RATE: float = float(os.getenv("SHARPE_RISK_FREE_RATE", "0.02"))
    SHARPE_PERIOD: int = int(os.getenv("SHARPE_PERIOD", "252"))
    OBV_ENABLED: bool = get_env_bool("OBV_ENABLED", True)
    OBI_DEPTH: int = int(os.getenv("OBI_DEPTH", "20"))
    OPEN_INTEREST_ENABLED: bool = get_env_bool("OPEN_INTEREST_ENABLED", True)
    FUNDING_RATE_ENABLED: bool = get_env_bool("FUNDING_RATE_ENABLED", True)
    SOCIAL_SENTIMENT_ENABLED: bool = get_env_bool("SOCIAL_SENTIMENT_ENABLED", False)
    TA_CACHE_TTL: int = int(os.getenv("TA_CACHE_TTL", "300"))
    TA_PIPELINE_INTERVAL: int = int(os.getenv("TA_PIPELINE_INTERVAL", "60"))
    TA_MIN_DATA_POINTS: int = int(os.getenv("TA_MIN_DATA_POINTS", "20"))
    TA_SYMBOLS: List[str] = field(
        default_factory=lambda: get_env_list("TA_SYMBOLS", "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,TRXUSDT")
    )

    ALPHA_LONG_THRESHOLD: float = float(os.getenv("ALPHA_LONG_THRESHOLD", "0.6"))
    ALPHA_SHORT_THRESHOLD: float = float(os.getenv("ALPHA_SHORT_THRESHOLD", "-0.6"))

    KALMAN_Q: float = float(os.getenv("KALMAN_Q", "1e-5"))
    KALMAN_R: float = float(os.getenv("KALMAN_R", "1e-2"))

    REGIME_WINDOW: int = int(os.getenv("REGIME_WINDOW", "80"))
    ENTROPY_M: int = int(os.getenv("ENTROPY_M", "3"))
    ENTROPY_R_FACTOR: float = float(os.getenv("ENTROPY_R_FACTOR", "0.2"))
    LEADLAG_MAX_LAG: int = int(os.getenv("LEADLAG_MAX_LAG", "10"))

    W_KALMAN: float = float(os.getenv("W_KALMAN", "0.20"))
    W_HILBERT: float = float(os.getenv("W_HILBERT", "0.20"))
    W_ENTROPY: float = float(os.getenv("W_ENTROPY", "0.20"))
    W_REGIME: float = float(os.getenv("W_REGIME", "0.20"))
    W_LEADLAG: float = float(os.getenv("W_LEADLAG", "0.20"))

# ✅=== System Config ===✅
@dataclass
class SystemConfig:
    MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", "2"))
    HEARTBEAT_INTERVAL: int = int(os.getenv("HEARTBEAT_INTERVAL", "30"))
    HEALTH_CHECK_TIMEOUT: int = int(os.getenv("HEALTH_CHECK_TIMEOUT", "60"))

# ✅=== Worker Config ===✅
@dataclass
class WorkerConfig:
    WORKER_B_INTERVAL: int = int(os.getenv("WORKER_B_INTERVAL", "5"))
    WORKER_B_WORKERS: int = int(os.getenv("WORKER_B_WORKERS", "3"))
    WORKER_B_PROC_MAXSIZE: int = int(os.getenv("WORKER_B_PROC_MAXSIZE", "2000"))

# ✅=== IO Config ===✅
@dataclass
class IOConfig:
    ENABLED: bool = get_env_bool("IO_ENABLED", True)
    WINDOW: int = int(os.getenv("IO_WINDOW", "15"))
    MIN_NOTIONAL: float = float(os.getenv("IO_MIN_NOTIONAL", "10000"))
    MOMENTUM_LOOKBACK: int = int(os.getenv("IO_MOMENTUM_LOOKBACK", "5"))
    DEPTH_LEVELS: int = int(os.getenv("IO_DEPTH_LEVELS", "20"))
    CACHE_TTL: int = int(os.getenv("IO_CACHE_TTL", "10"))
    
    @property
    def CASHFLOW_TIMEFRAMES(self) -> Dict[str, int]:
        return {
            "15m": 15,
            "1h": 60,
            "4h": 240,
            "12h": 720,
            "1d": 1440,
        }
    
    RSI_PERIOD: int = int(os.getenv("IO_RSI_PERIOD", "14"))
    OBI_DEPTH: int = int(os.getenv("IO_OBI_DEPTH", "20"))
    FUNDING_AVG: float = float(os.getenv("IO_FUNDING_AVG", "0.0"))
    FUNDING_STD: float = float(os.getenv("IO_FUNDING_STD", "0.0005"))
    OI_BASELINE: float = float(os.getenv("IO_OI_BASELINE", "1.0"))
    LIQUIDATION_BASELINE: float = float(os.getenv("IO_LIQUIDATION_BASELINE", "1.0"))
    TOP_N_MIGRATION: int = int(os.getenv("IO_TOP_N_MIGRATION", "10"))
    MAX_SYMBOLS_MARKET: int = int(os.getenv("IO_MAX_SYMBOLS_MARKET", "30"))
    QUOTE_ASSET: str = os.getenv("IO_QUOTE_ASSET", "USDT")
    IO_CONCURRENCY: int = int(os.getenv("IO_CONCURRENCY", "5"))

# ✅=== Telegram Config ===
@dataclass
class TelegramConfig:
    BOT_TOKEN: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
    ALERT_CHAT_ID: Optional[str] = os.getenv("ALERT_CHAT_ID")
    ENABLED: bool = get_env_bool("TELEGRAM_ENABLED", True)
    NOTIFICATION_LEVEL: str = os.getenv("NOTIFICATION_LEVEL", "INFO")  # DEBUG, INFO, WARNING, ERROR

# ✅=== Database Config ===
@dataclass
class DatabaseConfig:
    DB_PATH: str = os.getenv("DB_PATH", "data/bot.db")
    BACKUP_INTERVAL: int = int(os.getenv("DB_BACKUP_INTERVAL", "3600"))  # 1 hour
    MAX_BACKUP_FILES: int = int(os.getenv("MAX_BACKUP_FILES", "7"))

# ✅=== Cache Config ===
@dataclass
class CacheConfig:
    REDIS_URL: Optional[str] = os.getenv("REDIS_URL")
    CACHE_TTL: int = int(os.getenv("CACHE_TTL", "300"))
    MAX_CACHE_SIZE: int = int(os.getenv("MAX_CACHE_SIZE", "1000"))

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
    WORKER: WorkerConfig = field(default_factory=WorkerConfig)
    CACHE: CacheConfig = field(default_factory=CacheConfig)

# ✅--- Singleton Config Instance ---
@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Cached config instance for better performance"""
    return AppConfig()

CONFIG = get_config()

# ✅--- Runtime Config Güncelleme Fonksiyonları ---
def update_binance_keys(api_key: str, secret_key: str):
    CONFIG.BINANCE.API_KEY = api_key
    CONFIG.BINANCE.SECRET_KEY = secret_key

def update_binance_config(**kwargs):
    for k, v in kwargs.items():
        if hasattr(CONFIG.BINANCE, k):
            setattr(CONFIG.BINANCE, k, v)
        else:
            raise AttributeError(f"BinanceConfig parametresi bulunamadı: {k}")

def update_telegram_config(bot_token: Optional[str] = None, chat_id: Optional[str] = None):
    if bot_token:
        CONFIG.TELEGRAM.BOT_TOKEN = bot_token
    if chat_id:
        CONFIG.TELEGRAM.ALERT_CHAT_ID = chat_id

def reload_config():
    """Reload environment variables and refresh config"""
    load_dotenv(ENV_PATH, override=True)
    global CONFIG
    get_config.cache_clear()
    CONFIG = get_config()

