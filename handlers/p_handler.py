# handlers/p_handler.py
#- 	/p →CONFIG.SCAN_SYMBOLS default(filtre ekler btc ile btcusdt sonuç verir)
#- 	/P n → sayı girilirse limit = n oluyor.
#- 	/P d → düşenler.
#- 	/P coin1 coin2... → manuel seçili coinler.

# handlers/p_handler.py
import logging
import os
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from utils.binance_api import get_binance_client

LOG = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())

COMMAND = "P"
HELP = (
    "/P → ENV'deki SCAN_SYMBOLS listesi (hacme göre sıralı)\n"
    "/P n → En çok yükselen n coin (varsayılan 20)\n"
    "/P d → En çok düşen 20 coin\n"
    "/P coin1 coin2 ... → Belirtilen coin(ler)"
)

# ENV'den SCAN_SYMBOLS oku
SCAN_SYMBOLS = os.getenv(
    "SCAN_SYMBOLS",
    "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,TRXUSDT,CAKEUSDT,SUIUSDT,PEPEUSDT,ARPAUSDT,TURBOUSDT"
).split(",")

# -------------------------------------------------
# Symbol normalizasyon
# -------------------------------------------------
def normalize_symbol(sym: str) -> str:
    sym = sym.upper()
    if not sym.endswith("USDT"):
        sym += "USDT"
    return sym

# -------------------------------------------------
# Ticker verisi çekme
# -------------------------------------------------
async def fetch_ticker_data(symbols=None, descending=True, sort_by="change"):
    api = get_binance_client(None, None)  # Global instance
    data = await api.get_all_24h_tickers()
    if not data:
        return []

    # Sadece USDT pariteleri
    usdt_pairs = [d for d in data if d["symbol"].endswith("USDT")]

    # İstenen coinler varsa filtrele
    if symbols:
        wanted = {normalize_symbol(s) for s in symbols}
        usdt_pairs = [d for d in usdt_pairs if d["symbol"] in wanted]

    # Sıralama
    if sort_by == "volume":
        usdt_pairs.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
    else:
        usdt_pairs.sort(key=lambda x: float(x["priceChangePercent"]), reverse=descending)

    return usdt_pairs[:20]

# -------------------------------------------------
# Rapor formatlama
# -------------------------------------------------
def format_report(data, title):
    lines = [f"📈 {title}", "⚡Coin | Değişim | Hacim | Fiyat"]
    for i, coin in enumerate(data, start=1):
        symbol = coin["symbol"].replace("USDT", "")
        change = float(coin["priceChangePercent"])
        vol_usd = float(coin["quoteVolume"])
        price = float(coin["lastPrice"])

        # Hacim M veya B formatı
        if vol_usd >= 1_000_000_000:
            vol_fmt = f"${vol_usd/1_000_000_000:.1f}B"
        else:
            vol_fmt = f"${vol_usd/1_000_000:.1f}M"

        lines.append(f"{i}. {symbol}: {change:.2f}% | {vol_fmt} | {price}")
    return "\n".join(lines)

# -------------------------------------------------
# Telegram handler
# -------------------------------------------------
async def p_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args

    if not args:
        # /P → ENV'deki SCAN_SYMBOLS, hacme göre sıralı
        data = await fetch_ticker_data(symbols=SCAN_SYMBOLS, sort_by="volume")
        title = "SCAN_SYMBOLS (Hacme Göre)"
    elif args[0].lower() == "d":
        data = await fetch_ticker_data(descending=False)
        title = "Düşüş Trendindeki Coinler"
    elif args[0].isdigit():
        n = int(args[0])
        data = await fetch_ticker_data(descending=True)
        data = data[:n]
        title = f"En Çok Yükselen {n} Coin"
    else:
        data = await fetch_ticker_data(symbols=args)
        title = "Seçili Coinler"

    if not data:
        await update.message.reply_text("Veri alınamadı.")
        return

    report = format_report(data, title)
    await update.message.reply_text(report)

# -------------------------------------------------
# Plugin loader entry
# -------------------------------------------------
def register(application):
    for cmd in ("P", "p"):    #Komut küçük harf de desteklesin bunu dene

        application.add_handler(CommandHandler(cmd, p_handler))     #harf boyutu desteği için bu eklendi
        #        application.add_handler(CommandHandler(COMMAND, p_handler))    #harf boyutu desteği için bu iptal
    LOG.info("P handler registered.")

'''
bilgi eksikliği yaşanmasın diye /P help veya /P ? çağrısı da yardım mesajı döndürebilir:
        elif args[0] in {"help", "?", "h"}:
            await update.message.reply_text(HELP)
            return

✅ SONUÇ
Kodun oldukça başarılı. Sadece birkaç küçük kullanıcı hatası durumunu ele alırsan çok daha sağlam hale gelir. Eğer istersen /P komutunu inline butonlarla, emoji artı grafikli versiyonlarla da zenginleştirebiliriz.
İstersen /P için test case veya unittest yapısı da öneririm.
Hazırsan /P komutuna bağlı şekilde fiyat alarmı, analiz linki gibi entegre özellikler de eklenebilir.
'''
