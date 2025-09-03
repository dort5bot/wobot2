#binance_api.py 903-1614
'''
✅ Tüm API çağrıları try-except ile sarıldı
✅ Tutarlı logging kullanımı sağlandı
✅ Config yönetimi optimize edildi
✅ Circuit breaker pattern ile hata yönetimi
✅ Akıllı retry mekanizması (exponential backoff)
✅ Öncelik tabanlı işleme (semaphore ile concurrency kontrolü)
✅ Gelişmiş caching (endpoint bazlı TTL)
✅ Batch processing desteği
✅ Dinamik rate limiting
✅ WebSocket otomatik recovery
✅ Graceful shutdown
✅ Detaylı metrikler ve monitoring
.env den aldığı kişisel api key+api secret ile,sadece api gerektiren verileri çeker,sadece market verisi ceker (fiyat, funding,...)
'''

# utils/binance_api.py

import os
import time
import hmac
import hashlib
import json
import asyncio
import logging
import random
import httpx
import websockets
import pandas as pd
from typing import Any, Dict, List, Optional, Tuple, Callable, Union, Set
from aiolimiter import AsyncLimiter
from urllib.parse import urlencode
from dataclasses import dataclass
from collections import defaultdict
from enum import Enum
import ccxt.async_support as ccxt

from utils.config import CONFIG

# -------------------------------------------------------------
# Config Validation - Eksik ayarları kontrol et
# -------------------------------------------------------------
def _validate_config():
    required = ["BASE_URL", "REQUEST_TIMEOUT", "CONCURRENCY", "CACHE_TTL", "WS_RECONNECT_DELAY", "LOG_LEVEL"]
    missing = [r for r in required if not hasattr(CONFIG.BINANCE, r)]
    if missing:
        raise RuntimeError(f"Missing Binance config keys: {missing}")

_validate_config()

# -------------------------------------------------------------
# Logger - Tüm dosyada tutarlı logging
# -------------------------------------------------------------
LOG = logging.getLogger(__name__)
LOG.setLevel(CONFIG.BINANCE.LOG_LEVEL)
if not LOG.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    LOG.addHandler(handler)

# -------------------------------------------------------------
# CCXT-based BinanceAPI
# -------------------------------------------------------------
