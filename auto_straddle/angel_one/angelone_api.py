"""Module providing a function for angel one """

# pylint: disable=W1203
# pylint: disable=W0105
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103


# package import statement
import traceback
import time
import logging
from datetime import datetime
import pandas as pd
import requests
from SmartApi import SmartConnect  # or
#from smartapi.smartConnect import SmartConnect
import pyotp
import login as l
#import TelegramSend
#import credentials
import credentials as credentials



class angelone_api(object):

    def __init__(self):
        # create object of call
        self.l = l
        self.username = credentials.USER_NAME
        # self.pwd = ""
        self.pwd = credentials.PWD
        self.obj = SmartConnect(api_key=credentials.API_KEY)

        # login api call
        attempts = 5
        while attempts > 0:
            attempts = attempts - 1

            totp = pyotp.TOTP(credentials.TOTP)
            totp = totp.now()

            data = self.obj.generateSession(self.username, self.pwd, totp)
            if data['status']:
                break
            time.sleep(30)

            refreshToken = data['data']['refreshToken']

            # fetch the feedtoken
            credentials.FEED_TOKEN = self.obj.getfeedToken()

            # fetch User Profile
            credentials.TOKEN_MAP = self.obj.getProfile(refreshToken)

        self.intializeSymbolTokenMap()

    def teardown_connection(self):
        self.obj.terminateSession(self.username)

    def intializeSymbolTokenMap(self):
        try:
            url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
            d = requests.get(url, timeout=50).json()
            self.token_df = pd.DataFrame.from_dict(d)
            self.token_df['expiry'] = pd.to_datetime(self.token_df['expiry'])
            self.token_df = self.token_df.astype({'strike': float})
            self.l.token_map = self.token_df
            self.token_df.to_csv('token_map_angelone.csv')
        except Exception as e:
            print(f"Error executing intializeSymbolTokenMap: {e}")
            logging.error(f"Error executing intializeSymbolTokenMap: {e}")
            try:
                self.token_df = pd.read_csv('token_map_angelone.csv')
                self.l.token_map = self.token_df
            except Exception as e1:
                print(f"Error executing intializeSymbolTokenMap: {e1}")
                logging.error(f"Error executing intializeSymbolTokenMap: {e1}")
                raise e1

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
        elif exch_seg == 'MCX' and (instrumenttype == 'FUTCOM'):
            return df[(df['exch_seg'] == 'MCX') & (df['instrumenttype'] == instrumenttype) & (df['name'] == symbol)].sort_values(by=['expiry'])

    def place_order_commodity(self, symbol, qty, buy_sell):
        try:
            tokenInfo = self.getTokenInfo('MCX', 'FUTCOM', symbol, 0, 'X').iloc[0]
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
                "exchange": "MCX",
                "ordertype": "MARKET",
                "producttype": "CARRYFORWARD",
                "duration": "DAY",
                "quantity": qty
            }

            print(f" Time: {datetime.now().strftime('%H:%M:%S')} Symbol: {symbol}, Token: {token}, Lot: {lot}")
            try :
                orderparams["price"] = 0
                orderid = self.obj.placeOrder(orderparams)
                print(f" After order Time: {datetime.now().strftime('%H:%M:%S')})")
            except Exception as e:
                try:
                    print("Error placing order, trying again")
                    print(f"Error: {e}")
                    #x = TelegramSend.telegram_send_api()

                    # Send profit loss over telegramsend send_message
                    #x.send_message("-4008545231", f"Warning angel one {symbol} order Pls check")
                    time.sleep(2)
                    orderid = self.obj.placeOrder(orderparams)
                except Exception as e1:
                    print(''.join(traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)))
                    print(f"Error executing place_order: {e1}")
                    logging.error(f"Error executing place_order: {e1}")
                    return -1

            return orderid
        except Exception as e:
            #print("Order placement failed: {}".format(e.message))
            print(''.join(traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)))
            print(f"Error executing place_order: {e}")
            return -1


    def place_order(self, symbol, qty, buy_sell, strike_price, pe_ce):
        try:
            df = self.getTokenInfo('NFO', 'OPTIDX', symbol, strike_price, pe_ce)
            if df.empty:
                return -1

            if df.iloc[0]['expiry'] < datetime.strptime(datetime.now().strftime('%Y-%m-%d'), '%Y-%m-%d'):
                tokenInfo = df.iloc[1]
            else:
                tokenInfo = df.iloc[0]

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

            print(f" Time: {datetime.now().strftime('%H:%M:%S')} Symbol: {symbol}, Token: {token}, Lot: {lot}")
            try :
                orderparams["price"] = 0
                orderid = self.obj.placeOrder(orderparams)
                print(f" After order Time: {datetime.now().strftime('%H:%M:%S')})")
            except Exception as e:
                try:
                    print("Error placing order, trying again")
                    print(f"Error: {e}")
                    x = TelegramSend.telegram_send_api()

                    # Send profit loss over telegramsend send_message
                    x.send_message("-4008545231", f"Warning angel one {symbol} order Pls check")
                    time.sleep(2)
                    orderid = self.obj.placeOrder(orderparams)
                except Exception as e1:
                    print(''.join(traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)))
                    print(f"Error executing place_order: {e1}")
                    logging.error(f"Error executing place_order: {e1}")
                    return -1

            return orderid
        except Exception as e:
            #print("Order placement failed: {}".format(e.message))
            print(''.join(traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)))
            print(f"Error executing place_order: {e}")
            return -1

    def get_order_status(self, order_id):
        try:
            order_id = str(order_id)
            try:
                orderbook = self.obj.orderBook()['data']
            except Exception as e:
                try:
                    print("Error getting orderbook, trying again")
                    print(f"Error: {e}")
                    time.sleep(2)
                    orderbook = self.obj.orderBook()['data']
                except Exception as e1:
                    print(f"Error: {e1}")
                    return -1, -1

            order_ret = "Rejected"
            # get orderbook for the order id
            orderbook = pd.DataFrame(orderbook)

            order_status = orderbook.loc[orderbook.orderid == order_id, 'orderstatus'].values[0]
            if order_status == "complete":
                order_ret = "Complete"
            elif order_status == "Open":
                order_ret = "Open"
            elif order_status == "rejected":
                order_ret = "Rejected"
                #order_ret = "Complete"

            averageprice = orderbook.loc[orderbook.orderid == order_id, 'averageprice'].values[0]
            print("Order Status", order_ret)
            return order_ret, averageprice
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

orderid = angel_obj.place_order_commodity('GOLD', 1, 'SELL')

'''

'''
# Print timestamp with seconds
print("Starting")
angel_obj = angelone_api()
print(angel_obj)
print("Object created")
angel_obj.intializeSymbolTokenMap()
print("Initialized")

orderid = angel_obj.place_order('NIFTY', 50, 'SELL', 21300, 'PE')

# Print orderid type
print(type(orderid))


#print("Nifty order placed with order id: {}".format(orderid))

orderid = 240224000000150

# Convert orderid to string
orderid = str(orderid)

print(angel_obj.get_order_status(orderid))

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
