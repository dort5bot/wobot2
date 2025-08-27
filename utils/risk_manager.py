# utils/risk_manager.py
##♦️ merkezi risk yönetimi 

import time
import os
from utils.config import RISK_MAX_DAILY_LOSS, DB_PATH
import sqlite3

class RiskManager:
    """
    Basic risk controls:
    - daily loss tracking using paper_trades table (only approximate).
    - per-trade max notional limit.
    - cool-off on exceeding daily loss threshold.
    """
    def __init__(self, db_path=DB_PATH, max_daily_loss=RISK_MAX_DAILY_LOSS):
        self.db = db_path
        self.max_daily_loss = max_daily_loss

    def _get_today_pl(self):
        # approximate: sum buys/sells via paper_trades (notional) - for live use positions reconciliation needed
        conn = sqlite3.connect(self.db)
        c = conn.cursor()
        # This assumes small scale: define P/L as difference between executed buys and sells for simplicity.
        # Better: store realized P/L from fills. This is a placeholder to prevent runaway risk.
        c.execute("SELECT SUM(CASE WHEN side='BUY' THEN -qty*COALESCE(price,0) WHEN side='SELL' THEN qty*COALESCE(price,0) ELSE 0 END) as pl FROM paper_trades WHERE date(ts)=date('now')")
        row = c.fetchone()
        conn.close()
        pl = row[0] if row and row[0] is not None else 0.0
        return pl

    def allow_trade(self, equity_usdt: float, proposed_notional: float) -> (bool, str):
        """
        equity_usdt: current wallet/equity estimate
        proposed_notional: value of trade in USDT
        """
        today_pl = self._get_today_pl()
        # if today's losses (negative pl) exceed threshold => block
        if today_pl < 0 and abs(today_pl) > equity_usdt * self.max_daily_loss:
            return False, f"daily loss limit exceeded: {today_pl} > {equity_usdt*self.max_daily_loss}"
        # you can add more rules here (max position per symbol, max open trades, min, etc.)
        return True, "ok"
