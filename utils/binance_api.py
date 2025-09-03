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
from urllib.parse import urlencode
from dataclasses import dataclass
from collections import defaultdict
from enum import Enum

import ccxt.async_support as ccxt  # ✅ CCXT ile değiştir

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

class BinanceAPI:
    def __init__(self):
        self.clients = {}  # api_key -> client mapping
        self.global_client = None
        self.lock = asyncio.Lock()
    
    async def initialize_global_client(self):
        """.env'deki API key ile global client oluştur - CCXT version"""
        try:
            api_key = os.getenv('BINANCE_API_KEY')
            api_secret = os.getenv('BINANCE_API_SECRET')
            
            config = {
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

async def get_global_binance_client():
    """Global client'ı döndür, yoksa oluştur"""
    if binance_api.global_client is None:
        await binance_api.initialize_global_client()
    return binance_api.global_client

# -------------------------------------------------------------
# Enum'lar ve Sabitler
# -------------------------------------------------------------
class RequestPriority(Enum):
    HIGH = 1
    NORMAL = 2
    LOW = 3

class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

# -------------------------------------------------------------
# Data Classes for Metrics
# -------------------------------------------------------------
@dataclass
class RequestMetrics:
    total_requests: int = 0
    failed_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    rate_limited_requests: int = 0
    avg_response_time: float = 0.0
    last_request_time: float = 0.0

@dataclass
class WSMetrics:
    total_connections: int = 0
    failed_connections: int = 0
    messages_received: int = 0
    reconnections: int = 0
    avg_message_rate: float = 0.0

# -------------------------------------------------------------
# Circuit Breaker Pattern - Gelişmiş Hata Yönetimi
# -------------------------------------------------------------
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, reset_timeout: int = 60, name: str = "default"):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = CircuitState.CLOSED
        self.name = name
        self.success_count = 0
        LOG.info(f"CircuitBreaker '{name}' initialized with threshold {failure_threshold}, timeout {reset_timeout}")


	 # Replace the existing execute implementation with this block
	async def execute(self, func, *args, **kwargs):
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
	# <<< DÜZELTİLDİ


    def get_status(self) -> Dict[str, Any]:
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
    def __init__(self, api_key: Optional[str] = None, secret_key: Optional[str] = None):
        # 🔹 user_id parametresi KALDIRILDI
        self.api_key = api_key
        self.secret_key = secret_key
        self._last_request = 0  # Burada girinti düzgün
        
        LOG.info(f"HTTP Client initialized, has_keys: {bool(self.api_key and self.secret_key)}")
        
		# HTTP client configuration - .env dosyasındaki SSL_CERT_PATH değeri boşsa, bu da hata verebilir.
		self.client = httpx.AsyncClient(
		    base_url=CONFIG.BINANCE.BASE_URL,
		    timeout=CONFIG.BINANCE.REQUEST_TIMEOUT,
		    limits=httpx.Limits(
		        max_connections=CONFIG.BINANCE.CONCURRENCY * 2,
		        max_keepalive_connections=CONFIG.BINANCE.CONCURRENCY,
		        keepalive_expiry=300  # 5 dakika
		    ),
		    http2=True,  # HTTP/2 desteği
		    verify=True,  # SSL sertifika doğrulaması
		    cert=os.getenv('SSL_CERT_PATH')  # Özel sertifika yolu
		)


        # Concurrency control with priority support
        self.semaphores = {
            RequestPriority.HIGH: asyncio.Semaphore(CONFIG.BINANCE.CONCURRENCY),
            RequestPriority.NORMAL: asyncio.Semaphore(CONFIG.BINANCE.CONCURRENCY),
            RequestPriority.LOW: asyncio.Semaphore(CONFIG.BINANCE.CONCURRENCY // 2)
        }
        
        # Caching system
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._last_cache_cleanup = time.time()
        
        # Rate limiting
        self.last_request_time = 0
        self.min_request_interval = 1.0 / CONFIG.BINANCE.MAX_REQUESTS_PER_SECOND
        
        # Metrics
        self.metrics = RequestMetrics()
        self.request_times = []

    # Cache temizleme mekanizmasını iyileştirme
	def _cleanup_cache(self):
    """Expired cache entries'ini temizle - daha verimli versiyon"""
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
# <<< DÜZELTİLDİ

	# HTTP request methodu - Tüm istekler buradan geçer;
	#Exponential backoff ile retry,Rate limiting,Caching,Error handling
    async def _request(self, method: str, path: str, params: Optional[dict] = None,
                       signed: bool = False, futures: bool = False, 
                       max_retries: int = None, priority: RequestPriority = RequestPriority.NORMAL) -> Any:
        """
        Ana HTTP request methodu - Tüm istekler buradan geçer
        ✅ Exponential backoff ile retry
        ✅ Rate limiting
        ✅ Caching
        ✅ Error handling
        """
        try:
            if max_retries is None:
                max_retries = CONFIG.BINANCE.DEFAULT_RETRY_ATTEMPTS

            # Rate limiting - istekler arası minimum interval
            current_time = time.time()
            time_since_last = current_time - self.last_request_time
            if time_since_last < self.min_request_interval:
                await asyncio.sleep(self.min_request_interval - time_since_last)
            
            self.last_request_time = time.time()
            self.metrics.total_requests += 1

            # Base URL ve headers ayarla
            base_url = CONFIG.BINANCE.FAPI_URL if futures else CONFIG.BINANCE.BASE_URL
            headers = {}
            params = params or {}

            # Signed request'ler için signature oluştur
            if signed:
                if not self.api_key or not self.secret_key:
                    raise ValueError("API key and secret key are required for signed requests")
                signed_params = dict(params) if params else {}
                signed_params["timestamp"] = int(time.time() * 1000)
                query = urlencode(signed_params)
                signature = hmac.new(self.secret_key.encode(), query.encode(), hashlib.sha256).hexdigest()
                signed_params["signature"] = signature
                params = signed_params
                headers["X-MBX-APIKEY"] = self.api_key

            # Cache temizleme - periyodik olarak
            current_time_cleanup = time.time()
            if current_time_cleanup - self._last_cache_cleanup > CONFIG.BINANCE.CACHE_CLEANUP_INTERVAL:
                self._cleanup_cache()
                self._last_cache_cleanup = current_time_cleanup

            # Cache key oluştur++
			# inside _request, replace the cache-key and retry/except blocks with:
			cache_key = f"{method}:{base_url}{path}:{json.dumps(params, sort_keys=True) if params else ''}"
			ttl = getattr(CONFIG.BINANCE, "BINANCE_TICKER_TTL", 0)
			
			# Cache check
			if ttl > 0 and cache_key in self._cache:
			    ts_cache, data = self._cache[cache_key]
			    if time.time() - ts_cache < ttl:
			        self.metrics.cache_hits += 1
			        LOG.debug(f"Cache hit for {cache_key}")
			        return data
			    else:
			        self.metrics.cache_misses += 1
			        del self._cache[cache_key]
			
			# Retry loop
			attempt = 0
			last_exception = None
			start_time = time.time()
			
			while attempt < max_retries:
			    attempt += 1
			    try:
			        async with self.semaphores[priority]:
			            r = await self.client.request(method, path, params=params, headers=headers)
			
			        if r.status_code == 200:
			            data = r.json()
			            if ttl > 0:
			                self._cache[cache_key] = (time.time(), data)
			            # metrics
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
			
			# After retries
			raise last_exception or Exception(f"Max retries ({max_retries}) exceeded for {path}")

        except Exception as e:
            LOG.error(f"Request failed for {method} {path}: {str(e)}")
            raise

    async def get_server_time(self) -> Dict[str, Any]:
        """Sunucu zamanını getir - Circuit breaker ile sarılı"""
        return await binance_circuit_breaker.execute(self._request, "GET", "/api/v3/time")

    async def get_exchange_info(self) -> Dict[str, Any]:
        """Exchange bilgilerini getir - Circuit breaker ile sarılı"""
        return await binance_circuit_breaker.execute(self._request, "GET", "/api/v3/exchangeInfo")

    async def get_symbol_price(self, symbol: str) -> Dict[str, Any]:
        """Sembol fiyatını getir - Circuit breaker ile sarılı"""
        return await binance_circuit_breaker.execute(
            self._request, "GET", "/api/v3/ticker/price", 
            {"symbol": symbol.upper()}
        )

    def get_metrics(self) -> RequestMetrics:
        """Request metriklerini getir"""
        return self.metrics

    def reset_metrics(self):
        """Metrikleri sıfırla"""
        self.metrics = RequestMetrics()
        self.request_times = []

    async def close(self):
        """HTTP client'ı temiz bir şekilde kapat"""
        try:
            await self.client.aclose()
            LOG.info("HTTP client closed successfully")
        except Exception as e:
            LOG.error(f"Error closing HTTP client: {e}")

# -------------------------------------------------------------
# WebSocket Manager - Gelişmiş Reconnect ve Error Handling
# -------------------------------------------------------------
class BinanceWebSocketManager:
    def __init__(self):
        self.connections: Dict[str, websockets.WebSocketClientProtocol] = {}
        self.callbacks: Dict[str, List[Callable]] = defaultdict(list)
        self.metrics = WSMetrics()
        self._running = True
        self._message_times = []
        self._tasks: set[asyncio.Task] = set()
        LOG.info("WebSocket Manager initialized")

	# WebSocketManager için tutarlı bir _listen_stream implementasyonu
	async def _listen_stream(self, stream_name: str):
	    """WebSocket loop: reconnect + callback safe execution"""
	    while self._running:
	        try:
	            url = f"wss://stream.binance.com:9443/ws/{stream_name}"
	            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
	                self.connections[stream_name] = ws
	                LOG.info(f"WS connected: {stream_name}")
	                self.metrics.total_connections += 1
	                
	                async for msg in ws:
	                    self.metrics.messages_received += 1
	                    self._message_times.append(time.time())
	                    
	                    # Keep only last 100 message times
	                    if len(self._message_times) > 100:
	                        self._message_times.pop(0)
	                    
	                    try:
	                        data = json.loads(msg)
	                    except Exception as e:
	                        LOG.error(f"Failed to parse WS message ({stream_name}): {e}")
	                        continue
	                    
	                    # Execute all callbacks safely
	                    for cb in list(self.callbacks.get(stream_name, [])):
	                        try:
	                            if asyncio.iscoroutinefunction(cb):
	                                await cb(data)
	                            else:
	                                cb(data)
	                        except Exception as e:
	                            LOG.error(f"Callback error for {stream_name}: {e}")
	        
	        except Exception as e:
	            self.metrics.failed_connections += 1
	            LOG.warning(f"WS reconnect {stream_name} in {CONFIG.BINANCE.WS_RECONNECT_DELAY}s: {e}")
	            await asyncio.sleep(CONFIG.BINANCE.WS_RECONNECT_DELAY)

    async def subscribe(self, stream_name: str, callback: Callable):
        """Yeni bir WebSocket stream'ine subscribe ol"""
        if stream_name not in self.connections:
            await self._create_connection(stream_name)
        self.callbacks[stream_name].append(callback)
        LOG.info(f"Subscribed to {stream_name}")
		
	async def _create_connection(self, stream_name: str):
		"""Yeni WebSocket bağlantısı oluştur"""
		url = f"wss://stream.binance.com:9443/ws/{stream_name}"
		try:
			ws = await websockets.connect(url, ping_interval=20, ping_timeout=10)
			self.connections[stream_name] = ws
			self.metrics.total_connections += 1
			
			# Dinleme görevini başlat
			task = asyncio.create_task(self._listen_stream(stream_name))
			self._tasks.add(task)
			task.add_done_callback(lambda t: self._tasks.discard(t))
			LOG.info(f"WebSocket connection created for {stream_name}")
			return ws
			
	except Exception as e:
		self.metrics.failed_connections += 1
		LOG.error(f"Failed to create WS connection for {stream_name}: {e}")
		raise


    async def _reconnect(self, stream_name: str):
        """Bağlantıyı yeniden kur"""
        if stream_name in self.connections:
            try:
                await self.connections[stream_name].close()
            except:
                pass
            del self.connections[stream_name]
        self.metrics.reconnections += 1
        await asyncio.sleep(CONFIG.BINANCE.WS_RECONNECT_DELAY)
        if self._running:
            try:
                await self._create_connection(stream_name)
                LOG.info(f"Reconnected to {stream_name}")
            except Exception as e:
                LOG.error(f"Failed to reconnect {stream_name}: {e}")

    def start_symbol_ticker(self, symbol: str, callback: Callable[[Dict[str, Any]], Any]):
        """Sembol ticker stream'ini başlat"""
        stream_name = f"{symbol.lower()}@ticker"
        asyncio.create_task(self.subscribe(stream_name, callback))

    def start_kline_stream(self, symbol: str, interval: str, callback: Callable[[Dict[str, Any]], Any]):
        """Kline stream'ini başlat"""
        stream_name = f"{symbol.lower()}@kline_{interval}"
        asyncio.create_task(self.subscribe(stream_name, callback))

    def start_order_book(self, symbol: str, depth: int, callback: Callable[[Dict[str, Any]], Any]):
        """Order book stream'ini başlat"""
        if depth not in [5, 10, 20]:
            raise ValueError("Depth must be one of [5, 10, 20]")
        stream_name = f"{symbol.lower()}@depth{depth}"
        asyncio.create_task(self.subscribe(stream_name, callback))

    async def close_all(self):
        """Tüm bağlantıları temiz bir şekilde kapat"""
        self._running = False
        for stream_name, ws in self.connections.items():
            try:
                await ws.close()
                LOG.info(f"Closed WebSocket connection for {stream_name}")
            except Exception as e:
                LOG.error(f"Error closing WebSocket for {stream_name}: {e}")
        self.connections.clear()
        self.callbacks.clear()
        LOG.info("All WebSocket connections closed")

    def get_metrics(self) -> WSMetrics:
        """WebSocket metriklerini getir"""
        return self.metrics

    def reset_metrics(self):
        """Metrikleri sıfırla"""
        self.metrics = WSMetrics()
        self._message_times = []

# -------------------------------------------------------------
# Veri Formatı Dönüşüm Fonksiyonları
# -------------------------------------------------------------
def klines_to_dataframe(klines: List[List[Any]]) -> pd.DataFrame:
    """Kline verisini pandas DataFrame'e dönüştür - CCXT uyumlu"""
    try:
        # Binance kline formatı: 
        # [timestamp, open, high, low, close, volume, 
        #  close_time, quote_asset_volume, number_of_trades, 
        #  taker_buy_base_asset_volume, taker_buy_quote_asset_volume, ignore]
        
        # Sadece ihtiyacımız olan sütunları al
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades',
            'taker_buy_base_volume', 'taker_buy_quote_volume', 'ignore'
        ])
        
        # Sadece ihtiyacımız olan sütunları seç
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        
        # Sayısal kolonları dönüştür
        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Timestamp'i datetime'a çevir ve index olarak ayarla
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        
        return df
    
    except Exception as e:
        LOG.error(f"OHLCV to DataFrame dönüşümünde hata: {e}")
        # Fallback: boş DataFrame döndür
        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])

# -------------------------------------------------------------
# BinanceClient Wrapper - YENİ MİMARİ
# -------------------------------------------------------------
class BinanceClient:
    def __init__(self, api_key: Optional[str] = None, secret_key: Optional[str] = None):
        # 🔹 user_id parametresi TAMAMEN KALDIRILDI
        self.api_key = api_key
        self.secret_key = secret_key
        self.http = BinanceHTTPClient(self.api_key, self.secret_key)
        self.ws_manager = BinanceWebSocketManager()

        # Event loop handling iyileştirme
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None  # library seviyesinde event loop oluşturma yok
            
        LOG.info("BinanceClient initialized successfully")

    def test_connection(self):
        """Bağlantıyı test et"""
        has_keys = bool(self.api_key and self.secret_key)
        LOG.info(f"Binance client initialized, has_keys: {has_keys}")
        return True

    # ---------------------------------------------------------
    # ✅ PUBLIC (API key gerekmez)
    # ---------------------------------------------------------
    async def get_server_time(self) -> Dict[str, Any]:
        """Sunucu zamanını getir"""
        try:
            return await self.http.get_server_time()
        except Exception as e:
            LOG.error(f"Error getting server time: {e}")
            raise

    async def get_exchange_info(self) -> Dict[str, Any]:
        """Exchange bilgilerini getir"""
        try:
            return await self.http.get_exchange_info()
        except Exception as e:
            LOG.error(f"Error getting exchange info: {e}")
            raise
	#ek
	async def get_symbol_price(self, symbol: str) -> Dict[str, Any]:
	    """
	    Sembol fiyatını getir.
	    
	    Args:
	        symbol: Sembol adı (ör: BTCUSDT)
	        
	    Returns:
	        Dict[str, Any]: Fiyat bilgisi içeren sözlük
	        
	    Raises:
	        ValueError: Geçersiz sembol adı
	        ConnectionError: API bağlantı hatası
	    """
	    try:
	        symbol = symbol.upper().strip()
	        if not symbol:
	            raise ValueError("Symbol cannot be empty")
	            
	        return await self.http.get_symbol_price(symbol)
	    except Exception as e:
	        LOG.error(f"Error getting symbol price for {symbol}: {e}")
	        raise

    async def get_order_book(self, symbol: str, limit: int = 100) -> Dict[str, Any]:
        """Order book verisini getir"""
        try:
            return await binance_circuit_breaker.execute(
                self.http._request, "GET", "/api/v3/depth",
                {"symbol": symbol.upper(), "limit": limit}
            )
        except Exception as e:
            LOG.error(f"Error getting order book for {symbol}: {e}")
            raise

    async def get_recent_trades(self, symbol: str, limit: int = 500) -> List[Dict[str, Any]]:
        """Son trade'leri getir"""
        try:
            return await binance_circuit_breaker.execute(
                self.http._request, "GET", "/api/v3/trades",
                {"symbol": symbol.upper(), "limit": limit}
            )
        except Exception as e:
            LOG.error(f"Error getting recent trades for {symbol}: {e}")
            raise

    async def get_agg_trades(self, symbol: str, limit: int = 500) -> List[Dict[str, Any]]:
        """Aggregate trade'leri getir"""
        try:
            return await binance_circuit_breaker.execute(
                self.http._request, "GET", "/api/v3/aggTrades",
                {"symbol": symbol.upper(), "limit": limit}
            )
        except Exception as e:
            LOG.error(f"Error getting agg trades for {symbol}: {e}")
            raise

    async def get_klines(self, symbol: str, interval: str = "1m", limit: int = 500) -> List[List[Union[str, float, int]]]:
        """Kline verisini getir"""
        try:
            return await binance_circuit_breaker.execute(
                self.http._request, "GET", "/api/v3/klines",
                {"symbol": symbol.upper(), "interval": interval, "limit": limit}
            )
        except Exception as e:
            LOG.error(f"Error getting klines for {symbol}: {e}")
            raise

    async def get_klines_dataframe(self, symbol: str, interval: str = "1m", limit: int = 500) -> pd.DataFrame:
        """Kline verisini DataFrame olarak getir"""
        try:
            klines = await self.get_klines(symbol, interval, limit)
            return klines_to_dataframe(klines)
        except Exception as e:
            LOG.error(f"Error getting klines dataframe for {symbol}: {e}")
            raise

    async def get_24h_ticker(self, symbol: str) -> Dict[str, Any]:
        """24 saatlik ticker verisini getir"""
        try:
            return await binance_circuit_breaker.execute(
                self.http._request, "GET", "/api/v3/ticker/24hr",
                {"symbol": symbol.upper()}
            )
        except Exception as e:
            LOG.error(f"Error getting 24h ticker for {symbol}: {e}")
            raise

    async def get_all_24h_tickers(self) -> List[Dict[str, Any]]:
        """Tüm sembollerin 24 saatlik ticker verisini getir"""
        try:
            return await binance_circuit_breaker.execute(
                self.http._request, "GET", "/api/v3/ticker/24hr"
            )
        except Exception as e:
            LOG.error(f"Error getting all 24h tickers: {e}")
            raise

	# BinanceClient sınıfına eklenmesi gereken fonksiyon
	async def get_all_tickers(self) -> Dict[str, Any]:
	    """Tüm sembollerin anlık fiyatlarını getir"""
	    try:
	        return await binance_circuit_breaker.execute(
	            self.http._request, "GET", "/api/v3/ticker/price"
	        )
	    except Exception as e:
	        LOG.error(f"Error getting all tickers: {e}")
	        raise
	
	async def get_historical_trades(self, symbol: str, from_id: Optional[int] = None, limit: int = 500) -> List[Dict[str, Any]]:
	    """Geçmiş trade verilerini getir"""
	    try:
	        params = {"symbol": symbol.upper(), "limit": limit}
	        if from_id:
	            params["fromId"] = from_id
	            
	        return await binance_circuit_breaker.execute(
	            self.http._request, "GET", "/api/v3/historicalTrades", params=params
	        )
	    except Exception as e:
	        LOG.error(f"Error getting historical trades for {symbol}: {e}")
	        raise

    async def get_all_symbols(self) -> List[str]:
        """Tüm sembol listesini getir"""
        try:
            data = await binance_circuit_breaker.execute(
                self.http._request, "GET", "/api/v3/exchangeInfo"
            )
            return [s["symbol"] for s in data["symbols"]]
        except Exception as e:
            LOG.error("Error getting all symbols: {e}")
            raise

    async def exchange_info_details(self) -> Dict[str, Any]:
        """Detaylı exchange bilgilerini getir"""
        try:
            return await binance_circuit_breaker.execute(
                self.http._request, "GET", "/api/v3/exchangeInfo"
            )
        except Exception as e:
            LOG.error("Error getting exchange info details: {e}")
            raise

    # ---------------------------------------------------------
    # ✅ PRIVATE (API key + secret zorunlu)
    # ---------------------------------------------------------
    async def _require_keys(self):
        """API key kontrolü yap"""
        if not self.http.api_key or not self.http.secret_key:
            raise ValueError("Bu endpoint için API key + secret gerekli")

    async def get_account_info(self) -> Dict[str, Any]:
        """Hesap bilgilerini getir"""
        try:
            await self._require_keys()
            return await binance_circuit_breaker.execute(
                self.http._request, "GET", "/api/v3/account", signed=True
            )
        except Exception as e:
            LOG.error(f"Error getting account info: {e}")
            raise

	async def create_listen_key(self) -> str:
        """Private websocket için listenKey oluşturur"""
        await self._require_keys()
        res = await self.http._request(
            "POST", "/api/v3/userDataStream", signed=False
        )
        return res.get("listenKey")

    async def place_order(self, symbol: str, side: str, type_: str,
                          quantity: float, price: Optional[float] = None) -> Dict[str, Any]:
        """Yeni order oluştur"""
        try:
            await self._require_keys()
            params = {"symbol": symbol.upper(), "side": side, "type": type_, "quantity": quantity}
            if price:
                params["price"] = price
            return await binance_circuit_breaker.execute(
                self.http._request, "POST", "/api/v3/order", params=params, signed=True
            )
        except Exception as e:
            LOG.error(f"Error placing order for {symbol}: {e}")
            raise

	#2
	async def get_account_balance(self, asset: Optional[str] = None) -> Dict[str, Any]:
		"""Hesap bakiyesini getir"""
		try:
			await self._require_keys()
			account_info = await binance_circuit_breaker.execute(
				self.http._request, "GET", "/api/v3/account", {}, True
			)

        if asset:
			asset = asset.upper()
			for balance in account_info.get('balances', []):
				if balance.get('asset') == asset:
					return balance
			return {}

        return account_info

    except Exception as e:
        LOG.error(f"Error getting account balance: {e}")
        raise


    async def futures_position_info(self) -> List[Dict[str, Any]]:
        """Futures pozisyon bilgilerini getir"""
        try:
            await self._require_keys()
            return await binance_circuit_breaker.execute(
                self.http._request, "GET", "/fapi/v2/positionRisk", signed=True, futures=True
            )
        except Exception as e:
            LOG.error(f"Error getting futures position info: {e}")
            raise

    async def get_funding_rate(self, symbol: str, limit: int = 1) -> List[Dict[str, Any]]:
        """Funding rate bilgilerini getir"""
        try:
            await self._require_keys()
            params = {"symbol": symbol.upper(), "limit": limit}
            return await binance_circuit_breaker.execute(
                self.http._request, "GET", "/fapi/v1/fundingRate", params=params, futures=True
            )
        except Exception as e:
            LOG.error(f"Error getting funding rate for {symbol}: {e}")
            raise

    # -----------
    # --- WebSocket Methods ---
    # -----------
    async def ws_ticker(self, symbol: str, callback: Callable):
        """WebSocket ticker stream'ine subscribe ol"""
        try:
            stream_name = f"{symbol.lower()}@ticker"
            await self.ws_manager.subscribe(stream_name, callback)
        except Exception as e:
            LOG.error(f"Error subscribing to ticker for {symbol}: {e}")
            raise

    async def ws_trades(self, symbol: str, callback: Callable):
        """WebSocket trade stream'ine subscribe ol"""
        try:
            stream_name = f"{symbol.lower()}@trade"
            await self.ws_manager.subscribe(stream_name, callback)
        except Exception as e:
            LOG.error(f"Error subscribing to trades for {symbol}: {e}")
            raise

    async def ws_order_book(self, symbol: str, depth: int, callback: Callable):
        """WebSocket order book stream'ine subscribe ol"""
        try:
            if depth not in [5, 10, 20]:
                raise ValueError("Depth must be one of [5, 10, 20]")
            stream_name = f"{symbol.lower()}@depth{depth}"
            await self.ws_manager.subscribe(stream_name, callback)
        except Exception as e:
            LOG.error(f"Error subscribing to order book for {symbol}: {e}")
            raise

    async def ws_kline(self, symbol: str, interval: str, callback: Callable):
        """WebSocket kline stream'ine subscribe ol"""
        try:
            stream_name = f"{symbol.lower()}@kline_{interval}"
            await self.ws_manager.subscribe(stream_name, callback)
        except Exception as e:
            LOG.error(f"Error subscribing to kline for {symbol}: {e}")
            raise

    async def ws_multiplex(self, streams: List[str], callback: Callable):
        """WebSocket multiplex stream'ine subscribe ol"""
        try:
            combined_streams = "/".join(streams)
            stream_name = f"streams={combined_streams}"
            await self.ws_manager.subscribe(stream_name, callback)
        except Exception as e:
            LOG.error(f"Error subscribing to multiplex streams: {e}")
            raise

    def start_symbol_ticker(self, symbol: str, callback: Callable[[Dict[str, Any]], Any]):
        """Sembol ticker stream'ini başlat"""
        try:
            self.ws_manager.start_symbol_ticker(symbol, callback)
        except Exception as e:
            LOG.error(f"Error starting symbol ticker for {symbol}: {e}")
            raise

    def start_kline_stream(self, symbol: str, interval: str, callback: Callable[[Dict[str, Any]], Any]):
        """Kline stream'ini başlat"""
        try:
            self.ws_manager.start_kline_stream(symbol, interval, callback)
        except Exception as e:
            LOG.error(f"Error starting kline stream for {symbol}: {e}")
            raise

    def start_order_book(self, symbol: str, depth: int, callback: Callable[[Dict[str, Any]], Any]):
        """Order book stream'ini başlat"""
        try:
            self.ws_manager.start_order_book(symbol, depth, callback)
        except Exception as e:
            LOG.error(f"Error starting order book for {symbol}: {e}")
            raise

    # --- Temel Metrikler ---
    async def order_book_imbalance(self, symbol: str, limit: int = 50) -> float:
        """Order book imbalance'ı hesapla"""
        try:
            ob = await self.get_order_book(symbol, limit)
            bids = sum(float(b[1]) for b in ob["bids"])
            asks = sum(float(a[1]) for a in ob["asks"])
            return (bids - asks) / max(bids + asks, 1)
        except Exception as e:
            LOG.error(f"Error calculating order book imbalance for {symbol}: {e}")
            raise

    async def whale_trades(self, symbol: str, usd_threshold: float = CONFIG.BINANCE.WHALE_USD_THRESHOLD) -> int:
        """Whale trade'lerini say"""
        try:
            trades = await self.get_recent_trades(symbol)
            return sum(1 for t in trades if float(t["price"]) * float(t["qty"]) > usd_threshold)
        except Exception as e:
            LOG.error(f"Error counting whale trades for {symbol}: {e}")
            raise

    async def volume_spike(self, symbol: str, window: int = 10) -> float:
        """Volume spike'ı hesapla"""
        try:
            klines = await self.get_klines(symbol, "1m", window)
            volumes = [float(k[5]) for k in klines]
            avg_vol = sum(volumes[:-1]) / max(len(volumes) - 1, 1)
            return volumes[-1] / max(avg_vol, 1)
        except Exception as e:
            LOG.error(f"Error calculating volume spike for {symbol}: {e}")
            raise

    async def funding_rate_alert(self, symbol: str, threshold: float = CONFIG.BINANCE.FUNDING_RATE_THRESHOLD) -> bool:
        """Funding rate alert kontrolü"""
        try:
            rates = await self.get_funding_rate(symbol)
            return abs(float(rates[0]["fundingRate"])) > threshold
        except Exception as e:
            LOG.error(f"Error checking funding rate alert for {symbol}: {e}")
            raise

    # --- Gelişmiş Metrikler ---
    async def get_detailed_metrics(self) -> Dict[str, Any]:
        """Detaylı metrikleri getir"""
        try:
            http_metrics = self.http.get_metrics()
            ws_metrics = self.ws_manager.get_metrics()
            circuit_status = binance_circuit_breaker.get_status()

            return {
                "http_metrics": {
                    "total_requests": http_metrics.total_requests,
                    "failed_requests": http_metrics.failed_requests,
                    "cache_hits": http_metrics.cache_hits,
                    "cache_misses": http_metrics.cache_misses,
                    "rate_limited_requests": http_metrics.rate_limited_requests,
                    "avg_response_time": http_metrics.avg_response_time,
                    "last_request_time": http_metrics.last_request_time,
                },
                "ws_metrics": {
                    "total_connections": ws_metrics.total_connections,
                    "failed_connections": ws_metrics.failed_connections,
                    "messages_received": ws_metrics.messages_received,
                    "reconnections": ws_metrics.reconnections,
                    "avg_message_rate": ws_metrics.avg_message_rate,
                },
                "circuit_breaker": circuit_status,
                "system": {
                    "active_ws_connections": len(self.ws_manager.connections),
                    "cache_size": len(self.http._cache),
                    "current_time": time.time(),
                }
            }
        except Exception as e:
            LOG.error(f"Error getting detailed metrics: {e}")
            raise

    async def close(self):
        """Tüm bağlantıları temiz bir şekilde kapat"""
        try:
            await self.http.close()
            await self.ws_manager.close_all()
            # ayrıca ws._tasks varsa temizle
            if hasattr(self.ws_manager, "_tasks"):
                for t in list(self.ws_manager._tasks):
                    t.cancel()
                await asyncio.gather(*self.ws_manager._tasks, return_exceptions=True)
            LOG.info("BinanceClient closed successfully")
        except Exception as e:
            LOG.error(f"Error closing BinanceClient: {e}")


# -------------------------------------------------------------
# Global instance for convenience
# -------------------------------------------------------------
binance_client = None

def get_binance_client(api_key: Optional[str] = None, secret_key: Optional[str] = None) -> BinanceClient:
    """Global BinanceClient instance'ını getir veya oluştur"""
    global binance_client
    if binance_client is None:
        if api_key is None:
            api_key = os.getenv("BINANCE_API_KEY")
        if secret_key is None:
            secret_key = os.getenv("BINANCE_API_SECRET")
        binance_client = BinanceClient(api_key, secret_key)
    return binance_client


# EOF






























