##handlers/ap_handler.py

import asyncio
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from utils.ap_utils import build_ap_report_lines

# /ap -> 3 satırlık özet skor
async def ap_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("AP skor raporu hazırlanıyor... ⏳")
    try:
        lines = await build_ap_report_lines()
        text = "\n".join(lines)
        await msg.edit_text(text)
    except Exception as e:
        await msg.edit_text(f"❌ Hata oluştu: {e}")

def register(application):
    application.add_handler(CommandHandler("ap", ap_handler))
