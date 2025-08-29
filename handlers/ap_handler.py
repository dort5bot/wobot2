'''
ap_handler.py — publick + private (api)
ap_handler iki moda ayrılarak API key olanlara avantajlar sunacak şekilde geliştirilebilir.
✨ Gelecekte Ekleyebileceğin Avantajlar
Kaldıraç ve margin riski analizi (private mode)
Gerçek zamanlı likidasyon riski tespiti
Portföy bazlı short/long uyumu
Otomatik pozisyon uyarısı: “Pozisyonun AP skoru düştü!” gibi

'''
from utils.apikey_utils import get_apikey

async def ap_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("AP skor raporu hazırlanıyor... ⏳")
    try:
        user_id = update.effective_user.id
        symbols = context.args if context.args else ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

        user_key = get_apikey(user_id)

        if user_key:
            try:
                api_key, secret_key = user_key.split(":")
                client = BinanceClient(api_key, secret_key)
                mode = "private"
            except ValueError:
                await msg.edit_text("❌ API key format hatası.")
                return
        else:
            client = BinanceClient()  # Public erişim
            mode = "public"

        results = await build_ap_report_lines_pro(client=client, symbols=symbols)

        text = "\n".join(results)

        if mode == "private":
            text += "\n\n🔐 Özel API verisi ile daha hassas skorlar hesaplandı."

        await msg.edit_text(text)

    except Exception as e:
        await msg.edit_text(f"❌ Hata oluştu: {e}")
