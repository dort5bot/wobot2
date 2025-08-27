#alarm_handler.py


import logging
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from utils.apikey_utils import (
    add_alarm,
    get_user_alarms,
    delete_alarm,       # düzeltildi
    cleanup_old_alarms
)
from utils.monitoring import telegram_alert

LOG = logging.getLogger("alarm_handler")
LOG.addHandler(logging.NullHandler())


# ---------------- ALARM İŞLEMLERİ ---------------- #
def create_alarm(user_id: int, alarm_type: str, value: str):
    """Yeni alarm oluşturur"""
    add_alarm(user_id, {"type": alarm_type, "value": value})
    LOG.info(f"Alarm eklendi | user_id={user_id}, type={alarm_type}, value={value}")
    telegram_alert(f"✅ Alarm oluşturuldu\n📌 Tür: {alarm_type}\n🎯 Değer: {value}")


def trigger_alarm(user_id: int, alarm_id: int, message: str):
    """Alarm tetikler ve siler"""
    telegram_alert(f"🚨 Alarm Tetiklendi!\n\n{message}")
    delete_alarm(alarm_id)  # düzeltildi
    LOG.info(f"Alarm tetiklendi ve silindi | user_id={user_id}, alarm_id={alarm_id}")


def list_alarms(user_id: int):
    """Mevcut alarmları listeler"""
    alarms = get_user_alarms(user_id)
    if not alarms:
        telegram_alert("ℹ️ Henüz kayıtlı alarmınız yok.")
        return
    msg_lines = ["📋 Mevcut Alarmlar:"]
    for alarm_id, alarm_type, value, created_at in alarms:
        msg_lines.append(f"#{alarm_id} | {alarm_type} = {value} | ⏱ {created_at}")
    telegram_alert("\n".join(msg_lines))


def cleanup_old(days: int = 60):
    """Belirtilen günden eski alarmları siler"""
    cleanup_old_alarms(days)
    LOG.info(f"{days} günden eski alarmlar temizlendi.")
    telegram_alert(f"🧹 {days} günden eski alarmlar temizlendi.")


# ---------------- TELEGRAM KOMUTLARI ---------------- #
async def _cmd_alarm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /alarm komutu:
    /alarm add [tip] [değer] → Alarm ekler
    /alarm list → Alarmları listeler
    /alarm clean [gün] → Eski alarmları temizler
    """
    try:
        user_id = update.effective_user.id
        args = context.args

        if not args:
            await update.message.reply_text("❌ Kullanım: /alarm add|list|clean ...")
            return

        action = args[0].lower()

        if action == "add" and len(args) >= 3:
            alarm_type = args[1]
            value = args[2]
            create_alarm(user_id, alarm_type, value)
            await update.message.reply_text(f"✅ Alarm eklendi: {alarm_type} = {value}")

        elif action == "list":
            alarms = get_user_alarms(user_id)
            if not alarms:
                await update.message.reply_text("ℹ️ Henüz kayıtlı alarmınız yok.")
                return
            msg_lines = ["📋 Mevcut Alarmlar:"]
            for alarm_id, alarm_type, value, created_at in alarms:
                msg_lines.append(f"#{alarm_id} | {alarm_type} = {value} | ⏱ {created_at}")
            await update.message.reply_text("\n".join(msg_lines))

        elif action == "clean":
            days = int(args[1]) if len(args) >= 2 else 60
            cleanup_old_alarms(days)
            await update.message.reply_text(f"🧹 {days} günden eski alarmlar temizlendi.")

        else:
            await update.message.reply_text("❌ Geçersiz kullanım. /alarm add|list|clean")

    except Exception as e:
        LOG.error(f"Alarm komutunda hata: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Hata: {e}")


# ---------------- PLUGIN LOADER İÇİN ---------------- #
def register(application):
    """Plugin loader tarafından çağrılır"""
    application.add_handler(CommandHandler("alarm", _cmd_alarm))
    LOG.info("Alarm handler registered.")
