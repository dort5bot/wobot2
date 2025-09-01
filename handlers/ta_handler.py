# handlers/ta_handler.py

import asyncio
import pandas as pd
from telegram import Update
from telegram.ext import CommandHandler, CallbackContext

from utils.binance_api import get_binance_api
from utils.config import CONFIG
from utils.ta_utils import alpha_signal, scan_market


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


def get_kalman_symbol(kalman_score: float) -> str:
    """Kalman skoruna göre ok sembolü döndürür"""
    if kalman_score > 0:
        return "↑"
    elif kalman_score < 0:
        return "↓"
    return "→"


def format_coin_name(symbol: str) -> str:
    """Sembolü kısaltır (ETHUSDT -> ETH)"""
    if symbol.endswith("USDT"):
        return symbol[:-4]
    return symbol


def get_trend_commentary(trend_type: str, count: int) -> str:
    """Trend tipine göre yorum ekleri döndürür"""
    comments = {
        "trend": [
            "📈 Trend devam ediyor, pullback'ler alım fırsatı",
            "🚀 Trend güçlü, pozisyonları koru",
            "🎯 Trend coin'leri öne çıkıyor"
        ],
        "crash": [
            "⚠️  Crash rejimi riskli, dikkatli ol!",
            "🔻 Düşüş eğilimi devam ediyor",
            "⏳ Bottom yakın olabilir, dip alımları izle"
        ],
        "range": [
            "🔄 Range'de sıkışmış, breakout bekleniyor",
            "📊 Range coin'leri watchlist'e al, breakout sinyallini bekle",
            "⚖️  Alıcı-satıcı dengesi, yön arayışı"
        ]
    }
    
    # Rastgele bir yorum seç
    import random
    comment = random.choice(comments.get(trend_type, ["Analiz tamamlandı"]))
    
    # Coin sayısına göre ek yorum
    if count == 0:
        return "🟡 İlgili rejimde coin bulunamadı"
    elif count <= 3:
        return f"🟠 {comment} - Sınırlı sayıda"
    else:
        return f"🟢 {comment} - {count} coin"
    
    return f"🔵 {comment}"


async def get_market_data(mode: str = "config", top_n: int = None) -> dict:
    """Piyasa verilerini getirir"""
    api = get_binance_api()
    
    if mode == "all":
        info = await api.exchange_info_details()
        symbols = [s["symbol"] for s in info["symbols"] if s["quoteAsset"] == "USDT"]
    elif mode == "top" and top_n:
        tickers = await api.get_all_24h_tickers()
        usdt_pairs = [t for t in tickers if t["symbol"].endswith("USDT")]
        top_sorted = sorted(usdt_pairs, key=lambda x: float(x["quoteVolume"]), reverse=True)
        symbols = [t["symbol"] for t in top_sorted[:top_n]]
    else:
        symbols = CONFIG.BINANCE.SCAN_SYMBOLS

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
    
    return results


# ------------------------------------------------------------
# /t Komutu Handler - GELİŞTİRİLMİŞ VERSİYON
# ------------------------------------------------------------
def ta_handler(update: Update, context: CallbackContext) -> None:
    args = context.args
    chat_id = update.effective_chat.id

    async def _run():
        try:
            # ---------------------------------
            # Trend Filtreleme Komutları
            # ---------------------------------
            if len(args) >= 1 and args[0].lower() in ['trend', 't', 'tt', 'crash', 'c', 'range', 'r']:
                # Komut tipini belirle
                cmd = args[0].lower()
                if cmd in ['trend', 't', 'tt']:
                    trend_type = "trend"
                elif cmd in ['crash', 'c']:
                    trend_type = "crash"
                else:
                    trend_type = "range"
                
                # Varsayılan değerler
                limit = 10
                mode = "config"
                
                # Komut parametrelerini parse et
                if len(args) >= 2:
                    # İkinci parametre sayı mı?
                    if args[1].isdigit():
                        limit = int(args[1])
                    # Veya trend tipi mi? (crash, range)
                    elif args[1].lower() in ['crash', 'c', 'range', 'r'] and cmd in ['t', 'trend']:
                        if args[1].lower() in ['crash', 'c']:
                            trend_type = "crash"
                        elif args[1].lower() in ['range', 'r']:
                            trend_type = "range"
                
                # Üçüncü parametre sayı olabilir
                if len(args) >= 3 and args[2].isdigit():
                    limit = int(args[2])
                
                # Piyasa verilerini al
                results = await get_market_data(mode=mode)
                
                # Trend'e göre filtrele ve sırala
                filtered_coins = []
                for sym, res in results.items():
                    regime = res.get("detail", {}).get("regime_score", 0.0)
                    
                    if trend_type == "trend" and regime > 0.5:
                        filtered_coins.append((sym, regime, res))
                    elif trend_type == "crash" and regime < -0.5:
                        filtered_coins.append((sym, regime, res))
                    elif trend_type == "range" and -0.5 <= regime <= 0.5:
                        filtered_coins.append((sym, regime, res))
                
                # Rejim skoruna göre sırala (yüksekten düşüğe)
                filtered_coins.sort(key=lambda x: x[1], reverse=(trend_type != "crash"))
                
                # Limit uygula
                filtered_coins = filtered_coins[:limit]
                
                # Raporu oluştur
                trend_icons = {
                    "trend": "📈 TREND",
                    "crash": "📉 CRASH", 
                    "range": "↔️ RANGE"
                }
                
                text = f"{trend_icons[trend_type]} Coin'ler (Top {len(filtered_coins)})\n"
                text += "💢 coin | α-sk | Sinyal | Rejim | Kalman\n"
                
                for sym, regime, res in filtered_coins:
                    score = res.get("score", res.get("alpha_ta", {}).get("score", 0))
                    signal = res.get("signal", res.get("alpha_ta", {}).get("signal", 0))
                    kalman = res.get("detail", {}).get("kalman_score", 0.0)

                    sig_txt = "LONG" if signal == 1 else ("SHORT" if signal == -1 else "FLAT")
                    coin_name = format_coin_name(sym)
                    kalman_symbol = get_kalman_symbol(kalman)
                    
                    text += f"{coin_name}: α={round(score,2)} [{sig_txt}] | {round(regime,2)} | {kalman_symbol}\n"
                
                # Yorum ekle
                text += f"\n{get_trend_commentary(trend_type, len(filtered_coins))}"
                
                if not filtered_coins:
                    text = f"⚠️ {trend_icons[trend_type]} rejiminde coin bulunamadı."
                
                await context.bot.send_message(chat_id=chat_id, text=text)
                return

            # ---------------------------------
            # Orijinal Market Scan (geriye dönük uyumluluk)
            # ---------------------------------
            if len(args) == 0 or (len(args) == 1 and (args[0].lower() == "all" or args[0].isdigit())):
                mode = "config"
                top_n = None

                if len(args) == 1:
                    if args[0].lower() == "all":
                        mode = "all"
                    elif args[0].isdigit():
                        mode = "top"
                        top_n = int(args[0])

                results = await get_market_data(mode=mode, top_n=top_n)
                
                mode_text = "all" if mode == "all" else f"top{top_n}" if mode == "top" else "config"
                text = f"📊 Market Scan (4h, mode={mode_text})\n"
                text += "💢 coin | α-ta | Rejim | Kalman\n"
                
                for sym, res in results.items():
                    score = res.get("score", res.get("alpha_ta", {}).get("score", 0))
                    signal = res.get("signal", res.get("alpha_ta", {}).get("signal", 0))
                    regime = res.get("detail", {}).get("regime_score", 0.0)
                    kalman = res.get("detail", {}).get("kalman_score", 0.0)

                    sig_txt = "LONG" if signal == 1 else ("SHORT" if signal == -1 else "FLAT")
                    coin_name = format_coin_name(sym)
                    kalman_symbol = get_kalman_symbol(kalman)
                    
                    text += f"{coin_name}: α={round(score,2)} [{sig_txt}] | {regime_label(regime)}({round(regime,2)}) | {kalman_symbol}\n"

                # Genel market yorumu ekle
                total_coins = len(results)
                trend_coins = len([r for r in results.values() if r.get("detail", {}).get("regime_score", 0) > 0.5])
                trend_ratio = trend_coins / total_coins if total_coins > 0 else 0
                
                if trend_ratio > 0.7:
                    text += "\n🟢 Market güçlü trend modunda"
                elif trend_ratio < 0.3:
                    text += "\n🔴 Market zayıf, dikkatli ol"
                else:
                    text += "\n🟠 Market karışık, seçici davran"

                await context.bot.send_message(chat_id=chat_id, text=text)
                return

            # ---------------------------------
            # Tek Coin Analizi (orijinal)
            # ---------------------------------
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
            kalman = res["detail"].get("kalman_score", 0.0)

            entropy = res["detail"].get("entropy_score", 0.0)
            kalman_txt = get_kalman_symbol(kalman)
            leadlag = res["detail"].get("leadlag", {})

            text = (
                f"🔍 {format_coin_name(coin)} ({hours}h)\n"
                f"α_skor: {round(score,2)} → {sig_txt}\n"
                f"Rejim: {regime_label(regime)} ({round(regime,2)})\n"
                f"Entropy: {round(entropy,2)}\n"
                f"Kalman eğilim: {kalman_txt}\n"
                f"Lead–Lag (BTC): {leadlag.get('lag',0)} bar | corr={round(leadlag.get('corr',0),2)}\n"
            )
            
            # Tek coin yorumu
            if regime > 0.5:
                text += "\n🟢 Trend devam ediyor, pullback'ler alım fırsatı"
            elif regime < -0.5:
                text += "\n🔴 Düşüş eğilimi, dikkatli ol"
            else:
                text += "\n🟠 Range'de, breakout bekleyişi"
            
            await context.bot.send_message(chat_id=chat_id, text=text)

        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Hata: {e}")

    asyncio.ensure_future(_run())


# ------------------------------------------------------------
# Plugin loader uyumluluk
# ------------------------------------------------------------
def register(app):
    app.add_handler(CommandHandler("t", ta_handler))
