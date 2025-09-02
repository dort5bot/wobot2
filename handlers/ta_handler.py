# handlers/ta_handler.py> 902-0849

import asyncio
import pandas as pd
from telegram import Update
from telegram.ext import CommandHandler, CallbackContext
from datetime import datetime
import time
import logging
import random

# Binance API
from utils.binance_api import get_global_binance_client
from utils.config import CONFIG
from utils.ta_utils import (
    calculate_all_ta_hybrid_async, 
    generate_signals,
    health_check,
    get_cache_stats
)

# ------------------------------------------------------------
# Logger
# ------------------------------------------------------------
logger = logging.getLogger("ta_handler")

# ------------------------------------------------------------
# OHLCV Fetch - CCXT Uyumlu
# ------------------------------------------------------------
def map_interval_to_timeframe(interval: str) -> str:
    """Telegram interval'ını CCXT timeframe'ine dönüştür"""
    mapping = {
        '1m': '1m',
        '5m': '5m', 
        '15m': '15m',
        '30m': '30m',
        '1h': '1h',
        '4h': '4h',
        '1d': '1d',
        '1w': '1w'
    }
    return mapping.get(interval, '1h')

async def fetch_ohlcv(symbol: str, hours: int = 4, interval: str = "1h") -> pd.DataFrame:
    """CCXT ile OHLCV verisi al"""
    try:
        client = await get_global_binance_client()
        
        if client is None:
            logger.error("Binance client not available")
            return pd.DataFrame()
            
        # Timeframe mapping
        timeframe = map_interval_to_timeframe(interval)
        
        # Timestamp hesapla
        since = None
        if hours > 0:
            since = int((time.time() - hours * 3600) * 1000)
        
        # CCXT ile OHLCV verisi al
        ohlcv = await client.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=200)
        
        if not ohlcv or len(ohlcv) == 0:
            return pd.DataFrame()
        
        # CCXT formatını DataFrame'e dönüştür
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        
        # Sayısal kolonları dönüştür
        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            
        return df
        
    except Exception as e:
        logger.error(f"OHLCV fetch error for {symbol}: {e}")
        return pd.DataFrame()

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
    
    comment = random.choice(comments.get(trend_type, ["Analiz tamamlandı"]))
    
    if count == 0:
        return "🟡 İlgili rejimde coin bulunamadı"
    elif count <= 3:
        return f"🟠 {comment} - Sınırlı sayıda"
    else:
        return f"🟢 {comment} - {count} coin"

def get_signal_emoji(signal: int) -> str:
    if signal == 1:
        return "🟢"
    elif signal == -1:
        return "🔴"
    return "⚪"

def get_signal_text(signal: int) -> str:
    if signal == 1:
        return "LONG"
    elif signal == -1:
        return "SHORT"
    return "FLAT"

# ------------------------------------------------------------
# Market Tarama
# ------------------------------------------------------------
async def scan_market(symbols: list = None, interval: str = "1h", hours: int = 4) -> dict:
    if symbols is None:
        symbols = CONFIG.BINANCE.SCAN_SYMBOLS
    
    results = {}
    
    for symbol in symbols:
        try:
            df = await fetch_ohlcv(symbol, hours=hours, interval=interval)
            if len(df) < 20:
                continue
                
            ta_results = await calculate_all_ta_hybrid_async(df, symbol, None)
            signal_result = generate_signals(df)
            alpha_details = signal_result.get('alpha_details', {})
            
            results[symbol] = {
                'score': alpha_details.get('alpha_signal', 0),
                'signal': signal_result['signal'],
                'detail': {
                    'regime_score': alpha_details.get('regime_signal', 0),
                    'kalman_score': alpha_details.get('kalman_signal', 0),
                    'entropy_score': alpha_details.get('entropy', 0),
                    'leadlag': alpha_details.get('lead_lag', 0)
                }
            }
            
        except Exception as e:
            logger.error(f"{symbol} analiz hatası: {e}")
            continue
    
    return results

# ------------------------------------------------------------
# Market Raporu Fonksiyonu
# ------------------------------------------------------------
async def generate_market_report(results: dict, interval: str, hours: int, limit: int = None) -> str:
    if limit:
        sorted_coins = sorted(results.items(), key=lambda x: abs(x[1]['score']), reverse=True)[:limit]
    else:
        sorted_coins = sorted(results.items(), key=lambda x: abs(x[1]['score']), reverse=True)
    
    # İstatistikler
    total_coins = len(sorted_coins)
    trend_coins = sum(1 for _, data in sorted_coins if regime_label(data['detail']['regime_score']) == "trend")
    range_coins = sum(1 for _, data in sorted_coins if regime_label(data['detail']['regime_score']) == "range")
    crash_coins = sum(1 for _, data in sorted_coins if regime_label(data['detail']['regime_score']) == "crash")
    
    # En güçlü sinyaller
    top_signals = sorted_coins[:3]
    
    # Rapor oluştur
    current_time = datetime.utcnow().strftime("%H:%M UTC")
    
    text = f"📊 MARKET TARAMA RAPORU\n"
    text += f"⏰ {current_time} | {interval} Timeframe\n"
    text += f"📈 {hours} saatlik veri ile analiz\n\n"
    
    text += f"🔢 TOPLAM: {total_coins} coin taranıyor\n"
    text += f"📈 TREND: {trend_coins} coin ({int(trend_coins/total_coins*100)}%)\n"
    text += f"🔄 RANGE: {range_coins} coin ({int(range_coins/total_coins*100)}%)\n"
    text += f"📉 CRASH: {crash_coins} coin ({int(crash_coins/total_coins*100)}%)\n\n"
    
    text += "🏆 EN GÜÇLÜ SİNYALLER:\n"
    for i, (symbol, data) in enumerate(top_signals, 1):
        coin_name = format_coin_name(symbol)
        regime = regime_label(data['detail']['regime_score'])
        text += f"{i}. {coin_name}: α={data['score']:.2f} [{get_signal_text(data['signal'])}] | {regime}({data['detail']['regime_score']:.2f})\n"
    
    # Sistem durumu
    health = health_check()
    cache_stats = get_cache_stats()
    hit_ratio = cache_stats['hit_ratio'] * 100
    
    text += f"\n⚡ SİSTEM DURUMU:\n"
    text += f"• Cache: {hit_ratio:.1f}% isabet\n"
    text += f"• Hesaplamalar: {health['metrics']['total_calculations']:,}\n"
    text += f"• Hata oranı: {health['metrics']['error_rate']*100:.1f}%"
    
    return text

# ------------------------------------------------------------
# Geliştirilmiş TA Handler
# ------------------------------------------------------------
def ta_handler(update: Update, context: CallbackContext) -> None:
    args = context.args
    chat_id = update.effective_chat.id

    async def _run():
        try:
            # 1. Tek Coin Analizi: /t <coin_ismi> [saat]
            if args and len(args) >= 1 and not args[0].isdigit() and args[0].lower() not in ['status', 's', 'market', 'm', 'trend', 't', 'tt', 'crash', 'c', 'range', 'r', 'all']:
                coin = args[0].upper()
                if not coin.endswith("USDT"):
                    coin += "USDT"
                
                hours = int(args[1]) if len(args) > 1 and args[1].isdigit() else 4
                
                df = await fetch_ohlcv(coin, hours=hours, interval="1h")
                if len(df) < 20:
                    await context.bot.send_message(chat_id=chat_id, text="⚠️ Yetersiz veri")
                    return
                
                ta_results = await calculate_all_ta_hybrid_async(df, coin, None)
                signal_result = generate_signals(df)
                alpha_details = signal_result.get('alpha_details', {})
                
                # Mesaj oluşturma
                text = (
                    f"🔍 {format_coin_name(coin)} ({hours}h)\n"
                    f"α_skor: {round(alpha_details.get('alpha_signal', 0), 2)} → "
                    f"{get_signal_text(signal_result['signal'])}\n"
                    f"Rejim: {regime_label(alpha_details.get('regime_signal', 0))} "
                    f"({round(alpha_details.get('regime_signal', 0), 2)})\n"
                    f"Entropy: {round(alpha_details.get('entropy', 0), 2)}\n"
                    f"Kalman: {get_kalman_symbol(alpha_details.get('kalman_signal', 0))}\n"
                    f"Lead-Lag: {round(alpha_details.get('lead_lag', 0), 2)}\n"
                )
                
                # Yorum ekleme
                commentary = get_trend_commentary(regime_label(alpha_details.get('regime_signal', 0)), 1)
                text += f"\n{commentary}"
                
                await context.bot.send_message(chat_id=chat_id, text=text)
                return
                
            # 2. Market Tarama: /t [sayı]
            elif len(args) == 0 or (len(args) == 1 and (args[0].lower() == "all" or args[0].isdigit())):
                hours = int(args[0]) if args and args[0].isdigit() else 4
                
                if args and args[0].lower() == "all":
                    symbols = None  # Tüm coin'ler
                else:
                    symbols = CONFIG.BINANCE.SCAN_SYMBOLS
                
                results = await scan_market(symbols=symbols, interval="1h", hours=hours)
                
                # Sinyal gücüne göre sırala
                limit = 15
                sorted_coins = sorted(
                    results.items(), 
                    key=lambda x: abs(x[1]['score']), 
                    reverse=True
                )[:limit]
                
                mode_text = "all" if symbols is None else "top"
                text = f"📊 Market Scan ({hours}h, mode={mode_text})\n\n"
                text += "💢 coin | α-ta | Rejim | Kalman\n"
                
                for symbol, data in sorted_coins:
                    coin_name = format_coin_name(symbol)
                    regime = regime_label(data['detail']['regime_score'])
                    text += (
                        f"{coin_name}: α={data['score']:.2f} "
                        f"[{get_signal_text(data['signal'])}] | "
                        f"{regime[0]}({data['detail']['regime_score']:.2f}) | "
                        f"{get_kalman_symbol(data['detail']['kalman_score'])}\n"
                    )
                
                await context.bot.send_message(chat_id=chat_id, text=text)
                return
                
            # 3. Trend Filtreleme: /tt [limit|tip] [limit]
            elif len(args) >= 1 and args[0].lower() in ['trend', 't', 'tt']:
                trend_type = "trend"
                hours = 4
                limit = 15
                sub_type = None
                
                # Parametreleri parse et
                if len(args) > 1:
                    for arg in args[1:]:
                        if arg.isdigit():
                            limit = int(arg)
                        elif arg in ['c', 'crash']:
                            sub_type = 'crash'
                        elif arg in ['r', 'range']:
                            sub_type = 'range'
                
                results = await scan_market(interval="1h", hours=hours)
                
                # Trend filtreleme
                filtered_coins = {}
                for symbol, data in results.items():
                    regime = regime_label(data['detail']['regime_score'])
                    
                    if regime == "trend":
                        if sub_type == 'crash' and data['detail']['regime_score'] < -0.3:
                            filtered_coins[symbol] = data
                        elif sub_type == 'range' and abs(data['detail']['regime_score']) <= 0.3:
                            filtered_coins[symbol] = data
                        elif sub_type is None:
                            filtered_coins[symbol] = data
                
                # Sıralama
                sorted_coins = sorted(
                    filtered_coins.items(), 
                    key=lambda x: abs(x[1]['score']), 
                    reverse=True
                )[:limit]
                
                # Mesaj oluşturma
                if not sorted_coins:
                    text = f"⚠️ {trend_type.upper()} rejiminde coin bulunamadı"
                    await context.bot.send_message(chat_id=chat_id, text=text)
                    return
                
                trend_name = "TREND"
                if sub_type == 'crash':
                    trend_name = "TREND-CRASH"
                elif sub_type == 'range':
                    trend_name = "TREND-RANGE"
                    
                text = f"📊 {trend_name} Coin'ler (Top {len(sorted_coins)})\n\n"
                text += "💢 coin | α-sk | Sinyal | Rejim | Kalman\n"
                
                for symbol, data in sorted_coins:
                    coin_name = format_coin_name(symbol)
                    regime_score = data['detail']['regime_score']
                    text += (
                        f"{coin_name}: α={data['score']:.2f} "
                        f"[{get_signal_text(data['signal'])}] | "
                        f"{regime_label(regime_score)[0]}({regime_score:.2f}) | "
                        f"{get_kalman_symbol(data['detail']['kalman_score'])}\n"
                    )
                
                # Yorum ekleme
                commentary = get_trend_commentary("trend", len(sorted_coins))
                text += f"\n{commentary}"
                
                await context.bot.send_message(chat_id=chat_id, text=text)
                return
                
            # 4. Crash Filtreleme: /tc [limit]
            elif len(args) >= 1 and args[0].lower() in ['crash', 'c']:
                hours = 4
                limit = 15
                
                # Parametreleri parse et
                if len(args) > 1 and args[1].isdigit():
                    limit = int(args[1])
                
                results = await scan_market(interval="1h", hours=hours)
                
                # Crash filtreleme
                filtered_coins = {}
                for symbol, data in results.items():
                    regime = regime_label(data['detail']['regime_score'])
                    if regime == "crash":
                        filtered_coins[symbol] = data
                
                # Sıralama
                sorted_coins = sorted(
                    filtered_coins.items(), 
                    key=lambda x: abs(x[1]['score']), 
                    reverse=True
                )[:limit]
                
                # Mesaj oluşturma
                if not sorted_coins:
                    text = "⚠️ CRASH rejiminde coin bulunamadı"
                    await context.bot.send_message(chat_id=chat_id, text=text)
                    return
                
                text = f"📉 CRASH Coin'ler (Top {len(sorted_coins)})\n\n"
                text += "💢 coin | α-sk | Sinyal | Rejim | Kalman\n"
                
                for symbol, data in sorted_coins:
                    coin_name = format_coin_name(symbol)
                    regime_score = data['detail']['regime_score']
                    text += (
                        f"{coin_name}: α={data['score']:.2f} "
                        f"[{get_signal_text(data['signal'])}] | "
                        f"{regime_label(regime_score)[0]}({regime_score:.2f}) | "
                        f"{get_kalman_symbol(data['detail']['kalman_score'])}\n"
                    )
                
                # Yorum ekleme
                commentary = get_trend_commentary("crash", len(sorted_coins))
                text += f"\n{commentary}"
                
                await context.bot.send_message(chat_id=chat_id, text=text)
                return
                
            # 5. Range Filtreleme: /tr [limit]
            elif len(args) >= 1 and args[0].lower() in ['range', 'r']:
                hours = 4
                limit = 15
                
                # Parametreleri parse et
                if len(args) > 1 and args[1].isdigit():
                    limit = int(args[1])
                
                results = await scan_market(interval="1h", hours=hours)
                
                # Range filtreleme
                filtered_coins = {}
                for symbol, data in results.items():
                    regime = regime_label(data['detail']['regime_score'])
                    if regime == "range":
                        filtered_coins[symbol] = data
                
                # Sıralama
                sorted_coins = sorted(
                    filtered_coins.items(), 
                    key=lambda x: abs(x[1]['score']), 
                    reverse=True
                )[:limit]
                
                # Mesaj oluşturma
                if not sorted_coins:
                    text = "⚠️ RANGE rejiminde coin bulunamadı"
                    await context.bot.send_message(chat_id=chat_id, text=text)
                    return
                
                text = f"🔄 RANGE Coin'ler (Top {len(sorted_coins)})\n\n"
                text += "💢 coin | α-sk | Sinyal | Rejim | Kalman\n"
                
                for symbol, data in sorted_coins:
                    coin_name = format_coin_name(symbol)
                    regime_score = data['detail']['regime_score']
                    text += (
                        f"{coin_name}: α={data['score']:.2f} "
                        f"[{get_signal_text(data['signal'])}] | "
                        f"{regime_label(regime_score)[0]}({regime_score:.2f}) | "
                        f"{get_kalman_symbol(data['detail']['kalman_score'])}\n"
                    )
                
                # Yorum ekleme
                commentary = get_trend_commentary("range", len(sorted_coins))
                text += f"\n{commentary}"
                
                await context.bot.send_message(chat_id=chat_id, text=text)
                return
                
            # 6. Sistem Durumu: /ts
            elif len(args) >= 1 and args[0].lower() in ['status', 's']:
                health = health_check()
                cache_stats = get_cache_stats()
                hit_ratio = cache_stats['hit_ratio'] * 100
                
                text = f"🔄 TA Sistemi Durumu\n"
                text += f"📊 Durum: {health['status']}\n"
                text += f"💾 Cache: {cache_stats['hits']}/{cache_stats['hits']+cache_stats['misses']} isabet ({hit_ratio:.1f}%)\n"
                text += f"📈 Hesaplamalar: {health['metrics']['total_calculations']:,}\n"
                text += f"❌ Hatalar: {health['metrics']['calculation_errors']}\n"
                text += f"📉 Hata oranı: {health['metrics']['error_rate']*100:.1f}%"
                
                await context.bot.send_message(chat_id=chat_id, text=text)
                return
                
            # 7. Market Raporu: /tm [limit|Timeframe] [Timeframe]
            elif len(args) >= 1 and args[0].lower() in ['market', 'm']:
                hours = 4
                interval = "1h"
                limit = None
                
                # Parametreleri parse et
                if len(args) > 1:
                    for arg in args[1:]:
                        if arg.endswith('h'):
                            try:
                                hours = int(arg[:-1])
                            except:
                                pass
                        elif arg in ['1h', '4h', '1d']:
                            interval = arg
                        elif arg.isdigit():
                            limit = int(arg)
                
                results = await scan_market(interval=interval, hours=hours)
                report = await generate_market_report(results, interval, hours, limit)
                
                await context.bot.send_message(chat_id=chat_id, text=report)
                return
                
            # Yardım mesajı
            else:
                help_text = """
📊 TA Handler Komutları:

1. 🔍 Tek Coin Analizi
   /t <coin_ismi> [saat]
   Örnek: /t btc 12

2. 📈 Market Tarama
   /t [sayı]
   Örnek: /t 20

3. 🚀 Trend Filtreleme
   /tt [limit|tip] [limit]
   Örnek: /tt c 10

4. 📉 Crash Filtreleme
   /tc [limit]
   Örnek: /tc 8

5. 🔄 Range Filtreleme
   /tr [limit]
   Örnek: /tr 7

6. ⚡ Sistem Durumu
   /ts

7. 📊 Market Raporu
   /tm [limit|Timeframe] [Timeframe]
   Örnek: /tm 50 4h
                """
                await context.bot.send_message(chat_id=chat_id, text=help_text)

        except Exception as e:
            logger.error(f"TA handler error: {e}")
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Hata: {str(e)}")

    asyncio.ensure_future(_run())

# ------------------------------------------------------------
# Plugin loader
# ------------------------------------------------------------
def register(app):
    app.add_handler(CommandHandler("t", ta_handler))
    app.add_handler(CommandHandler("tt", ta_handler))
    app.add_handler(CommandHandler("tc", ta_handler))
    app.add_handler(CommandHandler("tr", ta_handler))
    app.add_handler(CommandHandler("ts", ta_handler))
    app.add_handler(CommandHandler("tm", ta_handler))

#EOF
