wobot1 = robot1
/README.md

worker yapısına gecis

============================================
önce kaynak: binance > herşey buna bağlı
* herşey dahil edilmeli
* spot-future -vb
* hata kontrol vb
* max pro yapılmalı
* *geliştirmelerde isim değişmeden kalmak zorunda
* SONRA bunun üstüne entegre edilmeli herşey


=============================================


# BinanceClient Kullanım Kılavuzu

Bu client, Binance REST API için **public** ve **private** endpointleri kapsar.  
Aşağıda hangi fonksiyonun API key gerektirdiği listelenmiştir.

## ✅ Public Fonksiyonlar (API key gerektirmez)
- `get_order_book(symbol, limit=100)` → Order book bilgisi
- `get_recent_trades(symbol, limit=500)` → Son işlemler
- `get_agg_trades(symbol, limit=500)` → Agg trade verileri
- `get_klines(symbol, interval="1m", limit=500)` → Mum (kline) verileri
- `get_24h_ticker(symbol)` → 24h ticker
- `get_all_24h_tickers()` → Tüm semboller için 24h ticker
- `get_all_symbols()` → Tüm sembol listesi
- `exchange_info_details()` → Exchange metadata bilgisi
 ` ` `
 ` ` `


✅ 
GLOBAL API (.env)
-WorkerA, WorkerB, WorkerC → Sadece VERİ OKUMA, trade yapmaz

KİŞİSEL API (DB)  
-PersonalTrader → Sadece ALARM/TRADE işlemleri
-- Her kullanıcı için ayrı client
--Real-time DB query + caching

🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶
🔶🔶🔶🔶🔶utils/binance_api.py🔶🔶🔶🔶🔶
🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶
1. ✅ Gelişmiş hata yönetimi - Detaylı logging ve error tracking
2. ✅ Performans monitoring - Connection pool metrics ve detaylı istatistikler
3. ✅ Akıllı retry mekanizması - Status code'a göre optimize edilmiş retry
4. ✅ Priority tabanlı işleme - High/normal/low priority semaphore'lar
5. ✅ Gelişmiş caching - Endpoint tipine göre farklı TTL stratejileri
6. ✅ Batch processing - batch_request() metodu ile çoklu paralel istekler
7. ✅ Dinamik rate limiting - Gerçek zamanlı limit yönetimi ve backoff
8. ✅ Otomatik WebSocket recovery - Exponential backoff ile reconnect
9. ✅ Graceful shutdown - Kaynakların düzgünce temizlenmesi
10. ✅ Detaylı metrikler - get_detailed_metrics() ile kapsamlı monitoring



🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶
🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶
utils/init_db.py

✅ Otomatik schema migration (eksik kolonları ekler)
✅ Index optimizasyonları
✅ Foreign key desteği
✅ Integrity check fonksiyonu
✅ Daha profesyonel logging
✅ Daha kapsamlı tablo yapısı


🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶
🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶🔶
## utils/ta_utils.py

1. Unified RSI/MACD: Tek bir fonksiyon hem pandas hem de liste/array inputlarını destekler
2. Cache Limit: MAX_CACHE_ENTRIES ile cache büyümesi kontrol altında
3. Error Handling: Kapsamlı hata yönetimi ve logging
4. Config Fallback: CONFIG yüklenemezse güvenli default değerler
5. Thread Safety: Asyncio/threading uyumluluğu
6. Alpha Signals: Gelişmiş sinyal üretme mekanizması
7. Backward Compatibility: Eski kodlarla uyumluluk
<img width="524" height="129" alt="image" src="https://github.com/user-attachments/assets/b5082c45-6044-4c5c-b072-ec4f7476839b" />








