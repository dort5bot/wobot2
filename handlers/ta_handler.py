# handlers/ta_handler.py 901-2211>> 901-2345
# handlers/ta_handler.py
import asyncio
import pandas as pd
from telegram import Update
from telegram.ext import CommandHandler, CallbackContext

# ❌ Eski: from utils.binance_api import get_binance_api
# ✅ Yeni:
from utils.binance_api import get_binance_client
from utils.config import CONFIG
from utils.ta_utils import (
    calculate_all_ta_hybrid_async, 
    generate_signals,
    klines_to_dataframe,
    health_check,
    get_cache_stats
)

# ------------------------------------------------------------
# OHLCV Fetch (Güncellenmiş)
# ------------------------------------------------------------
async def fetch_ohlcv(symbol: str, hours: int = 4, interval: str = "1h") -> pd.DataFrame:
    # ❌ Eski: client = get_binance_api()
    # ✅ Yeni:
    client = get_binance_client(None, None)  # Global instance'ı kullan
    limit = max(hours * 3, 200)
    klines = await client.get_klines(symbol, interval=interval, limit=limit)
    return klines_to_dataframe(klines)

# ------------------------------------------------------------
# Yardımcı Fonksiyonlar
# ------------------------------------------------------------
def regime_label(score: float) -> str:
    if score > 0.5:
        return "trend"
    elif score < -0.5:
        return "crash"
    return "range"

def get_kalman_symbol(kalman_score: float) -> str:
    if kalman_score > 0:
        return "↑"
    elif kalman_score < 0:
        return "↓"
    return "→"

def format_coin_name(symbol: str) -> str:
    if symbol.endswith("USDT"):
        return symbol[:-4]
    return symbol

def get_trend_commentary(trend_type: str, count: int) -> str:
    comments = {
        "trend": [
            "📈 Trend devam ediyor, pullback'ler alım fırsatı",
            "🚀 Trend güçlü, pozisyonları koru",
            "🎯 Trend coin'leri öne çıkıyor"
        ],
        "crash": [
            "⚠️ Crash rejimi riskli, dikkatli ol!",
            "🔻 Düşüş eğilimi devam ediyor",
            "⏳ Bottom yakın olabilir, dip alımları izle"
        ],
        "range": [
            "🔄 Range'de sıkışmış, breakout bekleniyor",
            "📊 Range coin'leri watchlist'e al, breakout sinyallini bekle",
            "⚖️ Alıcı-satıcı dengesi, yön arayışı"
        ]
    }
    
    import random
    comment = random.choice(comments.get(trend_type, ["Analiz tamamlandı"]))
    
    if count == 0:
        return "🟡 İlgili rejimde coin bulunamadı"
    elif count <= 3:
        return f"🟠 {comment} - Sınırlı sayıda"
    else:
        return f"🟢 {comment} - {count} coin"

# ------------------------------------------------------------
# Market Tarama (Güncellenmiş)
# ------------------------------------------------------------
async def scan_market(symbols: list = None, interval: str = "1h", hours: int = 4) -> dict:
    """Yeni ta_utils ile uyumlu market tarama fonksiyonu"""
    if symbols is None:
        symbols = CONFIG.BINANCE.SCAN_SYMBOLS
    
    results = {}
    
    for symbol in symbols:
        try:
            df = await fetch_ohlcv(symbol, hours=hours, interval=interval)
            if len(df) < 20:  # Minimum data kontrolü
                continue
                
            # TA hesaplamaları
            ta_results = await calculate_all_ta_hybrid_async(df, symbol)
            
            # Sinyal üretme
            signal_result = generate_signals(df)
            
            # Alpha detayları (ta_utils'deki alpha_ta fonksiyonuna uyumlu)
            alpha_details = signal_result.get('alpha_details', {})
            
            results[symbol] = {
                'score': alpha_details.get('alpha_signal', 0),
                'signal': signal_result['signal'],
                'detail': {
                    'regime_score': alpha_details.get('regime_signal', 0),
                    'kalman_score': alpha_details.get('kalman_signal', 0),
                    'entropy_score': alpha_details.get('entropy', 0),
                    'leadlag': {
                        'corr': alpha_details.get('lead_lag', 0),
                        'lag': 0  # Bu bilgi alpha_ta'da yok, güncellenebilir
                    }
                }
            }
            
        except Exception as e:
            print(f"{symbol} analiz hatası: {e}")
            continue
    
    return results

# ------------------------------------------------------------
# Geliştirilmiş TA Handler
# ------------------------------------------------------------
def ta_handler(update: Update, context: CallbackContext) -> None:
    args = context.args
    chat_id = update.effective_chat.id

    async def _run():
        try:
            # Sistem durumu komutu
            if args and args[0].lower() in ['status', 'health', 'durum']:
                health = health_check()
                cache_stats = get_cache_stats()
                
                text = f"🔄 TA Sistemi Durumu\n"
                text += f"📊 Durum: {health['status']}\n"
                text += f"💾 Cache: {cache_stats['hits']}/{cache_stats['hits']+cache_stats['misses']} isabet\n"
                text += f"📈 Hesaplamalar: {health['metrics']['total_calculations']}\n"
                text += f"❌ Hatalar: {health['metrics']['calculation_errors']}\n"
                
                await context.bot.send_message(chat_id=chat_id, text=text)
                return
                
            # Trend filtreleme komutları
            if len(args) >= 1 and args[0].lower() in ['trend', 't', 'tt', 'crash', 'c', 'range', 'r']:
                trend_type = args[0].lower()
                hours = int(args[1]) if len(args) > 1 else 4
                
                results = await scan_market(interval="1h", hours=hours)
                
                # Trend filtreleme
                filtered_coins = {}
                for symbol, data in results.items():
                    regime = regime_label(data['detail']['regime_score'])
                    
                    if trend_type in ['trend', 't', 'tt'] and regime == "trend":
                        filtered_coins[symbol] = data
                    elif trend_type in ['crash', 'c'] and regime == "crash":
                        filtered_coins[symbol] = data
                    elif trend_type in ['range', 'r'] and regime == "range":
                        filtered_coins[symbol] = data
                
                # Sıralama
                sorted_coins = sorted(
                    filtered_coins.items(), 
                    key=lambda x: abs(x[1]['score']), 
                    reverse=True
                )[:15]  # En fazla 15 coin
                
                # Mesaj oluşturma
                if not sorted_coins:
                    text = f"⚠️ {trend_type.upper()} rejiminde coin bulunamadı"
                    await context.bot.send_message(chat_id=chat_id, text=text)
                    return
                
                text = f"📊 {trend_type.upper()} Rejimi ({hours}sa)\n\n"
                for symbol, data in sorted_coins:
                    coin_name = format_coin_name(symbol)
                    text += (
                        f"{coin_name:6} α:{data['score']:.2f} "
                        f"{get_kalman_symbol(data['detail']['kalman_score'])} "
                        f"{'🟢' if data['signal'] == 1 else '🔴' if data['signal'] == -1 else '⚪'}\n"
                    )
                
                # Yorum ekleme
                commentary = get_trend_commentary(regime_label(data['detail']['regime_score']), len(sorted_coins))
                text += f"\n{commentary}"
                
                await context.bot.send_message(chat_id=chat_id, text=text)
                
            # Market scan komutu
            elif len(args) == 0 or (len(args) == 1 and (args[0].lower() == "all" or args[0].isdigit())):
                hours = int(args[0]) if args and args[0].isdigit() else 4
                
                results = await scan_market(interval="1h", hours=hours)
                
                # Sinyal gücüne göre sırala
                sorted_coins = sorted(
                    results.items(), 
                    key=lambda x: abs(x[1]['score']), 
                    reverse=True
                )[:15]
                
                text = f"🔍 Market Scan ({hours}sa)\n\n"
                for symbol, data in sorted_coins:
                    coin_name = format_coin_name(symbol)
                    regime = regime_label(data['detail']['regime_score'])
                    text += (
                        f"{coin_name:6} α:{data['score']:.2f} "
                        f"{regime[:1]} {get_kalman_symbol(data['detail']['kalman_score'])} "
                        f"{'🟢' if data['signal'] == 1 else '🔴' if data['signal'] == -1 else '⚪'}\n"
                    )
                
                await context.bot.send_message(chat_id=chat_id, text=text)
                
            # Tek coin analizi
            else:
                coin = args[0].upper() + "USDT" if not args[0].upper().endswith("USDT") else args[0].upper()
                hours = int(args[1]) if len(args) > 1 else 4
                
                df = await fetch_ohlcv(coin, hours=hours, interval="1h")
                if len(df) < 20:
                    await context.bot.send_message(chat_id=chat_id, text="⚠️ Yetersiz veri")
                    return
                
                # Yeni TA pipeline kullanımı
                ta_results = await calculate_all_ta_hybrid_async(df, coin)
                signal_result = generate_signals(df)
                alpha_details = signal_result.get('alpha_details', {})
                
                # Mesaj oluşturma
                text = (
                    f"🔍 {format_coin_name(coin)} ({hours}h)\n"
                    f"α_skor: {round(alpha_details.get('alpha_signal', 0), 2)} → "
                    f"{'LONG' if signal_result['signal'] == 1 else 'SHORT' if signal_result['signal'] == -1 else 'FLAT'}\n"
                    f"Rejim: {regime_label(alpha_details.get('regime_signal', 0))} "
                    f"({round(alpha_details.get('regime_signal', 0), 2)})\n"
                    f"Entropy: {round(alpha_details.get('entropy', 0), 2)}\n"
                    f"Kalman: {get_kalman_symbol(alpha_details.get('kalman_signal', 0))}\n"
                    f"Lead-Lag: {round(alpha_details.get('lead_lag', 0), 2)}\n"
                )
                
                await context.bot.send_message(chat_id=chat_id, text=text)

        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Hata: {str(e)}")

    asyncio.ensure_future(_run())

# ------------------------------------------------------------
# Plugin loader
# ------------------------------------------------------------
def register(app):
    app.add_handler(CommandHandler("t", ta_handler))
    app.add_handler(CommandHandler("ta", ta_handler))  # Alternatif komut
