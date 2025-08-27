# handlers/cglas_handler.py
from coinglass_utils import (
    etf_btc_list, etf_btc_flows_history, etf_eth_list, etf_eth_flows_history,
    futures_supported_coins, spot_supported_coins,
    option_info, futures_liquidation_history, spot_price_history,
    open_interest_exchange_list
)
from telegram.ext import CommandHandler
import sys


def handler(command: str, arg: str = None):
    """Kullanıcı komutuna göre ilgili endpoint fonksiyonunu çağırır."""
    if command == "/etf":
        print("Bitcoin ETF Listesi:")
        print(etf_btc_list())
        print("\nEthereum ETF Listesi:")
        print(etf_eth_list())
    elif command == "/etf" and arg == "BTC":
        print("Bitcoin ETF Akış Geçmişi:")
        print(etf_btc_flows_history())
    elif command == "/etf" and arg == "ETH":
        print("Ethereum ETF Akış Geçmişi:")
        print(etf_eth_flows_history())
    elif command == "/cglas":
        print("Desteklenen Futures Coin’ler:")
        print(futures_supported_coins())
        print("\nDesteklenen Spot Coin’ler:")
        print(spot_supported_coins())
        print("\nOptions Bilgisi:")
        print(option_info())
    elif command == "/liq" and arg:
        print(f"{arg} Futures Liquidation History:")
        print(futures_liquidation_history(pair=arg))
    elif command == "/oi" and arg:
        print(f"{arg} Futures Open Interest Exchange List:")
        print(open_interest_exchange_list(symbol=arg))
    else:
        print("Geçersiz komut. Kullanılabilir komutlar: /etf [BTC|ETH], /cglas, /liq <pair>, /oi <symbol>")


# --plugin loader uyumluluk
def etf_command(update, context):
    args = context.args
    if not args:
        handler("/etf")
    elif args[0].upper() == "BTC":
        handler("/etf", "BTC")
    elif args[0].upper() == "ETH":
        handler("/etf", "ETH")
    else:
        update.message.reply_text("Geçersiz argüman. Kullanım: /etf [BTC|ETH]")


def cglas_command(update, context):
    handler("/cglas")


def liq_command(update, context):
    args = context.args
    if args:
        handler("/liq", args[0])
    else:
        update.message.reply_text("Kullanım: /liq <pair>")


def oi_command(update, context):
    args = context.args
    if args:
        handler("/oi", args[0])
    else:
        update.message.reply_text("Kullanım: /oi <symbol>")


def register(app):
    app.add_handler(CommandHandler("etf", etf_command))
    app.add_handler(CommandHandler("cglas", cglas_command))
    app.add_handler(CommandHandler("liq", liq_command))
    app.add_handler(CommandHandler("oi", oi_command))


if __name__ == "__main__":
    # Basit CLI: python handler.py /etf BTC
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    handler(cmd, arg)
