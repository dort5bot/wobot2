# cglas_handler.py

import logging
import sys
import asyncio
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from utils.coinglass_utils import (
    etf_btc_list, etf_btc_flows_history, etf_eth_list, etf_eth_flows_history,
    futures_supported_coins, spot_supported_coins,
    option_info, futures_liquidation_history,
    open_interest_exchange_list
)

LOG = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())

# -------------------------
# Async handler fonksiyonu
# -------------------------
async def handler(command: str, arg: str = None):
    """Kullanıcı komutuna göre ilgili endpoint fonksiyonunu async olarak çağırır."""
    if command == "/etf":
        btc = await etf_btc_list()
        eth = await etf_eth_list()
        return f"Bitcoin ETF Listesi:\n{btc}\n\nEthereum ETF Listesi:\n{eth}"

    elif command == "/etf" and arg == "BTC":
        btc_flows = await etf_btc_flows_history()
        return f"Bitcoin ETF Akış Geçmişi:\n{btc_flows}"

    elif command == "/etf" and arg == "ETH":
        eth_flows = await etf_eth_flows_history()
        return f"Ethereum ETF Akış Geçmişi:\n{eth_flows}"

    elif command == "/cglas":
        fut = await futures_supported_coins()
        spot = await spot_supported_coins()
        opt = await option_info()
        return f"Desteklenen Futures Coin’ler:\n{fut}\n\nDesteklenen Spot Coin’ler:\n{spot}\n\nOptions Bilgisi:\n{opt}"

    elif command == "/liq" and arg:
        liq = await futures_liquidation_history(pair=arg)
        return f"{arg} Futures Liquidation History:\n{liq}"

    elif command == "/oi" and arg:
        oi = await open_interest_exchange_list(symbol=arg)
        return f"{arg} Futures Open Interest Exchange List:\n{oi}"

    else:
        return "Geçersiz komut. Kullanılabilir komutlar: /etf [BTC|ETH], /cglas, /liq <pair>, /oi <symbol>"

# -------------------------
# Plugin loader async komutlar
# -------------------------
async def etf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        result = await handler("/etf")
    elif args[0].upper() == "BTC":
        result = await handler("/etf", "BTC")
    elif args[0].upper() == "ETH":
        result = await handler("/etf", "ETH")
    else:
        await update.message.reply_text("Geçersiz argüman. Kullanım: /etf [BTC|ETH]")
        return
    await update.message.reply_text(str(result))

async def cglas_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = await handler("/cglas")
    await update.message.reply_text(str(result))

async def liq_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if args:
        result = await handler("/liq", args[0])
        await update.message.reply_text(str(result))
    else:
        await update.message.reply_text("Kullanım: /liq <pair>")

async def oi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if args:
        result = await handler("/oi", args[0])
        await update.message.reply_text(str(result))
    else:
        await update.message.reply_text("Kullanım: /oi <symbol>")

# -------------------------
# Register plugin
# -------------------------
def register(app):
    app.add_handler(CommandHandler("etf", etf_command))
    app.add_handler(CommandHandler("cglas", cglas_command))
    app.add_handler(CommandHandler("liq", liq_command))
    app.add_handler(CommandHandler("oi", oi_command))

# -------------------------
# CLI Async test
# -------------------------
async def main_cli():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    res = await handler(cmd, arg)
    print(res)

if __name__ == "__main__":
    asyncio.run(main_cli())
