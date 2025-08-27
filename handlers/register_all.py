#register_all.py
# buraya ekleme manuel olacak
#Sadece kullanıcı bazlı handler’lar ekleniyor → API key, trade, alarm.
#Güvenlik: Her kullanıcı kendi API key’i ile çalışıyor, başka kullanıcıların key’i veya trades’ine erişim yok.



from telegram.ext import CommandHandler

# --- Import user-specific / kişisel handler register fonksiyonları ---
from handlers.apikey_handler import register as register_user_handlers
# .. Buraya diğer kişisel handler register fonksiyonlarını ekleyebilirsin
# Örnek:
# from handlers.trade_handler import register as register_trade
# from handlers.alarm_handler import register as register_alarm

def register(app):
    """
    Plugin loader uyumlu register fonksiyonu.
    app -> telegram.ext.Application objesi
    Sadece kullanıcı bazlı (API key, trade, alarm) handler’lar ekleniyor.
    Global veri sorgulama handler’ları opsiyonel ve eklenmeyebilir.
    """

    # -----------------------------------
    # 1️⃣ Kişisel / user-specific handler’lar
    # -----------------------------------
    register_user_handlers(app)
    
    # .. Kişisel handler’ları buraya ekle
    # Örnek:
    # register_trade(app)
    # register_alarm(app)

    print("✅ Kişisel handler’lar dispatcher’a eklendi (plugin loader uyumlu).")
