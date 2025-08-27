# handlers/ticker_handler.py
#sadece canlı akışı gözlemlemek için var, 
#anlamlı bir veri saklama veya işleme yapmaz
#gerçek uygulamada komut veya veri kaydı için genişletmek gerekir.

# handlers/ticker_pubsub.py
# Async pub/sub: dict + çoklu subscriber
import asyncio

# Global dict: en güncel fiyat/hacim
market_data = {}

# Subscriber listesi
subscribers = set()

# -----------------------------
# Publisher (data handle)
# -----------------------------
async def handle_ticker_data(data):
    try:
        symbol = data.get("s") or data.get("symbol")
        last_price = float(data.get("c") or data.get("lastPrice", 0))
        vol = float(data.get("v") or data.get("volume", 0))

        # En güncel veri
        market_data[symbol] = {"price": last_price, "vol": vol}

        # Her subscriber'a async gönderim
        for queue in subscribers:
            # Eğer queue doluysa en eskiyi at
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            await queue.put({"symbol": symbol, "price": last_price, "vol": vol})

    except Exception:
        pass

# -----------------------------
# Subscriber yönetimi
# -----------------------------
def create_subscriber(maxsize=1000):
    """
    Yeni subscriber ekler ve queue döner
    """
    queue = asyncio.Queue(maxsize=maxsize)
    subscribers.add(queue)
    return queue

def remove_subscriber(queue):
    """
    Subscriber siler
    """
    subscribers.discard(queue)

# -----------------------------
# Örnek subscriber task
# -----------------------------
async def subscriber_task(name, queue):
    while True:
        data = await queue.get()
        # Örnek filtreleme
        if data["symbol"] in ("ETHUSDT", "BTCUSDT"):
            print(f"{name} received: {data}")

# -----------------------------
# Kullanım Örneği
# -----------------------------
# queue1 = create_subscriber()
# asyncio.create_task(subscriber_task("Sub1", queue1))
# queue2 = create_subscriber()
# asyncio.create_task(subscriber_task("Sub2", queue2))
# handle_ticker_data() -> websocket veya bot stream’den çağrılır
