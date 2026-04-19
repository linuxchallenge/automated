"""
Test: place GOLD commodity BUY order via Upstox using get_best_price (LIMIT order).
Run from auto_straddle directory:
    python -m upstox.test_commodity_buy
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from upstox.upstox_api import upstox_api

print("Initializing Upstox...")
api = upstox_api()
print(f"Access token: {api.access_token[:30]}...")

symbol = 'GOLD'
qty = 1
buy_sell = 'BUY'

print(f"\nFetching best price for {symbol}...")
# First test get_best_price standalone
from upstox.upstox_api import upstox_api
import pandas as pd

token_info = api.getTokenInfo('MCX', 'FUTCOM', 'GOLDM', 0, 'X')
if token_info.empty:
    print("ERROR: No token found for GOLDM MCX FUTCOM")
    sys.exit(1)

t = token_info.iloc[0]
instrument_token = t['instrument_key']
print(f"Token: {instrument_token}, expiry: {t['expiry']}, lot: {t.get('lot_size', '?')}")

price = api.get_best_price(instrument_token, buy_sell)
print(f"get_best_price returned: {price}")

if price <= 0:
    print("ERROR: Could not get price. Aborting without placing order.")
    sys.exit(1)

print(f"\nPlacing LIMIT BUY order for GOLD qty={qty} price={price}...")
order_id, expiry = api.place_order_commodity(symbol, qty, buy_sell)
print(f"\nResult: order_id={order_id}, expiry={expiry}")
