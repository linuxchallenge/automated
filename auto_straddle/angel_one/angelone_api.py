# package import statement
import pandas as pd
import requests
#from SmartApi import SmartConnect  # or 
from smartapi.smartConnect import SmartConnect
import time
import logging
import pyotp
import login as l 
#import credentials
import angel_one.credentials as credentials



class angelone_api(object):

    def __init__(self):
        # create object of call
        self.l = l
        self.username = credentials.USER_NAME
        # self.pwd = ""
        self.pwd = credentials.PWD
        self.obj = SmartConnect(api_key=credentials.API_KEY)

        # login api call
        totp = pyotp.TOTP(credentials.TOTP)
        totp = totp.now()
        attempts = 5
        while attempts > 0:
            attempts = attempts - 1
            data = self.obj.generateSession(self.username, self.pwd, totp)
            if data['status']:
                break
            time.sleep(2)

            refreshToken = data['data']['refreshToken']

            # fetch the feedtoken
            credentials.FEED_TOKEN = self.obj.getfeedToken()

            # fetch User Profile
            credentials.TOKEN_MAP = self.obj.getProfile(refreshToken)

        self.intializeSymbolTokenMap()

    def teardown_connection(self):
        self.obj.terminateSession(self.username)

    def intializeSymbolTokenMap(self):
        url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        d = requests.get(url).json()
        self.token_df = pd.DataFrame.from_dict(d)
        self.token_df['expiry'] = pd.to_datetime(self.token_df['expiry'])
        self.token_df = self.token_df.astype({'strike': float})
        self.l.token_map = self.token_df

    def getTokenInfo(self, exch_seg, instrumenttype, symbol, strike_price, pe_ce):
        df = self.l.token_map
        strike_price = strike_price * 100
        if exch_seg == 'NSE':
            eq_df = df[(df['exch_seg'] == 'NSE') & (df['symbol'].str.contains('EQ'))]
            return eq_df[eq_df['name'] == symbol]
        elif exch_seg == 'NFO' and ((instrumenttype == 'FUTSTK') or (instrumenttype == 'FUTIDX')):
            return df[(df['exch_seg'] == 'NFO') & (df['instrumenttype'] == instrumenttype) & (
                        df['name'] == symbol)].sort_values(by=['expiry'])
        elif exch_seg == 'NFO' and (instrumenttype == 'OPTSTK' or instrumenttype == 'OPTIDX'):
            return df[(df['exch_seg'] == 'NFO') & (df['instrumenttype'] == instrumenttype) & (df['name'] == symbol) & (
                        df['strike'] == strike_price) & (df['symbol'].str.endswith(pe_ce))].sort_values(by=['expiry'])


    def place_order(self, symbol, qty, buy_sell, strike_price, pe_ce):
        try:
            tokenInfo = self.getTokenInfo('NFO', 'OPTIDX', symbol, strike_price, pe_ce).iloc[0]
            symbol = tokenInfo['symbol']
            token = tokenInfo['token']
            lot = int(tokenInfo['lotsize'])

            if qty % lot != 0:
                return -1

            orderparams = {
                "variety": "NORMAL",
                "tradingsymbol": symbol,
                "symboltoken": token,
                "transactiontype": buy_sell,
                "exchange": "NFO",
                "ordertype": "MARKET",
                "producttype": "INTRADAY",
                "duration": "DAY",
                "quantity": qty
            }

            orderid = self.obj.placeOrder(orderparams)

            return orderid
        except Exception as e:
            #print("Order placement failed: {}".format(e.message))
            print(''.join(traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)))
            print(f"Error executing place_order: {e}")
            print("Failed: {}".format(e))
            return -1

    def get_order_status(self, order_id):
        try:
            orderbook = self.obj.orderBook()['data']

            # get orderbook for the order id
            orderbook = pd.DataFrame(orderbook)
            order_status = orderbook.loc[orderbook.orderid == order_id, 'orderstatus'].values[0]
            averageprice = orderbook.loc[orderbook.orderid == order_id, 'averageprice'].values[0]
            print("Order Status", order_status)
            return order_status, averageprice
        except Exception as e:
            print(''.join(traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)))
            print(f"Error executing get_order_status: {e}")
            return -1, -1

'''
# Print timestamp with seconds
print("Starting")
angel_obj = angelone_api()
print(angel_obj)
print("Object created")
angel_obj.intializeSymbolTokenMap()
print("Initialized")

orderid = angel_obj.place_order('NIFTY', 50, 'SELL', 21300, 'PE')

print("Nifty order placed with order id: {}".format(orderid))

angel_obj.get_order_status(orderid)

#orderid = angel_obj.place_order('FINNIFTY', 40, 'SELL', 20100, 'PE')

#angel_obj.get_order_status(orderid)

#print("FinNifty order placed with order id: {}".format(orderid))

#orderid = angel_obj.place_order('BANKNIFTY', 15, 'SELL', 44800, 'PE')

#angel_obj.get_order_status(orderid)

#print("FinNifty order placed with order id: {}".format(orderid))


print("Test API")
angel_obj.teardown_connection()
print("Connection closed")
'''