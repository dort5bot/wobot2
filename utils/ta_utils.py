# ta_utils.py
# Free Render uyumlu hibrit TA pipeline (Güncellenmiş Final)
from __future__ import annotations

import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import asyncio
import math
import time
import logging
import traceback
from typing import Dict, Optional, Tuple, List, Any, Callable, Union
from dataclasses import dataclass
from collections import defaultdict

# Config yüklemesi; eğer eksikse güvenli default davranış
try:
    from utils.config import CONFIG
except Exception:
    # Fallback CONFIG minimal obje (sadece kullandığımız alanlar için)
    class _C:
        class TA:
            EMA_PERIOD = 12
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
            SHARPE_PERIOD = 252
            SHARPE_RISK_FREE_RATE = 0.0
            ENTROPY_M = 2
            ENTROPY_R_FACTOR = 0.2
            REGIME_WINDOW = 20
            LEADLAG_MAX_LAG = 5
            W_KALMAN = 1.0
            W_HILBERT = 1.0
            W_ENTROPY = 1.0
            W_REGIME = 1.0
            W_LEADLAG = 1.0
            ALPHA_LONG_THRESHOLD = 0.6
            ALPHA_SHORT_THRESHOLD = -0.6
            EMA_PERIODS = [8, 21]
            TA_PIPELINE_INTERVAL = 60
            TA_MIN_DATA_POINTS = 20
            TA_CACHE_TTL = 300
            MAX_CACHE_ENTRIES = 1000  # Yeni: Cache limiti
        class SYSTEM:
            MAX_WORKERS = 2
    CONFIG = _C()

# Logger
logger = logging.getLogger("ta_utils")
if not logger.handlers:
    ch = logging.StreamHandler()
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    ch.setFormatter(formatter)
    logger.addHandler(ch)
logger.setLevel(logging.INFO)

# ------------------------------------------------------------
# İç yardımcılar ve Cache Mekanizması
# ------------------------------------------------------------

def _get_max_workers(default: int = 2) -> int:
    system = getattr(CONFIG, "SYSTEM", None)
    if system is not None and hasattr(system, "MAX_WORKERS"):
        try:
            mw = int(getattr(system, "MAX_WORKERS"))
            return mw if mw > 0 else default
        except Exception:
            return default
    return default

@dataclass
class TAMetrics:
    total_calculations: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    calculation_errors: int = 0

class TACache:
    def __init__(self, max_entries: int = 1000):
        self._cache = {}
        self._hit_count = 0
        self._miss_count = 0
        self._last_cleanup = time.time()
        self.max_entries = max_entries
    
    def get_ta_result(self, symbol: str, timeframe: str, indicator: str) -> Optional[Any]:
        key = f"{symbol}_{timeframe}_{indicator}"
        val = self._cache.get(key)
        if val:
            if time.time() < val['expiry']:
                self._hit_count += 1
                return val['value']
            else:
                # expired
                try:
                    del self._cache[key]
                except KeyError:
                    pass
        self._miss_count += 1
        return None
    
    def set_ta_result(self, symbol: str, timeframe: str, indicator: str, value: Any, ttl: int = 300):
        # Cache limit kontrolü
        if len(self._cache) >= self.max_entries:
            self._cleanup_oldest(100)  # En eski 100 entry'i temizle
        
        key = f"{symbol}_{timeframe}_{indicator}"
        self._cache[key] = {
            'value': value,
            'expiry': time.time() + ttl,
            'created': time.time()
        }
        self._cleanup_expired()
    
    def _cleanup_expired(self):
        current_time = time.time()
        if current_time - self._last_cleanup > 300:
            expired_keys = [k for k, v in self._cache.items() if v['expiry'] < current_time]
            for key in expired_keys:
                try:
                    del self._cache[key]
                except KeyError:
                    pass
            self._last_cleanup = current_time
    
    def _cleanup_oldest(self, count: int = 100):
        """En eski entry'leri temizle"""
        if len(self._cache) <= count:
            return
        
        # Created zamanına göre sırala ve en eski 'count' kadarını sil
        sorted_entries = sorted(self._cache.items(), key=lambda x: x[1]['created'])
        for key, _ in sorted_entries[:count]:
            try:
                del self._cache[key]
            except KeyError:
                pass
    
    def get_stats(self) -> Dict[str, Any]:
        total = self._hit_count + self._miss_count
        hit_ratio = self._hit_count / total if total > 0 else 0.0
        return {
            'size': len(self._cache),
            'hits': self._hit_count,
            'misses': self._miss_count,
            'hit_ratio': hit_ratio,
            'max_entries': self.max_entries
        }

# Global instances with max cache entries limit
max_cache_entries = getattr(CONFIG.TA, 'MAX_CACHE_ENTRIES', 1000)
ta_cache = TACache(max_entries=max_cache_entries)
ta_metrics = TAMetrics()

# =============================================================
# Trend İndikatörleri (pandas / series oriented)
# =============================================================

def ema(df: pd.DataFrame, period: Optional[int] = None, column: str = "close") -> pd.Series:
    try:
        period = period or CONFIG.TA.EMA_PERIOD
        return df[column].ewm(span=period, adjust=False).mean()
    except Exception as e:
        logger.error("EMA hesaplanamadı: %s", e)
        return pd.Series(dtype=float, index=df.index)

def macd(df: pd.DataFrame, fast: Optional[int] = None, slow: Optional[int] = None, 
         signal: Optional[int] = None, column: str = "close") -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD hesaplar - pandas DataFrame/Series için.
    """
    try:
        fast = fast or CONFIG.TA.MACD_FAST
        slow = slow or CONFIG.TA.MACD_SLOW
        signal = signal or CONFIG.TA.MACD_SIGNAL

        if isinstance(df, (pd.DataFrame, pd.Series)):
            series = df[column] if isinstance(df, pd.DataFrame) else df
            ema_fast = series.ewm(span=fast, adjust=False).mean()
            ema_slow = series.ewm(span=slow, adjust=False).mean()
            macd_line = ema_fast - ema_slow
            signal_line = macd_line.ewm(span=signal, adjust=False).mean()
            hist = macd_line - signal_line
            return macd_line, signal_line, hist
        else:
            # fallback: use list-based implementation
            return _macd_list_implementation(df, fast, slow, signal)
    except Exception as e:
        logger.error("MACD hesaplanamadı: %s", e)
        empty_series = pd.Series(dtype=float, index=df.index if hasattr(df, 'index') else range(len(df)))
        return empty_series, empty_series, empty_series

def adx(df: pd.DataFrame, period: Optional[int] = None) -> pd.Series:
    try:
        period = period or CONFIG.TA.ADX_PERIOD
        high, low, close = df['high'], df['low'], df['close']
        tr = np.maximum(high - low,
                       np.maximum(abs(high - close.shift(1)),
                                 abs(low - close.shift(1))))
        plus_dm = np.where((high - high.shift(1)) > (low.shift(1) - low),
                          np.maximum(high - high.shift(1), 0), 0)
        minus_dm = np.where((low.shift(1) - low) > (high - high.shift(1)),
                           np.maximum(low.shift(1) - low, 0), 0)

        tr_smooth = pd.Series(tr).rolling(window=period).sum()
        plus_di = 100 * (pd.Series(plus_dm).rolling(window=period).sum() / (tr_smooth + 1e-12))
        minus_di = 100 * (pd.Series(minus_dm).rolling(window=period).sum() / (tr_smooth + 1e-12))
        dx = (100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-12))
        adx_val = dx.rolling(window=period).mean()
        return adx_val
    except Exception as e:
        logger.error("ADX hesaplanamadı: %s", e)
        return pd.Series(dtype=float, index=df.index)

def vwap(df: pd.DataFrame) -> pd.Series:
    try:
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        return (typical_price * df['volume']).cumsum() / (df['volume'].cumsum() + 1e-12)
    except Exception as e:
        logger.error("VWAP hesaplanamadı: %s", e)
        return pd.Series(dtype=float, index=df.index)

def cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    try:
        tp = (df['high'] + df['low'] + df['close']) / 3
        sma = tp.rolling(period).mean()
        mad = tp.rolling(period).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
        return (tp - sma) / (0.015 * (mad + 1e-12))
    except Exception as e:
        logger.error("CCI hesaplanamadı: %s", e)
        return pd.Series(dtype=float, index=df.index)

def momentum(df: pd.DataFrame, period: int = 10) -> pd.Series:
    try:
        return df['close'] / df['close'].shift(period) * 100
    except Exception as e:
        logger.error("Momentum hesaplanamadı: %s", e)
        return pd.Series(dtype=float, index=df.index)

# =============================================================
# Momentum İndikatörleri (RSI / Stochastic unified)
# =============================================================

def rsi(arr: Union[pd.DataFrame, pd.Series, List[float], np.ndarray], 
        period: Optional[int] = None, column: str = "close") -> Union[pd.Series, List[float]]:
    """
    Tek bir fonksiyon hem pandas (DataFrame/Series) hem de liste/array tabanlı RSI döndürür.
    """
    try:
        # pandas DataFrame
        if isinstance(arr, pd.DataFrame):
            series = arr[column]
            period = period or CONFIG.TA.RSI_PERIOD
            delta = series.diff()
            gain = delta.clip(lower=0)
            loss = -delta.clip(upper=0)
            avg_gain = gain.ewm(span=period, adjust=False).mean()
            avg_loss = loss.ewm(span=period, adjust=False).mean()
            rs = avg_gain / (avg_loss + 1e-12)
            return 100 - (100 / (1 + rs))
        
        # pandas Series
        elif isinstance(arr, pd.Series):
            series = arr
            period = period or CONFIG.TA.RSI_PERIOD
            delta = series.diff()
            gain = delta.clip(lower=0)
            loss = -delta.clip(upper=0)
            avg_gain = gain.ewm(span=period, adjust=False).mean()
            avg_loss = loss.ewm(span=period, adjust=False).mean()
            rs = avg_gain / (avg_loss + 1e-12)
            return 100 - (100 / (1 + rs))
        
        # list / numpy array
        else:
            prices = list(arr)
            period = period or CONFIG.TA.RSI_PERIOD
            if len(prices) < period + 1:
                return []
            
            gains = []
            losses = []
            
            for i in range(1, len(prices)):
                change = prices[i] - prices[i-1]
                gains.append(change if change > 0 else 0)
                losses.append(-change if change < 0 else 0)
            
            # Calculate first average gains and losses
            avg_gain = sum(gains[:period]) / period
            avg_loss = sum(losses[:period]) / period
            
            rsi_values = []
            for i in range(period, len(gains)):
                if avg_loss == 0:
                    rsi_val = 100.0
                else:
                    rs = avg_gain / avg_loss
                    rsi_val = 100 - (100 / (1 + rs))
                
                rsi_values.append(rsi_val)
                
                # Update averages
                avg_gain = (avg_gain * (period - 1) + gains[i]) / period
                avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            
            return rsi_values
            
    except Exception as e:
        logger.exception("RSI calculation error: %s", e)
        return [] if not isinstance(arr, (pd.Series, pd.DataFrame)) else pd.Series(dtype=float)

def stochastic(df: pd.DataFrame, k_period: Optional[int] = None, d_period: Optional[int] = None) -> Tuple[pd.Series, pd.Series]:
    try:
        k_period = k_period or CONFIG.TA.STOCH_K
        d_period = d_period or CONFIG.TA.STOCH_D
        low_min = df["low"].rolling(window=k_period).min()
        high_max = df["high"].rolling(window=k_period).max()
        k = 100 * (df["close"] - low_min) / (high_max - low_min + 1e-12)
        d = k.rolling(window=d_period).mean()
        return k, d
    except Exception as e:
        logger.error("Stochastic hesaplanamadı: %s", e)
        empty_series = pd.Series(dtype=float, index=df.index)
        return empty_series, empty_series

# =============================================================
# Volatilite & Risk
# =============================================================

def atr(df: pd.DataFrame, period: Optional[int] = None) -> pd.Series:
    try:
        period = period or CONFIG.TA.ATR_PERIOD
        high_low = df["high"] - df["low"]
        high_close = abs(df["high"] - df["close"].shift())
        low_close = abs(df["low"] - df["close"].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()
    except Exception as e:
        logger.error("ATR hesaplanamadı: %s", e)
        return pd.Series(dtype=float, index=df.index)

def bollinger_bands(df: pd.DataFrame, period: Optional[int] = None, column: str = "close") -> Tuple[pd.Series, pd.Series, pd.Series]:
    try:
        period = period or CONFIG.TA.BB_PERIOD
        sma = df[column].rolling(window=period).mean()
        std = df[column].rolling(window=period).std()
        upper = sma + (CONFIG.TA.BB_STDDEV * std)
        lower = sma - (CONFIG.TA.BB_STDDEV * std)
        return upper, sma, lower
    except Exception as e:
        logger.error("Bollinger Bands hesaplanamadı: %s", e)
        empty_series = pd.Series(dtype=float, index=df.index)
        return empty_series, empty_series, empty_series

def sharpe_ratio(df: pd.DataFrame, risk_free_rate: Optional[float] = None, 
                period: Optional[int] = None, column: str = "close") -> float:
    try:
        period = period or CONFIG.TA.SHARPE_PERIOD
        risk_free_rate = risk_free_rate or CONFIG.TA.SHARPE_RISK_FREE_RATE
        returns = df[column].pct_change().dropna()
        excess = returns - risk_free_rate / period
        if excess.std() == 0:
            return 0.0
        return float((excess.mean() / (excess.std() + 1e-12)) * math.sqrt(period))
    except Exception as e:
        logger.error("Sharpe Ratio hesaplanamadı: %s", e)
        return 0.0

def max_drawdown(df: pd.DataFrame, column: str = "close") -> float:
    try:
        roll_max = df[column].cummax()
        drawdown = (df[column] - roll_max) / (roll_max + 1e-12)
        return float(drawdown.min())
    except Exception as e:
        logger.error("Max Drawdown hesaplanamadı: %s", e)
        return 0.0

def historical_volatility(df: pd.DataFrame, period: int = 30) -> pd.Series:
    try:
        log_returns = np.log(df['close'] / df['close'].shift(1))
        vol = log_returns.rolling(period).std() * np.sqrt(252) * 100
        return vol
    except Exception as e:
        logger.error("Historical Volatility hesaplanamadı: %s", e)
        return pd.Series(dtype=float, index=df.index)

def ulcer_index(df: pd.DataFrame, period: int = 14) -> pd.Series:
    try:
        rolling_max = df['close'].rolling(period).max()
        drawdown = (df['close'] - rolling_max) / (rolling_max + 1e-12) * 100
        return np.sqrt((drawdown.pow(2)).rolling(period).mean())
    except Exception as e:
        logger.error("Ulcer Index hesaplanamadı: %s", e)
        return pd.Series(dtype=float, index=df.index)

# =============================================================
# Hacim & Likidite
# =============================================================

def obv(df: pd.DataFrame) -> pd.Series:
    try:
        obv_series = (np.sign(df["close"].diff()) * df["volume"]).fillna(0).cumsum()
        return obv_series
    except Exception as e:
        logger.error("OBV hesaplanamadı: %s", e)
        return pd.Series(dtype=float, index=df.index)

def cmf(df: pd.DataFrame, period: int = 20) -> pd.Series:
    try:
        mfm = ((df['close'] - df['low']) - (df['high'] - df['close'])) / (df['high'] - df['low'] + 1e-12)
        mfv = mfm * df['volume']
        return mfv.rolling(period).sum() / (df['volume'].rolling(period).sum() + 1e-12)
    except Exception as e:
        logger.error("CMF hesaplanamadı: %s", e)
        return pd.Series(dtype=float, index=df.index)

def order_book_imbalance(bids: list, asks: list) -> float:
    try:
        bid_vol = sum([b[1] for b in bids]) if bids else 0.0
        ask_vol = sum([a[1] for a in asks]) if asks else 0.0
        denom = (bid_vol + ask_vol) or 1e-12
        return (bid_vol - ask_vol) / denom
    except Exception as e:
        logger.error("Order Book Imbalance hesaplanamadı: %s", e)
        return 0.0

def open_interest_placeholder():
    return None

# =============================================================
# Market Structure
# =============================================================

def market_structure(df: pd.DataFrame) -> pd.DataFrame:
    try:
        highs = df['high']
        lows = df['low']
        structure = pd.DataFrame(index=df.index)
        structure['higher_high'] = highs > highs.shift(1)
        structure['lower_low'] = lows < lows.shift(1)
        return structure
    except Exception as e:
        logger.error("Market Structure hesaplanamadı: %s", e)
        return pd.DataFrame()

def breakout(df: pd.DataFrame, period: int = 20) -> pd.Series:
    try:
        rolling_high = df['close'].rolling(period).max()
        rolling_low = df['close'].rolling(period).min()
        cond_long = df['close'] > rolling_high.shift(1)
        cond_short = df['close'] < rolling_low.shift(1)
        signal = pd.Series(index=df.index, dtype=float)
        signal[cond_long] = 1.0
        signal[cond_short] = -1.0
        signal.fillna(0, inplace=True)
        return signal
    except Exception as e:
        logger.error("Breakout hesaplanamadı: %s", e)
        return pd.Series(dtype=float, index=df.index)

# =============================================================
# Binance Entegre IO Fonksiyonları
# =============================================================

async def fetch_funding_rate_binance(symbol: str = "BTCUSDT") -> float:
    try:
        from utils.binance_api import get_binance_api
        client = get_binance_api()
        # client.get_funding_rate may be async or sync; handle both
        if asyncio.iscoroutinefunction(getattr(client, "get_funding_rate", None)):
            funding_data = await client.get_funding_rate(symbol, limit=1)
        else:
            # run in threadpool
            loop = asyncio.get_event_loop()
            funding_data = await loop.run_in_executor(None, lambda: client.get_funding_rate(symbol, limit=1))
        return float(funding_data[0].get('fundingRate', 0.0)) if funding_data else 0.0
    except Exception as e:
        logger.warning("Funding rate çekilemedi (%s). Varsayılan kullanılıyor. Hata: %s", symbol, e)
        return 0.0

async def fetch_social_sentiment_binance(symbol: str = "BTC") -> Dict[str, float]:
    try:
        await asyncio.sleep(0.01)
        return {"sentiment": 0.5}
    except Exception as e:
        logger.warning("Social sentiment çekilemedi: %s", e)
        return {"sentiment": 0.5}

async def get_live_order_book_imbalance(symbol: str = "BTCUSDT") -> float:
    try:
        from utils.binance_api import get_binance_api
        client = get_binance_api()
        if asyncio.iscoroutinefunction(getattr(client, "get_order_book", None)):
            ob = await client.get_order_book(symbol, limit=100)
        else:
            loop = asyncio.get_event_loop()
            ob = await loop.run_in_executor(None, lambda: client.get_order_book(symbol, limit=100))
        bids = [[float(b[0]), float(b[1])] for b in ob.get('bids', [])]
        asks = [[float(a[0]), float(a[1])] for a in ob.get('asks', [])]
        return order_book_imbalance(bids, asks)
    except Exception as e:
        logger.warning("Order book imbalance hesaplanamadı: %s", e)
        return 0.0

# =============================================================
# Registry
# =============================================================

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
    pipeline_logger = logging.getLogger("ta_pipeline")
    
    try:
        from utils.binance_api import get_binance_api
        client = get_binance_api()
    except Exception:
        client = None
        pipeline_logger.warning("Binance client bulunamadı; bazı IO fonksiyonları çalışmayabilir.")

    pipeline_interval = getattr(CONFIG.TA, 'TA_PIPELINE_INTERVAL', 60)
    min_data_points = getattr(CONFIG.TA, 'TA_MIN_DATA_POINTS', 20)
    cache_ttl = getattr(CONFIG.TA, 'TA_CACHE_TTL', 300)

    while True:
        try:
            cached_result = ta_cache.get_ta_result(symbol, interval, 'full_analysis')
            if cached_result:
                pipeline_logger.debug("Using cached TA results for %s", symbol)
                if callback:
                    try:
                        await callback(cached_result)
                    except Exception:
                        pipeline_logger.exception("Callback hata")
                await asyncio.sleep(pipeline_interval)
                continue

            if client is None:
                pipeline_logger.warning("Client yok, bekleniyor...")
                await asyncio.sleep(30)
                continue

            # Veriyi çek
            if asyncio.iscoroutinefunction(getattr(client, "get_klines", None)):
                klines = await client.get_klines(symbol, interval, limit=100)
            else:
                loop = asyncio.get_event_loop()
                klines = await loop.run_in_executor(None, lambda: client.get_klines(symbol, interval, limit=100))

            if not klines:
                pipeline_logger.warning("Kline verisi alınamadı, bekleniyor...")
                await asyncio.sleep(30)
                continue

            df = klines_to_dataframe(klines)

            if len(df) < min_data_points:
                pipeline_logger.warning("Insufficient data for %s: %d points", symbol, len(df))
                await asyncio.sleep(30)
                continue

            # TA hesapla (threaded)
            ta_results = await asyncio.get_event_loop().run_in_executor(
                None, lambda: calculate_all_ta_hybrid(df, symbol)
            )

            # Sinyal üret
            signal = generate_signals(df)

            # Sonuçları paketle
            result = {
                'symbol': symbol,
                'timestamp': time.time(),
                'price': float(df['close'].iloc[-1]),
                'signal': signal,
                'ta_metrics': {k: float(v.iloc[-1]) if hasattr(v, 'iloc') else v 
                              for k, v in ta_results.items() if v is not None}
            }

            # Cache'e kaydet
            ta_cache.set_ta_result(symbol, interval, 'full_analysis', result, ttl=cache_ttl)

            # Callback ile gönder
            if callback and isinstance(signal, dict) and signal.get('signal', 0) != 0:
                try:
                    await callback(result)
                except Exception:
                    pipeline_logger.exception("Callback sırasında hata oluştu")

            await asyncio.sleep(pipeline_interval)

        except asyncio.CancelledError:
            pipeline_logger.info("Pipeline cancelled for %s", symbol)
            break
        except Exception as e:
            pipeline_logger.exception("Trading pipeline error for %s: %s", symbol, e)
            await asyncio.sleep(30)

# Genel registry
TA_FUNCTIONS = {
    **CPU_FUNCTIONS,
    "order_book_imbalance": order_book_imbalance,
    "open_interest_placeholder": open_interest_placeholder,
}

# =============================================================
# Hibrit Pipeline - Geliştirilmiş
# =============================================================

def calculate_cpu_functions(df: pd.DataFrame, max_workers: Optional[int] = None) -> dict:
    results: dict = {}
    max_workers = max_workers or _get_max_workers(default=2)
    
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for name, func in CPU_FUNCTIONS.items():
                # submit with df; many functions expect df; those that don't should handle type
                futures[executor.submit(func, df)] = name

            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                    ta_metrics.total_calculations += 1
                except Exception as e:
                    results[name] = None
                    ta_metrics.calculation_errors += 1
                    logger.exception("[CPU TA ERROR] %s hesaplanamadı: %s", name, e)
    except Exception as e:
        logger.exception("calculate_cpu_functions genel hata: %s", e)
    
    return results

async def calculate_io_functions(symbol: str = "BTCUSDT") -> dict:
    results: dict = {}
    names = list(IO_FUNCTIONS.keys())
    tasks = []
    
    for name in names:
        try:
            func = IO_FUNCTIONS[name]
            tasks.append(func(symbol))
        except Exception as e:
            logger.exception("IO fonksiyon hazırlanırken hata: %s", e)
            tasks.append(asyncio.sleep(0, result=None))
    
    completed = await asyncio.gather(*tasks, return_exceptions=True)
    
    for name, res in zip(names, completed):
        if isinstance(res, Exception):
            results[name] = None
            ta_metrics.calculation_errors += 1
            logger.warning("[I/O TA ERROR] %s hesaplanamadı: %s", name, res)
        else:
            results[name] = res
            ta_metrics.total_calculations += 1
    
    return results

def _run_asyncio(coro):
    """
    Güvenli asyncio runner: eğer event loop çalışıyorsa yeni bir loop yaratır.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        new_loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(new_loop)
            return new_loop.run_until_complete(coro)
        finally:
            new_loop.close()
            asyncio.set_event_loop(loop)
    else:
        if loop is None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            try:
                loop.close()
            except Exception:
                pass

async def calculate_all_ta_hybrid_async(df: pd.DataFrame, symbol: str = "BTCUSDT", 
                                      max_workers: Optional[int] = None) -> dict:
    cpu_results = await asyncio.get_event_loop().run_in_executor(
        None, lambda: calculate_cpu_functions(df, max_workers)
    )
    io_results = await calculate_io_functions(symbol)
    return {**cpu_results, **io_results}

def calculate_all_ta_hybrid(df: pd.DataFrame, symbol: str = "BTCUSDT", 
                          max_workers: Optional[int] = None) -> dict:
    return _run_asyncio(calculate_all_ta_hybrid_async(df, symbol, max_workers))

# =============================================================
# Advanced Signal Stack: alpha_ta
# =============================================================

def kalman_filter_series(prices: pd.Series, q: Optional[float] = None, r: Optional[float] = None) -> pd.Series:
    try:
        if q is None:
            q = getattr(CONFIG.TA, "KALMAN_Q", 1e-5)
        if r is None:
            r = getattr(CONFIG.TA, "KALMAN_R", 1e-2)
        
        x = 0.0
        p = 1.0
        out = []
        initialized = False
        
        vals = prices.fillna(method="ffill").values
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
        
        return pd.Series(out, index=prices.index)
    except Exception as e:
        logger.exception("Kalman filter error: %s", e)
        return prices

def hilbert_transform_series(prices: pd.Series) -> pd.Series:
    try:
        from scipy.signal import hilbert
        vals = prices.fillna(method="ffill").values
        analytic_signal = hilbert(vals)
        return pd.Series(np.abs(analytic_signal), index=prices.index)
    except ImportError:
        logger.warning("scipy not available for Hilbert transform")
        return prices
    except Exception as e:
        logger.exception("Hilbert transform error: %s", e)
        return prices

def sample_entropy_series(prices: pd.Series, m: Optional[int] = None, r_factor: Optional[float] = None) -> pd.Series:
    try:
        m = m or getattr(CONFIG.TA, "ENTROPY_M", 2)
        r_factor = r_factor or getattr(CONFIG.TA, "ENTROPY_R_FACTOR", 0.2)
        
        vals = prices.fillna(method="ffill").values
        n = len(vals)
        if n < m + 1:
            return pd.Series([0.0] * n, index=prices.index)
        
        r = r_factor * np.std(vals)
        
        def _maxdist(x_i, x_j):
            return max([abs(ua - va) for ua, va in zip(x_i, x_j)])
        
        def _phi(m_val):
            x = [[vals[j] for j in range(i, i + m_val)] for i in range(n - m_val + 1)]
            c = [0.0] * (n - m_val + 1)
            
            for i in range(len(x)):
                for j in range(len(x)):
                    if i != j and _maxdist(x[i], x[j]) <= r:
                        c[i] += 1
            
            return sum(c) / (len(x) * (len(x) - 1))
        
        out = [0.0] * n
        for i in range(m, n):
            if i >= m:
                try:
                    phi_m = _phi(m)
                    phi_m1 = _phi(m + 1)
                    out[i] = -np.log(phi_m1 / (phi_m + 1e-12))
                except Exception:
                    out[i] = out[i-1] if i > 0 else 0.0
            else:
                out[i] = 0.0
        
        return pd.Series(out, index=prices.index)
    except Exception as e:
        logger.exception("Sample entropy error: %s", e)
        return pd.Series([0.0] * len(prices), index=prices.index)

def market_regime_classification(df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
    try:
        window = window or getattr(CONFIG.TA, "REGIME_WINDOW", 20)
        returns = df['close'].pct_change()
        volatility = returns.rolling(window).std()
        trend = df['close'].rolling(window).mean()
        current_trend = (df['close'] - trend) / (trend + 1e-12)
        
        regime = pd.Series(index=df.index, dtype=str)
        regime[(volatility < volatility.quantile(0.25)) & (current_trend > 0.02)] = "BULL_CALM"
        regime[(volatility < volatility.quantile(0.25)) & (current_trend < -0.02)] = "BEAR_CALM"
        regime[(volatility > volatility.quantile(0.75)) & (current_trend > 0.02)] = "BULL_VOLATILE"
        regime[(volatility > volatility.quantile(0.75)) & (current_trend < -0.02)] = "BEAR_VOLATILE"
        regime[regime.isna()] = "NEUTRAL"
        return regime
    except Exception as e:
        logger.exception("Market regime classification error: %s", e)
        return pd.Series(["NEUTRAL"] * len(df), index=df.index)

def lead_lag_correlation(df: pd.DataFrame, max_lag: Optional[int] = None) -> Tuple[float, int]:
    try:
        max_lag = max_lag or getattr(CONFIG.TA, "LEADLAG_MAX_LAG", 5)
        close = df['close'].values
        volume = df['volume'].values
        
        best_corr = -1.0
        best_lag = 0
        
        for lag in range(-max_lag, max_lag + 1):
            if lag < 0:
                corr = np.corrcoef(close[:lag], volume[-lag:])[0, 1]
            elif lag > 0:
                corr = np.corrcoef(close[lag:], volume[:-lag])[0, 1]
            else:
                corr = np.corrcoef(close, volume)[0, 1]
            
            if abs(corr) > abs(best_corr):
                best_corr = corr
                best_lag = lag
        
        return best_corr, best_lag
    except Exception as e:
        logger.exception("Lead-lag correlation error: %s", e)
        return 0.0, 0

def alpha_ta(df: pd.DataFrame, symbol: str = "BTCUSDT") -> Dict[str, Any]:
    try:
        close = df['close']
        
        # Advanced signal components
        kalman = kalman_filter_series(close)
        hilbert = hilbert_transform_series(close)
        entropy = sample_entropy_series(close)
        regime = market_regime_classification(df)
        lead_lag_corr, lead_lag = lead_lag_correlation(df)
        
        # Weights from config
        w_kalman = getattr(CONFIG.TA, "W_KALMAN", 1.0)
        w_hilbert = getattr(CONFIG.TA, "W_HILBERT", 1.0)
        w_entropy = getattr(CONFIG.TA, "W_ENTROPY", 1.0)
        w_regime = getattr(CONFIG.TA, "W_REGIME", 1.0)
        w_leadlag = getattr(CONFIG.TA, "W_LEADLAG", 1.0)
        
        # Normalize components
        kalman_norm = (kalman - kalman.mean()) / (kalman.std() + 1e-12)
        hilbert_norm = (hilbert - hilbert.mean()) / (hilbert.std() + 1e-12)
        entropy_norm = (entropy - entropy.mean()) / (entropy.std() + 1e-12)
        
        # Regime scoring
        regime_map = {
            "BULL_CALM": 1.0,
            "BULL_VOLATILE": 0.7,
            "NEUTRAL": 0.0,
            "BEAR_CALM": -0.7,
            "BEAR_VOLATILE": -1.0
        }
        regime_score = regime.map(regime_map).fillna(0.0)
        
        # Lead-lag scoring
        leadlag_score = lead_lag_corr * 2.0  # Scale to reasonable range
        
        # Composite alpha score
        alpha_score = (
            w_kalman * kalman_norm.iloc[-1] +
            w_hilbert * hilbert_norm.iloc[-1] +
            w_entropy * entropy_norm.iloc[-1] +
            w_regime * regime_score.iloc[-1] +
            w_leadlag * leadlag_score
        )
        
        # Thresholds from config
        long_threshold = getattr(CONFIG.TA, "ALPHA_LONG_THRESHOLD", 0.6)
        short_threshold = getattr(CONFIG.TA, "ALPHA_SHORT_THRESHOLD", -0.6)
        
        # Signal generation
        signal = 0
        if alpha_score >= long_threshold:
            signal = 1
        elif alpha_score <= short_threshold:
            signal = -1
        
        return {
            "alpha_score": float(alpha_score),
            "signal": signal,
            "kalman": float(kalman.iloc[-1]),
            "hilbert": float(hilbert.iloc[-1]),
            "entropy": float(entropy.iloc[-1]),
            "regime": regime.iloc[-1],
            "lead_lag_corr": float(lead_lag_corr),
            "lead_lag": lead_lag
        }
    except Exception as e:
        logger.exception("Alpha TA calculation error: %s", e)
        return {
            "alpha_score": 0.0,
            "signal": 0,
            "kalman": 0.0,
            "hilbert": 0.0,
            "entropy": 0.0,
            "regime": "NEUTRAL",
            "lead_lag_corr": 0.0,
            "lead_lag": 0
        }

def generate_signals(df: pd.DataFrame, symbol: str = "BTCUSDT") -> Dict[str, Any]:
    try:
        alpha_result = alpha_ta(df, symbol)
        
        # Basic TA signals
        rsi_val = rsi(df, column="close")
        macd_line, signal_line, hist = macd(df, column="close")
        upper_bb, middle_bb, lower_bb = bollinger_bands(df, column="close")
        
        # Signal logic
        signals = {
            "signal": alpha_result["signal"],
            "alpha_score": alpha_result["alpha_score"],
            "rsi": float(rsi_val.iloc[-1]) if len(rsi_val) > 0 else 50.0,
            "macd_hist": float(hist.iloc[-1]) if len(hist) > 0 else 0.0,
            "bb_position": float((df['close'].iloc[-1] - lower_bb.iloc[-1]) / 
                               (upper_bb.iloc[-1] - lower_bb.iloc[-1] + 1e-12)) if len(upper_bb) > 0 else 0.5,
            "timestamp": time.time()
        }
        
        return signals
    except Exception as e:
        logger.exception("Signal generation error: %s", e)
        return {
            "signal": 0,
            "alpha_score": 0.0,
            "rsi": 50.0,
            "macd_hist": 0.0,
            "bb_position": 0.5,
            "timestamp": time.time()
        }

# =============================================================
# Yardımcı Fonksiyonlar
# =============================================================

def klines_to_dataframe(klines: list) -> pd.DataFrame:
    try:
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df
    except Exception as e:
        logger.error("Klines to DataFrame dönüşüm hatası: %s", e)
        return pd.DataFrame()

def get_cache_stats() -> Dict[str, Any]:
    return ta_cache.get_stats()

def get_metrics() -> TAMetrics:
    return ta_metrics

def clear_cache():
    global ta_cache
    ta_cache = TACache(max_entries=getattr(CONFIG.TA, 'MAX_CACHE_ENTRIES', 1000))

# =============================================================
# Geriye dönük uyumluluk için yardımcı fonksiyonlar
# =============================================================

def _macd_list_implementation(prices: list, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[List[float], List[float], List[float]]:
    """
    Liste/array tabanlı MACD implementasyonu (geriye dönük uyumluluk için)
    """
    try:
        if len(prices) < slow + signal:
            return [], [], []
        
        ema_fast = []
        ema_slow = []
        
        # EMA fast
        ema_f = prices[0]
        multiplier_fast = 2 / (fast + 1)
        for price in prices:
            ema_f = (price - ema_f) * multiplier_fast + ema_f
            ema_fast.append(ema_f)
        
        # EMA slow
        ema_s = prices[0]
        multiplier_slow = 2 / (slow + 1)
        for price in prices:
            ema_s = (price - ema_s) * multiplier_slow + ema_s
            ema_slow.append(ema_s)
        
        # MACD line
        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        
        # Signal line (EMA of MACD)
        signal_line = []
        ema_sig = macd_line[0]
        multiplier_signal = 2 / (signal + 1)
        for macd_val in macd_line:
            ema_sig = (macd_val - ema_sig) * multiplier_signal + ema_sig
            signal_line.append(ema_sig)
        
        # Histogram
        hist = [m - s for m, s in zip(macd_line, signal_line)]
        
        return macd_line, signal_line, hist
    except Exception as e:
        logger.exception("List-based MACD calculation error: %s", e)
        return [], [], []

# =============================================================
# Main
# =============================================================

if __name__ == "__main__":
    # Test kodu
    test_data = {
        'open': [100, 101, 102, 103, 104, 105, 106, 107, 108, 109],
        'high': [101, 102, 103, 104, 105, 106, 107, 108, 109, 110],
        'low': [99, 100, 101, 102, 103, 104, 105, 106, 107, 108],
        'close': [100.5, 101.5, 102.5, 103.5, 104.5, 105.5, 106.5, 107.5, 108.5, 109.5],
        'volume': [1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800, 1900]
    }
    test_df = pd.DataFrame(test_data)
    
    # Test hesaplamaları
    print("Testing EMA:", ema(test_df, period=3).tail())
    print("Testing RSI:", rsi(test_df, period=3).tail())
    
    # Cache test
    ta_cache.set_ta_result("TEST", "1m", "test_value", 42, ttl=10)
    print("Cache test:", ta_cache.get_ta_result("TEST", "1m", "test_value"))
    
    print("TA Utils loaded successfully!")
