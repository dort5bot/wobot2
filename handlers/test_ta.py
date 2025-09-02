# test_ta.py
import asyncio
import pandas as pd
from utils.ta_utils import alpha_signal, scan_market

# Test verisi oluştur
test_data = {
    'open': [100, 101, 102, 103, 104, 105, 106, 107, 108, 109] * 10,
    'high': [105, 106, 107, 108, 109, 110, 111, 112, 113, 114] * 10,
    'low': [95, 96, 97, 98, 99, 100, 101, 102, 103, 104] * 10,
    'close': [102, 103, 104, 105, 106, 107, 108, 109, 110, 111] * 10,
    'volume': [1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000] * 10
}
df = pd.DataFrame(test_data)

# Test fonksiyonları
def test_alpha():
    result = alpha_signal(df)
    print("Alpha Signal Result:", result)
    return result

def test_scan():
    market_data = {"TESTUSDT": df}
    result = scan_market(market_data)
    print("Scan Market Result:", result)
    return result

if __name__ == "__main__":
    print("Testing alpha_signal...")
    test_alpha()
    
    print("\nTesting scan_market...")
    test_scan()
