# ta_utils.py
# Free Render uyumlu hibrit TA pipeline
# - CPU-bound: ThreadPoolExecutor
# - IO-bound: asyncio
# - MAX_WORKERS: CONFIG.SYSTEM.MAX_WORKERS varsa kullanılır, yoksa 2
# - Binance API entegreli gerçek zamanlı veri desteği
'''
✅ Tüm önerilen entegrasyonlar
✅ Binance API bağlantılı IO fonksiyonları
✅ Gelişmiş cache mekanizması
✅ Performans metrikleri ve monitoring
✅ Optimize edilmiş paralel işleme
✅ Safety improvement: adx artık pure function
✅ Type hint geliştirmeleri
✅ Health check fonksiyonları
'''

from __future__ import annotations

import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import asyncio
import math
import time
import logging  # ✅worker_d için
from typing import Dict, Optional, Tuple, List, Any, Callable
from dataclasses import dataclass
from collections import defaultdict

from utils.config import CONFIG

# ------------------------------------------------------------
# İç yardımcılar ve Cache Mekanizması
# ------------------------------------------------------------

def _get_max_workers(default: int = 2) -> int:
    # CONFIG.SYSTEM.MAX_WORKERS tanımlıysa onu kullan, değilse default
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
    """TA hesaplamaları için önbellek katmanı."""
    def __init__(self):
        self._cache = {}
        self._hit_count = 0
        self._miss_count = 0
        self._last_cleanup = time.time()
    
    def get_ta_result(self, symbol: str, timeframe: str, indicator: str) -> Optional[Any]:
        key = f"{symbol}_{timeframe}_{indicator}"
        if key in self._cache:
            cache_data = self._cache[key]
            if time.time() < cache_data['expiry']:
                self._hit_count += 1
                return cache_data['value']
            else:
                del self._cache[key]
        self._miss_count += 1
        return None
    
    def set_ta_result(self, symbol: str, timeframe: str, indicator: str, value: Any, ttl: int = 300):
        key = f"{symbol}_{timeframe}_{indicator}"
        self._cache[key] = {
            'value': value,
            'expiry': time.time() + ttl
        }
        self._cleanup_expired()
    
    def _cleanup_expired(self):
        current_time = time.time()
        if current_time - self._last_cleanup > 300:  # 5 dakikada bir temizle
            expired_keys = [k for k, v in self._cache.items() if v['expiry'] < current_time]
            for key in expired_keys:
                del self._cache[key]
            self._last_cleanup = current_time
    
    def get_stats(self) -> Dict[str, int]:
        return {
            'size': len(self._cache),
            'hits': self._hit_count,
            'misses': self._miss_count,
            'hit_ratio': self._hit_count / max(self._hit_count + self._miss_count, 1)
        }

# Global instances
ta_cache = TACache()
ta_metrics = TAMetrics()

# =============================================================
# Trend İndikatörleri
# =============================================================

def ema(df: pd.DataFrame, period: Optional[int] = None, column: str = "close") -> pd.Series:
    """Exponential Moving Average (EMA) hesaplar."""
    period = period or CONFIG.TA.EMA_PERIOD
    return df[column].ewm(span=period, adjust=False).mean()

def macd(df: pd.DataFrame, fast: Optional[int] = None, slow: Optional[int] = None, 
         signal: Optional[int] = None, column: str = "close") -> Tuple[pd.Series, pd.Series, pd.Series]:
    """MACD (Moving Average Convergence Divergence) hesaplar."""
    fast = fast or CONFIG.TA.MACD_FAST
    slow = slow or CONFIG.TA.MACD_SLOW
    signal = signal or CONFIG.TA.MACD_SIGNAL

    ema_fast = df[column].ewm(span=fast, adjust=False).mean()
    ema_slow = df[column].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line

    return macd_line, signal_line, hist

def adx(df: pd.DataFrame, period: Optional[int] = None) -> pd.Series:
    """Average Directional Index (ADX) hesaplar."""
    period = period or CONFIG.TA.ADX_PERIOD

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

def vwap(df: pd.DataFrame) -> pd.Series:
    """Volume Weighted Average Price (VWAP) hesaplar."""
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    return (typical_price * df['volume']).cumsum() / df['volume'].cumsum()

def cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Commodity Channel Index (CCI) hesaplar."""
    tp = (df['high'] + df['low'] + df['close']) / 3
    sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    return (tp - sma) / (0.015 * mad)

def momentum(df: pd.DataFrame, period: int = 10) -> pd.Series:
    """Momentum Oscillator hesaplar."""
    return df['close'] / df['close'].shift(period) * 100

# =============================================================
# Momentum İndikatörleri
# =============================================================

def rsi(df: pd.DataFrame, period: Optional[int] = None, column: str = "close") -> pd.Series:
    """Relative Strength Index (RSI) hesaplar."""
    period = period or CONFIG.TA.RSI_PERIOD

    delta = df[column].diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)

    avg_gain = pd.Series(gain, index=df.index).rolling(window=period).mean()
    avg_loss = pd.Series(loss, index=df.index).rolling(window=period).mean()
    rs = avg_gain / (avg_loss + 1e-12)

    return 100 - (100 / (1 + rs))

def stochastic(df: pd.DataFrame, k_period: Optional[int] = None, d_period: Optional[int] = None) -> Tuple[pd.Series, pd.Series]:
    """Stochastic Oscillator hesaplar."""
    k_period = k_period or CONFIG.TA.STOCH_K
    d_period = d_period or CONFIG.TA.STOCH_D

    low_min = df["low"].rolling(window=k_period).min()
    high_max = df["high"].rolling(window=k_period).max()
    k = 100 * (df["close"] - low_min) / (high_max - low_min + 1e-12)
    d = k.rolling(window=d_period).mean()
    return k, d

# =============================================================
# Volatilite & Risk
# =============================================================

def atr(df: pd.DataFrame, period: Optional[int] = None) -> pd.Series:
    """Average True Range (ATR) hesaplar."""
    period = period or CONFIG.TA.ATR_PERIOD

    high_low = df["high"] - df["low"]
    high_close = abs(df["high"] - df["close"].shift())
    low_close = abs(df["low"] - df["close"].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def bollinger_bands(df: pd.DataFrame, period: Optional[int] = None, column: str = "close") -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands hesaplar."""
    period = period or CONFIG.TA.BB_PERIOD

    sma = df[column].rolling(window=period).mean()
    std = df[column].rolling(window=period).std()
    upper = sma + (CONFIG.TA.BB_STDDEV * std)
    lower = sma - (CONFIG.TA.BB_STDDEV * std)
    return upper, sma, lower

def sharpe_ratio(df: pd.DataFrame, risk_free_rate: Optional[float] = None, period: Optional[int] = None, column: str = "close") -> float:
    """Sharpe Ratio hesaplar."""
    period = period or CONFIG.TA.SHARPE_PERIOD
    risk_free_rate = risk_free_rate or CONFIG.TA.SHARPE_RISK_FREE_RATE

    returns = df[column].pct_change()
    excess = returns - risk_free_rate / period
    return (excess.mean() / (excess.std() + 1e-12)) * np.sqrt(period)

def max_drawdown(df: pd.DataFrame, column: str = "close") -> float:
    """Max Drawdown hesaplar."""
    roll_max = df[column].cummax()
    drawdown = (df[column] - roll_max) / (roll_max + 1e-12)
    return drawdown.min()

def historical_volatility(df: pd.DataFrame, period: int = 30) -> pd.Series:
    """Historical Volatility (annualized, %) hesaplar."""
    log_returns = np.log(df['close'] / df['close'].shift(1))
    vol = log_returns.rolling(period).std() * np.sqrt(252) * 100
    return vol

def ulcer_index(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Ulcer Index hesaplar."""
    rolling_max = df['close'].rolling(period).max()
    drawdown = (df['close'] - rolling_max) / (rolling_max + 1e-12) * 100
    return np.sqrt((drawdown.pow(2)).rolling(period).mean())

# =============================================================
# Hacim & Likidite
# =============================================================

def obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume (OBV) hesaplar."""
    obv = (np.sign(df["close"].diff()) * df["volume"]).fillna(0).cumsum()
    return obv

def cmf(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Chaikin Money Flow (CMF) hesaplar."""
    mfm = ((df['close'] - df['low']) - (df['high'] - df['close'])) / (df['high'] - df['low'] + 1e-12)
    mfv = mfm * df['volume']
    return mfv.rolling(period).sum() / (df['volume'].rolling(period).sum() + 1e-12)

def order_book_imbalance(bids: list, asks: list) -> float:
    """Order Book Imbalance (OBI) hesaplar."""
    bid_vol = sum([b[1] for b in bids]) if bids else 0
    ask_vol = sum([a[1] for a in asks]) if asks else 0
    denom = (bid_vol + ask_vol) or 1e-12
    return (bid_vol - ask_vol) / denom

def open_interest_placeholder():
    """Open Interest için placeholder (borsa API ile entegre edilecek)."""
    return None

# =============================================================
# Market Structure
# =============================================================

def market_structure(df: pd.DataFrame) -> pd.DataFrame:
    """Market Structure High/Low (MSH/MSL) hesaplar."""
    highs = df['high']
    lows = df['low']
    structure = pd.DataFrame(index=df.index)
    structure['higher_high'] = highs > highs.shift(1)
    structure['lower_low'] = lows < lows.shift(1)
    return structure

def breakout(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Breakout (Donchian Channel tarzı) sinyal döndürür."""
    rolling_high = df['close'].rolling(period).max()
    rolling_low = df['close'].rolling(period).min()

    cond_long = df['close'] > rolling_high.shift(1)
    cond_short = df['close'] < rolling_low.shift(1)

    signal = pd.Series(index=df.index, dtype=float)
    signal[cond_long] = 1.0   # Long breakout
    signal[cond_short] = -1.0 # Short breakout
    signal.fillna(0, inplace=True)

    return signal

# =============================================================
# Binance Entegre IO Fonksiyonları
# =============================================================

async def fetch_funding_rate_binance(symbol: str = "BTCUSDT") -> float:
    """Binance'den gerçek funding rate çeker."""
    try:
        from utils.binance_api import get_binance_api
        client = get_binance_api()
        funding_data = await client.get_funding_rate(symbol, limit=1)
        return float(funding_data[0]['fundingRate']) if funding_data else 0.001
    except Exception as e:
        print(f"Funding rate çekilemedi: {e}")
        return 0.001

async def fetch_social_sentiment_binance(symbol: str = "BTC") -> Dict[str, float]:
    """Social sentiment için placeholder."""
    await asyncio.sleep(0.01)
    return {"sentiment": 0.5}

async def get_live_order_book_imbalance(symbol: str = "BTCUSDT") -> float:
    """Gerçek zamanlı order book imbalance hesaplar."""
    try:
        from utils.binance_api import get_binance_api
        client = get_binance_api()
        ob = await client.get_order_book(symbol, limit=100)
        bids = [[float(b[0]), float(b[1])] for b in ob['bids']]
        asks = [[float(a[0]), float(a[1])] for a in ob['asks']]
        return order_book_imbalance(bids, asks)
    except Exception as e:
        print(f"Order book imbalance hesaplanamadı: {e}")
        return 0.0

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
    
    from utils.binance_api import get_binance_api
    client = get_binance_api()
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
            break
        except Exception as e:
            logger.error("Trading pipeline error for %s: %s", symbol, e)  # ✅ logger kullan
            await asyncio.sleep(30)  # ❌ pipeline_interval değil, sabit bekleme

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
    config_ta = CONFIG.TA  # Cache config for performance

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
                print(f"[CPU TA ERROR] {name} hesaplanamadı: {e}")
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
            print(f"[I/O TA ERROR] {name} hesaplanamadı: {res}")
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
    return pd.Series(out, index=prices.index, name="kalman")

def _hilbert_fallback(x: np.ndarray) -> np.ndarray:
    n = len(x)
    Xf = np.fft.fft(x)
    h = np.zeros(n)
    if n % 2 == 0:
        h[0] = 1; h[n//2] = 1; h[1:n//2] = 2
    else:
        h[0] = 1; h[1:(n+1)//2] = 2
    return np.fft.ifft(Xf * h)

def hilbert_features(prices: pd.Series) -> dict:
    x = prices.fillna(method="ffill").values.astype(float)
    try:
        from scipy.signal import hilbert
        analytic = hilbert(x)
    except Exception:
        analytic = _hilbert_fallback(x)
    amp = np.abs(analytic)
    phase = np.unwrap(np.angle(analytic))
    inst_freq = np.diff(phase, prepend=phase[0])
    return {
        "amp": pd.Series(amp, index=prices.index),
        "inst_freq": pd.Series(inst_freq, index=prices.index),
    }

def _phi(m: int, r: float, series: np.ndarray) -> float:
    n = len(series)
    if n <= m + 1:
        return np.inf
    x = np.array([series[i:i+m] for i in range(n - m + 1)])
    d = np.max(np.abs(x[:, None, :] - x[None, :, :]), axis=2)
    C = (d <= r).sum(axis=1) / (n - m + 1)
    C = C[C > 0]
    return np.sum(np.log(C)) / (len(C) + 1e-12) if len(C) else np.inf

def approximate_entropy(series: pd.Series, m: Optional[int] = None, r: Optional[float] = None) -> float:
    s = series.dropna().values.astype(float)
    if len(s) < 5:
        return np.nan
    m = CONFIG.TA.ENTROPY_M if m is None else m
    if r is None:
        r = CONFIG.TA.ENTROPY_R_FACTOR * np.std(s)
    return float(_phi(m, r, s) - _phi(m+1, r, s))

def sample_entropy(series: pd.Series, m: Optional[int] = None, r: Optional[float] = None) -> float:
    s = series.dropna().values.astype(float)
    if len(s) < 5:
        return np.nan
    m = CONFIG.TA.ENTROPY_M if m is None else m
    if r is None:
        r = CONFIG.TA.ENTROPY_R_FACTOR * np.std(s)
    n = len(s)
    xm = np.array([s[i:i+m] for i in range(n-m)])
    xm1 = np.array([s[i:i+m+1] for i in range(n-m-1)])
    def count_sim(x, tol):
        d = np.max(np.abs(x[:, None, :] - x[None, :, :]), axis=2)
        return (d <= tol).sum() - len(x)
    B = count_sim(xm, r)
    A = count_sim(xm1, r)
    if B <= 0 or A <= 0:
        return np.nan
    return float(-np.log(A / B))

def permutation_entropy(series: pd.Series, m: Optional[int] = None) -> float:
    s = series.dropna().values.astype(float)
    m = 3 if m is None else m
    if len(s) < m:
        return np.nan
    patterns = {}
    for i in range(len(s) - m + 1):
        pat = tuple(np.argsort(s[i:i+m]))
        patterns[pat] = patterns.get(pat, 0) + 1
    p = np.array(list(patterns.values()), dtype=float)
    p /= p.sum()
    return float(-np.sum(p * np.log(p + 1e-12)) / np.log(math.factorial(m)))

def detect_regime(df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
    """
    Basit rejim skoru: trendiness ~ trend_z - penalty(vol_z)
    """
    window = CONFIG.TA.REGIME_WINDOW if window is None else window
    px = df["close"].astype(float)
    if len(px) < window + 5:
        return pd.Series([0.0] * len(px), index=px.index, name="regime_score")
    ret = px.pct_change()
    vol = ret.rolling(window).std()
    def slope(x):
        idx = np.arange(len(x))
        b, a = np.polyfit(idx, x, 1)
        return b
    trend = px.rolling(window).apply(slope, raw=True)
    def zscore(s):
        m = s.rolling(window).mean()
        sd = s.rolling(window).std()
        return (s - m) / (sd + 1e-9)
    trend_z = zscore(trend).fillna(0).clip(-3,3)/3.0
    vol_z = zscore(vol).fillna(0).clip(-3,3)/3.0
    score = trend_z - 0.5*np.maximum(vol_z-0.5, 0.0)
    return score.fillna(0.0).rename("regime_score")

def leadlag_xcorr(target: pd.Series, reference: pd.Series, max_lag: Optional[int] = None) -> dict:
    """
    target ve reference getirileri arasında maksimum korelasyonu bulur.
    """
    max_lag = CONFIG.TA.LEADLAG_MAX_LAG if max_lag is None else max_lag
    x = target.pct_change().dropna().values
    y = reference.pct_change().dropna().values
    L = min(len(x), len(y))
    if L < max_lag + 5:
        return {"lag": 0, "corr": 0.0, "score": 0.0}
    x = x[-L:]; y = y[-L:]
    best_corr, best_lag = 0.0, 0
    for lag in range(-max_lag, max_lag+1):
        if lag < 0:
            corr = np.corrcoef(x[:lag], y[-lag:])[0,1]
        elif lag > 0:
            corr = np.corrcoef(x[lag:], y[:-lag])[0,1]
        else:
            corr = np.corrcoef(x, y)[0,1]
        if np.isfinite(corr) and abs(corr) > abs(best_corr):
            best_corr, best_lag = float(corr), lag
    score = float(np.tanh(best_corr))
    return {"lag": int(best_lag), "corr": float(best_corr), "score": score}

def compute_alpha_ta(df: pd.DataFrame, ref_series: Optional[pd.Series] = None) -> dict:
    """
    Döndürür: {"score": float, "detail": {...}, "series": {...}}
    """
    try:
        px = df["close"].astype(float)
        
        # Kalman
        kf = kalman_filter_series(px)
        kf_err = (px - kf).rolling(20).std()
        kf_score = float(np.tanh((kf.diff().iloc[-1]) / (float(kf_err.iloc[-1]) + 1e-9))) if len(kf) > 21 else 0.0

        # Hilbert
        h = hilbert_features(px)
        hf = h["inst_freq"].rolling(10).mean()
        ha = h["amp"].rolling(10).mean()
        hilbert_raw = (hf.diff().iloc[-1] if len(hf) > 1 else 0.0)
        hilbert_penalty = float(np.tanh((ha.pct_change().rolling(10).std().iloc[-1] if len(ha) > 11 else 0.0)))
        hilbert_score = float(np.tanh(hilbert_raw)) * (1.0 - 0.3*abs(hilbert_penalty))

        # Entropy
        apen = approximate_entropy(px)
        samp = sample_entropy(px)
        pent = permutation_entropy(px)
        ent_vals = np.array([v for v in [apen, samp, pent] if isinstance(v, (int,float)) and np.isfinite(v)])
        if len(ent_vals):
            ent_z = (ent_vals - np.nanmean(ent_vals)) / (np.nanstd(ent_vals) + 1e-9)
            entropy_score = float(np.tanh(-ent_z.mean()))
        else:
            entropy_score = 0.0

        # Rejim
        reg = detect_regime(df)
        regime_score = float(np.clip(reg.iloc[-1] if len(reg) else 0.0, -1.0, 1.0))

        # Lead-Lag (opsiyonel)
        if isinstance(ref_series, pd.Series) and len(ref_series) >= len(px)//2:
            ll = leadlag_xcorr(px, ref_series)
            leadlag_score = ll["score"]
            leadlag_detail = ll
        else:
            leadlag_score = 0.0
            leadlag_detail = {"lag": 0, "corr": 0.0, "score": 0.0}

        # Ağırlıklı birleşim
        w = CONFIG.TA
        score = (
            w.W_KALMAN   * kf_score +
            w.W_HILBERT  * hilbert_score +
            w.W_ENTROPY  * entropy_score +
            w.W_REGIME   * regime_score +
            w.W_LEADLAG  * leadlag_score
        )
        
        # Normalize weights
        total_w = w.W_KALMAN + w.W_HILBERT + w.W_ENTROPY + w.W_REGIME + w.W_LEADLAG
        if total_w != 0:
            score = score / total_w
        score = float(np.clip(score, -1.0, 1.0))

        return {
            "score": score,
            "detail": {
                "kalman_score": kf_score,
                "hilbert_score": hilbert_score,
                "entropy_score": entropy_score,
                "regime_score": regime_score,
                "leadlag": leadlag_detail,
            },
            "series": {
                "kalman": kf,
                "regime_score": reg,
            }
        }
    except Exception as e:
        print(f"[ALPHA_TA ERROR] {e}")
        return {"score": 0.0, "detail": {}, "series": {}}

def alpha_signal(df: pd.DataFrame, ref_series: Optional[pd.Series] = None) -> dict:
    res = compute_alpha_ta(df, ref_series=ref_series)
    s = res["score"]
    if s >= CONFIG.TA.ALPHA_LONG_THRESHOLD:
        sig = 1
    elif s <= CONFIG.TA.ALPHA_SHORT_THRESHOLD:
        sig = -1
    else:
        sig = 0
    res["signal"] = sig
    return res

# Registry'ye alpha_ta/alpha_signal ekleri
CPU_FUNCTIONS["alpha_ta"] = lambda df: compute_alpha_ta(df)
TA_FUNCTIONS["alpha_signal"] = lambda df: alpha_signal(df)

# =============================================================
# Optimized Market Scan & Signal Generation
# =============================================================

def generate_signals(df: pd.DataFrame, ref_series: Optional[pd.Series] = None) -> dict:
    """
    Basit klasik TA kararı + alpha_ta sinyali beraber.
    """
    try:
        indicators: Dict[str, float] = {}

        # Trend: EMA
        ema_fast = ema(df, period=CONFIG.TA.EMA_PERIODS[0])
        ema_slow = ema(df, period=CONFIG.TA.EMA_PERIODS[1])
        indicators["ema_fast"] = float(ema_fast.iloc[-1])
        indicators["ema_slow"] = float(ema_slow.iloc[-1])
        ema_signal = 1 if ema_fast.iloc[-1] > ema_slow.iloc[-1] else -1

        # MACD
        macd_line, signal_line, _ = macd(df)
        macd_val = float(macd_line.iloc[-1] - signal_line.iloc[-1])
        indicators["macd"] = macd_val
        macd_signal = 1 if macd_val > 0 else -1

        # Momentum: RSI
        rsi_val = float(rsi(df).iloc[-1])
        indicators["rsi"] = rsi_val
        rsi_signal = 1 if rsi_val < 30 else (-1 if rsi_val > 70 else 0)

        # Volatilite: ATR
        indicators["atr"] = float(atr(df).iloc[-1])

        # Hacim: OBV
        obv_series = obv(df)
        obv_val = float(obv_series.iloc[-1])
        indicators["obv"] = obv_val
        obv_signal = 1 if len(obv_series) > 20 and obv_val > float(obv_series.iloc[-20]) else -1

        weights = {"ema":0.3, "macd":0.3, "rsi":0.2, "obv":0.2}
        score = (ema_signal*weights["ema"] + macd_signal*weights["macd"] +
                 rsi_signal*weights["rsi"] + obv_signal*weights["obv"])

        if score >= CONFIG.TA.ALPHA_LONG_THRESHOLD:
            signal = 1
        elif score <= CONFIG.TA.ALPHA_SHORT_THRESHOLD:
            signal = -1
        else:
            signal = 0

        # alpha_ta
        alpha = alpha_signal(df, ref_series=ref_series)
        return {
            "signal": signal,
            "score": round(float(score), 4),
            "indicators": indicators,
            "alpha_ta": alpha
        }
    except Exception as e:
        print(f"[SIGNAL ERROR] Sinyal hesaplanamadı: {e}")
        return {"signal": 0, "score": 0.0, "indicators": {}, "alpha_ta": {"score": 0.0, "signal": 0}}

async def optimized_scan_market(market_data: Dict[str, pd.DataFrame], 
                               ref_series: Optional[pd.Series] = None,
                               max_workers: Optional[int] = None) -> dict:
    """
    Çoklu sembol taramasını paralel yapar.
    """
    results: Dict[str, dict] = {}
    max_workers = max_workers or _get_max_workers(default=2)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for symbol, df in market_data.items():
            if not isinstance(df, pd.DataFrame) or df.empty:
                results[symbol] = {"alpha_ta": {"score": 0.0, "signal": 0}}
                continue
            
            futures[executor.submit(alpha_signal, df, ref_series)] = symbol
        
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                results[symbol] = future.result()
            except Exception as e:
                print(f"[SCAN ERROR] {symbol}: {e}")
                results[symbol] = {"alpha_ta": {"score": 0.0, "signal": 0}}
    
    return results

def scan_market(market_data: Dict[str, pd.DataFrame], ref_close: Optional[pd.Series] = None) -> dict:
    """
    Çoklu sembol taraması (sync wrapper).
    """
    return _run_asyncio(optimized_scan_market(market_data, ref_close))

# =============================================================
# Health Check & Monitoring
# =============================================================

def get_ta_metrics() -> Dict[str, Any]:
    """TA sistem metriklerini döndürür."""
    return {
        "calculations": ta_metrics.__dict__,
        "cache_stats": ta_cache.get_stats(),
        "timestamp": time.time()
    }

def reset_ta_metrics():
    """TA metriklerini sıfırlar."""
    global ta_metrics
    ta_metrics = TAMetrics()

# =============================================================
# Utility Functions
# =============================================================

def klines_to_dataframe(klines: List[List[Any]]) -> pd.DataFrame:
    """Binance klines verisini pandas DataFrame'e dönüştürür."""
    df = pd.DataFrame(klines, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ])
    
    numeric_cols = ['open', 'high', 'low', 'close', 'volume']
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    df.set_index('open_time', inplace=True)
    return df[['open', 'high', 'low', 'close', 'volume']]

async def health_check() -> Dict[str, Any]:
    """Sistem sağlık durumunu kontrol eder."""
    try:
        from utils.binance_api import get_binance_api
        client = get_binance_api()
        http_metrics = client.get_http_metrics()
        ws_metrics = client.get_ws_metrics()
    except:
        http_metrics = {}
        ws_metrics = {}
    
    return {
        "http_metrics": http_metrics.__dict__ if hasattr(http_metrics, '__dict__') else http_metrics,
        "ws_metrics": ws_metrics.__dict__ if hasattr(ws_metrics, '__dict__') else ws_metrics,
        "ta_metrics": get_ta_metrics(),
        "timestamp": time.time(),
        "status": "healthy"

    }


# Hata yönetimi
def rsi(arr: list, period: int = 14) -> list:
    """Calculate RSI for a price array"""
    try:
        if len(arr) < period + 1:
            return []
            
        gains = []
        losses = []
        
        for i in range(1, len(arr)):
            change = arr[i] - arr[i-1]
            gains.append(change if change > 0 else 0)
            losses.append(-change if change < 0 else 0)
        
        # Calculate first average gains and losses
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        rsi_values = []
        for i in range(period, len(gains)):
            if avg_loss == 0:
                rsi = 100
            else:
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
            
            rsi_values.append(rsi)
            
            # Update averages
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
        return rsi_values
    except Exception as e:
        logging.error(f"RSI calculation error: {e}")
        return []

def macd(arr: list, fast_period: int = 12, slow_period: int = 26, signal_period: int = 9) -> tuple:
    """Calculate MACD for a price array"""
    try:
        if len(arr) < slow_period + signal_period:
            return [], [], []
            
        # Calculate EMAs
        ema_fast = _calculate_ema(arr, fast_period)
        ema_slow = _calculate_ema(arr, slow_period)
        
        # Calculate MACD line
        macd_line = [ema_fast[i] - ema_slow[i] for i in range(len(ema_fast))]
        
        # Calculate Signal line (EMA of MACD line)
        signal_line = _calculate_ema(macd_line, signal_period)
        
        # Calculate Histogram
        histogram = [macd_line[i] - signal_line[i] for i in range(len(signal_line))]
        
        return macd_line, signal_line, histogram
    except Exception as e:
        logging.error(f"MACD calculation error: {e}")
        return [], [], []

def _calculate_ema(prices: list, period: int) -> list:
    """Calculate Exponential Moving Average"""
    if len(prices) < period:
        return []
    
    ema_values = []
    multiplier = 2 / (period + 1)
    
    # First EMA is SMA
    sma = sum(prices[:period]) / period
    ema_values.append(sma)
    
    # Calculate subsequent EMAs
    for i in range(period, len(prices)):
        ema = (prices[i] - ema_values[-1]) * multiplier + ema_values[-1]
        ema_values.append(ema)
    
    return ema_values
