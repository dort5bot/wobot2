#io_utils.py

import math
import statistics
import time
from typing import Dict, Any, List, Optional

from utils.config import CONFIG  # ✅ config entegre

# ===============================
# --- Yardımcı Hesaplamalar ---
# ===============================

def safe_mean(values: List[float]) -> Optional[float]:
    try:
        return statistics.mean(values) if values else None
    except Exception:
        return None


def calc_momentum(klines: List[List[Any]]) -> Optional[float]:
    """RSI / momentum (config RSI_PERIOD)"""
    try:
        period = CONFIG.IO.RSI_PERIOD
        close_prices = [float(k[4]) for k in klines[-period:]]
        if len(close_prices) < 2:
            return None
        gains = [max(0, close_prices[i] - close_prices[i-1]) for i in range(1, len(close_prices))]
        losses = [max(0, close_prices[i-1] - close_prices[i]) for i in range(1, len(close_prices))]
        avg_gain = safe_mean(gains) or 0.0
        avg_loss = safe_mean(losses) or 0.0
        rs = avg_gain / avg_loss if avg_loss > 0 else float("inf")
        return 100 - (100 / (1 + rs))
    except Exception:
        return None


def calc_volatility(klines: List[List[Any]]) -> Optional[float]:
    try:
        closes = [float(k[4]) for k in klines]
        return statistics.pstdev(closes) if closes else None
    except Exception:
        return None


def calc_obi(order_book: Dict[str, Any]) -> Optional[float]:
    """Order Book Imbalance (config OBI_DEPTH ile sınırlı)"""
    try:
        depth = CONFIG.IO.OBI_DEPTH
        bids = sum(float(b[1]) for b in order_book.get("bids", [])[:depth])
        asks = sum(float(a[1]) for a in order_book.get("asks", [])[:depth])
        return (bids - asks) / (bids + asks) if (bids + asks) > 0 else None
    except Exception:
        return None


def calc_liquidity_layers(order_book: Dict[str, Any], price: float, pct_levels=None):
    """Derinlikte likidite yoğunluğu"""
    if pct_levels is None:
        pct_levels = [0.01, 0.02, 0.05]
    layers = {}
    try:
        for p in pct_levels:
            bid_layer = sum(float(b[1]) for b in order_book.get("bids", []) if float(b[0]) >= price * (1 - p))
            ask_layer = sum(float(a[1]) for a in order_book.get("asks", []) if float(a[0]) <= price * (1 + p))
            layers[f"layer_{int(p*100)}"] = {"bids": bid_layer, "asks": ask_layer}
    except Exception:
        pass
    return layers


def calc_taker_ratio(trades: List[Dict[str, Any]]) -> Optional[float]:
    try:
        buys = sum(float(t["qty"]) for t in trades if not t.get("isBuyerMaker"))
        sells = sum(float(t["qty"]) for t in trades if t.get("isBuyerMaker"))
        total = buys + sells
        return (buys - sells) / total if total > 0 else None
    except Exception:
        return None


def calc_vwap_taker_ratio(trades: List[Dict[str, Any]]) -> Optional[float]:
    try:
        buy_notional = sum(float(t["qty"]) * float(t.get("price", 0)) for t in trades if not t.get("isBuyerMaker"))
        sell_notional = sum(float(t["qty"]) * float(t.get("price", 0)) for t in trades if t.get("isBuyerMaker"))
        total = buy_notional + sell_notional
        return (buy_notional - sell_notional) / total if total > 0 else None
    except Exception:
        return None


def normalize_funding(funding_rate: float) -> Optional[float]:
    try:
        return (funding_rate - CONFIG.IO.FUNDING_AVG) / CONFIG.IO.FUNDING_STD if CONFIG.IO.FUNDING_STD != 0 else None
    except Exception:
        return None


def normalize_oi(oi: Optional[float]) -> Optional[float]:
    try:
        return oi / CONFIG.IO.OI_BASELINE if oi else None
    except Exception:
        return None


def normalize_liquidations(liquidations: Optional[float]) -> Optional[float]:
    try:
        return liquidations / CONFIG.IO.LIQUIDATION_BASELINE if liquidations else None
    except Exception:
        return None


# ===============================
# --- Çoklu Zaman Dilimi Cashflow ---
# ===============================

def calc_cashflow_ratios(trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Optional[float]]]:
    now = int(time.time() * 1000)
    ratios = {}
    for label, minutes in CONFIG.IO.CASHFLOW_TIMEFRAMES.items():
        cutoff = now - minutes * 60 * 1000
        sub_trades = [t for t in trades if t.get("ts", now) >= cutoff]
        ratios[label] = {
            "taker_ratio": calc_taker_ratio(sub_trades),
            "vwap_taker_ratio": calc_vwap_taker_ratio(sub_trades)
        }
    return {"ratios": ratios}


# ===============================
# --- IO Snapshot Builder ---
# ===============================

def build_io_snapshot(
    symbol: str,
    klines,
    order_book,
    trades,
    ticker,
    funding,
    oi: Optional[float] = None,
    liquidations: Optional[float] = None,
    with_cashflow: bool = True
):
    momentum = calc_momentum(klines)
    volatility = calc_volatility(klines)
    obi = calc_obi(order_book)
    liquidity_layers = calc_liquidity_layers(order_book, float(ticker.get("lastPrice", 0)))
    taker_ratio = calc_taker_ratio(trades)
    vwap_taker_ratio = calc_vwap_taker_ratio(trades)
    funding_rate = float(funding.get("fundingRate", 0))
    funding_norm = normalize_funding(funding_rate)
    oi_norm = normalize_oi(oi)
    liq_norm = normalize_liquidations(liquidations)

    trend_score = safe_mean([
        (momentum / 100 if momentum is not None else 0),
        (funding_norm if funding_norm is not None else 0),
    ]) or 0

    liquidity_score = safe_mean([
        (obi if obi is not None else 0),
        (vwap_taker_ratio if vwap_taker_ratio is not None else 0),
    ]) or 0

    risk_score = safe_mean([
        (volatility if volatility is not None else 0),
        (liq_norm if liq_norm is not None else 0),
    ]) or 0

    mts_score = trend_score + liquidity_score - risk_score

    snapshot = {
        "symbol": symbol,
        "momentum": momentum,
        "volatility": volatility,
        "obi": obi,
        "liquidity_layers": liquidity_layers,
        "taker_ratio": taker_ratio,
        "vwap_taker_ratio": vwap_taker_ratio,
        "funding_rate": funding_rate,
        "funding_norm": funding_norm,
        "open_interest": oi,
        "open_interest_norm": oi_norm,
        "liquidations": liquidations,
        "liquidations_norm": liq_norm,
        "trend_score": trend_score,
        "liquidity_score": liquidity_score,
        "risk_score": risk_score,
        "mts_score": mts_score,
    }

    if with_cashflow:
        snapshot.update(calc_cashflow_ratios(trades))

    return snapshot


# ===============================
# --- Çoklu Sembol Snapshot ---
# ===============================

def build_multi_snapshot(symbols_data: Dict[str, Dict[str, Any]], with_cashflow: bool = True) -> Dict[str, Any]:
    result = {}
    for symbol, data in symbols_data.items():
        snapshot = build_io_snapshot(
            symbol=symbol,
            klines=data.get("klines", []),
            order_book=data.get("order_book", {}),
            trades=data.get("trades", []),
            ticker=data.get("ticker", {}),
            funding=data.get("funding", {}),
            oi=data.get("oi"),
            liquidations=data.get("liquidations"),
            with_cashflow=with_cashflow
        )
        result[symbol] = snapshot
    return result
