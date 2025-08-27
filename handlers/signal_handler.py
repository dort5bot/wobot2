# handlers/signal_handler.py
# Mock dosya: Publish ve log fonksiyonu içeriyor.
import logging

LOG = logging.getLogger("signal_handler")

def publish_signal(signal_name: str, data: dict = None):
    """
    Sinyal yayınlama fonksiyonu (mock)
    signal_name: Sinyal adı
    data: Opsiyonel ek veri
    """
    if data is None:
        data = {}
    LOG.info(f"Signal published: {signal_name} | Data: {data}")
    print(f"[MOCK] Signal published: {signal_name} | Data: {data}")
