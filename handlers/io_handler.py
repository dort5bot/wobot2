# io_handler.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import math
import time
from typing import Dict, Any, List, Optional, Tuple

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from utils.config import CONFIG
from utils.binance_api import get_binance_api
from utils import io_utils

# =========================
# --- Utils ---------------
# =========================

ARROW_UP = "🔼"
ARROW_DOWN = "🔻"
ARROW_NEUTRAL = "➖"
ARROW_NEUTRAL_X = "✖️"

def _buyers_percent_from_taker_ratio(taker_ratio: Optional[float]) -> Optional[float]:
    if taker_ratio is None:
        return None
    return max(0.0, min(100.0, (1.0 + taker_ratio) * 50.0))

def _arrow_from_ratio(val: Optional[float], up_eps: float = 0.02, down_eps: float = -0.02) -> str:
    if val is None:
        return ARROW_NEUTRAL_X
    if val > up_eps:
        return ARROW_UP
    if val < down_eps:
        return ARROW_DOWN
    return ARROW_NEUTRAL

def _fmt_pct(x: Optional[float], nd=1) -> str:
    if x is None or math.isnan(x):
        return "—"
    return f"%{x:.{nd}f}".replace(".", ",")

def _fmt_ratio_as_power(x: Optional[float]) -> str:
    if x is None or math.isnan(x):
        return "—"
    power = max(0.1, min(2.0, 1.0 + x))
    return f"{power:.1f}X".replace(".", ",")

def _symbolize(arg: str) -> str:
    s = arg.upper()
    if s.endswith(CONFIG.IO.QUOTE_ASSET):
        return s
    return f"{s}{CONFIG.IO.QUOTE_ASSET}"

def _now_ms() -> int:
    return int(time.time() * 1000)

# =========================
# --- Data Fetch ----------
# =========================

async def _get_dynamic_usdt_symbols(api, max_symbols: int) -> List[str]:
    ex = await api.exchange_info_details()
    usdt_symbols: List[str] = [
        s["symbol"] for s in ex.get("symbols", [])
        if s.get("status") == "TRADING" and s.get("quoteAsset") == CONFIG.IO.QUOTE_ASSET
    ]

    tickers = await api.get_all_24h_tickers()
    vol_map: Dict[str, float] = {}
    for t in tickers:
        sym = t.get("symbol")
        if sym in usdt_symbols:
            try:
                vol_map[sym] = float(t.get("quoteVolume", 0.0))
            except Exception:
                vol_map[sym] = 0.0

    ranked = sorted(usdt_symbols, key=lambda s: vol_map.get(s, 0.0), reverse=True)
    return ranked[:max_symbols]

async def _resolve_market_symbol_list(api) -> List[str]:
    raw = getattr(CONFIG.BINANCE, "TOP_SYMBOLS_FOR_IO", ["BTCUSDT", "ETHUSDT"])
    if isinstance(raw, str):
        raw = [s.strip().upper() for s in raw.split(",") if s.strip()]

    if any(s in ("AUTO", "ALL") for s in raw):
        return await _get_dynamic_usdt_symbols(api, CONFIG.IO.MAX_SYMBOLS_MARKET)

    return [s.upper() for s in raw]

async def _fetch_symbol_pack(symbol: str) -> Dict[str, Any]:
    api = get_binance_api()
    kl = await api.get_klines(symbol, interval=CONFIG.BINANCE.STREAM_INTERVAL, limit=200)
    ob = await api.get_order_book(symbol, limit=100)
    tr = await api.get_recent_trades(symbol, limit=CONFIG.BINANCE.TRADES_LIMIT)
    tk = await api.get_24h_ticker(symbol)

    try:
        fr = await api.get_funding_rate(symbol, limit=1)
        funding = fr[0] if isinstance(fr, list) and fr else {"fundingRate": 0}
    except Exception:
        funding = {"fundingRate": 0}

    now = _now_ms()
    norm_trades = []
    for t in tr:
        t2 = dict(t)
        t2.setdefault("ts", now)
        norm_trades.append(t2)

    return {
        "klines": kl,
        "order_book": ob,
        "trades": norm_trades,
        "ticker": tk,
        "funding": funding,
        "oi": None,
        "liquidations": None,
    }

async def _build_snapshot(symbol: str) -> Dict[str, Any]:
    data = await _fetch_symbol_pack(symbol)
    return io_utils.build_io_snapshot(
        symbol=symbol,
        klines=data["klines"],
        order_book=data["order_book"],
        trades=data["trades"],
        ticker=data["ticker"],
        funding=data["funding"],
        oi=data["oi"],
        liquidations=data["liquidations"],
        with_cashflow=True,
    )

async def _build_snapshots(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    api = get_binance_api()
    packs: Dict[str, Dict[str, Any]] = await api.fetch_many(_fetch_symbol_pack, symbols)
    result: Dict[str, Dict[str, Any]] = {}
    for sym, dat in packs.items():
        if isinstance(dat, Exception):
            continue
        result[sym] = io_utils.build_io_snapshot(
            symbol=sym,
            klines=dat.get("klines", []),
            order_book=dat.get("order_book", {}),
            trades=dat.get("trades", []),
            ticker=dat.get("ticker", {}),
            funding=dat.get("funding", {}),
            oi=dat.get("oi"),
            liquidations=dat.get("liquidations"),
            with_cashflow=True,
        )
    return result

# =========================
# --- Formatting ----------
# =========================

def _format_timeframes_line(ratios: Dict[str, Dict[str, Optional[float]]]) -> List[str]:
    out = []
    order = ["15m", "1h", "4h", "12h", "1d"]
    rmap = ratios.get("ratios", {})
    for k in order:
        v = rmap.get(k, {})
        tr = v.get("taker_ratio")
        pct = _buyers_percent_from_taker_ratio(tr)
        arrow = _arrow_from_ratio(tr)
        out.append(f"{k}=> {_fmt_pct(pct)} {arrow}")
    return out

def _calc_group_volume_share(tickers: Dict[str, Dict[str, Any]], group: Optional[List[str]] = None) -> float:
    all_total = 0.0
    grp_total = 0.0
    for sym, tk in tickers.items():
        try:
            qv = float(tk.get("quoteVolume", 0.0))
        except Exception:
            qv = 0.0
        all_total += qv
        if group is None or sym in group:
            grp_total += qv
    if all_total <= 0:
        return 0.0
    return 100.0 * grp_total / all_total

def _build_cash_migration_table(snaps: Dict[str, Dict[str, Any]]) -> List[Tuple[str, float, float, float, str]]:
    vol_map = {sym: float(s.get("ticker", {}).get("quoteVolume", 0.0) or 0.0) for sym, s in snaps.items()}
    rows: List[Tuple[str, float, float, float, str]] = []
    weight_raw = {}
    for sym, s in snaps.items():
        ratios = s.get("ratios", {})
        tr15 = ratios.get("15m", {}).get("taker_ratio")
        buyers15 = _buyers_percent_from_taker_ratio(tr15) or 0.0
        mts = float(s.get("mts_score", 0.0))
        pattern = "".join(_arrow_from_ratio(ratios.get(k, {}).get("taker_ratio")) for k in ["15m","1h","4h","12h","1d"])
        weight_raw[sym] = (buyers15 / 100.0) * vol_map.get(sym, 0.0)
        rows.append((sym, 0.0, buyers15, mts, pattern))
    total_w = sum(weight_raw.values()) or 1.0
    final_rows = []
    for sym, _, buyers15, mts, pattern in rows:
        cash_share = 100.0 * weight_raw[sym] / total_w
        final_rows.append((sym, cash_share, buyers15, mts, pattern))
    final_rows.sort(key=lambda r: (round(r[3], 4), round(r[1], 4)), reverse=True)
    return final_rows

def _format_market_report(snaps: Dict[str, Dict[str, Any]]) -> str:
    vwap15_list = [s.get("ratios", {}).get("15m", {}).get("vwap_taker_ratio") for s in snaps.values()]
    avg_vwap15 = io_utils.safe_mean([v for v in vwap15_list if v is not None]) or 0.0
    short_power = _fmt_ratio_as_power(avg_vwap15)
    tickers = {s: d.get("ticker", {}) for s, d in snaps.items()}
    vol_share = _calc_group_volume_share(tickers)
    merged_ratios: Dict[str, Dict[str, Optional[float]]] = {"ratios": {}}
    for tf in CONFIG.IO.CASHFLOW_TIMEFRAMES.keys():
        vals = [s.get("ratios", {}).get(tf, {}).get("taker_ratio") for s in snaps.values()]
        merged_ratios["ratios"][tf] = {"taker_ratio": io_utils.safe_mean([v for v in vals if v is not None])}
    tf_lines = _format_timeframes_line(merged_ratios)
    rows = _build_cash_migration_table(snaps)[:CONFIG.IO.TOP_N_MIGRATION]
    buyers_1d = _buyers_percent_from_taker_ratio(merged_ratios["ratios"]["1d"]["taker_ratio"]) or 0.0
    yorum = ("Piyasa ciddi anlamda risk barındırıyor. Alım Yapma!\n"
             "Günlük nakit giriş oranı (1d) %50 üzerine çıkarsa risk azalacaktır.") if buyers_1d < 49.0 \
             else "Piyasa günlük nakit giriş oranı bakımından kötü durumda değil. Sert düşüş beklentim yok."
    parts = [
        "⚡ Market Nakit raporu",
        "Marketteki Tüm Coinlere Olan Nakit Girişi Raporu.",
        f"Kısa Vadeli Market Alım Gücü: {short_power}",
        f"Marketteki Hacim Payı:{_fmt_pct(vol_share, nd=1)}\n",
        "⚡ Market ait 5 zamana ait nakit yüzdesi",
        "\n".join(tf_lines),
        "\n⚡ Coin Nakit Göçü Raporu",
        "En çok nakit girişi olanlar:",
    ]
    for sym, cash_share, buyers15, mts, pat in rows:
        parts.append(f"{sym.replace(CONFIG.IO.QUOTE_ASSET,'')} Nakit: {_fmt_pct(cash_share)} 15m:{_fmt_pct(buyers15,0)} Mts: {mts:.1f} {pat}")
    parts.append("\n⚡ Yorum")
    parts.append(yorum)
    return "\n".join(parts)

def _format_coin_report(symbol: str, snap: Dict[str, Any], group_snapshots: Dict[str, Dict[str, Any]]) -> str:
    short_power = _fmt_ratio_as_power(snap.get("ratios", {}).get("15m", {}).get("vwap_taker_ratio"))
    tickers = {s: d.get("ticker", {}) for s, d in group_snapshots.items()}
    share = _calc_group_volume_share(tickers, group=[symbol])
    tf_lines = _format_timeframes_line(snap)
    buyers15 = _buyers_percent_from_taker_ratio(snap.get("ratios", {}).get("15m", {}).get("taker_ratio")) or 0.0
    mts = float(snap.get("mts_score", 0.0))
    pat = "".join(_arrow_from_ratio(snap.get("ratios", {}).get(k, {}).get("taker_ratio")) for k in ["15m","1h","4h","12h","1d"])
    line = f"{symbol.replace(CONFIG.IO.QUOTE_ASSET,'')} Nakit: %100,0 15m:{_fmt_pct(buyers15,0)} Mts: {mts:.1f} {pat}"
    buyers_1d = _buyers_percent_from_taker_ratio(snap.get("ratios", {}).get("1d", {}).get("taker_ratio")) or 0.0
    yorum = "Piyasa ciddi anlamda risk barındırıyor. Alım Yapma!" if buyers_1d < 49.0 else "Piyasa günlük nakit giriş oranı bakımından kötü durumda değil."
    parts = [
        f"Belirtilen Coin Grubu İçin Nakit Girişi Raporu. ({symbol} Grubu İçin)",
        f"Bu Grup İçin Kısa Vadeli Alım Gücü: {short_power}",
        f"Marketteki Hacim Payı:{_fmt_pct(share, nd=1)}\n",
        "\n".join(tf_lines),
        "\nEn çok nakit girişi olanlar:",
        line,
        "\n" + yorum,
    ]
    return "\n".join(parts)

# =========================
# --- Telegram Handlers ---
# =========================

async def io_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        api = get_binance_api()
        target_symbol: Optional[str] = None
        if context.args:
            target_symbol = _symbolize(context.args[0])
        symbols = await _resolve_market_symbol_list(api)
        if not symbols:
            await update.message.reply_text("Uygun sembol bulunamadı.")
            return
        snaps = await _build_snapshots(symbols)
        if target_symbol:
            symbol = target_symbol
            if symbol not in snaps:
                snap = await _build_snapshot(symbol)
                text = _format_coin_report(symbol, snap, snaps)
            else:
                text = _format_coin_report(symbol, snaps[symbol], snaps)
            await update.message.reply_text("inOut Raporu (/io coin)\n\n" + text)
            return
        text = _format_market_report(snaps)
        await update.message.reply_text("inOut Raporu (/io)\n\n" + text)
    except Exception as e:
        await update.message.reply_text(f"IO raporu oluşturulamadı: {e}")

# =========================
# --- Plugin Loader API ---
# =========================

def register(app) -> None:
    app.add_handler(CommandHandler("io", io_command))
