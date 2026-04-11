"""Module providing a function for main function """

# pylint: disable=W1203
# pylint: disable=W1201
# pylint: disable=W1202
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=C0325
# pylint: disable=W0201
# pylint: disable=W0621

import logging
import time
import pandas as pd
import angel_one.angelone_api as angel_api
import fivepaisa.fivepaise_api as fivepaise_module
import upstox.upstox_api as upstox_module

logger = logging.getLogger(__name__)

# Map commoidity to symbol
commodity_to_symbol = {
    'CRUDEOIL': 'CRUDEOILM',
    'NATURALGAS': 'NATGASMINI',
    'COPPER': 'COPPER',
    'GOLD': 'GOLDM',
    'LEAD': 'LEADMINI',
    'SILVER': 'SILVERM',
    'ZINC': 'ZINCMINI',
    'ALUMINIUM': 'ALUMINI',
}

class PlaceOrder:

    def __init__(self):
        # Instance variables - not shared across instances
        self.obj_1 = None
        self.obj_2 = None
        self.obj_3 = None
        self.account_id = None

    def init_account(self, account):
        self.account_id = account
        if account == 'deepti':
            self.obj_1 = angel_api.angelone_api()
        if account == 'leelu':
            self.obj_2 = fivepaise_module.fivepaise_api(account)
        if account == 'avanthi':
            self.obj_3 = upstox_module.upstox_api()


    def place_buy_orders_commodity(self, account, symbol, qty, expiry=None, isCommodity=True):

        # Implementation of commodity buy orders
        # Convert qty to integer
        qty = int(qty)

        print(f"Placing Buy order for account {account}: commodity {symbol}")
        logging.info(f"Placing Buy order for commodity account {account} {symbol}")
        order_id = 0
        expiry_ret = '2021-07-29'

        if account == 'deepti':
            order_id, expiry_ret = self.obj_1.place_order_commodity(symbol, qty, 'BUY', expiry, isCommodity)
            # Retry up to 3 times if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 3:
                retry_count += 1
                logging.warning(f"Order placement failed with order_id={order_id}, retry {retry_count}/3...")
                time.sleep(2)
                order_id, expiry_ret = self.obj_1.place_order_commodity(symbol, qty, 'BUY', expiry, isCommodity)

        if (account == 'leelu'):
            order_id, expiry_ret = self.obj_2.place_order_commodity(symbol, qty, 'BUY', expiry, isCommodity)
            # Retry up to 1 time if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 1:
                retry_count += 1
                logging.warning(f"Order placement failed with order_id={order_id}, retry {retry_count}/1...")
                time.sleep(2)
                order_id, expiry_ret = self.obj_2.place_order_commodity(symbol, qty, 'BUY', expiry, isCommodity)

        if (account == 'avanthi'):
            order_id, expiry_ret = self.obj_3.place_order_commodity(symbol, qty, 'BUY', expiry, isCommodity)
            # Retry up to 1 time if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 1:
                retry_count += 1
                logging.warning(f"Order placement failed with order_id={order_id}, retry {retry_count}/1...")
                time.sleep(2)
                order_id, expiry_ret = self.obj_3.place_order_commodity(symbol, qty, 'BUY', expiry, isCommodity)

        logging.info(f"Order id for account: {order_id}")
        return order_id, expiry_ret

    def place_sell_orders_commodity(self, account, symbol, qty, expiry=None, isCommodity=True):
        # Implementation of commodity sell orders
        # Convert qty to integer
        qty = int(qty)

        print(f"Placing Sell order for account {account}: commodity {symbol}")
        logging.info(f"Placing Sell order for commodity account {account} {symbol}")
        order_id = 0
        expiry_ret = '2021-07-29'

        if account == 'deepti':
            order_id, expiry_ret = self.obj_1.place_order_commodity(symbol, qty, 'SELL', expiry, isCommodity)
            # Retry up to 3 times if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 3:
                retry_count += 1
                logging.warning(f"Order placement failed with order_id={order_id}, retry {retry_count}/3...")
                time.sleep(1)
                order_id, expiry_ret = self.obj_1.place_order_commodity(symbol, qty, 'SELL', expiry, isCommodity)

        if (account == 'leelu'):
            order_id, expiry_ret = self.obj_2.place_order_commodity(symbol, qty, 'SELL', expiry, isCommodity)
            # Retry up to 1 time if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 1:
                retry_count += 1
                logging.warning(f"Order placement failed with order_id={order_id}, retry {retry_count}/1...")
                time.sleep(2)
                order_id, expiry_ret = self.obj_2.place_order_commodity(symbol, qty, 'SELL', expiry, isCommodity)

        if (account == 'avanthi'):
            order_id, expiry_ret = self.obj_3.place_order_commodity(symbol, qty, 'SELL', expiry, isCommodity)
            # Retry up to 1 time if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 1:
                retry_count += 1
                logging.warning(f"Order placement failed with order_id={order_id}, retry {retry_count}/1...")
                time.sleep(2)
                order_id, expiry_ret = self.obj_3.place_order_commodity(symbol, qty, 'SELL', expiry, isCommodity)

        logging.info(f"Order id for account: {order_id}")
        return order_id, expiry_ret


    def place_orders(self, account, atm_ce_strike, pe_ce, symbol, qty, intraday=True):
        multiplication_factor = {
            'NIFTY': 65,
            'BANKNIFTY': 30,
            'FINNIFTY': 65,
            'MIDCPNIFTY': 50,
            'SENSEX': 20
        }
        # Use .get() with default value of 1
        multiplier = multiplication_factor.get(symbol, 1)
        qty = qty * multiplier

        # Add try/except for type conversion
        try:
            qty = int(qty)
        except (ValueError, TypeError) as e:
            logging.error(f"Invalid quantity value: {qty}. Error: {e}")
            return -1

        print(f"Placing Sell order for account {account}: option with strike price {atm_ce_strike}")
        logging.info(f"Placing Sell order for account {account} {symbol}:  option with strike price {atm_ce_strike}")
        order_id = -1
        if account == 'deepti' and hasattr(self, 'obj_1') and self.obj_1 is not None:
            order_id = self.obj_1.place_order(symbol, qty, 'SELL', atm_ce_strike, pe_ce, intraday)
            logging.info(f"API Response for {account} open order: {order_id}")
            # Retry up to 3 times if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 3:
                retry_count += 1
                logging.warning(f"Order placement failed for {account} with order_id={order_id}, retry {retry_count}/3...")
                time.sleep(1)
                order_id = self.obj_1.place_order(symbol, qty, 'SELL', atm_ce_strike, pe_ce, intraday)
                logging.info(f"Retry {retry_count} API Response for {account} open order: {order_id}")
        elif account == 'leelu' and hasattr(self, 'obj_2') and self.obj_2 is not None:
            order_id, _ = self.obj_2.place_order(symbol, qty, 'SELL', atm_ce_strike, pe_ce, intraday)
            # Retry up to 1 time if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 1:
                retry_count += 1
                logging.warning(f"Order placement failed for {account} with order_id={order_id}, retry {retry_count}/1...")
                time.sleep(2)
                order_id, _ = self.obj_2.place_order(symbol, qty, 'SELL', atm_ce_strike, pe_ce, intraday)
        elif account == 'avanthi' and hasattr(self, 'obj_3') and self.obj_3 is not None:
            order_id = self.obj_3.place_order(symbol, qty, 'SELL', atm_ce_strike, pe_ce, intraday)
            # Retry up to 1 time if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 1:
                retry_count += 1
                logging.warning(f"Order placement failed for {account} with order_id={order_id}, retry {retry_count}/1...")
                time.sleep(2)
                order_id = self.obj_3.place_order(symbol, qty, 'SELL', atm_ce_strike, pe_ce, intraday)
        elif (account == 'dummy'):
            order_id = 987654321
        else:
            logging.error(f"Invalid account or API object not initialized: {account}")
            return -1

        # Standardize result check
        if order_id == -1 or order_id == 0 or order_id is None or pd.isna(order_id):
            logging.error(f"Order failed to generate a valid ID for {account}")
            return -1

        logging.info(f"Order id for account: {order_id}")
        print(f"Order id for account: {order_id}")
        return order_id

    def buy_hedge_orders(self, account, strike, pe_ce, symbol, qty, intraday=True):
        """Buy hedge options (protective OTM options for short strangle)"""
        multiplication_factor = {
            'NIFTY': 65,
            'BANKNIFTY': 30,
            'FINNIFTY': 65,
            'MIDCPNIFTY': 50,
            'SENSEX': 20
        }
        # Use .get() with default value of 1
        multiplier = multiplication_factor.get(symbol, 1)
        qty = qty * multiplier

        # Add try/except for type conversion
        try:
            qty = int(qty)
        except (ValueError, TypeError) as e:
            logging.error(f"Invalid quantity value: {qty}. Error: {e}")
            return -1

        print(f"Placing Buy hedge order for account {account}: option with strike price {strike}")
        logging.info(f"Placing Buy hedge order for account {account} {symbol}: option with strike price {strike}")
        order_id = -1
        if account == 'deepti' and hasattr(self, 'obj_1') and self.obj_1 is not None:
            order_id = self.obj_1.place_order(symbol, qty, 'BUY', strike, pe_ce, intraday)
            logging.info(f"API Response for {account} hedge order: {order_id}")
            # Retry up to 3 times if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 3:
                retry_count += 1
                logging.warning(f"Hedge order placement failed for {account} with order_id={order_id}, retry {retry_count}/3...")
                time.sleep(1)
                order_id = self.obj_1.place_order(symbol, qty, 'BUY', strike, pe_ce, intraday)
                logging.info(f"Retry {retry_count} API Response for {account} hedge order: {order_id}")
        elif account == 'leelu' and hasattr(self, 'obj_2') and self.obj_2 is not None:
            order_id, _ = self.obj_2.place_order(symbol, qty, 'BUY', strike, pe_ce, intraday)
            # Retry up to 3 times if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 3:
                retry_count += 1
                logging.warning(f"Hedge order placement failed for {account} with order_id={order_id}, retry {retry_count}/3...")
                time.sleep(1)
                order_id, _ = self.obj_2.place_order(symbol, qty, 'BUY', strike, pe_ce, intraday)
        elif account == 'avanthi' and hasattr(self, 'obj_3') and self.obj_3 is not None:
            order_id = self.obj_3.place_order(symbol, qty, 'BUY', strike, pe_ce, intraday)
            # Retry up to 3 times if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 3:
                retry_count += 1
                logging.warning(f"Hedge order placement failed for {account} with order_id={order_id}, retry {retry_count}/3...")
                time.sleep(1)
                order_id = self.obj_3.place_order(symbol, qty, 'BUY', strike, pe_ce, intraday)
        elif (account == 'dummy'):
            order_id = 987654322
        else:
            logging.error(f"Invalid account or API object not initialized: {account}")
            return -1

        # Standardize result check
        if order_id == -1 or order_id == 0 or order_id is None or pd.isna(order_id):
            logging.error(f"Hedge order failed to generate a valid ID for {account}")
            return -1

        logging.info(f"Hedge order id for account: {order_id}")
        print(f"Hedge order id for account: {order_id}")
        return order_id

    def close_hedge_orders(self, account, strike, pe_ce, symbol, qty, intraday=True):
        """Close hedge options (sell back the protective options)"""
        multiplication_factor = {
            'NIFTY': 65,
            'BANKNIFTY': 30,
            'FINNIFTY': 65,
            'MIDCPNIFTY': 50,
            'SENSEX': 20
        }
        qty = qty * multiplication_factor[symbol]

        # Convert qty to integer
        qty = int(qty)

        print(f"Closing hedge order for account {account}: option with strike price {strike}")
        logging.info(f"Closing hedge order for account {account}: option with strike price {strike} {symbol}")
        order_id = -1
        if (account == 'deepti' and hasattr(self, 'obj_1') and self.obj_1 is not None):
            order_id = self.obj_1.place_order(symbol, qty, 'SELL', strike, pe_ce, intraday)
            logging.info(f"API Response for {account} hedge close order: {order_id}")
            # Retry up to 3 times if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 3:
                retry_count += 1
                logging.warning(f"Hedge close order placement failed for {account} with order_id={order_id}, retry {retry_count}/3...")
                time.sleep(1)
                order_id = self.obj_1.place_order(symbol, qty, 'SELL', strike, pe_ce, intraday)
                logging.info(f"Retry {retry_count} API Response for {account} hedge close order: {order_id}")

        elif (account == 'leelu' and hasattr(self, 'obj_2') and self.obj_2 is not None):
            order_id, _ = self.obj_2.place_order(symbol, qty, 'SELL', strike, pe_ce, intraday)
            # Retry up to 3 times if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 3:
                retry_count += 1
                logging.warning(f"Hedge close order placement failed for {account} with order_id={order_id}, retry {retry_count}/3...")
                time.sleep(1)
                order_id, _ = self.obj_2.place_order(symbol, qty, 'SELL', strike, pe_ce, intraday)

        elif (account == 'avanthi' and hasattr(self, 'obj_3') and self.obj_3 is not None):
            order_id = self.obj_3.place_order(symbol, qty, 'SELL', strike, pe_ce, intraday)
            # Retry up to 3 times if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 3:
                retry_count += 1
                logging.warning(f"Hedge close order placement failed for {account} with order_id={order_id}, retry {retry_count}/3...")
                time.sleep(1)
                order_id = self.obj_3.place_order(symbol, qty, 'SELL', strike, pe_ce, intraday)
        elif (account == 'dummy'):
            order_id = 123456790

        # Standardize result check
        if order_id == -1 or order_id == 0 or order_id is None or pd.isna(order_id):
            logging.error(f"Hedge close order failed to generate a valid ID for {account}")
            return -1

        logging.info(f"Hedge close order id for account: {order_id}")
        return order_id

    def place_order_synthetic_future(self, account, symbol, qty, buy_sell, strike_price, pe_ce, expiry=None):
        multiplication_factor = {
            'NIFTY': 65,
            'BANKNIFTY': 30,
            'FINNIFTY': 65,
            'MIDCPNIFTY': 50
        }
        qty = qty * multiplication_factor[symbol]

        # Convert qty to integer
        qty = int(qty)

        print(f"Placing Sell order for account {account}: option with strike price {strike_price}")
        logging.info(f"Placing Sell order for account {account} {symbol}:  option with strike price {strike_price}")
        order_id = 0
        expiry_ret = None

        if account == 'deepti':
            order_id, expiry_ret = self.obj_1.place_order_synthetic_future(symbol, qty, buy_sell, strike_price, pe_ce, expiry)
            # Retry up to 3 times if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 3:
                retry_count += 1
                logging.warning(f"Synthetic future order placement failed with order_id={order_id}, retry {retry_count}/3...")
                time.sleep(1)
                order_id, expiry_ret = self.obj_1.place_order_synthetic_future(symbol, qty, buy_sell, strike_price, pe_ce, expiry)

        if account == 'leelu':
            order_id, expiry_ret = self.obj_2.place_order_synthetic_future(symbol, qty, buy_sell, strike_price, pe_ce, expiry)
            # Retry up to 3 times if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 3:
                retry_count += 1
                logging.warning(f"Synthetic future order placement failed with order_id={order_id}, retry {retry_count}/3...")
                time.sleep(1)
                order_id, expiry_ret = self.obj_2.place_order_synthetic_future(symbol, qty, buy_sell, strike_price, pe_ce, expiry)

        if account == 'avanthi':
            order_id, expiry_ret = self.obj_3.place_order_synthetic_future(symbol, qty, buy_sell, strike_price, pe_ce, expiry)
            # Retry up to 3 times if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 3:
                retry_count += 1
                logging.warning(f"Synthetic future order placement failed with order_id={order_id}, retry {retry_count}/3...")
                time.sleep(1)
                order_id, expiry_ret = self.obj_3.place_order_synthetic_future(symbol, qty, buy_sell, strike_price, pe_ce, expiry)

        return order_id, expiry_ret

    def place_orders_option_buy(self, account, atm_ce_strike, pe_ce, symbol, qty, buy_sell):
        multiplication_factor = {
            'NIFTY': 65,
            'BANKNIFTY': 30,
            'FINNIFTY': 65,
            'MIDCPNIFTY': 50
        }
        qty = qty * multiplication_factor[symbol]

        # Convert qty to integer
        qty = int(qty)

        print(f"Placing Sell order for account {account}: option with strike price {atm_ce_strike}")
        logging.info(f"Placing Sell order for account {account} {symbol}:  option with strike price {atm_ce_strike}")
        order_id = 0

        if account == 'deepti':
            order_id = self.obj_1.place_order_option_buy(symbol, qty, buy_sell, atm_ce_strike, pe_ce)
            # Retry up to 3 times if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 3:
                retry_count += 1
                logging.warning(f"Option buy order placement failed with order_id={order_id}, retry {retry_count}/3...")
                time.sleep(1)
                order_id = self.obj_1.place_order_option_buy(symbol, qty, buy_sell, atm_ce_strike, pe_ce)

        if (account == 'leelu'):
            order_id = self.obj_2.place_order(symbol, qty, buy_sell, atm_ce_strike, pe_ce)
            # Retry up to 3 times if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 3:
                retry_count += 1
                logging.warning(f"Option buy order placement failed with order_id={order_id}, retry {retry_count}/3...")
                time.sleep(1)
                order_id = self.obj_2.place_order(symbol, qty, buy_sell, atm_ce_strike, pe_ce)

        if (account == 'avanthi'):
            order_id = self.obj_3.place_order(symbol, qty, buy_sell, atm_ce_strike, pe_ce)
            # Retry up to 3 times if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 3:
                retry_count += 1
                logging.warning(f"Option buy order placement failed with order_id={order_id}, retry {retry_count}/3...")
                time.sleep(1)
                order_id = self.obj_3.place_order(symbol, qty, buy_sell, atm_ce_strike, pe_ce)

        logging.info(f"Order id for account: {order_id}")
        return order_id


    def close_orders(self, account, atm_ce_strike, pe_ce, symbol, qty, intraday=True):
        multiplication_factor = {
            'NIFTY': 65,
            'BANKNIFTY': 30,
            'FINNIFTY': 65,
            'MIDCPNIFTY': 50,
            'SENSEX': 20
        }
        qty = qty * multiplication_factor[symbol]

        # Convert qty to integer
        qty = int(qty)

        print(f"Closing order for account {account}: option with strike price {atm_ce_strike}")
        logging.info(f"Closing order for account {account}: option with strike price {atm_ce_strike} {symbol}")
        order_id = -1
        if (account == 'deepti' and hasattr(self, 'obj_1') and self.obj_1 is not None):
            order_id = self.obj_1.place_order(symbol, qty, 'BUY', atm_ce_strike, pe_ce, intraday)
            logging.info(f"API Response for {account} close order: {order_id}")
            # Retry up to 3 times if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 3:
                retry_count += 1
                logging.warning(f"Close order placement failed for {account} with order_id={order_id}, retry {retry_count}/3...")
                time.sleep(1)  # Brief delay between retries
                order_id = self.obj_1.place_order(symbol, qty, 'BUY', atm_ce_strike, pe_ce, intraday)
                logging.info(f"Retry {retry_count} API Response for {account} close order: {order_id}")

        elif (account == 'leelu' and hasattr(self, 'obj_2') and self.obj_2 is not None):
            order_id, _ = self.obj_2.place_order(symbol, qty, 'BUY', atm_ce_strike, pe_ce, intraday)
            # Retry up to 3 times if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 3:
                retry_count += 1
                logging.warning(f"Close order placement failed for {account} with order_id={order_id}, retry {retry_count}/3...")
                time.sleep(1)  # Brief delay between retries
                order_id, _ = self.obj_2.place_order(symbol, qty, 'BUY', atm_ce_strike, pe_ce, intraday)

        elif (account == 'avanthi' and hasattr(self, 'obj_3') and self.obj_3 is not None):
            order_id = self.obj_3.place_order(symbol, qty, 'BUY', atm_ce_strike, pe_ce, intraday)
            # Retry up to 3 times if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 3:
                retry_count += 1
                logging.warning(f"Close order placement failed for {account} with order_id={order_id}, retry {retry_count}/3...")
                time.sleep(1)  # Brief delay between retries
                order_id = self.obj_3.place_order(symbol, qty, 'BUY', atm_ce_strike, pe_ce, intraday)
        elif (account == 'dummy'):
            order_id = 123456789

        # Standardize result check - ensure we don't return NaN/None to the strategy
        if order_id == -1 or order_id == 0 or order_id is None or pd.isna(order_id):
            logging.error(f"Close order failed to generate a valid ID for {account}")
            return -1

        logging.info(f"Order id for close account: {order_id}")
        return order_id

    def get_ledger_balance(self, account):
        """Get the ledger balance for a specific account"""
        print(f"Fetching ledger balance for account: {account}")
        balance = 0.0
        if account == 'deepti' and hasattr(self, 'obj_1') and self.obj_1 is not None:
            balance = self.obj_1.get_ledger_balance()
        elif account == 'leelu' and hasattr(self, 'obj_2') and self.obj_2 is not None:
            balance = self.obj_2.get_ledger_balance()
        elif account == 'avanthi' and hasattr(self, 'obj_3') and self.obj_3 is not None:
            balance = self.obj_3.get_ledger_balance()
        elif account == 'dummy':
            balance = 100000.0
        else:
            logging.error(f"Invalid account or API object not initialized: {account}")
            return 0.0

        print(f"Ledger balance for {account}: {balance}")
        return balance

    def order_status(self, account, order_id, old_price):
        print(f"Order status for order id {order_id}")
        logging.info(f"Order status for order id {order_id}")
        order_status = ''
        average_price = 0
        if (account == 'deepti'):
            order_status, average_price = self.obj_1.get_order_status(order_id)

        if (account == 'leelu'):
            order_status, average_price = self.obj_2.get_order_status(order_id)
            if (order_status == -1):
                order_status, average_price = self.obj_2.get_order_status(order_id)
                if (order_status == -1):
                    # API failed twice - return error status instead of assuming Complete
                    logging.error(f"Failed to get order status for {account} order_id={order_id} after 2 retries")
                    order_status = 'APIError'
                    average_price = -1
        if (account == 'avanthi'):
            order_status, average_price = self.obj_3.get_order_status(order_id)
            if (order_status == -1):
                order_status, average_price = self.obj_3.get_order_status(order_id)
                if (order_status == -1):
                    # API failed twice - return error status instead of assuming Complete
                    logging.error(f"Failed to get order status for {account} order_id={order_id} after 2 retries")
                    order_status = 'APIError'
                    average_price = -1

        if (account == 'dummy'):
            order_status = 'Complete'
            average_price = old_price
        return order_status, average_price

    def get_commodity_position(self, account, symbol, trade_type):
        """Check if there is an open commodity position at the broker matching the intended trade.

        Returns:
            (trade_type, avg_price) if a matching position is found, (None, 0) otherwise.
        """
        if account == 'deepti' and hasattr(self, 'obj_1') and self.obj_1 is not None:
            return self.obj_1.get_commodity_position(symbol, trade_type)
        elif account == 'leelu' and hasattr(self, 'obj_2') and self.obj_2 is not None:
            return self.obj_2.get_commodity_position(symbol, trade_type)
        elif account == 'avanthi' and hasattr(self, 'obj_3') and self.obj_3 is not None:
            return self.obj_3.get_commodity_position(symbol, trade_type)
        elif account == 'dummy':
            return None, 0
        logging.error(f"get_commodity_position: invalid account or API not initialized: {account}")
        return None, 0

    def place_cash_order(self, account, symbol, quantity, side):
        print(f"Placing order for account {account}: symbol {symbol}")
        logging.info(f"Placing order for account {account} {symbol}")
        order_id = 0

        if account == 'deepti':
            order_id = self.obj_1.place_order_cash(symbol, quantity, side)
            # Retry up to 3 times if order_id is invalid (0, -1, or None)
            retry_count = 0
            while (order_id == -1 or order_id == 0 or order_id is None) and retry_count < 3:
                retry_count += 1
                logging.warning(f"Cash order placement failed with order_id={order_id}, retry {retry_count}/3...")
                time.sleep(1)
                order_id = self.obj_1.place_order_cash(symbol, quantity, side)

        logging.info(f"Order id for account: {order_id}")
        return order_id


# Test code to simulate session expiry error
if __name__ == "__main__":
    print("\n" + "="*80)
    print("SESSION EXPIRY SIMULATION TEST")
    print("="*80 + "\n")

    print("Step 1: Creating PlaceOrder object and initializing accounts...")
    place_order = PlaceOrder()
    place_order.init_account('leelu')
    place_order.init_account('avanthi')
    print("✓ Both accounts initialized successfully\n")

    print("Step 2: Placing immediate orders (should work)...")
    print("\n--- Testing avanthi account (immediate order) ---")
    # place_orders(account, atm_ce_strike, pe_ce, symbol, qty, intraday=True)
    order_id = place_order.place_orders('avanthi', 57000, 'PE', 'BANKNIFTY', 30)
    print(f"Order ID: {order_id}")
    if order_id > 0:
        status, price = place_order.obj_3.get_order_status(order_id)
        print(f"Order Status: {status}, Price: {price}")
    else:
        print(f"❌ Order failed with order_id: {order_id}")

    print("\n--- Testing leelu account (immediate order) ---")
    # place_orders(account, atm_ce_strike, pe_ce, symbol, qty, intraday=True)
    order_id = place_order.place_orders('leelu', 57000, 'PE', 'BANKNIFTY', 30)
    print(f"Order ID: {order_id}")
    if order_id > 0:
        status, price = place_order.obj_2.get_order_status(order_id)
        print(f"Order Status: {status}, Price: {price}")
    else:
        print(f"❌ Order failed with order_id: {order_id}")

    print("\n" + "="*80)
    print("NOTE: To simulate session expiry error:")
    print("1. Wait 60+ minutes after login")
    print("2. Or manually set session to expire in fivepaise_api.py")
    print("3. Then try placing orders again")
    print("="*80 + "\n")

    # Uncomment the following to wait and test session expiry
    print("Waiting 5 seconds to simulate delay...")
    time.sleep(5)
    print("\nStep 3: Placing orders after delay...")
    order_id = place_order.place_orders('leelu', 57000, 'PE', 'BANKNIFTY', 30)
    print(f"Order ID after delay: {order_id}")
    if order_id > 0:
        status, price = place_order.obj_2.get_order_status(order_id)
        print(f"Order Status: {status}, Price: {price}")
    else:
        print(f"❌ Session expired! Order failed with order_id: {order_id}")


    time.sleep(10)
    print("\nStep 4: Placing orders after delay...")
    order_id = place_order.place_orders('avanthi', 57000, 'PE', 'BANKNIFTY', 30)
    print(f"Order ID after delay: {order_id}")
    if order_id > 0:
        status, price = place_order.obj_2.get_order_status(order_id)
        print(f"Order Status: {status}, Price: {price}")
    else:
        print(f"❌ Session expired! Order failed with order_id: {order_id}")
