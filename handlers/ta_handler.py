# handlers/ta_handler.py

import asyncio
import pandas as pd
from telegram import Update
from telegram.ext import CommandHandler, CallbackContext

from utils.binance_api import get_binance_api
from utils.config import CONFIG
from utils.ta_utils import (
    alpha_signal,
    scan_market,
    get_detailed_metrics,
    health_check,
)

# ------------------------------------------------------------
# OHLCV Fetch
# ------------------------------------------------------------
async def fetch_ohlcv(symbol: str, hours: int = 4, interval: str = "1h") -> pd.DataFrame:
    client = get_binance_api()
    limit = max(hours * 3, 200)
    kl = await client.get_klines(symbol, interval=interval, limit=limit)

    df = pd.DataFrame(
        kl,
        columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "trades", "taker_base", "taker_quote", "ignore"
        ]
    )
    df = df.astype({
        "open": float, "high": float, "low": float,
        "close": float, "volume": float
    })
    return df


def regime_label(score: float) -> str:
    if score > 0.5:
        return "trend"
    elif score < -0.5:
        return "crash"
    return "range"


# ------------------------------------------------------------
# /t Tek Coin Analizi veya Market Tarama
# ------------------------------------------------------------
def ta_handler(update: Update, context: CallbackContext) -> None:
    args = context.args
    chat_id = update.effective_chat.id
    api = get_binance_api()

    async def _run():
        try:
            # Market tarama (all veya topN)
            if len(args) == 0 or (len(args) == 1 and (args[0].lower() == "all" or args[0].isdigit())):
                mode = "config"
                symbols = CONFIG.BINANCE.SCAN_SYMBOLS

                if len(args) == 1 and args[0].lower() == "all":
                    info = await api.exchange_info_details()
                    symbols = [s["symbol"] for s in info["symbols"] if s["quoteAsset"] == "USDT"]
                    mode = "all"

                elif len(args) == 1 and args[0].isdigit():
                    top_n = int(args[0])
                    tickers = await api.get_all_24h_tickers()
                    usdt_pairs = [t for t in tickers if t["symbol"].endswith("USDT")]
                    top_sorted = sorted(usdt_pairs, key=lambda x: float(x["quoteVolume"]), reverse=True)
                    symbols = [t["symbol"] for t in top_sorted[:top_n]]
                    mode = f"top{top_n}"

                # veri çek
                data = {}
                for sym in symbols:
                    try:
                        df = await fetch_ohlcv(sym, hours=4, interval="1h")
                        data[sym] = df
                    except Exception:
                        continue

                btc_ref = data.get("BTCUSDT", None)
                ref_close = btc_ref["close"] if btc_ref is not None else None
                results = scan_market(data, ref_close=ref_close)

                text = f"📊 Market Scan (4h, mode={mode})\n"
                for sym, res in results.items():
                    score = res.get("score", 0.0)
                    sig = res.get("signal", 0)
                    sig_txt = "LONG" if sig == 1 else ("SHORT" if sig == -1 else "FLAT")
                    regime = res.get("detail", {}).get("regime_score", 0.0)
                    text += f"{sym}: α={round(score,2)} [{sig_txt}] | Rejim={regime_label(regime)}\n"

                await context.bot.send_message(chat_id=chat_id, text=text)
                return

            # Tek coin analizi
            coin = args[0].upper() + "USDT" if not args[0].upper().endswith("USDT") else args[0].upper()
            hours = int(args[1]) if len(args) > 1 else 4
            interval = "1h"

            df = await fetch_ohlcv(coin, hours=hours, interval=interval)
            btc_df = await fetch_ohlcv("BTCUSDT", hours=hours, interval=interval)
            ref_close = btc_df["close"] if btc_df is not None else None

            res = alpha_signal(df, ref_series=ref_close)
            score = res["score"]
            sig = res["signal"]
            sig_txt = "LONG" if sig == 1 else ("SHORT" if sig == -1 else "FLAT")
            regime = res["detail"].get("regime_score", 0.0)
            entropy = res["detail"].get("entropy_score", 0.0)
            kalman = res["detail"].get("kalman_score", 0.0)
            kalman_txt = "↑" if kalman > 0 else ("↓" if kalman < 0 else "→")
            leadlag = res["detail"].get("leadlag", {})

            text = (
                f"🔍 {coin} ({hours}h)\n"
                f"α_skor: {round(score,2)} → {sig_txt}\n"
                f"Rejim: {regime_label(regime)} ({round(regime,2)})\n"
                f"Entropy: {round(entropy,2)}\n"
                f"Kalman eğilim: {kalman_txt}\n"
                f"Lead–Lag (BTC): {leadlag.get('lag',0)} bar | corr={round(leadlag.get('corr',0),2)}\n"
            )
            await context.bot.send_message(chat_id=chat_id, text=text)

        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Hata: {e}")

    asyncio.ensure_future(_run())


# ------------------------------------------------------------
# /tt Trend filtreleme
# ------------------------------------------------------------
def tt_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id

    async def _run():
        try:
            results = scan_market({})
            trend_coins = [
                (sym, res) for sym, res in results.items()
                if res.get("detail", {}).get("regime_score", 0) > 0.5
            ]
            text = "📈 TREND Coin’ler\n"
            for sym, res in trend_coins[:10]:
                text += f"{sym}: α={round(res['score'],2)} [{res['signal']}] | Rejim=trend\n"
            await context.bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Hata: {e}")

    asyncio.ensure_future(_run())


# ------------------------------------------------------------
# /tc Crash filtreleme
# ------------------------------------------------------------
def tc_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id

    async def _run():
        try:
            results = scan_market({})
            crash_coins = [
                (sym, res) for sym, res in results.items()
                if res.get("detail", {}).get("regime_score", 0) < -0.5
            ]
            text = "📉 CRASH Coin’ler\n"
            for sym, res in crash_coins[:10]:
                text += f"{sym}: α={round(res['score'],2)} [{res['signal']}] | Rejim=crash\n"
            await context.bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Hata: {e}")

    asyncio.ensure_future(_run())


# ------------------------------------------------------------
# /tr Range filtreleme
# ------------------------------------------------------------
def tr_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id

    async def _run():
        try:
            results = scan_market({})
            range_coins = [
                (sym, res) for sym, res in results.items()
                if -0.5 <= res.get("detail", {}).get("regime_score", 0) <= 0.5
            ]
            text = "🔄 RANGE Coin’ler\n"
            for sym, res in range_coins[:10]:
                text += f"{sym}: α={round(res['score'],2)} [{res['signal']}] | Rejim=range\n"
            await context.bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Hata: {e}")

    asyncio.ensure_future(_run())


# ------------------------------------------------------------
# /ts Sistem durumu
# ------------------------------------------------------------
def ts_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    try:
        metrics = get_detailed_metrics()
        health = health_check()
        text = (
            f"🔄 TA Sistemi Durumu\n"
            f"📊 Durum: {health['status']}\n"
            f"💾 Cache: {metrics['cache']['hits']}/{metrics['cache']['misses']} isabet\n"
            f"📈 Hesaplamalar: {metrics['calculations']['total']}\n"
            f"❌ Hatalar: {metrics['calculations']['errors']}\n"
        )
        context.bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        context.bot.send_message(chat_id=chat_id, text=f"⚠️ Hata: {e}")


# ------------------------------------------------------------
# /tm Market raporu
# ------------------------------------------------------------
def tm_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id

    async def _run():
        try:
            results = scan_market({})
            total = len(results)
            trend = sum(1 for r in results.values() if r.get("detail", {}).get("regime_score", 0) > 0.5)
            crash = sum(1 for r in results.values() if r.get("detail", {}).get("regime_score", 0) < -0.5)
            range_c = total - trend - crash

            text = (
                f"📊 MARKET TARAMA RAPORU\n"
                f"🔢 TOPLAM: {total} coin\n"
                f"📈 TREND: {trend} coin\n"
                f"🔄 RANGE: {range_c} coin\n"
                f"📉 CRASH: {crash} coin\n"
            )
            await context.bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Hata: {e}")

    asyncio.ensure_future(_run())


# ------------------------------------------------------------
# Register
# ------------------------------------------------------------
def register(app):
    app.add_handler(CommandHandler("t", ta_handler))
    app.add_handler(CommandHandler("tt", tt_handler))
    app.add_handler(CommandHandler("tc", tc_handler))
    app.add_handler(CommandHandler("tr", tr_handler))
    app.add_handler(CommandHandler("ts", ts_handler))
    app.add_handler(CommandHandler("tm", tm_handler))
