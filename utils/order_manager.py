# utils/order_manager.py 901-2250
# utils/order_manager.py
import os
import logging
from typing import Optional, Dict, Any
from .binance_api import get_binance_client  # ✅ Yeni import
from . import db
import math

LOG = logging.getLogger("order_manager")
LOG.addHandler(logging.NullHandler())

PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() in ("1", "true", "yes")

class OrderManager:
    def __init__(self, risk_per_trade: float = 0.01, leverage: int = 1, paper_mode: Optional[bool] = None):
        # ❌ Eski: self.api = get_binance_api()
        # ✅ Yeni: API key'leri config'ten al veya None olarak geç
        self.api = get_binance_client(None, None)  # Global instance'ı kullan
        self.risk_per_trade = risk_per_trade
        self.leverage = leverage
        self.paper_mode = PAPER_MODE if paper_mode is None else paper_mode
        self._exchange_info = None

    async def init_exchange_info(self):
        if self._exchange_info is None:
            self._exchange_info = await self.api.get_exchange_info()  # ✅ Güncel method

    async def get_futures_balance(self) -> Optional[float]:
        try:
            # ✅ Futures balance için yeni method
            acc_info = await self.api.futures_position_info()
            # Total wallet balance hesapla
            total_balance = 0.0
            for position in acc_info:
                if 'walletBalance' in position:
                    total_balance += float(position.get('walletBalance', 0))
            return total_balance
        except Exception as e:
            LOG.warning("get_futures_balance failed: %s", e)
            return None

    async def calc_futures_qty(self, balance_usdt: float, entry_price: float, risk_pct: Optional[float] = None, leverage: Optional[int] = None) -> float:
        if risk_pct is None:
            risk_pct = self.risk_per_trade
        if leverage is None:
            leverage = self.leverage
        notional = balance_usdt * risk_pct * leverage
        qty = notional / entry_price
        return float(qty)

    async def place_futures_market(self, symbol: str, side: str, qty: float, extra: dict = None):
        LOG.info("place_futures_market %s %s %s", symbol, side, qty)
        
        if self.paper_mode:
            db.log_paper_trade(symbol, side, qty, None, source="signal")
            return {"paper": True, "symbol": symbol, "side": side, "qty": qty}
        
        # ✅ Güncel order placement
        try:
            order_params = {
                "symbol": symbol.upper(),
                "side": side.upper(),
                "type": "MARKET",
                "quantity": qty
            }
            
            if extra:
                order_params.update(extra)
                
            return await self.api.place_order(**order_params)
        except Exception as e:
            LOG.error("Futures market order failed: %s", e)
            raise

    async def process_decision(self, decision: Dict[str, Any]):
        symbol = decision.get("symbol")
        dec = decision.get("decision")
        strength = float(decision.get("strength", 0.0))
        reason = decision.get("reason", "")

        if dec == "HOLD":
            LOG.info("Decision HOLD for %s: %s", symbol, reason)
            return {"ok": True, "note": "HOLD"}

        balance = await self.get_futures_balance()
        if balance is None:
            LOG.warning("Cannot read futures balance; aborting decision")
            return {"ok": False, "error": "no_balance"}

        base_risk = self.risk_per_trade
        scaled_risk = min(0.5, base_risk * (0.5 + strength))

        # ✅ Güncel price method
        price_data = await self.api.get_symbol_price(symbol)
        if not price_data or 'price' not in price_data:
            return {"ok": False, "error": "no_price"}
        
        price = float(price_data['price'])

        qty = await self.calc_futures_qty(balance, price, risk_pct=scaled_risk)
        result = await self.place_futures_market(symbol, dec, qty)

        LOG.info("Executed %s %s qty=%s result=%s", dec, symbol, qty, result)
        return {"ok": True, "result": result}
