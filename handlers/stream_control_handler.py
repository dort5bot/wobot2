# Dinamik stream ekleme/çıkarma (
#handlers/stream_control_handler.py

from telegram.ext import CommandHandler

async def add_stream(update, context):
    symbol = context.args[0].upper() if context.args else None
    if not symbol:
        await update.message.reply_text("Usage: /add_stream SYMBOL")
        return
    # stream_mgr global veya singleton olmalı
    stream_mgr.add_symbol(symbol)
    await update.message.reply_text(f"Stream added: {symbol}")

async def remove_stream(update, context):
    symbol = context.args[0].upper() if context.args else None
    if not symbol:
        await update.message.reply_text("Usage: /remove_stream SYMBOL")
        return
    stream_mgr.remove_symbol(symbol)
    await update.message.reply_text(f"Stream removed: {symbol}")

def register(application):
    application.add_handler(CommandHandler("add_stream", add_stream))
    application.add_handler(CommandHandler("remove_stream", remove_stream))
