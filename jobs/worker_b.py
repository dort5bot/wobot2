import asyncio
from utils import data_provider as dp, risk_manager, order_manager, signal_evaluator, config_worker
async def run_forever():
    while True:
        try:
            ctx = {"ticker": dp.get_tickers(config_worker.SYMBOLS), "funding": dp.get_funding(config_worker.SYMBOLS)}
            sig = signal_evaluator.evaluate(ctx)
            if sig.get("action") in ("BUY","SELL") and risk_manager.check(sig, ctx):
                order_manager.place_order(sig)
        except Exception as e:
            print("worker_b error", e)
        await asyncio.sleep(config_worker.WORKER_B_INTERVAL)