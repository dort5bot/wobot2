# utils/personal_trader.py
"""
Kişisel API'lerle trade/alarm işlemleri yapan sınıf
DB'den real-time API key çeker, cache'ler
"""

import asyncio
from typing import Optional, Dict
from utils.binance_api import BinanceClient
from utils.apikey_utils import get_apikey
import logging

LOG = logging.getLogger("personal_trader")

class PersonalTrader:
    def __init__(self):
        self.clients: Dict[int, BinanceClient] = {}
        self.lock = asyncio.Lock()
    
    async def get_client(self, user_id: int) -> Optional[BinanceClient]:
        """Thread-safe client getter with caching"""
        async with self.lock:
            # Cache'te varsa direkt dön
            if user_id in self.clients:
                return self.clients[user_id]
            
            # DB'den API key al
            api_key, secret_key = get_apikey(user_id)
            if not api_key or not secret_key:
                return None
            
            # Yeni client oluştur ve cache'e ekle
            try:
                client = BinanceClient(api_key, secret_key)
                self.clients[user_id] = client
                LOG.info(f"New client created for user {user_id}")
                return client
            except Exception as e:
                LOG.error(f"Client creation failed for user {user_id}: {e}")
                return None
    
    async def execute_trade(self, user_id: int, trade_data: dict):
        """Kişisel trade işlemi"""
        client = await self.get_client(user_id)
        if not client:
            raise Exception("❌ Lütfen önce /apikey ile API key ekleyin")
        
        return await client.place_order(trade_data)
    
    async def set_alarm(self, user_id: int, alarm_data: dict):
        """Kişisel alarm işlemi"""
        client = await self.get_client(user_id)
        if not client:
            raise Exception("❌ Lütfen önce /apikey ile API key ekleyin")
        
        return await client.set_alarm(alarm_data)
    
    async def get_balance(self, user_id: int):
        """Kişisel bakiye sorgulama"""
        client = await self.get_client(user_id)
        if not client:
            raise Exception("❌ Lütfen önce /apikey ile API key ekleyin")
        
        return await client.get_account_balance()

# Global instance
personal_trader = PersonalTrader()
