##handlers/paper_handler.py
import os
from telegram import Update
from telegram.ext import ContextTypes
from utils.paper_utils import log_paper_trade, get_paper_trades

PAPER_MODE = os.getenv("PAPER_MODE", "false").lower() == "true"

async def paper_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not PAPER_MODE:
        await update.message.reply_text("❌ Paper mode devre dışı.")
        return

    user_id = update.effective_user.id
    args = context.args

    if len(args) < 3:
        await update.message.reply_text("Kullanım: /paper <buy/sell> <symbol> <miktar>")
        return

    action = args[0].lower()
    symbol = args[1].upper()
    quantity = float(args[2])
    price = 100  # Burada gerçek fiyat API'den çekilebilir

    log_paper_trade(user_id, action, symbol, quantity, price)
    await update.message.reply_text(f"📄 Paper trade kaydedildi: {action.upper()} {quantity} {symbol} @ {price}")

async def paper_log_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    trades = get_paper_trades(user_id)
    if not trades:
        await update.message.reply_text("📭 Henüz paper trade kaydınız yok.")
        return

    msg = "📜 Paper Trade Log:\n"
    for t in trades:
        msg += f"{t[0].upper()} {t[1]} {t[2]} adet @ {t[3]} USD ({t[4]})\n"
    await update.message.reply_text(msg)
