# handlers/alarm_handler.py - auto-converted stub to use data_provider
from utils import data_provider as dp
async def register(app):
    # app.add_handler(CommandHandler(...)) - implement registration in your main.py
    pass

# Example handler function
async def handle(update, context):
    # replace with proper command logic
    data = dp.get_price("BTCUSDT")
    try:
        await update.message.reply_text(f"BTCUSDT: {data}")
    except Exception:
        if hasattr(context, 'bot'):
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"BTCUSDT: {data}")
