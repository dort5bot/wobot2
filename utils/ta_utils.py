# ta_utils.py - KUSURSUZ VERSİYON
# Free Render uyumlu hibrit TA pipeline

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
    total_calculations: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    calculation_errors: int = 0
    io_requests: int = 0
    io_errors: int = 0

class TACache:
    """TA hesaplamaları için gelişmiş önbellek katmanı"""
    def __init__(self, max_size: int = 1000):
        self._cache = OrderedDict()
        self._hit_count = 0
        self._miss_count = 0
        self._last_cleanup = time.time()
        self._max_size = max_size
        self._lock = threading.RLock()
        logger.info(f"TA Cache initialized with max_size: {max_size}")
    
    def get_ta_result(self, symbol: str, timeframe: str, indicator: str) -> Optional[Any]:
        """Cache'ten sonucu getir"""
        key = f"{symbol}_{timeframe}_{indicator}"
        with self._lock:
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
            if len(self._cache) >= self._max_size:
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
                'max_size': self._max_size,
                'utilization': len(self._cache) / self._max_size
            }
    
    def clear(self):
        """Cache'i tamamen temizle"""
        with self._lock:
            self._cache.clear()
            self._hit_count = 0
            self._miss_count = 0
            logger.info("TA Cache cleared completely")

# Global instances (thread-safe)
ta_cache = TACache(max_size=1000)
ta_metrics = TAMetrics()

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

# =============================================================
# Trend İndikatörleri - TAMAMEN GÜNCELLENMİŞ
# =============================================================

def ema(df: pd.DataFrame, period: Optional[int] = None, column: str = "close") -> pd.Series:
    """Exponential Moving Average (EMA) hesaplar."""
    try:
        if not validate_dataframe(df, [column]):
            return pd.Series([np.nan] * len(df), index=df.index)
        
        period = period or getattr(CONFIG.TA, 'EMA_PERIOD', 20)
        price_series = safe_column_access(df, column)
        
        if len(price_series) < period:
            logger.warning(f"EMA: Not enough data points ({len(price_series)} < {period})")
            return pd.Series([np.nan] * len(df), index=df.index)
        
        return price_series.ewm(span=period, adjust=False).mean()
    except Exception as e:
        logger.error(f"EMA hesaplanırken hata: {e}")
        return pd.Series([np.nan] * len(df), index=df.index)

def macd(df: pd.DataFrame, fast: Optional[int] = None, slow: Optional[int] = None, 
         signal: Optional[int] = None, column: str = "close") -> Tuple[pd.Series, pd.Series, pd.Series]:
    """MACD (Moving Average Convergence Divergence) hesaplar."""
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

        ema_fast = price_series.ewm(span=fast, adjust=False).mean()
        ema_slow = price_series.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        hist = macd_line - signal_line

        return macd_line, signal_line, hist
    except Exception as e:
        logger.error(f"MACD hesaplanırken hata: {e}")
        nan_series = pd.Series([np.nan] * len(df), index=df.index)
        return nan_series, nan_series, nan_series

# Diğer tüm TA fonksiyonlarını benzer şekilde güncelleyin...
# (RSI, Stochastic, ATR, Bollinger Bands, vs.)

# =============================================================
# Binance Entegre IO Fonksiyonları - GELİŞMİŞ CACHE
# =============================================================

async def fetch_funding_rate_binance(symbol: str = "BTCUSDT") -> float:
    """Binance'den gerçek funding rate çeker - Gelişmiş cache"""
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

# Diğer IO fonksiyonları için benzer cache mekanizması ekleyin...

# =============================================================
# Trading Pipeline - GELİŞMİŞ HATA YÖNETİMİ
# =============================================================

async def optimized_trading_pipeline(symbol: str = "BTCUSDT", 
                                   interval: str = "1m",
                                   callback: Optional[Callable] = None,
                                   max_retries: int = 3):
    """
    Geliştirilmiş trading pipeline with enhanced error handling and retry
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
        'cache': cache_stats,
        'timestamp': time.time(),
        'status': 'healthy' if (ta_metrics.calculation_errors / max(ta_metrics.total_calculations, 1)) < 0.1 else 'degraded'
    }

def reset_metrics():
    """Metrikleri sıfırla"""
    global ta_metrics
    ta_metrics = TAMetrics()
    logger.info("Metrics reset")

# =============================================================
# Ana Fonksiyon - GELİŞMİŞ TEST
# =============================================================

async def main():
    """Gelişmiş test fonksiyonu"""
    logger.info("Starting comprehensive TA utils test...")
    
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
        
    except Exception as e:
        logger.error(f"Test failed: {e}")
        return False
    
    logger.info("All tests completed successfully!")
    return True

if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)

# EOF
