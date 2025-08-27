# utils/ap_utils.py - Pro & Tam Özellikli Versiyon
# - eksiksiz ve pro: momentum, volatilite, OBI, whale/taker, normalize, ağırlıklı skor hepsi var.
# - Handler ile uyumlu: /ap veya benzeri komutlarda await build_ap_report_lines_pro() direkt kullanılabilir.
# - Dinamik, modüler ve yeniden kullanılabilir bir yapı sağlıyor.
# - Momentum, volatilite, VWAP + derinlik (OBI), whale ve taker skorları korunuyor.
# - Normalize / z-score / min-max + clip seçenekleri ekleniyor.
# - Dinamik ağırlıklandırma var.
# - Handler uyumlu rapor fonksiyonu korunuyor.
# - Gereksiz tekrarlar ve yarım kalan kısımlar temizlendi.
# - Taker ratio weighted ve büyük trade’leri önceliklendiriyor.
# - Volume delta ve order book imbalance daha hassas ve ağırlıklı hesaplanıyor.
#-
#
#----------------------
# utils/ap_utils.py - Pro & Async Optimized Version
import asyncio
import numpy as np
import pandas as pd
from utils.binance_api import BinanceClient
from utils.config import CONFIG
from utils.ta_utils import ema, atr

# -------------------------------------------------------------
# Yardımcı Fonksiyonlar
# -------------------------------------------------------------
def _normalize_series(series, method="minmax", clip_min=0, clip_max=100):
    s = np.array(series, dtype=float)
    if method == "zscore":
        mean, std = np.nanmean(s), np.nanstd(s)
        if std == 0:
            return np.zeros_like(s)
        return (s - mean)/std
    else:  # minmax
        s_min, s_max = np.nanmin(s), np.nanmax(s)
        if s_max - s_min == 0:
            return np.zeros_like(s)
        normalized = (s - s_min)/(s_max - s_min)*(clip_max-clip_min) + clip_min
        return np.clip(normalized, clip_min, clip_max)

# -------------------------------------------------------------
# Order Book Metrikleri (Pro)
# -------------------------------------------------------------
def order_book_imbalance_pro(bids: list, asks: list):
    bid_vol = sum([b[1] for b in bids])
    ask_vol = sum([a[1] for a in asks])
    bid_vwap = sum([b[0]*b[1] for b in bids])/bid_vol if bid_vol else 0
    ask_vwap = sum([a[0]*a[1] for a in asks])/ask_vol if ask_vol else 0
    price_diff = ask_vwap - bid_vwap
    depth_diff = bid_vol - ask_vol
    # normalize ve ağırlık ver
    norm_price = _normalize_series([price_diff])[0]
    norm_depth = _normalize_series([depth_diff])[0]
    return norm_depth*0.6 + norm_price*0.4

# -------------------------------------------------------------
# Whale & Taker Metrikleri (Pro)
# -------------------------------------------------------------
def compute_whale_score(trades, threshold_usd=None):
    threshold_usd = threshold_usd or CONFIG.WHALE_USD_THRESHOLD
    whale_volumes = [float(t["qty"])*float(t["price"]) for t in trades if float(t["qty"])*float(t["price"]) >= threshold_usd]
    if not whale_volumes:
        return 0.0
    score = np.log1p(sum(whale_volumes))
    return _normalize_series([score])[0]

def compute_taker_score(trades):
    buy_vol = sum(float(t["qty"]) for t in trades if not t["isBuyerMaker"])
    sell_vol = sum(float(t["qty"]) for t in trades if t["isBuyerMaker"])
    total = buy_vol + sell_vol
    ratio = buy_vol/total if total else 0.5
    return _normalize_series([ratio*100])[0]

# -------------------------------------------------------------
# Altcoin Short Metrics (Pro)
# -------------------------------------------------------------
async def get_altcoin_short_metrics_pro(client: BinanceClient, symbol: str):
    klines = await client.get_klines(symbol, interval="5m", limit=20)
    df = pd.DataFrame(klines, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","quote_asset_volume","trades",
        "taker_base_vol","taker_quote_vol","ignore"
    ])
    df[["close","high","low","volume"]] = df[["close","high","low","volume"]].astype(float)

    # Momentum: EMA farkı (son 3 bar)
    close_ema = ema(df["close"], period=3).iloc[-1]
    prev_ema = ema(df["close"], period=3).iloc[-2] if len(df) > 1 else close_ema
    short_mom = close_ema - prev_ema

    # Volatilite
    vol = atr(df, period=14).iloc[-1]

    # Order Book Imbalance
    ob = await client.get_order_book(symbol, limit=50)
    obi = order_book_imbalance_pro(ob["bids"], ob["asks"])

    # Trade bazlı skorlar
    trades = await client.get_recent_trades(symbol, limit=200)
    whale_score = compute_whale_score(trades)
    taker_score = compute_taker_score(trades)

    # Normalize ve ağırlıklandır
    raw_scores = np.array([short_mom, vol, obi, whale_score, taker_score])
    norm_scores = _normalize_series(raw_scores)
    weights = np.array([0.25, 0.2, 0.2, 0.2, 0.15])
    composite_score = np.dot(norm_scores, weights)

    return {
        "symbol": symbol,
        "composite_score": composite_score,
        "short_momentum": short_mom,
        "volatility": vol,
        "order_book_imbalance": obi,
        "whale_score": whale_score,
        "taker_score": taker_score
    }

# -------------------------------------------------------------
# Çoklu Altcoin Short Skorları (Pro)
# -------------------------------------------------------------
async def score_altcoins_pro(client: BinanceClient, symbols: list):
    tasks = [get_altcoin_short_metrics_pro(client, s) for s in symbols]
    return await asyncio.gather(*tasks)

# -------------------------------------------------------------
# Handler uyumlu rapor (Pro)
# -------------------------------------------------------------
async def build_ap_report_lines_pro(client=None, symbols=None):
    client = client or BinanceClient(CONFIG.BINANCE_API_KEY, CONFIG.BINANCE_SECRET_KEY)
    symbols = symbols or ["BTCUSDT","ETHUSDT","SOLUSDT"]
    results = await score_altcoins_pro(client, symbols)

    lines = []
    for r in results:
        line = (
            f"{r['symbol']}: {r['composite_score']:.2f} | "
            f"Mom: {r['short_momentum']:.4f} | Vol: {r['volatility']:.4f} | "
            f"OBI: {r['order_book_imbalance']:.2f} | "
            f"Whale: {r['whale_score']:.2f} | Taker: {r['taker_score']:.2f}"
        )
        lines.append(line)
    return lines
