# ta_utils.py 901-2307>>902--102
# Free Render uyumlu hibrit TA pipeline
# - CPU-bound: ThreadPoolExecutor
# - IO-bound: asyncio
# - MAX_WORKERS: CONFIG.SYSTEM.MAX_WORKERS varsa kullanılır, yoksa 2
# - Binance API entegreli gerçek zamanlı veri desteği
#TA_utils Fonksiyonlarını CCXT'e Uyarlı
'''
✅ Tüm önerilen entegrasyonlar
✅ Binance API bağlantılı IO fonksiyonları
✅ Gelişmiş cache mekanizması
✅ Performans metrikleri ve monitoring
✅ Optimize edilmiş paralel işleme
✅ Safety improvement: adx artık pure function
✅ Type hint geliştirmeleri
✅ Health check fonksiyonları
✅ Cache limit mekanizması eklendi
✅ Config fallback mekanizması eklendi
✅ Logger entegrasyonu tamamlandı
✅ Thread safety iyileştirmeleri
✅ Hata yakalama ve fallback mekanizmaları geliştirildi
✅ Alpha signals mekanizması iyileştirildi
'''

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

# Config fallback mekanizması
try:
    from utils.config import CONFIG
except ImportError:
    # Config yüklenemezse güvenli default değerler
    logging.warning("utils.config yüklenemedi, güvenli default değerler kullanılıyor")
    
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

# ------------------------------------------------------------
# Logger Kurulumu
# ------------------------------------------------------------
logger = logging.getLogger("ta_utils")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# ------------------------------------------------------------
# İç yardımcılar ve Cache Mekanizması
# ------------------------------------------------------------

def _get_max_workers(default: int = 2) -> int:
    # CONFIG.SYSTEM.MAX_WORKERS tanımlıysa onu kullan, değilse default
    try:
        system = getattr(CONFIG, "SYSTEM", None)
        if system is not None and hasattr(system, "MAX_WORKERS"):
            try:
                mw = int(getattr(system, "MAX_WORKERS"))
                return mw if mw > 0 else default
            except Exception:
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

class TACache:
    """TA hesaplamaları için önbellek katmanı. Thread-safe ve cache limitli."""
    def __init__(self, max_size: int = 1000):
        self._cache = OrderedDict()
        self._hit_count = 0
        self._miss_count = 0
        self._last_cleanup = time.time()
        self._max_size = max_size
        self._lock = threading.RLock()
    
    def get_ta_result(self, symbol: str, timeframe: str, indicator: str) -> Optional[Any]:
        key = f"{symbol}_{timeframe}_{indicator}"
        with self._lock:
            if key in self._cache:
                cache_data = self._cache[key]
                # LRU özelliği için en son kullanılanı en sona taşı
                self._cache.move_to_end(key)
                if time.time() < cache_data['expiry']:
                    self._hit_count += 1
                    return cache_data['value']
                else:
                    # Süresi dolmuş entry'yi sil
                    del self._cache[key]
            self._miss_count += 1
            return None
    
    def set_ta_result(self, symbol: str, timeframe: str, indicator: str, value: Any, ttl: int = 300):
        key = f"{symbol}_{timeframe}_{indicator}"
        with self._lock:
            # Cache limit kontrolü
            if len(self._cache) >= self._max_size:
                # LRU prensibiyle en eski entry'yi sil
                self._cache.popitem(last=False)
            
            self._cache[key] = {
                'value': value,
                'expiry': time.time() + ttl
            }
            # LRU özelliği için en son ekleneni en sona taşı
            self._cache.move_to_end(key)
            self._cleanup_expired()
    
    def _cleanup_expired(self):
        current_time = time.time()
        if current_time - self._last_cleanup > 300:  # 5 dakikada bir temizle
            expired_keys = [k for k, v in self._cache.items() if v['expiry'] < current_time]
            for key in expired_keys:
                del self._cache[key]
            self._last_cleanup = current_time
    
    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self._hit_count + self._miss_count
            return {
                'size': len(self._cache),
                'hits': self._hit_count,
                'misses': self._miss_count,
                'hit_ratio': self._hit_count / max(total, 1),
                'max_size': self._max_size
            }
    
    def clear(self):
        """Cache'i tamamen temizler"""
        with self._lock:
            self._cache.clear()
            self._hit_count = 0
            self._miss_count = 0

# Global instances (thread-safe)
ta_cache = TACache(max_size=1000)
ta_metrics = TAMetrics()

# =============================================================
# Trend İndikatörleri
# =============================================================

def ema(df: pd.DataFrame, period: Optional[int] = None, column: str = "close") -> pd.Series:
    """Exponential Moving Average (EMA) hesaplar."""
    try:
        period = period or getattr(CONFIG.TA, 'EMA_PERIOD', 20)
        return df[column].ewm(span=period, adjust=False).mean()
    except Exception as e:
        logger.error(f"EMA hesaplanırken hata: {e}")
        return pd.Series([np.nan] * len(df), index=df.index)

def macd(df: pd.DataFrame, fast: Optional[int] = None, slow: Optional[int] = None, 
         signal: Optional[int] = None, column: str = "close") -> Tuple[pd.Series, pd.Series, pd.Series]:
    """MACD (Moving Average Convergence Divergence) hesaplar."""
    try:
        fast = fast or getattr(CONFIG.TA, 'MACD_FAST', 12)
        slow = slow or getattr(CONFIG.TA, 'MACD_SLOW', 26)
        signal = signal or getattr(CONFIG.TA, 'MACD_SIGNAL', 9)

        ema_fast = df[column].ewm(span=fast, adjust=False).mean()
        ema_slow = df[column].ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        hist = macd_line - signal_line

        return macd_line, signal_line, hist
    except Exception as e:
        logger.error(f"MACD hesaplanırken hata: {e}")
        nan_series = pd.Series([np.nan] * len(df), index=df.index)
        return nan_series, nan_series, nan_series

def adx(df: pd.DataFrame, period: Optional[int] = None) -> pd.Series:
    """Average Directional Index (ADX) hesaplar."""
    try:
        period = period or getattr(CONFIG.TA, 'ADX_PERIOD', 14)

        # Pure function implementation - no mutation
        high, low, close = df['high'], df['low'], df['close']
        
        tr = np.maximum(high - low,
                       np.maximum(abs(high - close.shift(1)),
                                 abs(low - close.shift(1))))
        plus_dm = np.where((high - high.shift(1)) > (low.shift(1) - low),
                          np.maximum(high - high.shift(1), 0), 0)
        minus_dm = np.where((low.shift(1) - low) > (high - high.shift(1)),
                           np.maximum(low.shift(1) - low, 0), 0)

        tr_smooth = pd.Series(tr).rolling(window=period).sum()
        plus_di = 100 * (pd.Series(plus_dm).rolling(window=period).sum() / tr_smooth)
        minus_di = 100 * (pd.Series(minus_dm).rolling(window=period).sum() / tr_smooth)
        dx = (100 * abs(plus_di - minus_di) / (plus_di + minus_di))
        adx_val = dx.rolling(window=period).mean()

        return adx_val
    except Exception as e:
        logger.error(f"ADX hesaplanırken hata: {e}")
        return pd.Series([np.nan] * len(df), index=df.index)

def vwap(df: pd.DataFrame) -> pd.Series:
    """Volume Weighted Average Price (VWAP) hesaplar."""
    try:
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        return (typical_price * df['volume']).cumsum() / df['volume'].cumsum()
    except Exception as e:
        logger.error(f"VWAP hesaplanırken hata: {e}")
        return pd.Series([np.nan] * len(df), index=df.index)

def cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Commodity Channel Index (CCI) hesaplar."""
    try:
        tp = (df['high'] + df['low'] + df['close']) / 3
        sma = tp.rolling(period).mean()
        mad = tp.rolling(period).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
        return (tp - sma) / (0.015 * mad)
    except Exception as e:
        logger.error(f"CCI hesaplanırken hata: {e}")
        return pd.Series([np.nan] * len(df), index=df.index)

def momentum(df: pd.DataFrame, period: int = 10) -> pd.Series:
    """Momentum Oscillator hesaplar."""
    try:
        return df['close'] / df['close'].shift(period) * 100
    except Exception as e:
        logger.error(f"Momentum hesaplanırken hata: {e}")
        return pd.Series([np.nan] * len(df), index=df.index)

# =============================================================
# Momentum İndikatörleri
# =============================================================

def rsi(df: pd.DataFrame, period: Optional[int] = None, column: str = "close") -> pd.Series:
    """Relative Strength Index (RSI) hesaplar."""
    try:
        period = period or getattr(CONFIG.TA, 'RSI_PERIOD', 14)

        delta = df[column].diff()
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)

        avg_gain = pd.Series(gain, index=df.index).rolling(window=period).mean()
        avg_loss = pd.Series(loss, index=df.index).rolling(window=period).mean()
        rs = avg_gain / (avg_loss + 1e-12)

        return 100 - (100 / (1 + rs))
    except Exception as e:
        logger.error(f"RSI hesaplanırken hata: {e}")
        return pd.Series([np.nan] * len(df), index=df.index)

def stochastic(df: pd.DataFrame, k_period: Optional[int] = None, d_period: Optional[int] = None) -> Tuple[pd.Series, pd.Series]:
    """Stochastic Oscillator hesaplar."""
    try:
        k_period = k_period or getattr(CONFIG.TA, 'STOCH_K', 14)
        d_period = d_period or getattr(CONFIG.TA, 'STOCH_D', 3)

        low_min = df["low"].rolling(window=k_period).min()
        high_max = df["high"].rolling(window=k_period).max()
        k = 100 * (df["close"] - low_min) / (high_max - low_min + 1e-12)
        d = k.rolling(window=d_period).mean()
        return k, d
    except Exception as e:
        logger.error(f"Stochastic hesaplanırken hata: {e}")
        nan_series = pd.Series([np.nan] * len(df), index=df.index)
        return nan_series, nan_series

# =============================================================
# Volatilite & Risk
# =============================================================

def atr(df: pd.DataFrame, period: Optional[int] = None) -> pd.Series:
    """Average True Range (ATR) hesaplar."""
    try:
        period = period or getattr(CONFIG.TA, 'ATR_PERIOD', 14)

        high_low = df["high"] - df["low"]
        high_close = abs(df["high"] - df["close"].shift())
        low_close = abs(df["low"] - df["close"].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()
    except Exception as e:
        logger.error(f"ATR hesaplanırken hata: {e}")
        return pd.Series([np.nan] * len(df), index=df.index)

def bollinger_bands(df: pd.DataFrame, period: Optional[int] = None, column: str = "close") -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands hesaplar."""
    try:
        period = period or getattr(CONFIG.TA, 'BB_PERIOD', 20)
        stddev = getattr(CONFIG.TA, 'BB_STDDEV', 2.0)

        sma = df[column].rolling(window=period).mean()
        std = df[column].rolling(window=period).std()
        upper = sma + (stddev * std)
        lower = sma - (stddev * std)
        return upper, sma, lower
    except Exception as e:
        logger.error(f"Bollinger Bands hesaplanırken hata: {e}")
        nan_series = pd.Series([np.nan] * len(df), index=df.index)
        return nan_series, nan_series, nan_series

def sharpe_ratio(df: pd.DataFrame, risk_free_rate: Optional[float] = None, period: Optional[int] = None, column: str = "close") -> float:
    """Sharpe Ratio hesaplar."""
    try:
        period = period or getattr(CONFIG.TA, 'SHARPE_PERIOD', 252)
        risk_free_rate = risk_free_rate or getattr(CONFIG.TA, 'SHARPE_RISK_FREE_RATE', 0.02)

        returns = df[column].pct_change()
        excess = returns - risk_free_rate / period
        return (excess.mean() / (excess.std() + 1e-12)) * np.sqrt(period)
    except Exception as e:
        logger.error(f"Sharpe Ratio hesaplanırken hata: {e}")
        return 0.0

def max_drawdown(df: pd.DataFrame, column: str = "close") -> float:
    """Max Drawdown hesaplar."""
    try:
        roll_max = df[column].cummax()
        drawdown = (df[column] - roll_max) / (roll_max + 1e-12)
        return drawdown.min()
    except Exception as e:
        logger.error(f"Max Drawdown hesaplanırken hata: {e}")
        return 0.0

def historical_volatility(df: pd.DataFrame, period: int = 30) -> pd.Series:
    """Historical Volatility (annualized, %) hesaplar."""
    try:
        log_returns = np.log(df['close'] / df['close'].shift(1))
        vol = log_returns.rolling(period).std() * np.sqrt(252) * 100
        return vol
    except Exception as e:
        logger.error(f"Historical Volatility hesaplanırken hata: {e}")
        return pd.Series([np.nan] * len(df), index=df.index)

def ulcer_index(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Ulcer Index hesaplar."""
    try:
        rolling_max = df['close'].rolling(period).max()
        drawdown = (df['close'] - rolling_max) / (rolling_max + 1e-12) * 100
        return np.sqrt((drawdown.pow(2)).rolling(period).mean())
    except Exception as e:
        logger.error(f"Ulcer Index hesaplanırken hata: {e}")
        return pd.Series([np.nan] * len(df), index=df.index)

# =============================================================
# Hacim & Likidite
# =============================================================

def obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume (OBV) hesaplar."""
    try:
        obv = (np.sign(df["close"].diff()) * df["volume"]).fillna(0).cumsum()
        return obv
    except Exception as e:
        logger.error(f"OBV hesaplanırken hata: {e}")
        return pd.Series([np.nan] * len(df), index=df.index)

def cmf(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Chaikin Money Flow (CMF) hesaplar."""
    try:
        mfm = ((df['close'] - df['low']) - (df['high'] - df['close'])) / (df['high'] - df['low'] + 1e-12)
        mfv = mfm * df['volume']
        return mfv.rolling(period).sum() / (df['volume'].rolling(period).sum() + 1e-12)
    except Exception as e:
        logger.error(f"CMF hesaplanırken hata: {e}")
        return pd.Series([np.nan] * len(df), index=df.index)

def order_book_imbalance(bids: list, asks: list) -> float:
    """Order Book Imbalance (OBI) hesaplar."""
    try:
        bid_vol = sum([b[1] for b in bids]) if bids else 0
        ask_vol = sum([a[1] for a in asks]) if asks else 0
        denom = (bid_vol + ask_vol) or 1e-12
        return (bid_vol - ask_vol) / denom
    except Exception as e:
        logger.error(f"Order Book Imbalance hesaplanırken hata: {e}")
        return 0.0

def open_interest_placeholder():
    """Open Interest için placeholder (borsa API ile entegre edilecek)."""
    return None

# =============================================================
# Market Structure
# =============================================================

def market_structure(df: pd.DataFrame) -> pd.DataFrame:
    """Market Structure High/Low (MSH/MSL) hesaplar."""
    try:
        highs = df['high']
        lows = df['low']
        structure = pd.DataFrame(index=df.index)
        structure['higher_high'] = highs > highs.shift(1)
        structure['lower_low'] = lows < lows.shift(1)
        return structure
    except Exception as e:
        logger.error(f"Market Structure hesaplanırken hata: {e}")
        return pd.DataFrame(index=df.index)

def breakout(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Breakout (Donchian Channel tarzı) sinyal döndürür."""
    try:
        rolling_high = df['close'].rolling(period).max()
        rolling_low = df['close'].rolling(period).min()

        cond_long = df['close'] > rolling_high.shift(1)
        cond_short = df['close'] < rolling_low.shift(1)

        signal = pd.Series(index=df.index, dtype=float)
        signal[cond_long] = 1.0   # Long breakout
        signal[cond_short] = -1.0 # Short breakout
        signal.fillna(0, inplace=True)

        return signal
    except Exception as e:
        logger.error(f"Breakout hesaplanırken hata: {e}")
        return pd.Series([0.0] * len(df), index=df.index)

# =============================================================
# Binance Entegre IO Fonksiyonları
# =============================================================

# ta_utils.py
# Fonksiyonun başında cache kontrolü
cache_key = f"funding_rate_{symbol}"
cached = ta_cache.get_ta_result(symbol, "funding_rate", "funding_rate")
if cached is not None:
    return cached
    
async def fetch_funding_rate_binance(symbol: str = "BTCUSDT") -> float:
    """Binance'den gerçek funding rate çeker - CCXT version"""
    try:
        from utils.binance_api import get_global_binance_client
        client = await get_global_binance_client()
        
        if client is None:
            logger.warning("Binance client not available for funding rate")
            return 0.001
            
        try:
            # Önce futures marketlerini yükle
            await client.load_markets()
            
            # Sembolün futures markette olup olmadığını kontrol et
            futures_symbol = symbol.replace('/', '').replace(':', '').upper()
            if futures_symbol not in client.markets:
                logger.warning(f"Symbol {futures_symbol} not found in futures markets")
                return 0.001
            
            market = client.markets[futures_symbol]
            
            # Yalnızca linear ve inverse contract'lar için funding rate al
            if market.get('linear', False) or market.get('inverse', False):
                funding_data = await client.fetch_funding_rate(symbol)
                return float(funding_data['fundingRate']) if funding_data else 0.001
            else:
                logger.warning(f"Symbol {futures_symbol} is not a linear or inverse contract, skipping funding rate")
                return 0.001
                
        except Exception as e:
            if "authentication" in str(e).lower() or "api" in str(e).lower():
                logger.warning(f"Funding rate için yetki yetersiz: {e}")
            elif "not supported" in str(e).lower() or "fetchFundingRate" in str(e).lower():
                logger.warning(f"Funding rate desteklenmiyor: {e}")
            else:
                logger.error(f"Funding rate çekme hatası: {e}")
            return 0.001
                
    except Exception as e:
        logger.error(f"Funding rate çekilemedi: {e}")
        return 0.001

# Fonksiyon sonunda cache'e kaydetme
ta_cache.set_ta_result(symbol, "funding_rate", "funding_rate", result, ttl=300)

async def get_live_order_book_imbalance(symbol: str = "BTCUSDT") -> float:
    """Gerçek zamanlı order book imbalance hesaplar - CCXT version"""
    try:
        from utils.binance_api import get_global_binance_client
        client = await get_global_binance_client()
        
        if client is None:
            logger.warning("Binance client not available for order book")  # LOG -> logger
            return 0.0
            
        # CCXT ile order book
        ob = await client.fetch_order_book(symbol, limit=100)
        bids = ob['bids']  # [[price, amount], ...]
        asks = ob['asks']  # [[price, amount], ...]
        
        bid_vol = sum(b[1] for b in bids) if bids else 0
        ask_vol = sum(a[1] for a in asks) if asks else 0
        denom = (bid_vol + ask_vol) or 1e-12
        
        return (bid_vol - ask_vol) / denom
        
    except Exception as e:
        logger.error(f"Order book imbalance hesaplanamadı: {e}")  # LOG -> logger
        return 0.0        # Fallback değer

async def fetch_social_sentiment_binance(symbol: str = "BTC") -> Dict[str, float]:
    """Social sentiment için placeholder."""
    try:
        await asyncio.sleep(0.01)
        return {"sentiment": 0.5}  # Fallback değer
    except Exception as e:
        logger.error(f"Social sentiment çekilemedi: {e}")
        return {"sentiment": 0.5}  # Fallback değer


# =============================================================
# Registry
# =============================================================

# CPU-bound olarak toplu hesaplanacak fonksiyonlar
CPU_FUNCTIONS = {
    "ema": ema,
    "macd": macd,
    "adx": adx,
    "vwap": vwap,
    "cci": cci,
    "momentum": momentum,
    "rsi": rsi,
    "stochastic": stochastic,
    "atr": atr,
    "bollinger_bands": bollinger_bands,
    "sharpe_ratio": sharpe_ratio,
    "max_drawdown": max_drawdown,
    "historical_volatility": historical_volatility,
    "ulcer_index": ulcer_index,
    "obv": obv,
    "cmf": cmf,
    "market_structure": market_structure,
    "breakout": breakout,
}

# I/O-bound asenkron fonksiyonlar
IO_FUNCTIONS = {
    "funding_rate": fetch_funding_rate_binance,
    "social_sentiment": fetch_social_sentiment_binance,
    "order_book_imbalance": get_live_order_book_imbalance,
}

# =============================================================
# Trading Pipeline Functions
# =============================================================
async def optimized_trading_pipeline(symbol: str = "BTCUSDT", 
                                   interval: str = "1m",
                                   callback: Optional[Callable] = None):
    """
    Geliştirilmiş trading pipeline with caching and error handling
    """
    logger = logging.getLogger("ta_pipeline")
    
    try:
        # ❌ Eski: from utils.binance_api import get_binance_api
        # ✅ Yeni:
        from utils.binance_api import get_binance_client
        client = get_binance_client(None, None)  # Global instance'ı kullan
    except ImportError:
        logger.error("Binance API modülü yüklenemedi")
        return
    
    pipeline_interval = getattr(CONFIG.TA, 'TA_PIPELINE_INTERVAL', 60)
    min_data_points = getattr(CONFIG.TA, 'TA_MIN_DATA_POINTS', 20)
    cache_ttl = getattr(CONFIG.TA, 'TA_CACHE_TTL', 300)
    
    while True:
        try:
            # 1. ÖNCE cache kontrolü
            cached_result = ta_cache.get_ta_result(symbol, interval, 'full_analysis')
            
            if cached_result:
                logger.debug("Using cached TA results for %s", symbol)
                if callback:
                    await callback(cached_result)
                await asyncio.sleep(pipeline_interval)
                continue
            
            # 2. Cache yoksa, veriyi çek
            klines = await client.get_klines(symbol, interval, limit=100)
            if not klines:
                logger.warning(f"{symbol} için kline verisi alınamadı")
                await asyncio.sleep(30)
                continue
            
            df = klines_to_dataframe(klines)
            
            # 3. ŞİMDİ data kontrolü yap (df artık tanımlı)
            if len(df) < min_data_points:
                logger.warning("Insufficient data for %s: %d points", symbol, len(df))
                await asyncio.sleep(30)
                continue
            
            # 4. TA hesapla (threaded)
            ta_results = await asyncio.get_event_loop().run_in_executor(
                None, lambda: calculate_all_ta_hybrid(df, symbol)
            )
            
            # 5. Sinyal üret
            signal = generate_signals(df)
            
            # 6. Sonuçları paketle
            result = {
                'symbol': symbol,
                'timestamp': time.time(),
                'price': float(df['close'].iloc[-1]),
                'signal': signal,
                'ta_metrics': {k: float(v.iloc[-1]) if hasattr(v, 'iloc') else v 
                              for k, v in ta_results.items() if v is not None}
            }
            
            # 7. Cache'e kaydet
            ta_cache.set_ta_result(symbol, interval, 'full_analysis', result, ttl=cache_ttl)
            
            # 8. Callback ile gönder
            if callback and signal['signal'] != 0:
                await callback(result)
            
            # 9. Interval kadar bekle
            await asyncio.sleep(pipeline_interval)
            
        except asyncio.CancelledError:
            logger.info(f"Trading pipeline for {symbol} cancelled")
            break
        except Exception as e:
            logger.error("Trading pipeline error for %s: %s", symbol, e)
            await asyncio.sleep(30)

# Genel amaçlı string-çağrı registry
TA_FUNCTIONS = {
    **CPU_FUNCTIONS,
    "order_book_imbalance": order_book_imbalance,
    "open_interest_placeholder": open_interest_placeholder,
}

# =============================================================
# Hibrit Pipeline - Geliştirilmiş
# =============================================================

def calculate_cpu_functions(df: pd.DataFrame, max_workers: Optional[int] = None) -> dict:
    """
    CPU-bound fonksiyonları paralelde hesaplar.
    """
    results: dict = {}
    max_workers = max_workers or _get_max_workers(default=2)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for name, func in CPU_FUNCTIONS.items():
            futures[executor.submit(func, df)] = name

        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
                ta_metrics.total_calculations += 1
            except Exception as e:
                results[name] = None
                ta_metrics.calculation_errors += 1
                logger.error(f"[CPU TA ERROR] {name} hesaplanamadı: {e}")
    return results

async def calculate_io_functions(symbol: str = "BTCUSDT") -> dict:
    """
    I/O-bound (asenkron) fonksiyonları birlikte yürütür.
    """
    results: dict = {}
    names = list(IO_FUNCTIONS.keys())
    tasks = []
    
    for name in names:
        if name == "order_book_imbalance":
            tasks.append(IO_FUNCTIONS[name](symbol))
        else:
            tasks.append(IO_FUNCTIONS[name](symbol))
    
    completed = await asyncio.gather(*tasks, return_exceptions=True)
    
    for name, res in zip(names, completed):
        if isinstance(res, Exception):
            results[name] = None
            ta_metrics.calculation_errors += 1
            logger.error(f"[I/O TA ERROR] {name} hesaplanamadı: {res}")
        else:
            results[name] = res
            ta_metrics.total_calculations += 1
    
    return results

def _run_asyncio(coro):
    """
    Jupyter / Bot / Web server ortamlarında güvenli asyncio çalıştırma.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            new_loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(new_loop)
                return new_loop.run_until_complete(coro)
            finally:
                new_loop.close()
                asyncio.set_event_loop(loop)
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        new_loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(new_loop)
            return new_loop.run_until_complete(coro)
        finally:
            new_loop.close()
            asyncio.set_event_loop(None)

async def calculate_all_ta_hybrid_async(df: pd.DataFrame, symbol: str = "BTCUSDT", 
                                      max_workers: Optional[int] = None) -> dict:
    """
    Tüm TA'leri hibrit olarak hesaplar (async version).
    """
    cpu_results = await asyncio.get_event_loop().run_in_executor(
        None, lambda: calculate_cpu_functions(df, max_workers)
    )
    io_results = await calculate_io_functions(symbol)
    return {**cpu_results, **io_results}

def calculate_all_ta_hybrid(df: pd.DataFrame, symbol: str = "BTCUSDT", 
                          max_workers: Optional[int] = None) -> dict:
    """
    Tüm TA'leri hibrit olarak hesaplar (sync wrapper).
    """
    return _run_asyncio(calculate_all_ta_hybrid_async(df, symbol, max_workers))

# =============================================================
# Advanced Signal Stack: alpha_ta
# =============================================================

def kalman_filter_series(prices: pd.Series, q: Optional[float] = None, r: Optional[float] = None) -> pd.Series:
    """
    Minimal 1D random-walk Kalman.
    """
    try:
        if q is None:
            q = getattr(CONFIG.TA, "KALMAN_Q", 1e-5)
        if r is None:
            r = getattr(CONFIG.TA, "KALMAN_R", 1e-2)

        x = 0.0
        p = 1.0
        out = []
        initialized = False

        # ❌ Eski: vals = prices.fillna(method="ffill").values
        # ✅ Yeni: 
        vals = prices.ffill().values  # fillna(method="ffill") yerine ffill() kullan
        for z in vals:
            z = float(z)
            if not initialized:
                x = z
                initialized = True

            x_prior = x
            p_prior = p + q

            k = p_prior / (p_prior + r)
            x = x_prior + k * (z - x_prior)
            p = (1 - k) * p_prior
            out.append(x)
        return pd.Series(out, index=prices.index, name="kalman")
    except Exception as e:
        logger.error(f"Kalman filter hesaplanırken hata: {e}")
        return pd.Series([np.nan] * len(prices), index=prices.index, name="kalman")

def _hilbert_fallback(x: np.ndarray) -> np.ndarray:
    n = len(x)
    Xf = np.fft.fft(x)
    h = np.zeros(n)
    if n % 2 == 0:
        h[0] = 1; h[n//2] = 1; h[1:n//2] = 2
    else:
        h[0] = 1; h[1:(n+1)//2] = 2
    return np.fft.ifft(Xf * h).imag

def hilbert_transform(series: pd.Series) -> pd.Series:
    """
    Hilbert transform (analytic signal) hesaplar.
    """
    try:
        from scipy.signal import hilbert
        ht = hilbert(series.values)
        return pd.Series(np.imag(ht), index=series.index)
    except ImportError:
        return pd.Series(_hilbert_fallback(series.values), index=series.index)
    except Exception as e:
        logger.error(f"Hilbert transform hesaplanırken hata: {e}")
        return pd.Series([np.nan] * len(series), index=series.index)

def sample_entropy(series: pd.Series, m: Optional[int] = None, r_factor: Optional[float] = None) -> float:
    """
    Sample Entropy hesaplar.
    """
    try:
        if m is None:
            m = getattr(CONFIG.TA, "ENTROPY_M", 3)
        if r_factor is None:
            r_factor = getattr(CONFIG.TA, "ENTROPY_R_FACTOR", 0.2)

        x = series.dropna().values
        n = len(x)
        r = r_factor * np.std(x)
        if n < m + 1:
            return 0.0

        def _maxdist(xi, xj, m):
            return max([abs(xi[k] - xj[k]) for k in range(m)])

        def _phi(m):
            patterns = [x[i:i+m] for i in range(n - m + 1)]
            C = [0.0] * (n - m + 1)
            for i in range(n - m + 1):
                for j in range(n - m + 1):
                    if i != j and _maxdist(patterns[i], patterns[j], m) <= r:
                        C[i] += 1
            return sum(C) / ((n - m + 1) * (n - m))

        if _phi(m + 1) == 0 or _phi(m) == 0:
            return 0.0
        return -np.log(_phi(m + 1) / _phi(m))
    except Exception as e:
        logger.error(f"Sample entropy hesaplanırken hata: {e}")
        return 0.0

def market_regime(series: pd.Series, window: Optional[int] = None) -> pd.Series:
    """
    Market regime (trending/mean-reverting) skoru hesaplar.
    Basitleştirilmiş versiyon - Hurst exponent yerine basit trend analizi.
    """
    try:
        if window is None:
            window = getattr(CONFIG.TA, "REGIME_WINDOW", 80)

        if len(series) < window:
            return pd.Series([0.5] * len(series), index=series.index, name="regime")

        # Basit trend tespiti
        regime_scores = []
        for i in range(len(series)):
            if i < window:
                regime_scores.append(0.5)
                continue
                
            window_data = series.iloc[i-window:i]
            if len(window_data) < 2:
                regime_scores.append(0.5)
                continue
                
            # Linear regression slope ile trend tespiti
            x = np.arange(len(window_data))
            y = window_data.values
            slope = np.polyfit(x, y, 1)[0]
            
            # Slope'u 0-1 aralığına normalize et
            normalized_slope = 0.5 + (slope / (2 * np.std(y) + 1e-12))
            regime_scores.append(np.clip(normalized_slope, 0.0, 1.0))
        
        # Eksik değerleri doldur
        regime_series = pd.Series(regime_scores, index=series.index)
        regime_series = regime_series.ffill().bfill().fillna(0.5)
        
        return regime_series
        
    except Exception as e:
        logger.error(f"Market regime hesaplanırken hata: {e}")
        return pd.Series([0.5] * len(series), index=series.index, name="regime")

def lead_lag_correlation(series: pd.Series, max_lag: Optional[int] = None) -> float:
    """
    Lead-lag correlation hesaplar.
    """
    try:
        if max_lag is None:
            max_lag = getattr(CONFIG.TA, "LEADLAG_MAX_LAG", 10)

        x = series.dropna().values
        if len(x) < 2 * max_lag:
            return 0.0

        x = (x - np.mean(x)) / (np.std(x) + 1e-12)
        best_corr = 0.0
        for lag in range(1, max_lag + 1):
            if lag >= len(x):
                break
            corr = np.corrcoef(x[:-lag], x[lag:])[0, 1]
            if abs(corr) > abs(best_corr):
                best_corr = corr
        return best_corr
    except Exception as e:
        logger.error(f"Lead-lag correlation hesaplanırken hata: {e}")
        return 0.0

def alpha_ta(df: pd.DataFrame, column: str = "close") -> dict:
    """
    Gelişmiş alpha sinyalleri üretir.
    """
    try:
        series = df[column]
        
        # 1. Kalman Filter
        kalman = kalman_filter_series(series)
        kalman_signal = np.sign(kalman.diff()).iloc[-1] if len(kalman) > 0 else 0.0
        
        # 2. Hilbert Transform
        hilbert = hilbert_transform(series)
        hilbert_signal = np.sign(hilbert).iloc[-1] if len(hilbert) > 0 else 0.0
        
        # 3. Sample Entropy
        entropy = sample_entropy(series)
        entropy_signal = -1.0 if entropy < 0.5 else 1.0  # Low entropy -> trending
        
        # 4. Market Regime
        regime = market_regime(series)
        regime_signal = regime.iloc[-1] if len(regime) > 0 else 0.5
        
        # 5. Lead-Lag Correlation
        lead_lag = lead_lag_correlation(series)
        lead_lag_signal = np.sign(lead_lag) if not np.isnan(lead_lag) else 0.0
        
        # Ağırlıkları config'ten al veya default kullan
        w_kalman = getattr(CONFIG.TA, "W_KALMAN", 0.20)
        w_hilbert = getattr(CONFIG.TA, "W_HILBERT", 0.20)
        w_entropy = getattr(CONFIG.TA, "W_ENTROPY", 0.20)
        w_regime = getattr(CONFIG.TA, "W_REGIME", 0.20)
        w_leadlag = getattr(CONFIG.TA, "W_LEADLAG", 0.20)
        
        # Toplam ağırlığın 1.0 olduğundan emin ol
        total_weight = w_kalman + w_hilbert + w_entropy + w_regime + w_leadlag
        if total_weight != 1.0:
            # Ağırlıkları normalize et
            w_kalman /= total_weight
            w_hilbert /= total_weight
            w_entropy /= total_weight
            w_regime /= total_weight
            w_leadlag /= total_weight
        
        # Ağırlıklı sinyal
        alpha_signal = (
            w_kalman * kalman_signal +
            w_hilbert * hilbert_signal +
            w_entropy * entropy_signal +
            w_regime * (regime_signal - 0.5) * 2.0 +  # 0-1 aralığını -1 ile +1 arasına dönüştür
            w_leadlag * lead_lag_signal
        )
        
        return {
            "alpha_signal": alpha_signal,
            "kalman_signal": kalman_signal,
            "hilbert_signal": hilbert_signal,
            "entropy": entropy,
            "entropy_signal": entropy_signal,
            "regime": regime_signal,
            "regime_signal": (regime_signal - 0.5) * 2.0,
            "lead_lag": lead_lag,
            "lead_lag_signal": lead_lag_signal,
            "weights": {
                "kalman": w_kalman,
                "hilbert": w_hilbert,
                "entropy": w_entropy,
                "regime": w_regime,
                "leadlag": w_leadlag
            }
        }
    except Exception as e:
        logger.error(f"Alpha TA hesaplanırken hata: {e}")
        return {
            "alpha_signal": 0.0,
            "kalman_signal": 0.0,
            "hilbert_signal": 0.0,
            "entropy": 0.5,
            "entropy_signal": 0.0,
            "regime": 0.5,
            "regime_signal": 0.0,
            "lead_lag": 0.0,
            "lead_lag_signal": 0.0,
            "weights": {
                "kalman": 0.20,
                "hilbert": 0.20,
                "entropy": 0.20,
                "regime": 0.20,
                "leadlag": 0.20
            }
        }

# =============================================================
# Sinyal Üretme
# =============================================================

def generate_signals(df: pd.DataFrame) -> dict:
    """
    Çoklu zaman dilimi ve indikatör sinyallerini birleştirir.
    """
    try:
        # 1. Temel TA sinyalleri
        macd_line, signal_line, hist = macd(df)
        rsi_val = rsi(df)
        stoch_k, stoch_d = stochastic(df)
        upper_bb, middle_bb, lower_bb = bollinger_bands(df)
        
        # 2. Alpha sinyalleri
        alpha_results = alpha_ta(df)
        
        # 3. Sinyal kuralları
        price = df['close'].iloc[-1]
        
        # MACD sinyali
        macd_signal = 1.0 if macd_line.iloc[-1] > signal_line.iloc[-1] else -1.0
        
        # RSI sinyali
        rsi_signal = 0.0
        if rsi_val.iloc[-1] > 70:
            rsi_signal = -1.0
        elif rsi_val.iloc[-1] < 30:
            rsi_signal = 1.0
        
        # Bollinger Bands sinyali
        bb_signal = 0.0
        if price < lower_bb.iloc[-1]:
            bb_signal = 1.0
        elif price > upper_bb.iloc[-1]:
            bb_signal = -1.0
        
        # Stochastic sinyali
        stoch_signal = 1.0 if stoch_k.iloc[-1] < 20 else (-1.0 if stoch_k.iloc[-1] > 80 else 0.0)
        
        # 4. Sinyal ağırlıkları
        signals = {
            'macd': macd_signal,
            'rsi': rsi_signal,
            'bb': bb_signal,
            'stoch': stoch_signal,
            'alpha': alpha_results['alpha_signal']
        }
        
        # 5. Toplam sinyal (ağırlıklı ortalama)
        weights = {
            'macd': 0.2,
            'rsi': 0.2,
            'bb': 0.2,
            'stoch': 0.2,
            'alpha': 0.2
        }
        
        total_signal = sum(signals[s] * weights[s] for s in signals)
        
        # 6. Eşik değerleri config'ten al
        long_threshold = getattr(CONFIG.TA, "ALPHA_LONG_THRESHOLD", 0.6)
        short_threshold = getattr(CONFIG.TA, "ALPHA_SHORT_THRESHOLD", -0.6)
        
        # 7. Son sinyal
        final_signal = 0
        if total_signal >= long_threshold:
            final_signal = 1
        elif total_signal <= short_threshold:
            final_signal = -1
        
        return {
            'signal': final_signal,
            'signals': signals,
            'total_signal': total_signal,
            'alpha_details': alpha_results
        }
    except Exception as e:
        logger.error(f"Sinyal üretilirken hata: {e}")
        return {
            'signal': 0,
            'signals': {},
            'total_signal': 0.0,
            'alpha_details': {}
        }

# =============================================================
# Yardımcı Fonksiyonlar
# =============================================================

def klines_to_dataframe(klines: list) -> pd.DataFrame:
    """Binance klines'ını DataFrame'e dönüştürür."""
    try:
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        
        # Sayısal kolonları dönüştür
        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Timestamp'i datetime'a çevir
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        
        return df
    except Exception as e:
        logger.error(f"Klines to DataFrame dönüşümünde hata: {e}")
        return pd.DataFrame()

def get_ta_function(name: str) -> Optional[Callable]:
    """İsimle TA fonksiyonu döndürür."""
    return TA_FUNCTIONS.get(name)

def get_ta_metrics() -> TAMetrics:
    """TA metriklerini döndürür."""
    return ta_metrics

def get_cache_stats() -> Dict[str, Any]:
    """Cache istatistiklerini döndürür."""
    return ta_cache.get_stats()

def clear_cache() -> None:
    """Cache'i temizler."""
    ta_cache.clear()

def health_check() -> Dict[str, Any]:
    """Sistem sağlık durumunu kontrol eder."""
    try:
        # Test verisi oluştur
        test_data = pd.DataFrame({
            'open': [100, 101, 102, 103, 104],
            'high': [105, 106, 107, 108, 109],
            'low': [95, 96, 97, 98, 99],
            'close': [102, 103, 104, 105, 106],
            'volume': [1000, 2000, 3000, 4000, 5000]
        })
        
        # Test hesaplamaları
        test_ema = ema(test_data)
        test_rsi = rsi(test_data)
        test_macd = macd(test_data)
        
        # Başarılı hesaplamaları kontrol et
        ema_ok = not test_ema.isna().all()
        rsi_ok = not test_rsi.isna().all()
        macd_ok = not all(s.isna().all() for s in test_macd)
        
        return {
            'status': 'healthy' if all([ema_ok, rsi_ok, macd_ok]) else 'degraded',
            'components': {
                'ema': ema_ok,
                'rsi': rsi_ok,
                'macd': macd_ok
            },
            'cache_stats': get_cache_stats(),
            'metrics': {
                'total_calculations': ta_metrics.total_calculations,
                'calculation_errors': ta_metrics.calculation_errors,
                'error_rate': ta_metrics.calculation_errors / max(ta_metrics.total_calculations, 1)
            }
        }
    except Exception as e:
        logger.error(f"Health check sırasında hata: {e}")
        return {
            'status': 'unhealthy',
            'error': str(e),
            'components': {
                'ema': False,
                'rsi': False,
                'macd': False
            }
        }

# =============================================================
# Asenkron/Senkron Loop Helper
# =============================================================

def run_async_safe(coro):
    """
    Asenkron fonksiyonları güvenli şekilde çalıştırır (thread-safe).
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Mevcut loop çalışıyorsa, yeni bir thread'de çalıştır
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(lambda: asyncio.run(coro))
                return future.result()
        else:
            # Loop çalışmıyorsa doğrudan çalıştır
            return asyncio.run(coro)
    except RuntimeError:
        # Hiç loop yoksa yeni oluştur
        return asyncio.run(coro)

# =============================================================
# Ana Fonksiyon
# =============================================================

async def main():
    """Test fonksiyonu."""
    # Test verisi
    data = {
        'open': [100, 101, 102, 103, 104, 105, 106, 107, 108, 109] * 10,
        'high': [105, 106, 107, 108, 109, 110, 111, 112, 113, 114] * 10,
        'low': [95, 96, 97, 98, 99, 100, 101, 102, 103, 104] * 10,
        'close': [102, 103, 104, 105, 106, 107, 108, 109, 110, 111] * 10,
        'volume': [1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000] * 10
    }
    df = pd.DataFrame(data)
    
    # TA hesapla
    results = await calculate_all_ta_hybrid_async(df, "BTCUSDT")
    print(f"Hesaplanan {len(results)} TA indikatörü")
    
    # Sinyal üret
    signal = generate_signals(df)
    print(f"Sinyal: {signal}")
    
    # Health check
    health = health_check()
    print(f"Health: {health}")

if __name__ == "__main__":
    asyncio.run(main())

# EOF







