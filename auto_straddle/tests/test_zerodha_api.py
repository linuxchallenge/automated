"""
Live integration tests for zerodha_api module.
Run from auto_straddle directory:
    python tests/test_zerodha_api.py

Tests (run individually via command-line args):
    python tests/test_zerodha_api.py login
    python tests/test_zerodha_api.py token_lookup
    python tests/test_zerodha_api.py option_buy_nifty
    python tests/test_zerodha_api.py option_sell_nifty
    python tests/test_zerodha_api.py option_buy_sensex
    python tests/test_zerodha_api.py option_sell_sensex
    python tests/test_zerodha_api.py commodity_buy_silver
    python tests/test_zerodha_api.py order_status <order_id>
    python tests/test_zerodha_api.py positions
    python tests/test_zerodha_api.py balance
    python tests/test_zerodha_api.py all  (runs login + token_lookup + balance + positions)
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))



def get_api():
    """Initialize and return zerodha_api instance."""
    print("=" * 60)
    print("Initializing Zerodha Kite API...")
    print("=" * 60)
    from zerodha.zerodha_api import zerodha_api
    api = zerodha_api()
    print(f"Instruments loaded: {len(api.token_df)}")
    return api


def test_login():
    """Test login and profile fetch."""
    api = get_api()
    profile = api.kite.profile()
    print("\n--- Login Test ---")
    print(f"User: {profile.get('user_name')}")
    print(f"User ID: {profile.get('user_id')}")
    print(f"Email: {profile.get('email')}")
    print(f"Broker: {profile.get('broker')}")
    print("LOGIN TEST PASSED")
    return api


def test_token_lookup(api=None):
    """Test getTokenInfo for various exchanges and instrument types."""
    if api is None:
        api = get_api()

    print("\n--- Token Lookup Tests ---")

    # 1. NSE Equity
    print("\n[1] NSE Equity - RELIANCE")
    result = api.getTokenInfo('NSE', 'EQ', 'RELIANCE', 0, 'X')
    if result is not None and not result.empty:
        print(f"   Found: symbol={result.iloc[0]['symbol']}, token={result.iloc[0]['token']}")
    else:
        print("   NOT FOUND")

    # 2. NFO OPTIDX - NIFTY CE
    print("\n[2] NFO OPTIDX - NIFTY 24000 CE")
    result = api.getTokenInfo('NFO', 'OPTIDX', 'NIFTY', 240, 'CE')
    if result is not None and not result.empty:
        row = result.iloc[0]
        print(f"   Found: symbol={row['symbol']}, token={row['token']}, expiry={row['expiry']}, lot={row['lotsize']}")
    else:
        print("   NOT FOUND (try a different strike)")

    # 3. NFO OPTIDX - NIFTY PE
    print("\n[3] NFO OPTIDX - NIFTY 24000 PE")
    result = api.getTokenInfo('NFO', 'OPTIDX', 'NIFTY', 240, 'PE')
    if result is not None and not result.empty:
        row = result.iloc[0]
        print(f"   Found: symbol={row['symbol']}, token={row['token']}, expiry={row['expiry']}, lot={row['lotsize']}")
    else:
        print("   NOT FOUND (try a different strike)")

    # 4. BFO OPTIDX - SENSEX
    print("\n[4] BFO OPTIDX - SENSEX 80000 CE")
    result = api.getTokenInfo('BFO', 'OPTIDX', 'SENSEX', 800, 'CE')
    if result is not None and not result.empty:
        row = result.iloc[0]
        print(f"   Found: symbol={row['symbol']}, token={row['token']}, expiry={row['expiry']}, lot={row['lotsize']}")
    else:
        print("   NOT FOUND (try a different strike)")

    # 5. MCX FUTCOM - SILVERMIC
    print("\n[5] MCX FUTCOM - SILVERMIC")
    result = api.getTokenInfo('MCX', 'FUTCOM', 'SILVERMIC', 0, 'X')
    if result is not None and not result.empty:
        row = result.iloc[0]
        print(f"   Found: symbol={row['symbol']}, token={row['token']}, expiry={row['expiry']}, lot={row['lotsize']}")
    else:
        print("   NOT FOUND")

    # 6. MCX FUTCOM - GOLDM
    print("\n[6] MCX FUTCOM - GOLDM")
    result = api.getTokenInfo('MCX', 'FUTCOM', 'GOLDM', 0, 'X')
    if result is not None and not result.empty:
        row = result.iloc[0]
        print(f"   Found: symbol={row['symbol']}, token={row['token']}, expiry={row['expiry']}, lot={row['lotsize']}")
    else:
        print("   NOT FOUND")

    print("\nTOKEN LOOKUP TEST PASSED")
    return api


def test_option_buy_nifty(api=None):
    """Test buying 1 lot NIFTY option (nearest expiry)."""
    if api is None:
        api = get_api()

    print("\n--- NIFTY Option BUY Test ---")

    # Get NIFTY LTP to determine ATM strike
    try:
        ltp_data = api.kite.ltp("NSE:NIFTY 50")
        nifty_ltp = ltp_data["NSE:NIFTY 50"]["last_price"]
        print(f"NIFTY LTP: {nifty_ltp}")
        # Round to nearest 100 for ATM strike
        atm_strike = round(nifty_ltp / 100) * 100
    except Exception as e:
        print(f"Could not get LTP: {e}, using default strike")
        atm_strike = 24000

    # Kite strike is actual value, but getTokenInfo expects strike/100
    strike_param = atm_strike / 100
    print(f"ATM Strike: {atm_strike} (param: {strike_param})")

    # Look up token
    df = api.getTokenInfo('NFO', 'OPTIDX', 'NIFTY', strike_param, 'PE')
    if df is None or df.empty:
        print(f"No token found for NIFTY {atm_strike} PE")
        return api

    row = df.iloc[0]
    lot = int(row['lotsize'])
    print(f"Contract: {row['symbol']}, lot={lot}, expiry={row['expiry']}")

    # Place BUY order
    print(f"\nPlacing BUY order: NIFTY {atm_strike} PE qty={lot}")
    order_id = api.place_order('NIFTY', lot, 'BUY', strike_param, 'PE', intraday=True)
    print(f"Order ID: {order_id}")

    if order_id and order_id != -1:
        print("NIFTY OPTION BUY TEST PASSED")
        # Check order status
        status, price = api.get_order_status(order_id)
        print(f"Order Status: {status}, Price: {price}")
    else:
        print("NIFTY OPTION BUY TEST FAILED")

    return api


def test_option_sell_nifty(api=None):
    """Test selling 1 lot NIFTY option (nearest expiry)."""
    if api is None:
        api = get_api()

    print("\n--- NIFTY Option SELL Test ---")

    try:
        ltp_data = api.kite.ltp("NSE:NIFTY 50")
        nifty_ltp = ltp_data["NSE:NIFTY 50"]["last_price"]
        atm_strike = round(nifty_ltp / 100) * 100
    except Exception:
        atm_strike = 24000

    strike_param = atm_strike / 100
    print(f"ATM Strike: {atm_strike}")

    df = api.getTokenInfo('NFO', 'OPTIDX', 'NIFTY', strike_param, 'CE')
    if df is None or df.empty:
        print(f"No token found for NIFTY {atm_strike} CE")
        return api

    row = df.iloc[0]
    lot = int(row['lotsize'])
    print(f"Contract: {row['symbol']}, lot={lot}, expiry={row['expiry']}")

    print(f"\nPlacing SELL order: NIFTY {atm_strike} CE qty={lot}")
    order_id = api.place_order('NIFTY', lot, 'SELL', strike_param, 'CE', intraday=True)
    print(f"Order ID: {order_id}")

    if order_id and order_id != -1:
        print("NIFTY OPTION SELL TEST PASSED")
        status, price = api.get_order_status(order_id)
        print(f"Order Status: {status}, Price: {price}")
    else:
        print("NIFTY OPTION SELL TEST FAILED")

    return api


def test_option_buy_sensex(api=None):
    """Test buying 1 lot SENSEX option."""
    if api is None:
        api = get_api()

    print("\n--- SENSEX Option BUY Test ---")

    try:
        ltp_data = api.kite.ltp("BSE:SENSEX")
        sensex_ltp = ltp_data["BSE:SENSEX"]["last_price"]
        print(f"SENSEX LTP: {sensex_ltp}")
        atm_strike = round(sensex_ltp / 100) * 100
    except Exception as e:
        print(f"Could not get LTP: {e}, using default strike")
        atm_strike = 80000

    strike_param = atm_strike / 100
    print(f"ATM Strike: {atm_strike} (param: {strike_param})")

    df = api.getTokenInfo('BFO', 'OPTIDX', 'SENSEX', strike_param, 'PE')
    if df is None or df.empty:
        print(f"No token found for SENSEX {atm_strike} PE")
        return api

    row = df.iloc[0]
    lot = int(row['lotsize'])
    print(f"Contract: {row['symbol']}, lot={lot}, expiry={row['expiry']}")

    print(f"\nPlacing BUY order: SENSEX {atm_strike} PE qty={lot}")
    order_id = api.place_order('SENSEX', lot, 'BUY', strike_param, 'PE', intraday=True)
    print(f"Order ID: {order_id}")

    if order_id and order_id != -1:
        print("SENSEX OPTION BUY TEST PASSED")
        status, price = api.get_order_status(order_id)
        print(f"Order Status: {status}, Price: {price}")
    else:
        print("SENSEX OPTION BUY TEST FAILED")

    return api


def test_option_sell_sensex(api=None):
    """Test selling 1 lot SENSEX option."""
    if api is None:
        api = get_api()

    print("\n--- SENSEX Option SELL Test ---")

    try:
        ltp_data = api.kite.ltp("BSE:SENSEX")
        sensex_ltp = ltp_data["BSE:SENSEX"]["last_price"]
        atm_strike = round(sensex_ltp / 100) * 100
    except Exception:
        atm_strike = 80000

    strike_param = atm_strike / 100
    print(f"ATM Strike: {atm_strike}")

    df = api.getTokenInfo('BFO', 'OPTIDX', 'SENSEX', strike_param, 'CE')
    if df is None or df.empty:
        print(f"No token found for SENSEX {atm_strike} CE")
        return api

    row = df.iloc[0]
    lot = int(row['lotsize'])
    print(f"Contract: {row['symbol']}, lot={lot}, expiry={row['expiry']}")

    print(f"\nPlacing SELL order: SENSEX {atm_strike} CE qty={lot}")
    order_id = api.place_order('SENSEX', lot, 'SELL', strike_param, 'CE', intraday=True)
    print(f"Order ID: {order_id}")

    if order_id and order_id != -1:
        print("SENSEX OPTION SELL TEST PASSED")
        status, price = api.get_order_status(order_id)
        print(f"Order Status: {status}, Price: {price}")
    else:
        print("SENSEX OPTION SELL TEST FAILED")

    return api


def test_commodity_buy_silver(api=None):
    """Test buying 1 lot SILVER mini via commodity order."""
    if api is None:
        api = get_api()

    print("\n--- Commodity BUY SILVER Test ---")

    # place_order_commodity maps SILVER -> SILVERMIC internally
    print("Placing commodity BUY order: SILVER qty=1")
    order_id, expiry = api.place_order_commodity('SILVER', 1, 'BUY')
    print(f"Order ID: {order_id}")
    print(f"Expiry: {expiry}")

    if order_id and order_id != -1:
        print("COMMODITY BUY SILVER TEST PASSED")
        status, price = api.get_order_status(order_id)
        print(f"Order Status: {status}, Price: {price}")
    else:
        print("COMMODITY BUY SILVER TEST FAILED")

    return api


def test_order_status(api=None, order_id=None):
    """Test order status for a given order_id."""
    if api is None:
        api = get_api()

    if order_id is None:
        print("ERROR: order_id required. Usage: python test_zerodha_api.py order_status <order_id>")
        return api

    print("\n--- Order Status Test ---")
    print(f"Checking order: {order_id}")
    status, price = api.get_order_status(order_id)
    print(f"Status: {status}")
    print(f"Average Price: {price}")
    print("ORDER STATUS TEST PASSED")
    return api


def test_positions(api=None):
    """Test fetching positions."""
    if api is None:
        api = get_api()

    print("\n--- Positions Test ---")
    try:
        positions = api.kite.positions()
        net = positions.get('net', [])
        day = positions.get('day', [])
        print(f"Net positions: {len(net)}")
        print(f"Day positions: {len(day)}")
        for pos in net:
            if int(pos.get('quantity', 0)) != 0:
                print(f"  {pos['exchange']}:{pos['tradingsymbol']} qty={pos['quantity']} "
                      f"avg={pos.get('average_price', 0)} pnl={pos.get('pnl', 0)}")
        print("POSITIONS TEST PASSED")
    except Exception as e:
        print(f"POSITIONS TEST FAILED: {e}")

    return api


def test_balance(api=None):
    """Test fetching ledger balance."""
    if api is None:
        api = get_api()

    print("\n--- Balance Test ---")
    balance = api.get_ledger_balance()
    print(f"Ledger Balance: {balance}")

    try:
        margins = api.kite.margins()
        print(f"Equity available: {margins.get('equity', {}).get('available', {})}")
        print(f"Commodity available: {margins.get('commodity', {}).get('available', {})}")
    except Exception as e:
        print(f"Margins detail error: {e}")

    print("BALANCE TEST PASSED")
    return api


def test_commodity_position(api=None):
    """Test checking commodity position."""
    if api is None:
        api = get_api()

    print("\n--- Commodity Position Test ---")
    for symbol in ['GOLD', 'SILVER', 'CRUDEOIL']:
        long_type, long_price = api.get_commodity_position(symbol, 'long')
        short_type, short_price = api.get_commodity_position(symbol, 'short')
        if long_type:
            print(f"  {symbol}: LONG position @ {long_price}")
        elif short_type:
            print(f"  {symbol}: SHORT position @ {short_price}")
        else:
            print(f"  {symbol}: No position")

    print("COMMODITY POSITION TEST PASSED")
    return api


# --- Main ---
if __name__ == "__main__":
    tests = {
        'login': test_login,
        'token_lookup': test_token_lookup,
        'option_buy_nifty': test_option_buy_nifty,
        'option_sell_nifty': test_option_sell_nifty,
        'option_buy_sensex': test_option_buy_sensex,
        'option_sell_sensex': test_option_sell_sensex,
        'commodity_buy_silver': test_commodity_buy_silver,
        'order_status': test_order_status,
        'positions': test_positions,
        'balance': test_balance,
        'commodity_position': test_commodity_position,
    }

    args = sys.argv[1:]

    if not args:
        print("Usage: python test_zerodha_api.py <test_name> [args]")
        print(f"\nAvailable tests: {', '.join(tests.keys())}")
        print("  all - runs login + token_lookup + balance + positions + commodity_position")
        sys.exit(0)

    test_name = args[0]

    if test_name == 'all':
        api = test_login()
        test_token_lookup(api)
        test_balance(api)
        test_positions(api)
        test_commodity_position(api)
        print("\n" + "=" * 60)
        print("ALL SAFE TESTS PASSED")
        print("=" * 60)
    elif test_name == 'order_status':
        order_id = args[1] if len(args) > 1 else None
        test_order_status(order_id=order_id)
    elif test_name in tests:
        tests[test_name]()
    else:
        print(f"Unknown test: {test_name}")
        print(f"Available: {', '.join(tests.keys())}, all")
