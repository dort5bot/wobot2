# Minimal coinglass stub
def get_funding_rates(symbols=None):
    return {s: {"funding": 0.0001} for s in (symbols or [])}