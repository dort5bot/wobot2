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

import asyncio
import os
from typing import Dict, Optional, Any
import ccxt

# LOG nesnesinin tanımlandığını varsayalım (gerçek kodda mevcut olmalı)
LOG = ...  # Bu satır gerçek uygulamada uygun şekilde tanımlanmalı


class BinanceAPI:
    """Binance API'sine erişim için CCXT tabanlı istemci yöneticisi."""
    
    def __init__(self) -> None:
        """BinanceAPI sınıfını başlat.
        
        Attributes:
            clients: API anahtarlarını istemci nesnelerine eşleyen sözlük
            global_client: Global Binance istemcisi
            lock: Eşzamanlı erişim için kilit nesnesi
        """
        self.clients: Dict[str, Any] = {}  # api_key -> client mapping
        self.global_client: Optional[ccxt.binance] = None
        self.lock: asyncio.Lock = asyncio.Lock()
    
    async def initialize_global_client(self) -> None:
        """.env'deki API anahtarı ile global CCXT istemcisi oluştur.
        
        Çevresel değişkenlerden BINANCE_API_KEY ve BINANCE_API_SECRET değerlerini
        kullanarak global bir Binance istemcisi oluşturur. Eğer API anahtarları
        bulunamazsa anonim bir istemci oluşturur.
        
        Raises:
            Exception: İstemci oluşturulurken veya piyasa verileri yüklenirken hata
        """
        try:
            api_key = os.getenv('BINANCE_API_KEY')
            api_secret = os.getenv('BINANCE_API_SECRET')
            
            config: Dict[str, Any] = {
                'enableRateLimit': True,
                'rateLimit': 1000,  # ms
                'options': {
                    'defaultType': 'spot',  # veya 'future'
                    'adjustForTimeDifference': True,
                }
            }
            
            if api_key and api_secret:
                config['apiKey'] = api_key
                config['secret'] = api_secret
                self.global_client = ccxt.binance(config)
                LOG.info("Global Binance client (CCXT) .env API key ile oluşturuldu")
            else:
                self.global_client = ccxt.binance(config)
                LOG.warning(".env'de API key bulunamadı, anonymous CCXT client oluşturuldu")
                
            # CCXT client'ını load markets ile initialize et
            await self.global_client.load_markets()
                
        except Exception as e:
            LOG.error(f"Global client (CCXT) oluşturulamadı: {e}")
            # Fallback
            try:
                self.global_client = ccxt.binance({'enableRateLimit': True})
                await self.global_client.load_markets()
            except Exception as fallback_error:
                LOG.error(f"Fallback CCXT client da oluşturulamadı: {fallback_error}")
                self.global_client = None


# Singleton instance
binance_api = BinanceAPI()


async def get_global_binance_client() -> Optional[ccxt.binance]:
    """Global Binance istemcisini döndürür, eğer yoksa oluşturur.
    
    Returns:
        Optional[ccxt.binance]: Başlatılmış Binance istemcisi veya None
    """
    if binance_api.global_client is None:
        await binance_api.initialize_global_client()
    return binance_api.global_client

# -------------------------------------------------------------
# Enum'lar ve Sabitler
# -------------------------------------------------------------
class RequestPriority(Enum):
    """İstek öncelik seviyelerini tanımlayan enum."""
    HIGH = 1
    NORMAL = 2
    LOW = 3


class CircuitState(Enum):
    """Devre kesici durumlarını tanımlayan enum."""
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


# -------------------------------------------------------------
# Data Classes for Metrics
# -------------------------------------------------------------
@dataclass
class RequestMetrics:
    """HTTP istek metriklerini saklamak için veri sınıfı."""
    total_requests: int = 0
    failed_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    rate_limited_requests: int = 0
    avg_response_time: float = 0.0
    last_request_time: float = 0.0


@dataclass
class WSMetrics:
    """WebSocket metriklerini saklamak için veri sınıfı."""
    total_connections: int = 0
    failed_connections: int = 0
    messages_received: int = 0
    reconnections: int = 0
    avg_message_rate: float = 0.0


# -------------------------------------------------------------
# Circuit Breaker Pattern - Gelişmiş Hata Yönetimi
# -------------------------------------------------------------
class CircuitBreaker:
    """Devre kesici deseni için gelişmiş hata yönetimi sınıfı."""
    
    def __init__(self, failure_threshold: int = 5, reset_timeout: int = 60, name: str = "default") -> None:
        """CircuitBreaker sınıfını başlat.
        
        Args:
            failure_threshold: Devreyi açmak için gereken maksimum hata sayısı
            reset_timeout: Devreyi kapatmadan önce beklenecek süre (saniye)
            name: Devre kesici için tanımlayıcı isim
        """
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = CircuitState.CLOSED
        self.name = name
        self.success_count = 0
        LOG.info(f"CircuitBreaker '{name}' initialized with threshold {failure_threshold}, timeout {reset_timeout}")

    async def execute(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Bir fonksiyonu devre kesici kontrolü ile çalıştır.
        
        Args:
            func: Çalıştırılacak async fonksiyon
            *args: Fonksiyon pozisyonel argümanları
            **kwargs: Fonksiyon keyword argümanları
            
        Returns:
            Any: Fonksiyonun dönüş değeri
            
        Raises:
            Exception: Devre kesici açıksa veya fonksiyon hata verirse
        """
        current_time = time.time()

        if self.state == CircuitState.OPEN:
            if current_time - self.last_failure_time > self.reset_timeout:
                self.state = CircuitState.HALF_OPEN
                self.success_count = 0
                LOG.warning(f"CircuitBreaker '{self.name}' moving to HALF_OPEN state")
            else:
                remaining = self.reset_timeout - (current_time - self.last_failure_time)
                LOG.error(f"CircuitBreaker '{self.name}' is OPEN. Retry in {remaining:.1f}s")
                raise Exception(f"Circuit breaker is OPEN. Retry in {remaining:.1f}s")

        try:
            result = await func(*args, **kwargs)

            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= max(1, self.failure_threshold // 2):
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    LOG.info(f"CircuitBreaker '{self.name}' reset to CLOSED state after {self.success_count} successful executions")
            else:
                if self.state == CircuitState.CLOSED and self.failure_count > 0:
                    # decay failures slowly
                    self.failure_count = max(0, self.failure_count - 1)

            return result

        except Exception as e:
            self.last_failure_time = time.time()
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
                self.failure_count = self.failure_threshold
                LOG.error(f"CircuitBreaker '{self.name}' reverted to OPEN from HALF_OPEN due to failure")
            else:
                self.failure_count += 1
                if self.failure_count >= self.failure_threshold:
                    self.state = CircuitState.OPEN
                    LOG.error(f"CircuitBreaker '{self.name}' tripped to OPEN state due to {self.failure_count} failures")

            LOG.error(f"CircuitBreaker '{self.name}' execution failed: {str(e)}")
            raise

    def get_status(self) -> Dict[str, Any]:
        """Devre kesicinin mevcut durum bilgisini döndür.
        
        Returns:
            Dict[str, Any]: Durum bilgilerini içeren sözlük
        """
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_time": self.last_failure_time,
            "time_since_last_failure": time.time() - self.last_failure_time if self.last_failure_time > 0 else 0
        }


# Global circuit breaker instances
binance_circuit_breaker = CircuitBreaker(
    failure_threshold=CONFIG.BINANCE.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    reset_timeout=CONFIG.BINANCE.CIRCUIT_BREAKER_RESET_TIMEOUT,
    name="binance_main"
)

# -------------------------------------------------------------
# HTTP Katmanı: Retry + Exponential Backoff + TTL Cache
# -------------------------------------------------------------
