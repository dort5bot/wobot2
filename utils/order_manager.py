# utils/order_manager.py
import os
import logging
from typing import Optional, Dict, Any
from . import binance_api, db
import asyncio
import math

LOG = logging.getLogger("order_manager")
LOG.addHandler(logging.NullHandler())

PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() in ("1","true","yes")

class OrderManager:
    def __init__(self, api_module=binance_api, risk_per_trade: float = 0.01, leverage: int = 1, paper_mode: Optional[bool] = None):
        self.api = api_module
        self.risk_per_trade = risk_per_trade
        self.leverage = leverage
        self.paper_mode = PAPER_MODE if paper_mode is None else paper_mode
        self._exchange_info = None

    async def init_exchange_info(self):
        if self._exchange_info is None:
            self._exchange_info = await self.api.exchange_info()

    async def get_futures_balance(self) -> Optional[float]:
        try:
            acc = await self.api.get_futures_account()
            return float(acc.get("totalWalletBalance", 0))
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
        return await self.api.create_futures_order(symbol, side, "MARKET", quantity=qty, extra=extra)

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
        price = await self.api.get_price(symbol)
        if price is None:
            return {"ok": False, "error": "no_price"}
        qty = await self.calc_futures_qty(balance, price, risk_pct=scaled_risk)
        result = await self.place_futures_market(symbol, dec, qty)
        LOG.info("Executed %s %s qty=%s result=%s", dec, symbol, qty, result)
        return {"ok": True, "result": result}
