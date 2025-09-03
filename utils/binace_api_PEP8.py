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
class BinanceHTTPClient:
    """Binance HTTP API istemcisi için gelişmiş yönetim sınıfı."""
    
    def __init__(self, api_key: Optional[str] = None, secret_key: Optional[str] = None) -> None:
        """BinanceHTTPClient sınıfını başlat.
        
        Args:
            api_key: Binance API anahtarı (opsiyonel)
            secret_key: Binance gizli anahtarı (opsiyonel)
        """
        # 🔹 API key/secret
        self.api_key = api_key
        self.secret_key = secret_key
        self._last_request = 0

        # 🔹 aiolimiter: config üzerinden ayarlanabilir
        self.limiter = AsyncLimiter(
            CONFIG.BINANCE.LIMITER_RATE,
            CONFIG.BINANCE.LIMITER_PERIOD
        )

        LOG.info(f"HTTP Client initialized, has_keys: {bool(self.api_key and self.secret_key)}")

        # 🔹 HTTP client configuration
        self.client = httpx.AsyncClient(
            base_url=CONFIG.BINANCE.BASE_URL,
            timeout=CONFIG.BINANCE.REQUEST_TIMEOUT,
            limits=httpx.Limits(
                max_connections=CONFIG.BINANCE.CONCURRENCY * 2,
                max_keepalive_connections=CONFIG.BINANCE.CONCURRENCY,
                keepalive_expiry=300
            ),
            http2=True,
            verify=True,
            cert=os.getenv("SSL_CERT_PATH")
        )

        # 🔹 Concurrency control with priority support
        self.semaphores = {
            RequestPriority.HIGH: asyncio.Semaphore(CONFIG.BINANCE.CONCURRENCY),
            RequestPriority.NORMAL: asyncio.Semaphore(CONFIG.BINANCE.CONCURRENCY),
            RequestPriority.LOW: asyncio.Semaphore(CONFIG.BINANCE.CONCURRENCY // 2),
        }

        # 🔹 Cache system
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._last_cache_cleanup = time.time()

        # 🔹 Rate limiting timing
        self.last_request_time = 0
        self.min_request_interval = 1.0 / CONFIG.BINANCE.MAX_REQUESTS_PER_SECOND

        # 🔹 Metrics
        self.metrics = RequestMetrics()
        self.request_times: List[float] = []

    def _cleanup_cache(self) -> None:
        """Süresi dolmuş önbellek girdilerini temizle - daha verimli versiyon."""
        current_time = time.time()
        # Sık temizlemeyi önle
        if current_time - self._last_cache_cleanup < CONFIG.BINANCE.CACHE_CLEANUP_INTERVAL:
            return

        # Expired anahtarları topla
        expired_keys = [key for key, (ts, _) in self._cache.items()
                        if current_time - ts > CONFIG.BINANCE.BINANCE_TICKER_TTL]

        for key in expired_keys:
            del self._cache[key]

        # Cache boyutu sınırlaması
        if len(self._cache) > 1000:
            oldest_keys = sorted(self._cache.keys(), key=lambda k: self._cache[k][0])[:100]
            for key in oldest_keys:
                del self._cache[key]
            LOG.debug("Cache limit exceeded. Removed 100 oldest records")

        self._last_cache_cleanup = current_time
        LOG.debug(f"Cache cleanup completed. Removed {len(expired_keys)} expired entries.")

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False,
        futures: bool = False,
        max_retries: Optional[int] = None,
        priority: RequestPriority = RequestPriority.NORMAL,
    ) -> Any:
        """Ana HTTP request methodu - Tüm istekler buradan geçer.
        
        Exponential backoff ile retry, rate limiting, caching ve error handling
        özelliklerini içerir.
        
        Args:
            method: HTTP metodu (GET, POST, vb.)
            path: API endpoint yolu
            params: İstek parametreleri
            signed: İmzalı istek gerekiyor mu
            futures: Futures API kullanılacak mı
            max_retries: Maksimum yeniden deneme sayısı
            priority: İstek önceliği
            
        Returns:
            Any: API yanıt verisi
            
        Raises:
            ValueError: İmzalı istek için API anahtarı yoksa
            Exception: Maksimum yeniden deneme sayısı aşılırsa veya diğer hatalar
        """
        try:
            if max_retries is None:
                max_retries = CONFIG.BINANCE.DEFAULT_RETRY_ATTEMPTS

            # 🔹 Minimum interval kontrolü
            current_time = time.time()
            time_since_last = current_time - self.last_request_time
            if time_since_last < self.min_request_interval:
                await asyncio.sleep(self.min_request_interval - time_since_last)

            self.last_request_time = time.time()
            self.metrics.total_requests += 1

            # 🔹 Base URL ve headers
            base_url = CONFIG.BINANCE.FAPI_URL if futures else CONFIG.BINANCE.BASE_URL
            headers = {}
            params = params or {}

            # 🔹 Signed request
            if signed:
                if not self.api_key or not self.secret_key:
                    raise ValueError("API key and secret key are required for signed requests")
                signed_params = dict(params)
                signed_params["timestamp"] = int(time.time() * 1000)
                query = urlencode(signed_params)
                signature = hmac.new(self.secret_key.encode(), query.encode(), hashlib.sha256).hexdigest()
                signed_params["signature"] = signature
                params = signed_params
                headers["X-MBX-APIKEY"] = self.api_key
            # 🔹 Public ama API key mevcutsa yine ekle
            elif self.api_key:
                headers["X-MBX-APIKEY"] = self.api_key

            # 🔹 Cache cleanup
            if time.time() - self._last_cache_cleanup > CONFIG.BINANCE.CACHE_CLEANUP_INTERVAL:
                self._cleanup_cache()
                self._last_cache_cleanup = time.time()

            # 🔹 Cache kontrolü
            cache_key = f"{method}:{base_url}{path}:{json.dumps(params, sort_keys=True) if params else ''}"
            ttl = getattr(CONFIG.BINANCE, "BINANCE_TICKER_TTL", 0)

            if ttl > 0 and cache_key in self._cache:
                ts_cache, data = self._cache[cache_key]
                if time.time() - ts_cache < ttl:
                    self.metrics.cache_hits += 1
                    LOG.debug(f"Cache hit for {cache_key}")
                    return data
                else:
                    self.metrics.cache_misses += 1
                    del self._cache[cache_key]

            # 🔹 Retry loop
            attempt = 0
            last_exception = None
            start_time = time.time()

            while attempt < max_retries:
                attempt += 1
                try:
                    async with self.limiter:  # aiolimiter
                        async with self.semaphores[priority]:
                            r = await self.client.request(method, path, params=params, headers=headers)

                    if r.status_code == 200:
                        data = r.json()
                        if ttl > 0:
                            self._cache[cache_key] = (time.time(), data)

                        response_time = time.time() - start_time
                        self.request_times.append(response_time)
                        if len(self.request_times) > 100:
                            self.request_times.pop(0)

                        self.metrics.avg_response_time = sum(self.request_times) / len(self.request_times)
                        self.metrics.last_request_time = time.time()
                        return data

                    if r.status_code == 429:
                        self.metrics.rate_limited_requests += 1
                        retry_after = int(r.headers.get("Retry-After", 1))
                        delay = min(2 ** attempt, 60) + retry_after
                        LOG.warning(f"Rate limited for {path}. Sleeping {delay}s (attempt {attempt}/{max_retries})")
                        await asyncio.sleep(delay)
                        continue

                    r.raise_for_status()

                except httpx.HTTPStatusError as e:
                    if e.response is not None and e.response.status_code >= 500:
                        delay = min(2 ** attempt, 30)
                        LOG.warning(f"Server error {e.response.status_code} for {path}, retrying in {delay}s")
                        await asyncio.sleep(delay)
                        last_exception = e
                        continue
                    else:
                        self.metrics.failed_requests += 1
                        LOG.error(f"HTTP error {getattr(e.response,'status_code',None)} for {path}: {e}")
                        raise

                except (httpx.RequestError, asyncio.TimeoutError) as e:
                    last_exception = e
                    self.metrics.failed_requests += 1
                    delay = min(2 ** attempt, 60) + random.uniform(0, 0.3)
                    LOG.error(f"Request error for {path}: {e}, retrying in {delay:.1f}s")
                    await asyncio.sleep(delay)

            raise last_exception or Exception(f"Max retries ({max_retries}) exceeded for {path}")

        except Exception as e:
            LOG.error(f"Request failed for {method} {path}: {str(e)}")
            raise

    async def get_server_time(self) -> Dict[str, Any]:
        """Sunucu zamanını getir - Circuit breaker ile sarılı.
        
        Returns:
            Dict[str, Any]: Sunucu zamanı bilgisi
        """
        return await binance_circuit_breaker.execute(self._request, "GET", "/api/v3/time")

    async def get_exchange_info(self) -> Dict[str, Any]:
        """Exchange bilgilerini getir - Circuit breaker ile sarılı.
        
        Returns:
            Dict[str, Any]: Exchange bilgileri
        """
        return await binance_circuit_breaker.execute(self._request, "GET", "/api/v3/exchangeInfo")

    async def get_symbol_price(self, symbol: str) -> Dict[str, Any]:
        """Sembol fiyatını getir - Circuit breaker ile sarılı.
        
        Args:
            symbol: Sembol adı (örn: BTCUSDT)
            
        Returns:
            Dict[str, Any]: Sembol fiyat bilgisi
        """
        return await binance_circuit_breaker.execute(
            self._request, "GET", "/api/v3/ticker/price", 
            {"symbol": symbol.upper()}
        )

    def get_metrics(self) -> RequestMetrics:
        """Request metriklerini getir.
        
        Returns:
            RequestMetrics: İstek metrikleri nesnesi
        """
        return self.metrics

    def reset_metrics(self) -> None:
        """Metrikleri sıfırla."""
        self.metrics = RequestMetrics()
        self.request_times = []

    async def close(self) -> None:
        """HTTP client'ı temiz bir şekilde kapat."""
        try:
            await self.client.aclose()
            LOG.info("HTTP client closed successfully")
        except Exception as e:
            LOG.error(f"Error closing HTTP client: {e}")

# -------------------------------------------------------------
# WebSocket Manager - Gelişmiş Reconnect ve Error Handling
# -------------------------------------------------------------
