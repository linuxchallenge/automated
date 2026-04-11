"""
Test script: place NIFTY 23100 PE BUY order via Upstox.
Run from auto_straddle directory:
    python test_upstox_order.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from upstox.upstox_api import upstox_api

print("Initializing Upstox...")
api = upstox_api()
print(f"Access token: {api.access_token[:30]}...")
print(f"Token map rows: {len(api.token_df)}")

symbol = 'NIFTY'
strike = 23000.0
pe_ce = 'PE'
qty = 75   # 1 lot of NIFTY on Upstox (lot size = 75)
buy_sell = 'BUY'

# Look up token info directly (exchange is NSE_FO in Upstox CSV, not NFO)
print(f"\nLooking up {symbol} {int(strike)} {pe_ce}...")
import pandas as pd
df = api.token_df
opts = df[(df['exchange'] == 'NSE_FO') & (df['instrument_type'] == 'OPTIDX') &
          (df['name'] == symbol) & (df['strike'] == float(strike)) & (df['option_type'] == pe_ce)]
opts = opts.copy()
opts['expiry'] = pd.to_datetime(opts['expiry'])
opts = opts.sort_values('expiry')
# Skip today's expiry if multiple available
today = pd.Timestamp.now().normalize()
future = opts[opts['expiry'] > today]
t_info = future.iloc[0] if not future.empty else opts.iloc[0]

instrument_token = t_info['instrument_key']
lot = int(t_info.get('lot_size', 65))
expiry = t_info.get('expiry')
print(f"Found: {instrument_token}, lot={lot}, expiry={expiry}")

qty = lot

# Get market quote for LTP
import requests as req
print("\nFetching LTP...")
price = 0
try:
    url = f"{api.base_url}/market-quote/ltp?instrument_key={instrument_token}"
    resp = req.get(url, headers=api.get_headers())
    data = resp.json()
    print(f"Quote response: {data}")
    ltp = data.get('data', {}).get(instrument_token.replace('|', '%7C'), {}).get('last_price', 0)
    if ltp == 0:
        # Try alternate key format
        for v in data.get('data', {}).values():
            ltp = v.get('last_price', 0)
            if ltp > 0:
                break
    if ltp > 0:
        price = round(ltp * 1.005, 2)
        print(f"LTP={ltp}, using price={price}")
    else:
        print("LTP not available (market closed)")
except Exception as e:
    print(f"LTP error: {e}")

if price == 0:
    price = 400.0
    print(f"Using hardcoded test price: {price}")

# Place order
print(f"\nPlacing {buy_sell} {symbol} {int(strike)} {pe_ce} qty={qty} price={price}...")
orderparams = {
    "quantity": qty,
    "product": "D",
    "validity": "DAY",
    "price": price,
    "instrument_token": instrument_token,
    "order_type": "LIMIT",
    "transaction_type": buy_sell,
    "disclosed_quantity": 0,
    "trigger_price": 0.0,
    "is_amo": False
}

import requests as req
url = api.order_url
resp = req.post(url, headers=api.get_headers(), json=orderparams)
print(f"Status: {resp.status_code}")
print(f"Response: {resp.json()}")

res = resp.json()
if resp.status_code == 200 and res.get('status') == 'success':
    print(f"\nOrder placed! order_id={res['data']['order_id']}")
else:
    print(f"\nOrder FAILED: {res}")
