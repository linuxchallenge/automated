"""Module providing a function for 5 paise"""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=W0105


from io import StringIO
import time
from datetime import datetime
import traceback
import logging
import pandas as pd
from py5paisa import FivePaisaClient
#import py5paisa
import pyotp
import requests
import fivepaisa.credentials_2 as credentials_leelu
import fivepaisa.credentials_3 as credentials_avanthi
import TelegramSend

logger = logging.getLogger(__name__)

commodity_to_symbol = {
    'CRUDEOIL': 'CRUDEOILM',
    'NATURALGAS': 'NATGASMINI',
    'COPPER': 'COPPER',
    'GOLD': 'GOLDM',
    'LEAD': 'LEADMINI',
    'SILVER': 'SILVERM',
    'ZINC': 'ZINCMINI',
    'ALUMINIUM': 'ALUMINI',
    'NIFTY': 'NIFTY',
    'BANKNIFTY': 'BANKNIFTY',
    'FINNIFTY': 'FINNIFTY'
}


symbol_to_lot = {
    'CRUDEOIL': 10,
    'NATURALGAS': 250,
    'COPPER': 2500,
    'GOLD': 100,
    'LEAD': 1000,
    'ZINC': 1000,
    'ALUMINIUM': 1000,
    'SILVER': 5000,
}

class fivepaise_api(object):

    def __init__(self, account):
        # create object of call

        if account == 'leelu':
            cred={
                "APP_NAME":credentials_leelu.APP_NAME,
                "APP_SOURCE":credentials_leelu.APP_SOURCE,
                "USER_ID":credentials_leelu.USER_ID,
                "PASSWORD":credentials_leelu.PASSWORD,
                "USER_KEY":credentials_leelu.USER_KEY,
                "ENCRYPTION_KEY":credentials_leelu.ENCRYPTION_KEY,
            }
        elif account == 'avanthi':
            cred={
                "APP_NAME":credentials_avanthi.APP_NAME,
                "APP_SOURCE":credentials_avanthi.APP_SOURCE,
                "USER_ID":credentials_avanthi.USER_ID,
                "PASSWORD":credentials_avanthi.PASSWORD,
                "USER_KEY":credentials_avanthi.USER_KEY,
                "ENCRYPTION_KEY":credentials_avanthi.ENCRYPTION_KEY,
            }
        else:
            print("Invalid account")
            return

        self.obj = FivePaisaClient(cred=cred)

        attempts = 5
        while attempts > 0:
            if account == 'leelu':
                totp_pin = pyotp.TOTP(credentials_leelu.TOTP).now()

                self.session = self.obj.get_totp_session(credentials_leelu.CLIENTCODE,totp_pin,credentials_leelu.PIN)
                if self.session:
                    if None is self.obj.Login_check():
                        print("Login failed")
                        continue
                    break
            if account == 'avanthi':
                totp_pin = pyotp.TOTP(credentials_avanthi.TOTP).now()

                self.session = self.obj.get_totp_session(credentials_avanthi.CLIENTCODE,totp_pin,credentials_avanthi.PIN)
                if self.session:
                    break
            attempts = attempts - 1
            time.sleep(30)

        self.intializeSymbolTokenMap()

    def download_csv(self, url):
        response = requests.get(url, timeout=50)
        response.raise_for_status()  # Ensure the download was successful
        return response.text

    def intializeSymbolTokenMap(self):
        try:
            url = "https://openapi.5paisa.com/VendorsAPI/Service1.svc/ScripMaster/segment/All"
            self.csv_data = self.download_csv(url)
            self.scrip_master_df = pd.read_csv(StringIO(self.csv_data))
            self.scrip_master_df.to_csv("scrip_master_5paise.csv")
        except Exception as e:
            print(f"Error: {e}")

            # Check if the file exists
            try:
                self.scrip_master_df = pd.read_csv("scrip_master_5paise.csv")
            except Exception as e1:
                print(f"Error: {e1}")
                print("Error getting scrip_master_5paise.csv")
                raise e1

    def getTokenInfo(self, symbol, strike_price, pe_ce):
        # script_master_df has Name column like FINNIFTY 13 Feb 2024 CE 23750.00
        # Match the symbol and strike price and pe_ce and get ScriptCode collumn value
        df = self.scrip_master_df

        df = df[(df['SymbolRoot'] == symbol) & (df['StrikeRate'] == strike_price) & (df['ScripType'] == pe_ce)]
        # Sort the df by expiry date and get the first row
        df = df.sort_values(by='Expiry')

        # Add validation before accessing
        if df.empty:
            print(f"No matching options found for {symbol} {strike_price} {pe_ce}")
            return None

        # df.iloc[0]['expiry'] is before current date return the next row
        if df.iloc[0]['Expiry'] < datetime.now().strftime('%Y-%m-%d'):
            # Check if there's a next row before accessing
            if len(df) > 1:
                return df.iloc[1]
            else:
                print(f"No future expiry found for {symbol} {strike_price} {pe_ce}")
                return None
        return df.iloc[0]

    def get_commodity_symbol(self, symbol, expiry=None):
        df = self.scrip_master_df

        df = df[(df['SymbolRoot'] == symbol) & (df['ScripType'] == 'XX')]
        # Add validation for empty DataFrame
        if df.empty:
            print(f"No matching commodity found for {symbol}")
            return None

        # Sort the df by expiry date and get the first row
        df = df.sort_values(by='Expiry')

        if expiry is not None:
            df = df[df['Expiry'] == expiry]
            if df.empty:
                return None
            return df.iloc[0]

        # Check if the first expiry is within 10 days
        if len(df) > 1 and pd.to_datetime(df.iloc[0]['Expiry']) - pd.Timestamp.now() <= pd.Timedelta(days=10):
            return df.iloc[1]  # Return the next expiry
        elif len(df) > 0:
            return df.iloc[0]  # Return the first expiry
        else:
            return None

    def place_order_commodity(self, symbol, qty, buy_sell, expiry=None, isCommodity=True):
        tokenInfo = self.get_commodity_symbol(commodity_to_symbol[symbol], expiry)

        print("five paise place order")

        symbol = tokenInfo['SymbolRoot']
        token = tokenInfo['ScripCode']
        lot = int(tokenInfo['LotSize'])

        #qty = qty * lot

        print(f" Time: {datetime.now().strftime('%H:%M:%S')} Symbol: {symbol}, Token: {token}, Lot: {lot}")

        if buy_sell == 'BUY':
            buy_sell = 'B'
        else:
            buy_sell = 'S'
        try:
            if isCommodity:
                order_id = self.obj.place_order(OrderType=buy_sell, Exchange='M', ExchangeType='D', \
                                                ScripCode=int(token), Qty=int(qty), Price=0, IsIntraday=False)
            else:
                qty = qty * lot
                order_id = self.obj.place_order(OrderType=buy_sell, Exchange='N', ExchangeType='D', \
                                                ScripCode=int(token), Qty=int(qty), Price=0, IsIntraday=True)
            print(f" After order Time: {datetime.now().strftime('%H:%M:%S')})")
            print(f"Order id: {order_id['BrokerOrderID']} {order_id['Message']}")
            logger.info(f"Order id: {order_id['BrokerOrderID']} {order_id['Message']}")
        except Exception as e1:
            try:
                time.sleep(2)
                print(f" Retry order Time: {datetime.now().strftime('%H:%M:%S')})")
                print("Error placing order, trying again")
                logger.error("Error placing order, trying again %s", e1)

                x = TelegramSend.telegram_send_api()

                # Send profit loss over telegramsend send_message
                x.send_message("-4008545231", f"Warning 5 paise {symbol} order Pls check")

                order_id = self.obj.place_order(OrderType=buy_sell, Exchange='C', ExchangeType='C', \
                                                ScripCode=int(token), Qty=int(qty), Price=0, IsIntraday=True)
                print(f" After order Time: {datetime.now().strftime('%H:%M:%S')})")
                print(f"Order id: {order_id['BrokerOrderID']} {order_id['Message']}")
                logger.info(f"Order id: {order_id['BrokerOrderID']} {order_id['Message']}")
            except Exception as e2:
                print(''.join(traceback.format_exception(type(e2), e2, e2.__traceback__)))
                print(f"Error executing place_order: {e2}")
                logging.error("Error executing place_order: %s", e2)
                return -1, -1
        return order_id['BrokerOrderID'], tokenInfo['Expiry']

    def place_order(self, symbol, qty, buy_sell, strike_price, pe_ce, isIntraday=True):
        """
        Place an order for a given symbol, quantity, buy/sell action, strike price, and option type (PE/CE).
        
        Args:
            symbol: The underlying symbol (e.g., 'NIFTY', 'BANKNIFTY')
            qty: Quantity to trade
            buy_sell: 'BUY' or 'SELL'
            strike_price: Strike price of the option
            pe_ce: Option type 'PE' or 'CE'
            isIntraday: True for intraday orders, False for delivery/positional orders
        """
        tokenInfo = self.getTokenInfo(symbol, strike_price, pe_ce)

        if symbol == "SENSEX":
            # For SENSEX, we use the BFO exchange
            exchange = "B"
        else:
            # For other symbols, we use the NFO exchange
            exchange = "N"

        if tokenInfo is None:
            print(f"Could not find token info for {symbol} {strike_price} {pe_ce}")
            return -1, None

        print("five paise place order")

        symbol = tokenInfo['SymbolRoot']
        token = tokenInfo['ScripCode']
        lot = int(tokenInfo['LotSize'])

        print(f" Time: {datetime.now().strftime('%H:%M:%S')} Symbol: {symbol}, Token: {token}, Lot: {lot}, IsIntraday: {isIntraday}")

        if qty % lot != 0:
            return -1, None

        if buy_sell == 'BUY':
            buy_sell = 'B'
        else:
            buy_sell = 'S'

        try:
            # Use the isIntraday parameter in the order placement
            order_id = self.obj.place_order(
                OrderType=buy_sell,
                Exchange=exchange,
                ExchangeType='D',
                ScripCode=int(token),
                Qty=int(qty),
                Price=0,
                IsIntraday=isIntraday  # Actually use the parameter
            )
            print(f" After order Time: {datetime.now().strftime('%H:%M:%S')})")
            print(f"Order id: {order_id['BrokerOrderID']} {order_id['Message']}")
            logger.info(f"Order id: {order_id['BrokerOrderID']} {order_id['Message']}")

        except Exception as e1:
            try:
                time.sleep(2)
                print(f" Retry order Time: {datetime.now().strftime('%H:%M:%S')})")
                print("Error placing order, trying again")
                logger.error("Error placing order, trying again %s", e1)

                x = TelegramSend.telegram_send_api()
                x.send_message("-4008545231", f"Warning 5 paise {symbol} order Pls check")

                # Retry with the same isIntraday parameter
                order_id = self.obj.place_order(
                    OrderType=buy_sell,
                    Exchange='N',
                    ExchangeType='D',
                    ScripCode=int(token),
                    Qty=int(qty),
                    Price=0,
                    IsIntraday=isIntraday  # Use the parameter in retry as well
                )
                print(f" After order Time: {datetime.now().strftime('%H:%M:%S')})")
                print(f"Order id: {order_id['BrokerOrderID']} {order_id['Message']}")
                logger.info(f"Order id: {order_id['BrokerOrderID']} {order_id['Message']}")

            except Exception as e2:
                print(''.join(traceback.format_exception(type(e2), e2, e2.__traceback__)))
                print(f"Error executing place_order: {e2}")
                logging.error("Error executing place_order: %s", e2)
                return -1, None

        return order_id['BrokerOrderID'], tokenInfo['Expiry']  # Changed to return tuple

    def get_order_status(self, order_id):
        try:
            #orderbook = self.obj.orderBook()['OrderBookDetail']
            print(order_id)
            try :
                orderbook = self.obj.order_book()
            except Exception as e:
                try:
                    print("Error getting orderbook, trying again")
                    print(f"Error: {e}")
                    logger.error("Error getting orderbook, trying again %s", e)
                    time.sleep(2)
                    orderbook = self.obj.order_book()
                except Exception as e1:
                    print(f"Error: {e1}")
                    logger.error("Error getting orderbook, trying again %s", e1)
                    return -1, -1

            orderbook = pd.DataFrame(orderbook)

            # Check if order exists
            matching_orders = orderbook[orderbook.BrokerOrderId == order_id]
            if matching_orders.empty:
                print(f"Order {order_id} not found in orderbook")
                logger.warning(f"Order {order_id} not found in orderbook")
                return "NotFound", -1

            order_ret = "Rejected"
            order_status = matching_orders['OrderStatus'].values[0]
            if order_status == 'Fully Executed':
                order_ret = "Complete"
            elif order_status == 'Open':
                order_ret = "Open"
            elif order_status == 'Rejected By 5P':
                order_ret = "Rejected"
            average_price = matching_orders['AveragePrice'].values[0]

            return order_ret, average_price
        except Exception as e:
            print(''.join(traceback.format_exception(e, value=e, tb=e.__traceback__)))
            print(f"Error executing get_order_status: {e}")
            logger.error("Error executing get_order_status: %s", e)
            return -1, -1


"""
print("Starting")
angel_obj = fivepaise_api("avanthi")
print("Object created")
#orderid = angel_obj.place_order('BANKNIFTY', 15, 'SELL', 44800, 'PE')
orderid, expiry_gold = angel_obj.place_order_commodity('GOLD', 3, 'BUY', None)

print(orderid)
print(expiry_gold)

orderid, expiry = angel_obj.place_order_commodity('SILVER', 3, 'BUY', None)

print(orderid)
print(expiry)

orderid, expiry = angel_obj.place_order_commodity('ALUMINIUM', 3, 'BUY', None)

print(orderid)
print(expiry)

orderid, expiry = angel_obj.place_order_commodity('CRUDEOIL', 3, 'BUY', None)

print(orderid)
print(expiry)



orderid, expiry = angel_obj.place_order_commodity('GOLD', 3, 'SELL', expiry_gold)

print(orderid)
print(expiry)


"""


"""

print("Starting")
angel_obj = fivepaise_api("avanthi")
print("Object created")
orderid = angel_obj.place_order('BANKNIFTY', 15, 'SELL', 44800, 'PE')
print(orderid)

status, price = angel_obj.get_order_status(orderid)
print(status, price)
print("Initialized")


"""




'''







#https://github.com/pinecapital/copytrade/blob/master/main.py

cred={
    "APP_NAME":"5P58919414",
    "APP_SOURCE":"11436",
    "USER_ID":"BLe4fTGVsCt",
    "PASSWORD":"u4RzW1Pies6",
    "USER_KEY":"eJrR3CM6O0ZnArmzmLgxuToloMdLQfKa",
    "ENCRYPTION_KEY":"FLXWBhW70p0i4HhAXcH6RuB41zryq0k3"
    }


TOTP = 'GU4DSMJZGQYTIXZVKBDUWRKZ'

client = FivePaisaClient(cred=cred)

totp_pin = pyotp.TOTP(TOTP).now()

client.get_totp_session('mdeeptibhat@gmail.com',totp_pin,'246800')


#print(client.positions())

req_list_ = [{"Exch": "N", "ExchType": "C", "ScripData": "ITC_EQ"},
              {"Exch": "N", "ExchType": "C", "ScripCode": "2885"}]

#print(client.fetch_market_feed_scrip(req_list_))

#print(client.place_order(OrderType='B',Exchange='N',ExchangeType='C', ScripCode = 1660, Qty=1, Price=325, AHPlaced="Y"))

print(client.order_book())


'''
