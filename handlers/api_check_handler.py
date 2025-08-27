###api kontrol araci
#♦️api_check_handler.py

import asyncio
from telegram import Update
from telegram.ext import ContextTypes
from utils.api_check import test_coinglass_api

async def api_check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Coinglass API anahtarını parametre olarak alır ve test eder.
    Kullanım: /api_c <API_KEY>
    """
    if not context.args:
        await update.message.reply_text("❌ Lütfen API anahtarını yazın.\nÖrnek: /api_c 1234abcd...")
        return
    
    api_key = context.args[0]
    await update.message.reply_text(f"⏳ API anahtarı test ediliyor: `{api_key}`", parse_mode="Markdown")

    result_text = await asyncio.to_thread(test_coinglass_api, api_key)
    await update.message.reply_text(result_text)
