##♦️api_check.py
#api kontrol araci
#/api_c api

import requests

def test_coinglass_api(api_key: str):
    """
    Verilen Coinglass API anahtarını test eder.
    """
    url = "https://open-api.coinglass.com/api/pro/v1/futures/openInterest"
    headers = {"coinglassSecret": api_key}
    params = {"symbol": "BTC"}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                first_item = data.get("data", [])[:1]
                return f"✅ API anahtarı geçerli. Örnek veri: {first_item}"
            else:
                return f"⚠ API yanıtı geldi ama success=False: {data}"
        else:
            return f"❌ HTTP {response.status_code} - {response.text}"
    except requests.RequestException as e:
        return f"❌ API isteğinde hata: {e}"
