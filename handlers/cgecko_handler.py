#cgecko_handler.py

from telegram import Update
from telegram.ext import CallbackContext, CommandHandler
from utils.coingecko_utils import CoinGeckoAPI

cg = CoinGeckoAPI()

def cko(update: Update, context: CallbackContext):
    """
    /cko komutu: Piyasa ve temel coin bilgileri raporu
    """
    chat_id = update.effective_chat.id

    # 1️⃣ BTC ve ETH fiyatları
    prices = cg.get_price(ids="bitcoin,ethereum", vs_currencies="usd")

    # 2️⃣ BTC ve ETH piyasa verileri
    market_data = cg.get_market_data(ids="bitcoin,ethereum", vs_currency="usd")

    # 3️⃣ Trend coinler
    trending = cg.get_trending_coins()

    # 4️⃣ Global piyasa verileri
    global_data = cg.get_global_data()

    # Rapor formatı
    report = "📊 *CoinGecko Piyasa Raporu*\n\n"

    if prices:
        report += f"*Bitcoin (BTC) Fiyat:* ${prices.get('bitcoin', {}).get('usd', 'N/A')}\n"
        report += f"*Ethereum (ETH) Fiyat:* ${prices.get('ethereum', {}).get('usd', 'N/A')}\n\n"

    if market_data:
        for coin in market_data:
            report += f"{coin['name']} Market Cap: ${coin.get('market_cap', 'N/A'):,}\n"

    if trending:
        report += "\n🔥 *Trend Coinler:* \n"
        for c in trending[:5]:
            coin_info = c.get("item", {})
            report += f"- {coin_info.get('name')} ({coin_info.get('symbol')})\n"

    if global_data:
        report += f"\n🌐 *Toplam Piyasa Değeri:* ${global_data.get('total_market_cap', {}).get('usd', 'N/A'):,}\n"
        report += f"*24s Hacim:* ${global_data.get('total_volume', {}).get('usd', 'N/A'):,}\n"

    context.bot.send_message(chat_id=chat_id, text=report, parse_mode="Markdown")

# 🔹 Register fonksiyonu
def register(application):
    """
    /cko komutunu bot uygulamasına ekler.
    """
    application.add_handler(CommandHandler("cko", cko))
