"""
Test script: place NIFTY 23500 PE BUY order for leelu (5paisa account).
Run from auto_straddle directory:
    python test_fivepaisa_order.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fivepaisa.fivepaise_api import fivepaise_api

print("Initializing leelu 5paisa account...")
api = fivepaise_api('leelu')
print("Login done.")

symbol = 'NIFTY'
strike = 23500.0
pe_ce = 'PE'
qty = 75  # 1 lot of NIFTY (lot size = 75)
buy_sell = 'BUY'

# Look up the token dynamically from the API
token_info = api.getTokenInfo(symbol, strike, pe_ce)
if token_info is None:
    print(f"Could not find token info for {symbol} {strike} {pe_ce}. Exiting.")
    sys.exit(1)
token = int(token_info['ScripCode'])
lot = int(token_info['LotSize'])
qty = lot  # 1 lot
print(f"Token: {token}, LotSize: {lot}, Qty: {qty}")

# Try to get price from market depth first, then LTP, then fallback to hardcoded
price = 0

print("\nFetching market depth...")
try:
    resp = api.obj.fetch_market_depth_by_scrip(Exch='N', ExchangeType='D', ScripCode=str(token))
    entries = resp.get('MarketDepthData', [])
    asks = [e for e in entries if e.get('BbBuySellFlag') == 83 and e.get('Price', 0) > 0]
    price = float(asks[0]['Price']) if asks else 0
    print(f"Depth ask price: {price}")
except Exception as e:
    print(f"Depth error: {e}")

if price == 0:
    print("Depth price 0, trying LTP feed...")
    try:
        snap = api.obj.fetch_market_feed_scrip([{"Exch": "N", "ExchangeType": "D", "ScripCode": token}])
        data = snap.get('Data')
        if data:
            price = round(float(data[0]['LastRate']) * 1.005, 2)
            print(f"LTP price: {price}")
        else:
            print(f"LTP feed not available: {snap.get('Message')}")
    except Exception as e:
        print(f"LTP error: {e}")

if price == 0:
    price = 28.0  # Fallback: NIFTY 23500 PE ~27.45 from LPP rejection
    print(f"Using hardcoded test price: {price}")

print(f"\nPlacing {buy_sell} {symbol} {int(strike)} {pe_ce} qty={qty} price={price}...")
api._fix_shared_payload_bug()
order_id = api.obj.place_order(OrderType='B', Exchange='N', ExchangeType='D',
                                ScripCode=token, Qty=qty, Price=price, IsIntraday=False)
print(f"Raw response: {order_id}")
broker_order_id = order_id.get('BrokerOrderID', -1) if order_id else -1
message = order_id.get('Message', '') if order_id else ''
expiry = '2026-04-24'  # approximate
print(f"\nResult: broker_order_id={broker_order_id}, message={message}, expiry={expiry}")

if broker_order_id and broker_order_id not in (-1, 0):
    print("Order placed successfully!")
else:
    print("Order FAILED.")
