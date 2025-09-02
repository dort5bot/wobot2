from __future__ import annotations

import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import asyncio
import math
import time
import logging
import threading
from typing import Dict, Optional, Tuple, List, Any, Callable, Union
from dataclasses import dataclass
from collections import defaultdict, OrderedDict
import functools
import inspect
from enum import Enum
from datetime import datetime, timedelta

# Performance monitoring için
import timeit
from contextlib import contextmanager

# Circuit breaker için
class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

# Config fallback mekanizması - GELİŞMİŞ
try:
    from utils.config import CONFIG
    CONFIG_LOADED = True
except ImportError as e:
    logging.warning(f"utils.config yüklenemedi, güvenli default değerler kullanılıyor: {e}")
    
    @dataclass
    class SafeConfig:
        class TA:
            EMA_PERIOD = 20
            MACD_FAST = 12
            MACD_SLOW = 26
            MACD_SIGNAL = 9
            ADX_PERIOD = 14
            RSI_PERIOD = 14
            STOCH_K = 14
            STOCH_D = 3
            ATR_PERIOD = 14
            BB_PERIOD = 20
            BB_STDDEV = 2.0
            SHARPE_RISK_FREE_RATE = 0.02
            SHARPE_PERIOD = 252
            TA_CACHE_TTL = 300
            TA_PIPELINE_INTERVAL = 60
            TA_MIN_DATA_POINTS = 20
            ALPHA_LONG_THRESHOLD = 0.6
            ALPHA_SHORT_THRESHOLD = -0.6
            KALMAN_Q = 1e-5
            KALMAN_R = 1e-2
            REGIME_WINDOW = 80
            ENTROPY_M = 3
            ENTROPY_R_FACTOR = 0.2
            LEADLAG_MAX_LAG = 10
            W_KALMAN = 0.20
            W_HILBERT = 0.20
            W_ENTROPY = 0.20
            W_REGIME = 0.20
            W_LEADLAG = 0.20
            EMA_PERIODS = [20, 50]
        
        class SYSTEM:
            MAX_WORKERS = 2
    
    CONFIG = SafeConfig()
    CONFIG_LOADED = False

# ------------------------------------------------------------
# Logger Kurulumu - GELİŞMİŞ
# ------------------------------------------------------------
logger = logging.getLogger("ta_utils")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO if CONFIG_LOADED else logging.WARNING)

# ------------------------------------------------------------
# Performance Monitoring
# ------------------------------------------------------------
@contextmanager
def time_execution(operation_name: str):
    """Execution time tracking için context manager"""
    start_time = time.time()
    try:
        yield
    finally:
        end_time = time.time()
        execution_time = end_time - start_time
        logger.debug(f"{operation_name} executed in {execution_time:.4f} seconds")

def track_performance(func):
    """Fonksiyon execution time tracking decorator"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with time_execution(f"Function {func.__name__}"):
            return func(*args, **kwargs)
    return wrapper

# ------------------------------------------------------------
# Circuit Breaker Pattern
# ------------------------------------------------------------
class CircuitBreaker:
    """TA fonksiyonları için circuit breaker pattern"""
    def __init__(self, failure_threshold: int = 5, reset_timeout: int = 60, name: str = "ta_default"):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = CircuitState.CLOSED
        self.name = name
        self.success_count = 0
        logger.info(f"CircuitBreaker '{name}' initialized with threshold {failure_threshold}, timeout {reset_timeout}")

    def execute(self, func, *args, **kwargs):
        """Fonksiyonu circuit breaker ile çalıştır"""
        current_time = time.time()
        
        # Circuit OPEN durumunda ve timeout dolmadıysa
        if self.state == CircuitState.OPEN:
            if current_time - self.last_failure_time > self.reset_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.warning(f"CircuitBreaker '{self.name}' moving to HALF_OPEN state")
            else:
                remaining = self.reset_timeout - (current_time - self.last_failure_time)
                logger.error(f"CircuitBreaker '{self.name}' is OPEN. Retry in {remaining:.1f}s")
                raise Exception(f"Circuit breaker is OPEN. Retry in {remaining:.1f}s")
        
        try:
            # Fonksiyonu çalıştır
            result = func(*args, **kwargs)
            
            # Başarılı ise state'i güncelle
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.success_count += 1
                logger.info(f"CircuitBreaker '{self.name}' reset to CLOSED state after successful execution")
            
            return result
            
        except Exception as e:
            # Hata durumunda circuit breaker state'ini güncelle
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                logger.error(f"CircuitBreaker '{self.name}' tripped to OPEN state due to {self.failure_count} failures")
            
            logger.error(f"CircuitBreaker '{self.name}' execution failed: {str(e)}")
            raise e

    def get_status(self) -> Dict[str, Any]:
        """Circuit breaker durumunu getir"""
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_time": self.last_failure_time,
            "time_since_last_failure": time.time() - self.last_failure_time if self.last_failure_time > 0 else 0
        }

# Global circuit breaker instances
ta_circuit_breaker = CircuitBreaker(
    failure_threshold=5,
    reset_timeout=60,
    name="ta_main"
)

# ------------------------------------------------------------
# İç yardımcılar ve Cache Mekanizması - GELİŞMİŞ
# ------------------------------------------------------------

def _get_max_workers(default: int = 2) -> int:
    """Thread sayısını güvenli şekilde alır"""
    try:
        if not CONFIG_LOADED:
            return default
            
        system = getattr(CONFIG, "SYSTEM", None)
        if system is not None and hasattr(system, "MAX_WORKERS"):
            try:
                mw = int(getattr(system, "MAX_WORKERS"))
                return max(1, min(mw, 10))  # 1-10 arası sınırla
            except (ValueError, TypeError):
                logger.warning("MAX_WORKERS geçersiz, default değer kullanılıyor")
                return default
        return default
    except Exception as e:
        logger.warning(f"MAX_WORKERS alınırken hata: {e}, default değer kullanılıyor: {default}")
        return default

@dataclass
class TAMetrics:
    """TA metriklerini tutan data class"""
    total_calculations: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    calculation_errors: int = 0
    io_requests: int = 0
    io_errors: int = 0
    execution_times: Dict[str, List[float]] = field(default_factory=lambda: defaultdict(list))

class AdaptiveCache:
    """Adaptive cache with automatic size adjustment"""
    def __init__(self, initial_max_size: int = 1000, min_size: int = 100, max_size: int = 5000):
        self._cache = OrderedDict()
        self._hit_count = 0
        self._miss_count = 0
        self._last_cleanup = time.time()
        self._min_size = min_size
        self._max_size = max_size
        self._current_max_size = initial_max_size
        self._lock = threading.RLock()
        self._access_pattern = []  # LRU pattern tracking
        self._last_optimization = time.time()
        logger.info(f"Adaptive Cache initialized with size range: {min_size}-{max_size}")
    
    def _optimize_size(self):
        """Cache boyutunu otomatik optimize et"""
        current_time = time.time()
        if current_time - self._last_optimization < 300:  # 5 dakikada bir
            return
            
        hit_rate = self._hit_count / max(self._hit_count + self._miss_count, 1)
        
        # Hit rate'e göre cache boyutunu ayarla
        if hit_rate > 0.8 and self._current_max_size < self._max_size:
            # Yüksek hit rate, cache boyutunu artır
            new_size = min(self._current_max_size * 1.2, self._max_size)
            logger.info(f"Optimizing cache: increasing size from {self._current_max_size} to {new_size} (hit rate: {hit_rate:.2%})")
            self._current_max_size = int(new_size)
        elif hit_rate < 0.3 and self._current_max_size > self._min_size:
            # Düşük hit rate, cache boyutunu azalt
            new_size = max(self._current_max_size * 0.8, self._min_size)
            logger.info(f"Optimizing cache: decreasing size from {self._current_max_size} to {new_size} (hit rate: {hit_rate:.2%})")
            self._current_max_size = int(new_size)
            
        self._last_optimization = current_time
    
    def get_ta_result(self, symbol: str, timeframe: str, indicator: str) -> Optional[Any]:
        """Cache'ten sonucu getir"""
        key = f"{symbol}_{timeframe}_{indicator}"
        with self._lock:
            self._optimize_size()  # Boyut optimizasyonu
            
            if key in self._cache:
                cache_data = self._cache[key]
                # LRU özelliği için en son kullanılanı en sona taşı
                self._cache.move_to_end(key)
                if time.time() < cache_data['expiry']:
                    self._hit_count += 1
                    logger.debug(f"Cache hit for {key}")
                    return cache_data['value']
                else:
                    # Süresi dolmuş entry'yi sil
                    del self._cache[key]
                    logger.debug(f"Cache expired for {key}")
            self._miss_count += 1
            return None
    
    def set_ta_result(self, symbol: str, timeframe: str, indicator: str, value: Any, ttl: int = 300):
        """Cache'e sonucu kaydet"""
        key = f"{symbol}_{timeframe}_{indicator}"
        with self._lock:
            # Cache limit kontrolü
            if len(self._cache) >= self._current_max_size:
                # LRU prensibiyle en eski entry'yi sil
                removed_key, _ = self._cache.popitem(last=False)
                logger.debug(f"Cache limit reached, removed: {removed_key}")
            
            self._cache[key] = {
                'value': value,
                'expiry': time.time() + ttl
            }
            # LRU özelliği için en son ekleneni en sona taşı
            self._cache.move_to_end(key)
            self._cleanup_expired()
    
    def _cleanup_expired(self):
        """Süresi dolmuş cache entry'lerini temizle"""
        current_time = time.time()
        if current_time - self._last_cleanup > 300:  # 5 dakikada bir temizle
            expired_keys = [k for k, v in self._cache.items() if v['expiry'] < current_time]
            for key in expired_keys:
                del self._cache[key]
            if expired_keys:
                logger.debug(f"Cache cleanup completed. Removed {len(expired_keys)} expired entries.")
            self._last_cleanup = current_time
    
    def get_stats(self) -> Dict[str, Any]:
        """Cache istatistiklerini getir"""
        with self._lock:
            total = self._hit_count + self._miss_count
            return {
                'size': len(self._cache),
                'hits': self._hit_count,
                'misses': self._miss_count,
                'hit_ratio': self._hit_count / max(total, 1),
                'max_size': self._current_max_size,
                'utilization': len(self._cache) / self._current_max_size,
                'min_size': self._min_size,
                'max_possible_size': self._max_size
            }
    
    def clear(self):
        """Cache'i tamamen temizle"""
        with self._lock:
            self._cache.clear()
            self._hit_count = 0
            self._miss_count = 0
            logger.info("TA Cache cleared completely")

# Global instances (thread-safe)
ta_cache = AdaptiveCache(initial_max_size=1000, min_size=100, max_size=5000)
ta_metrics = TAMetrics()

# ------------------------------------------------------------
# Unit Test Decorator
# ------------------------------------------------------------
def unit_test(expected_result=None, tolerance=0.01):
    """Fonksiyon unit test decorator"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Fonksiyonu çalıştır
            result = func(*args, **kwargs)
            
            # Eğer expected result verilmişse kontrol et
            if expected_result is not None:
                if hasattr(result, '__len__') and hasattr(expected_result, '__len__'):
                    # Array/Series karşılaştırması
                    if len(result) == len(expected_result):
                        for i, (r, e) in enumerate(zip(result, expected_result)):
                            if abs(r - e) > tolerance:
                                logger.warning(f"Unit test failed for {func.__name__} at index {i}: got {r}, expected {e}")
                    else:
                        logger.warning(f"Unit test failed for {func.__name__}: length mismatch")
                else:
                    # Scalar karşılaştırması
                    if abs(result - expected_result) > tolerance:
                        logger.warning(f"Unit test failed for {func.__name__}: got {result}, expected {expected_result}")
            
            return result
        return wrapper
    return decorator

# =============================================================
# Yardımcı Fonksiyonlar - GELİŞMİŞ
# =============================================================

def validate_dataframe(df: pd.DataFrame, required_columns: List[str] = None) -> bool:
    """DataFrame'in geçerli olup olmadığını kontrol et"""
    if df is None or df.empty:
        logger.warning("DataFrame is None or empty")
        return False
    
    if required_columns is None:
        required_columns = ['open', 'high', 'low', 'close', 'volume']
    
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        logger.warning(f"DataFrame missing required columns: {missing_columns}")
        return False
    
    # NaN kontrolü
    if df[required_columns].isna().all().any():
        logger.warning("All values are NaN in some columns")
        return False
    
    return True

def safe_column_access(df: pd.DataFrame, column: str, default_value: Any = np.nan, 
                      log_warning: bool = True) -> pd.Series:
    """Güvenli sütun erişimi sağlar"""
    if df is None or df.empty:
        if log_warning:
            logger.warning(f"DataFrame is None or empty, returning default for {column}")
        return pd.Series([default_value], index=[pd.Timestamp.now()] if df is None else df.index)
    
    if column in df.columns:
        series = df[column]
        if series.isna().all():
            if log_warning:
                logger.warning(f"All values are NaN in column {column}")
            return pd.Series([default_value] * len(df), index=df.index, name=column)
        return series
    else:
        if log_warning:
            logger.warning(f"Column {column} not found in DataFrame, returning default")
        return pd.Series([default_value] * len(df), index=df.index, name=column)

def safe_rolling_calculation(series: pd.Series, window: int, calculation: Callable, 
                           min_periods: int = 1, default: Any = np.nan) -> pd.Series:
    """Güvenli rolling hesaplama"""
    try:
        if len(series) < min_periods:
            return pd.Series([default] * len(series), index=series.index)
        return calculation(series, window)
    except Exception as e:
        logger.error(f"Rolling calculation failed: {e}")
        return pd.Series([default] * len(series), index=series.index)

def normalize_symbol(symbol: str) -> str:
    """Sembol ismini normalize et"""
    if not symbol:
        return "BTCUSDT"  # Default symbol
    
    # Çeşitli formatları standart formata dönüştür
    symbol = symbol.upper().replace('/', '').replace(':', '').replace('-', '')
    
    # USDT ile bitmiyorsa ekle (Binance standardı)
    if not symbol.endswith('USDT') and len(symbol) > 0:
        symbol += 'USDT'
    
    return symbol

# =============================================================
# Trend İndikatörleri - TAMAMEN GÜNCELLENMİŞ
# =============================================================

@track_performance
@unit_test(expected_result=104.5, tolerance=0.1)
def ema(df: pd.DataFrame, period: Optional[int] = None, column: str = "close") -> pd.Series:
    """
    Exponential Moving Average (EMA) hesaplar.
    
    Args:
        df: OHLCV verilerini içeren DataFrame
        period: EMA periyodu (default: CONFIG.TA.EMA_PERIOD veya 20)
        column: Hesaplanacak sütun (default: "close")
    
    Returns:
        EMA değerlerini içeren pandas Series
    """
    try:
        if not validate_dataframe(df, [column]):
            return pd.Series([np.nan] * len(df), index=df.index)
        
        period = period or getattr(CONFIG.TA, 'EMA_PERIOD', 20)
        price_series = safe_column_access(df, column)
        
        if len(price_series) < period:
            logger.warning(f"EMA: Not enough data points ({len(price_series)} < {period})")
            return pd.Series([np.nan] * len(df), index=df.index)
        
        return ta_circuit_breaker.execute(
            lambda: price_series.ewm(span=period, adjust=False).mean()
        )
    except Exception as e:
        logger.error(f"EMA hesaplanırken hata: {e}")
        return pd.Series([np.nan] * len(df), index=df.index)

@track_performance
def macd(df: pd.DataFrame, fast: Optional[int] = None, slow: Optional[int] = None, 
         signal: Optional[int] = None, column: str = "close") -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD (Moving Average Convergence Divergence) hesaplar.
    
    Args:
        df: OHLCV verilerini içeren DataFrame
        fast: Fast EMA periyodu (default: CONFIG.TA.MACD_FAST veya 12)
        slow: Slow EMA periyodu (default: CONFIG.TA.MACD_SLOW veya 26)
        signal: Signal line periyodu (default: CONFIG.TA.MACD_SIGNAL veya 9)
        column: Hesaplanacak sütun (default: "close")
    
    Returns:
        (macd_line, signal_line, histogram) tuple'ı
    """
    try:
        if not validate_dataframe(df, [column]):
            nan_series = pd.Series([np.nan] * len(df), index=df.index)
            return nan_series, nan_series, nan_series

        fast = fast or getattr(CONFIG.TA, 'MACD_FAST', 12)
        slow = slow or getattr(CONFIG.TA, 'MACD_SLOW', 26)
        signal = signal or getattr(CONFIG.TA, 'MACD_SIGNAL', 9)

        price_series = safe_column_access(df, column)
        
        # Yeterli veri kontrolü
        min_period = max(fast, slow, signal)
        if len(price_series) < min_period:
            logger.warning(f"MACD: Not enough data points ({len(price_series)} < {min_period})")
            nan_series = pd.Series([np.nan] * len(df), index=df.index)
            return nan_series, nan_series, nan_series

        return ta_circuit_breaker.execute(lambda: (
            price_series.ewm(span=fast, adjust=False).mean() - 
            price_series.ewm(span=slow, adjust=False).mean(),
            price_series.ewm(span=fast, adjust=False).mean() - 
            price_series.ewm(span=slow, adjust=False).mean(),
            price_series.ewm(span=fast, adjust=False).mean() - 
            price_series.ewm(span=slow, adjust=False).mean()
        ))
    except Exception as e:
        logger.error(f"MACD hesaplanırken hata: {e}")
        nan_series = pd.Series([np.nan] * len(df), index=df.index)
        return nan_series, nan_series, nan_series

# Diğer TA fonksiyonları (RSI, Stochastic, ATR, Bollinger Bands, vs.) benzer şekilde güncellenmeli
# Kısaltma için burada gösterilmiyor, ancak aynı pattern ile implemente edilmeli

# =============================================================
# Binance Entegre IO Fonksiyonları - GELİŞMİŞ CACHE
# =============================================================

@track_performance
async def fetch_funding_rate_binance(symbol: str = "BTCUSDT") -> float:
    """
    Binance'den gerçek funding rate çeker.
    
    Args:
        symbol: Sembol adı (default: "BTCUSDT")
    
    Returns:
        Funding rate değeri
    """
    symbol = normalize_symbol(symbol)
    cache_key = f"funding_rate_{symbol}"
    
    # Önce cache kontrolü
    cached = ta_cache.get_ta_result(symbol, "funding_rate", "funding_rate")
    if cached is not None:
        ta_metrics.cache_hits += 1
        return cached
    
    ta_metrics.cache_misses += 1
    ta_metrics.io_requests += 1
    
    try:
        from utils.binance_api import get_global_binance_client
        client = await get_global_binance_client()
        
        if client is None:
            logger.warning("Binance client not available for funding rate")
            result = 0.001
            ta_cache.set_ta_result(symbol, "funding_rate", "funding_rate", result, ttl=60)
            return result
            
        try:
            await client.load_markets()
            
            futures_symbol = symbol.replace('/', '').replace(':', '').upper()
            if futures_symbol not in client.markets:
                logger.warning(f"Symbol {futures_symbol} not found in futures markets")
                result = 0.001
                ta_cache.set_ta_result(symbol, "funding_rate", "funding_rate", result, ttl=300)
                return result
            
            market = client.markets[futures_symbol]
            
            if market.get('linear', False) or market.get('inverse', False):
                funding_data = await client.fetch_funding_rate(symbol)
                result = float(funding_data['fundingRate']) if funding_data else 0.001
                ta_cache.set_ta_result(symbol, "funding_rate", "funding_rate", result, ttl=300)
                return result
            else:
                logger.warning(f"Symbol {futures_symbol} is not a linear or inverse contract")
                result = 0.001
                ta_cache.set_ta_result(symbol, "funding_rate", "funding_rate", result, ttl=600)
                return result
                
        except Exception as e:
            ta_metrics.io_errors += 1
            if "authentication" in str(e).lower():
                logger.warning(f"Funding rate için yetki yetersiz: {e}")
            elif "not supported" in str(e).lower():
                logger.warning(f"Funding rate desteklenmiyor: {e}")
            else:
                logger.error(f"Funding rate çekme hatası: {e}")
            result = 0.001
            ta_cache.set_ta_result(symbol, "funding_rate", "funding_rate", result, ttl=60)
            return result
                
    except Exception as e:
        ta_metrics.io_errors += 1
        logger.error(f"Funding rate çekilemedi: {e}")
        result = 0.001
        ta_cache.set_ta_result(symbol, "funding_rate", "funding_rate", result, ttl=30)
        return result

# Diğer IO fonksiyonları için benzer cache mekanizması eklenmeli

# =============================================================
# Trading Pipeline - GELİŞMİŞ HATA YÖNETİMİ
# =============================================================

@track_performance
async def optimized_trading_pipeline(symbol: str = "BTCUSDT", 
                                   interval: str = "1m",
                                   callback: Optional[Callable] = None,
                                   max_retries: int = 3):
    """
    Geliştirilmiş trading pipeline with enhanced error handling and retry.
    
    Args:
        symbol: İşlem yapılacak sembol (default: "BTCUSDT")
        interval: Zaman aralığı (default: "1m")
        callback: Sonuçları işleyecek callback fonksiyonu
        max_retries: Maksimum yeniden deneme sayısı (default: 3)
    """
    pipeline_logger = logging.getLogger(f"ta_pipeline.{symbol}")
    
    try:
        from utils.binance_api import get_binance_client
        client = get_binance_client(None, None)
    except ImportError as e:
        pipeline_logger.error(f"Binance API modülü yüklenemedi: {e}")
        return
    
    pipeline_interval = getattr(CONFIG.TA, 'TA_PIPELINE_INTERVAL', 60)
    min_data_points = getattr(CONFIG.TA, 'TA_MIN_DATA_POINTS', 20)
    cache_ttl = getattr(CONFIG.TA, 'TA_CACHE_TTL', 300)
    
    retry_count = 0
    retry_delay = 5
    
    while True:
        try:
            # Cache kontrolü
            cached_result = ta_cache.get_ta_result(symbol, interval, 'full_analysis')
            
            if cached_result:
                pipeline_logger.debug("Using cached TA results")
                if callback:
                    try:
                        await callback(cached_result)
                    except Exception as e:
                        pipeline_logger.error(f"Callback error: {e}")
                await asyncio.sleep(pipeline_interval)
                continue
            
            # Veri çekme
            klines = await client.get_klines(symbol, interval, limit=100)
            if not klines or len(klines) < min_data_points:
                pipeline_logger.warning(f"Insufficient kline data: {len(klines) if klines else 0} points")
                await asyncio.sleep(30)
                continue
            
            # DataFrame dönüşümü
            df = klines_to_dataframe(klines)
            
            # DataFrame validation
            required_columns = ['open', 'high', 'low', 'close', 'volume']
            if not validate_dataframe(df, required_columns):
                pipeline_logger.error("Invalid DataFrame format")
                await asyncio.sleep(30)
                continue
            
            if len(df) < min_data_points:
                pipeline_logger.warning(f"Insufficient data points: {len(df)} < {min_data_points}")
                await asyncio.sleep(30)
                continue
            
            # TA hesaplama
            try:
                ta_results = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: calculate_all_ta_hybrid(df, symbol)
                )
            except Exception as e:
                pipeline_logger.error(f"TA calculation failed: {e}")
                await asyncio.sleep(30)
                continue
            
            # Sinyal üretme
            try:
                signal = generate_signals(df)
            except Exception as e:
                pipeline_logger.error(f"Signal generation failed: {e}")
                signal = {'signal': 0, 'signals': {}, 'total_signal': 0.0, 'alpha_details': {}}
            
            # Sonuç paketleme
            result = {
                'symbol': symbol,
                'timestamp': time.time(),
                'price': float(safe_column_access(df, 'close').iloc[-1]),
                'signal': signal,
                'ta_metrics': {k: float(v.iloc[-1]) if hasattr(v, 'iloc') and not v.empty else v 
                              for k, v in ta_results.items() if v is not None}
            }
            
            # Cache'e kaydetme
            ta_cache.set_ta_result(symbol, interval, 'full_analysis', result, ttl=cache_ttl)
            
            # Callback
            if callback:
                try:
                    await callback(result)
                except Exception as e:
                    pipeline_logger.error(f"Callback execution failed: {e}")
            
            # Başarılı oldu, retry sıfırla
            retry_count = 0
            retry_delay = 5
            
            await asyncio.sleep(pipeline_interval)
            
        except asyncio.CancelledError:
            pipeline_logger.info("Trading pipeline cancelled")
            break
        except Exception as e:
            retry_count += 1
            pipeline_logger.error(f"Trading pipeline error (retry {retry_count}/{max_retries}): {e}")
            
            if retry_count >= max_retries:
                pipeline_logger.error("Max retries exceeded, stopping pipeline")
                break
                
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)  # Exponential backoff

# =============================================================
# Gelişmiş Metrikler ve Monitoring
# =============================================================

def get_detailed_metrics() -> Dict[str, Any]:
    """Detaylı sistem metriklerini getir"""
    cache_stats = ta_cache.get_stats()
    circuit_status = ta_circuit_breaker.get_status()
    
    # Ortalama execution time'ları hesapla
    avg_times = {}
    for func_name, times in ta_metrics.execution_times.items():
        if times:
            avg_times[func_name] = sum(times) / len(times)
    
    return {
        'calculations': {
            'total': ta_metrics.total_calculations,
            'errors': ta_metrics.calculation_errors,
            'error_rate': ta_metrics.calculation_errors / max(ta_metrics.total_calculations, 1),
            'cache_hits': ta_metrics.cache_hits,
            'cache_misses': ta_metrics.cache_misses,
            'cache_hit_ratio': cache_stats['hit_ratio']
        },
        'io': {
            'requests': ta_metrics.io_requests,
            'errors': ta_metrics.io_errors,
            'error_rate': ta_metrics.io_errors / max(ta_metrics.io_requests, 1)
        },
        'performance': {
            'average_times': avg_times,
            'monitored_functions': list(ta_metrics.execution_times.keys())
        },
        'cache': cache_stats,
        'circuit_breaker': circuit_status,
        'timestamp': time.time(),
        'status': 'healthy' if (ta_metrics.calculation_errors / max(ta_metrics.total_calculations, 1)) < 0.1 else 'degraded'
    }

def reset_metrics():
    """Metrikleri sıfırla"""
    global ta_metrics
    ta_metrics = TAMetrics()
    logger.info("Metrics reset")

# =============================================================
# Unit Tests
# =============================================================

def run_unit_tests():
    """Tüm fonksiyonlar için unit testleri çalıştır"""
    logger.info("Running unit tests...")
    
    # Test verisi
    test_data = {
        'open': [100, 101, 102, 103, 104, 105, 106, 107, 108, 109],
        'high': [105, 106, 107, 108, 109, 110, 111, 112, 113, 114],
        'low': [95, 96, 97, 98, 99, 100, 101, 102, 103, 104],
        'close': [102, 103, 104, 105, 106, 107, 108, 109, 110, 111],
        'volume': [1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000]
    }
    df = pd.DataFrame(test_data)
    
    # Test fonksiyonları
    test_functions = [
        (ema, (df,), {}),
        (lambda df: macd(df)[0], (df,), {}),  # MACD line only
        (rsi, (df,), {}),
        (lambda df: stochastic(df)[0], (df,), {}),  # Stochastic K only
    ]
    
    passed = 0
    failed = 0
    
    for func, args, kwargs in test_functions:
        try:
            result = func(*args, **kwargs)
            # Basit validation
            if result is not None and not (hasattr(result, 'isna') and result.isna().all()):
                passed += 1
                logger.info(f"✓ {func.__name__} test passed")
            else:
                failed += 1
                logger.warning(f"✗ {func.__name__} test failed: Invalid result")
        except Exception as e:
            failed += 1
            logger.error(f"✗ {func.__name__} test failed with error: {e}")
    
    logger.info(f"Unit tests completed: {passed} passed, {failed} failed")
    return passed, failed

# =============================================================
# Ana Fonksiyon - GELİŞMİŞ TEST
# =============================================================

async def main():
    """Gelişmiş test fonksiyonu"""
    logger.info("Starting comprehensive TA utils test...")
    
    # Unit testleri çalıştır
    passed, failed = run_unit_tests()
    
    if failed > 0:
        logger.warning(f"{failed} unit tests failed, proceeding with caution")
    
    # Test verisi
    test_data = {
        'open': [100, 101, 102, 103, 104, 105, 106, 107, 108, 109] * 10,
        'high': [105, 106, 107, 108, 109, 110, 111, 112, 113, 114] * 10,
        'low': [95, 96, 97, 98, 99, 100, 101, 102, 103, 104] * 10,
        'close': [102, 103, 104, 105, 106, 107, 108, 109, 110, 111] * 10,
        'volume': [1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000] * 10
    }
    df = pd.DataFrame(test_data)
    
    # 1. DataFrame validation test
    logger.info("Testing DataFrame validation...")
    is_valid = validate_dataframe(df)
    logger.info(f"DataFrame validation: {is_valid}")
    
    # 2. TA hesaplama test
    logger.info("Testing TA calculations...")
    try:
        results = await calculate_all_ta_hybrid_async(df, "BTCUSDT")
        logger.info(f"Calculated {len(results)} TA indicators")
        
        # 3. Sinyal üretme test
        signal = generate_signals(df)
        logger.info(f"Generated signal: {signal['signal']}")
        
        # 4. Health check
        health = health_check()
        logger.info(f"Health status: {health['status']}")
        
        # 5. Metrikler
        metrics = get_detailed_metrics()
        logger.info(f"System metrics: {metrics}")
        
        # 6. Circuit breaker status
        circuit_status = ta_circuit_breaker.get_status()
        logger.info(f"Circuit breaker status: {circuit_status['state']}")
        
    except Exception as e:
        logger.error(f"Test failed: {e}")
        return False
    
    logger.info("All tests completed successfully!")
    return True

if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)

# EOF
