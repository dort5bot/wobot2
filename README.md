worker yapısına gecis


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

Bu fonksiyonlar için **API key gerekmez**. Örnek kullanım:
```python
client = BinanceClient()
data = await client.get_order_book("BTCUSDT")

