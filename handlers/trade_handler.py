# handlers/trade_handler.py
from utils.personal_trader import personal_trader

async def buy(update, context):
    user_id = update.effective_user.id
    
    try:
        # Sadece kişisel API ile işlem yap
        result = await personal_trader.execute_trade(user_id, {
            'symbol': context.args[0],
            'side': 'BUY',
            'quantity': context.args[1],
            'price': context.args[2] if len(context.args) > 2 else None
        })
        await update.message.reply_text(f"✅ Trade başarılı: {result}")
    except Exception as e:
        await update.message.reply_text(f"❌ Hata: {str(e)}")

async def set_alarm_handler(update, context):
    user_id = update.effective_user.id
    
    try:
        result = await personal_trader.set_alarm(user_id, {
            'symbol': context.args[0],
            'condition': context.args[1],
            'price': context.args[2]
        })
        await update.message.reply_text(f"⏰ Alarm kuruldu: {result}")
    except Exception as e:
        await update.message.reply_text(f"❌ Alarm hatası: {str(e)}")
