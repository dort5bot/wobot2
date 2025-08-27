# handlers/cmc_etf_handler.py
#coinmarketcap
import logging
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from utils.cmc_api import get_latest_listings, get_global_metrics
import aiohttp
import asyncio

LOG = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())

COMMAND = "cmc"

# -------------------------
# ETF verisi placeholder (Coinglass veya başka API ile doldurulacak)
# -------------------------
async def get_etf_data() -> str:
    # Örnek: Coinglass API veya başka veri kaynağı
    # Bu kısmı kendi API ile doldurabilirsiniz
    try:
        async with aiohttp.ClientSession() as session:
            # URL ve headers örnektir
            url = "https://api.coinglass.com/api/pro/v1/etf/funds"  
            headers = {"API-KEY": "YOUR_COINGLASS_API_KEY"}
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Örnek: top 3 ETF
                    etf_report = ""
                    for etf in data.get("data", [])[:3]:
                        etf_report += f"{etf.get('name')}: ${etf.get('value')}\n"
                    return etf_report
    except Exception as e:
        LOG.exception(f"ETF Data fetch error: {e}")
    return "ETF verisi alınamadı."

# -------------------------
# /cmc handler
# -------------------------
async def cmc_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "📊 CoinMarketCap + ETF Piyasa Raporu\n\n"
    
    # 1️⃣ Global Metrics
    metrics = await get_global_metrics()
    if metrics:
        btc_dom = metrics.get("btc_dominance", 0)
        eth_dom = metrics.get("eth_dominance", 0)
        total_cap = metrics.get("total_market_cap", {}).get("USD", 0)
        total_vol = metrics.get("total_volume_24h", {}).get("USD", 0)
        
        msg += f"🌐 Global Piyasa:\n"
        msg += f"Toplam Market Cap: ${total_cap:,.0f}\n"
        msg += f"24H Hacim: ${total_vol:,.0f}\n"
        msg += f"BTC Dominance: {btc_dom:.2f}%\n"
        msg += f"ETH Dominance: {eth_dom:.2f}%\n\n"
    else:
        msg += "Global piyasa verisi alınamadı.\n\n"

    # 2️⃣ Top 10 Coin
    listings = await get_latest_listings(limit=10)
    if listings:
        msg += "🏆 Top 10 Coin (USD)\n"
        for coin in listings:
            symbol = coin.get("symbol", "")
            price = coin.get("quote", {}).get("USD", {}).get("price", 0)
            change24h = coin.get("quote", {}).get("USD", {}).get("percent_change_24h", 0)
            msg += f"{symbol}: ${price:,.2f} ({change24h:+.2f}%)\n"
    else:
        msg += "Top coin verisi alınamadı.\n"

    # 3️⃣ ETF verisi
    etf_report = await get_etf_data()
    msg += f"\n💹 ETF Verileri:\n{etf_report}"

    # Mesajı gönder
    await update.message.reply_text(msg)

# -------------------------
# register fonksiyonu handler_loader uyumlu
# -------------------------
def register(application):
    application.add_handler(CommandHandler(COMMAND, cmc_handler))
    LOG.info("cmc_etf_handler registered.")
