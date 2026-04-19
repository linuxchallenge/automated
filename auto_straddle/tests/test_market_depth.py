"""
Test market depth for BANKNIFTY 55600 PE and MCX COPPER using leelu account.
Run from auto_straddle directory:
    python test_market_depth.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fivepaisa.fivepaise_api import fivepaise_api

print("Logging in as leelu...")
api = fivepaise_api('leelu')
print("Login done.\n")

tests = [
    ("BANKNIFTY 55600 PE Apr28", '67521', 'N', 'D'),
    ("MCX COPPER Apr30",         '488791', 'M', 'D'),
]

for name, scrip_code, exch, exch_type in tests:
    print(f"--- {name} (ScripCode={scrip_code}, Exch={exch}, ExchType={exch_type}) ---")
    api._fix_shared_payload_bug()
    resp = api.obj.fetch_market_depth_by_scrip(Exch=exch, ExchType=exch_type, ScripCode=scrip_code)
    if resp is None:
        print("  Response: None\n")
        continue
    print(f"  Status={resp.get('Status')}, Message={resp.get('Message')}")
    entries = resp.get('MarketDepthData', [])
    bids = [e for e in entries if e.get('BbBuySellFlag') == 66 and e.get('Price', 0) > 0]
    asks = [e for e in entries if e.get('BbBuySellFlag') == 83 and e.get('Price', 0) > 0]
    print(f"  Best Bid: {bids[0]['Price'] if bids else 'N/A'}  |  Best Ask: {asks[0]['Price'] if asks else 'N/A'}")
    print(f"  All entries: {entries}\n")
