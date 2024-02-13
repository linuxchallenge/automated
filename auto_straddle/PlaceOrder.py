import logging
import angel_one.angelone_api as angel_api
import fivepaisa.fivepaise_api as fivepaise_api

logging.basicConfig(filename='/tmp/auostraddle.log', filemode='w',
                    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] %(message)s')

logging.getLogger().setLevel(logging.INFO)

class PlaceOrder:
    obj_1 = None

    def __init__(self):
        pass

    def init_account(self, account):
        self.account_id = account
        if (account == 'deepti'):
            self.obj_1 = angel_api.angelone_api()
        if (account == 'leelu'):
            self.obj_2 = fivepaise_api.fivepaise_api()

    def place_orders(self, account, atm_ce_strike, pe_ce, symbol, qty):
        multiplication_factor = {
            'NIFTY': 50,
            'BANKNIFTY': 15,
            'FINNIFTY': 40
        }
        qty = qty * multiplication_factor[symbol]

        # Convert qty to integer
        qty = int(qty)

        print(f"Placing Sell order for account {account}: option with strike price {atm_ce_strike}")
        logging.info(f"Placing Sell order for account {account} {symbol}:  option with strike price {atm_ce_strike}")
        order_id = 0

        if (account == 'deepti'):
            order_id = self.obj_1.place_order(symbol, qty, 'SELL', atm_ce_strike, pe_ce)

        if (account == 'leelu'):
            order_id = self.obj_2.place_order(symbol, qty, 'SELL', atm_ce_strike, pe_ce)
        return order_id

    def close_orders(self, account, atm_ce_strike, pe_ce, symbol, qty):
        multiplication_factor = {
            'NIFTY': 50,
            'BANKNIFTY': 15,
            'FINNIFTY': 40
        }
        qty = qty * multiplication_factor[symbol]

        # Convert qty to integer
        qty = int(qty)

        print(f"Closing order for account {account}: option with strike price {atm_ce_strike}")
        logging.info(f"Closing order for account {account}: option with strike price {atm_ce_strike} {symbol}")
        order_id = 0
        if (account == 'deepti'):
            order_id = self.obj_1.place_order(symbol, qty, 'BUY', atm_ce_strike, pe_ce)

        if (account == 'leelu'):
            order_id = self.obj_2.place_order(symbol, qty, 'BUY', atm_ce_strike, pe_ce)

        return order_id

    def order_status(self, order_id):
        print(f"Order status for order id {order_id}")
        logging.info(f"Order status for order id {order_id}")
        order_status = ''
        average_price = 0
        if (self.account_id == 'deepti'):
            order_status, average_price = self.obj_1.get_order_status(order_id)
        
        return order_status, average_price