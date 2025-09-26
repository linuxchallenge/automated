"""Module providing a function for angel one """

# pylint: disable=W1203
# pylint: disable=W0105
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=C0209


# package import statement
import traceback
import time
import logging
from datetime import datetime, timedelta
import pandas as pd
import requests
from SmartApi import SmartConnect  # or
#from smartapi.smartConnect import SmartConnect
import pyotp
import login as l
import TelegramSend
#import credentials
import angel_one.credentials as credentials

logger = logging.getLogger(__name__)

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


    def getTokenInfo(self, exch_seg, instrumenttype, symbol, strike_price, pe_ce, expiry=None):
        df = self.l.token_map
        strike_price = strike_price * 100
        if symbol == "SENSEX":
            return df[(df['exch_seg'] == 'BFO') & (df['instrumenttype'] == instrumenttype) & (df['name'] == symbol) & (
                df['strike'] == strike_price) & (df['symbol'].str.endswith(pe_ce))].sort_values(by=['expiry'])
        if exch_seg == 'NSE':
            eq_df = df[(df['exch_seg'] == 'NSE') & (df['symbol'].str.contains('EQ'))]
            return eq_df[eq_df['name'] == symbol]
        elif exch_seg == 'NFO' and ((instrumenttype == 'FUTSTK') or (instrumenttype == 'FUTIDX')):
            # if expiry is within 10 days then return the next expiry
            today = datetime.now().date()

            if expiry is not None:
                df['expiry'] = pd.to_datetime(df['expiry']).dt.date
                date_obj = pd.to_datetime(expiry).date()
                return df[(df['exch_seg'] == 'NFO') &  (df['instrumenttype'] == instrumenttype) & \
                        (df['name'] == symbol) & (df['expiry'] == date_obj)].sort_values(by=['expiry'])
            else:
                expiry_str = df[(df['exch_seg'] == 'NFO') & (df['instrumenttype'] == instrumenttype) & \
                                (df['name'] == symbol)].sort_values(by=['expiry']).iloc[0]['expiry']
                expiry_str = expiry_str.strftime('%Y-%m-%d')
                expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
                if (expiry_date - today).days <= 10:
                    return df[(df['exch_seg'] == 'NFO') & (df['instrumenttype'] == instrumenttype) & \
                            (df['name'] == symbol)].sort_values(by=['expiry']).iloc[1:2]
                return df[(df['exch_seg'] == 'NFO') & (df['instrumenttype'] == instrumenttype) & \
                        (df['name'] == symbol)].sort_values(by=['expiry'])
        elif exch_seg == 'NFO' and (instrumenttype == 'OPTSTK' or instrumenttype == 'OPTIDX'):
            return df[(df['exch_seg'] == 'NFO') & (df['instrumenttype'] == instrumenttype) & (df['name'] == symbol) & (
                        df['strike'] == strike_price) & (df['symbol'].str.endswith(pe_ce))].sort_values(by=['expiry'])
        elif exch_seg == 'MCX' and (instrumenttype == 'FUTCOM'):

            logger.info(f"Getting token info for MCX {symbol} {expiry}")
            print(f"Getting token info for MCX {symbol} {expiry}")
            # if expiry is within 10 days then return the next expiry
            today = datetime.now().date()

            if expiry is not None:
                df['expiry'] = pd.to_datetime(df['expiry']).dt.date
                date_obj = pd.to_datetime(expiry).date()
                return df[(df['exch_seg'] == 'MCX') & (df['name'] == symbol) & (df['expiry'] == date_obj)].sort_values(by=['expiry'])
            else:
                expiry_str = df[(df['exch_seg'] == 'MCX') & (df['instrumenttype'] == instrumenttype) & (df['name'] == symbol)].sort_values(by=['expiry']).iloc[0]['expiry']
                logger.info(f"Nearest expiry for {symbol} is {expiry_str}")
                expiry_str = expiry_str.strftime('%Y-%m-%d')
                expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
                logger.info(f"Expiry date object for {symbol} is {expiry_date}")
                if (expiry_date - today).days <= 10:
                    return df[(df['exch_seg'] == 'MCX') & (df['instrumenttype'] == instrumenttype) & (df['name'] == symbol)].sort_values(by=['expiry']).iloc[1:2]
                return df[(df['exch_seg'] == 'MCX') & (df['instrumenttype'] == instrumenttype) & (df['name'] == symbol)].sort_values(by=['expiry'])

    def place_order_cash(self, symbol, qty, buy_sell):
        try:
            print("Placing order for symbol: {}, qty: {}, buy_sell: {}".format(symbol, qty, buy_sell))
            tokenInfo = self.getTokenInfo('NSE', 'EQ', symbol, 0, 'X')
            token = tokenInfo.iloc[0]['token']
            params = {
                "variety":"NORMAL",
                "tradingsymbol":"{}-EQ".format(symbol),
                "symboltoken":token,
                "transactiontype":buy_sell,
                "exchange":"NSE",
                "ordertype":"MARKET",
                "producttype":"DELIVERY",
                "duration":"DAY",
                "quantity":qty
                }
            params["price"] = 0
            response = self.obj.placeOrder(params)
            return response
        except Exception as e:
            print("Order placement failed: {}".format(str(e)))
            logger.error(f"Order placement failed: {str(e)}")
            return -1

    def place_order_commodity(self, symbol, qty, buy_sell, expiry=None, iscommodity=True):
        logger.info(f"Placing commodity order for {symbol}, qty: {qty}, \
                    type: {buy_sell}, expiry: {expiry}, iscommodity: {iscommodity}")
        try:
            if symbol == 'GOLD':
                symbol = 'GOLDM'
            elif symbol == 'SILVER':
                symbol = 'SILVERM'
            elif symbol == 'CRUDEOIL':
                symbol = 'CRUDEOILM'
            elif symbol == 'LEAD':
                symbol = 'LEADMINI'
            elif symbol == 'ZINC':
                symbol = 'ZINCMINI'
            elif symbol == 'ALUMINIUM':
                symbol = 'ALUMINI'

            if iscommodity:
                tokenInfo = self.getTokenInfo('MCX', 'FUTCOM', symbol, 0, 'X', expiry).iloc[0]
            else:
                tokenInfo = self.getTokenInfo('NFO', 'FUTIDX', symbol, 0, 'X', expiry).iloc[0]
            symbol = tokenInfo['symbol']
            token = tokenInfo['token']
            lot = int(tokenInfo['lotsize'])
            logger.info(f"Token info: {tokenInfo}")
            logger.info(f"Symbol: {symbol}, Token: {token}, Lot size: {lot}")
            logger.info(f"Quantity requested: {qty}")
            logger.info(f"Expiry date: {tokenInfo['expiry']}")

            qty = qty * lot

            if iscommodity:
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
            else:
                orderparams = {
                    "variety": "NORMAL",
                    "tradingsymbol": symbol,
                    "symboltoken": token,
                    "transactiontype": buy_sell,
                    "exchange": "NFO",
                    "ordertype": "MARKET",
                    "producttype": "CARRYFORWARD",
                    "duration": "DAY",
                    "quantity": qty
                }

            print(f" Time: {datetime.now().strftime('%H:%M:%S')} Symbol: {symbol}, Token: {token}, Lot: {lot}")
            logger.info(f" Time: {datetime.now().strftime('%H:%M:%S')} Symbol: {symbol}, Token: {token}, Lot: {lot}")
            try:
                # Add timeout to API calls
                try:
                    orderparams["price"] = 0
                    orderid = self.obj.placeOrder(orderparams)  # Add timeout
                    print(f" After order Time: {datetime.now().strftime('%H:%M:%S')})")
                except requests.exceptions.Timeout:
                    print("Order placement timed out, retrying once")
                    logger.warning("Order placement timed out, retrying once")
                    time.sleep(2)
                    try:
                        orderid = self.obj.placeOrder(orderparams)
                    except Exception as e2:
                        print(''.join(traceback.format_exception(type(e2), e2, e2.__traceback__)))
                        print(f"Error executing place_order after timeout: {e2}")
                        logger.error(f"Error executing place_order after timeout: {e2}")
                        return -1, -1
            except Exception as e:
                try:
                    print("Error placing order, trying again")
                    logger.error(f"Error executing place_order again: {e}")
                    print(f"Error: {e}")
                    x = TelegramSend.telegram_send_api()

                    # Send profit loss over telegramsend send_message
                    x.send_message("-4008545231", f"Warning angel one {symbol} order Pls check")
                    time.sleep(2)
                    orderid = self.obj.placeOrder(orderparams)
                except Exception as e1:
                    print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                    print(f"Error executing place_order: {e1}")
                    logging.error(f"Error executing place_order: {e1}")
                    return -1, -1

            return orderid, tokenInfo['expiry']
        except Exception as e:
            logging.error(f"Error executing place_order: {e}")
            print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
            print(f"Error executing place_order: {e}")
            return -1, -1


    def place_order(self, symbol, qty, buy_sell, strike_price, pe_ce, intraday=True):

        product_type = "INTRADAY" if intraday else "CARRYFORWARD"

        try:
            df = self.getTokenInfo('NFO', 'OPTIDX', symbol, strike_price, pe_ce)
            if df.empty:
                return -1

            try:
                if pd.to_datetime(df.iloc[0]['expiry']).date() < datetime.now().date():
                    if len(df) > 1:
                        tokenInfo = df.iloc[1]
                    else:
                        tokenInfo = df.iloc[0]
                else:
                    tokenInfo = df.iloc[0]
            except Exception as e:
                print(f"Error executing place_order: {e}")
                logging.error(f"Error executing place_order: {e}")
                tokenInfo = df.iloc[0]

            if symbol == "SENSEX":
                exchange = "BFO"
            else:
                exchange = "NFO"

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
                "exchange": exchange,
                "ordertype": "MARKET",
                "producttype": product_type,
                "duration": "DAY",
                "quantity": qty
            }

            print(f" Time: {datetime.now().strftime('%H:%M:%S')} Symbol: {symbol}, Token: {token}, Lot: {lot} exchange: {exchange}")
            try:
                # Add timeout to API calls
                try:
                    orderparams["price"] = 0
                    orderid = self.obj.placeOrder(orderparams)  # Add timeout
                    print(f" After order Time: {datetime.now().strftime('%H:%M:%S')})")
                except requests.exceptions.Timeout:
                    print("Order placement timed out, retrying once")
                    logger.warning("Order placement timed out, retrying once")
                    time.sleep(2)
                    try:
                        orderid = self.obj.placeOrder(orderparams)
                    except Exception as e2:
                        print(''.join(traceback.format_exception(type(e2), e2, e2.__traceback__)))
                        print(f"Error executing place_order after timeout: {e2}")
                        logger.error(f"Error executing place_order after timeout: {e2}")
                        return -1
            except Exception as e:
                try:
                    print("Error placing order, trying again")
                    print(f"Error: {e}")
                    logger.error(f"Error executing place_order again: {e}")
                    x = TelegramSend.telegram_send_api()

                    # Send profit loss over telegramsend send_message
                    x.send_message("-4008545231", f"Warning angel one {symbol} order Pls check")
                    time.sleep(2)
                    orderid = self.obj.placeOrder(orderparams)
                except Exception as e1:
                    print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                    print(f"Error executing place_order: {e1}")
                    logging.error(f"Error executing place_order: {e1}")
                    return -1

            return orderid
        except Exception as e:
            logger.error(f"Error executing place_order: {e}")
            print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
            print(f"Error executing place_order: {e}")
            return -1

    def place_order_option_buy(self, symbol, qty, buy_sell, strike_price, pe_ce):
        try:
            df_org = self.l.token_map
            strike_price = strike_price * 100
            df =  df_org[(df_org['exch_seg'] == 'NFO') & (df_org['instrumenttype'] == 'OPTIDX') & (df_org['name'] == symbol) & (
                        df_org['strike'] == strike_price) & (df_org['symbol'].str.endswith(pe_ce))].sort_values(by=['expiry'])

            if df.empty:
                print("Token info not found")
                return -1

            try:
                today = datetime.now().date()
                next_month = today.replace(day=28) + timedelta(days=4)
                last_day = next_month - timedelta(days=next_month.day)

                df['expiry'] = pd.to_datetime(df['expiry']).dt.date
                current_month_expiries = df[df['expiry'] <= last_day]

                if not current_month_expiries.empty:
                    tokenInfo = current_month_expiries.iloc[-1]
                else:
                    next_month_expiries = df[df['expiry'] > last_day]
                    if not next_month_expiries.empty:
                        tokenInfo = next_month_expiries.iloc[-1]
                    else:
                        tokenInfo = df.iloc[-1]
            except Exception as e:
                logging.error(f"Error executing place_order: {e}")
                print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                print(f"Error executing place_order: {e}")
                tokenInfo = df.iloc[-1]

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
                "producttype": "CARRYFORWARD",
                "duration": "DAY",
                "quantity": qty
            }

            print(f" Time: {datetime.now().strftime('%H:%M:%S')} Symbol: {symbol}, Token: {token}, Lot: {lot}")
            try:
                # Add timeout to API calls
                try:
                    orderparams["price"] = 0
                    orderid = self.obj.placeOrder(orderparams)  # Add timeout
                    print(f" After order Time: {datetime.now().strftime('%H:%M:%S')})")
                except requests.exceptions.Timeout:
                    print("Order placement timed out, retrying once")
                    logger.warning("Order placement timed out, retrying once")
                    time.sleep(2)
                    try:
                        orderid = self.obj.placeOrder(orderparams)
                    except Exception as e2:
                        print(''.join(traceback.format_exception(type(e2), e2, e2.__traceback__)))
                        print(f"Error executing place_order after timeout: {e2}")
                        logger.error(f"Error executing place_order after timeout: {e2}")
                        return -1
            except Exception as e:
                try:
                    print("Error placing order, trying again")
                    print(f"Error: {e}")
                    logger.error(f"Error executing place_order again: {e}")
                    x = TelegramSend.telegram_send_api()

                    # Send profit loss over telegramsend send_message
                    x.send_message("-4008545231", f"Warning angel one {symbol} option buy order Pls check")
                    time.sleep(2)
                    orderid = self.obj.placeOrder(orderparams)
                except Exception as e1:
                    logging.error(f"Error executing place_order: {e1}")
                    print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                    print(f"Error executing place_order: {e1}")
                    return -1

            return orderid
        except Exception as e:
            #print("Order placement failed: {}".format(e.message))
            print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
            print(f"Error executing place_order: {e}")
            logger.error(f"Error executing place_order: {e}")
            return -1


    def place_order_sythetic_future(self, symbol, qty, buy_sell, strike_price, pe_ce, expiry=None):
        """
        Place an order for synthetic futures.
        Returns: (order_id, expiry_date) tuple or (-1, None) on failure
        """
        try:
            # 1. GET TOKEN INFO
            df = self.getTokenInfo("NFO", "OPTIDX", symbol, strike_price, pe_ce, expiry)

            if df is None or (isinstance(df, int) and df == -1) or df.empty:
                logger.error(f"No valid contracts found for {symbol} {strike_price} {pe_ce}")
                return -1, None

            # 2. DETERMINE EXPIRY - Simplified logic
            tokenInfo = None

            # Convert expiry column to datetime
            df['expiry'] = pd.to_datetime(df['expiry']).dt.date
            today = datetime.now().date()

            if expiry is not None:
                # Use specified expiry
                try:
                    expiry_date = pd.to_datetime(expiry).date()
                    matching_expiries = df[df['expiry'] == expiry_date]

                    if matching_expiries.empty:
                        logger.error(f"No contract found for specified expiry {expiry_date}")
                        return -1, None

                    tokenInfo = matching_expiries.iloc[0]
                except Exception as e:
                    logger.error(f"Error parsing expiry date {expiry}: {e}")
                    return -1, None
            else:
                # Find appropriate expiry - get the next available one at least 8 days out
                future_expiries = df[df['expiry'] > (today + timedelta(days=8))]

                if future_expiries.empty:
                    logger.error(f"No valid future expiries found for {symbol}")
                    return -1, None

                tokenInfo = future_expiries.iloc[0]  # Get earliest valid expiry

            # 3. PREPARE ORDER PARAMETERS
            symbol = tokenInfo['symbol']
            token = tokenInfo['token']
            lot = int(tokenInfo['lotsize'])

            logger.info(f"Selected contract: {symbol}, token: {token}, expiry: {tokenInfo['expiry']}")
            print(f"Selected contract: {symbol}, token: {token}, expiry: {tokenInfo['expiry']}")

            if qty % lot != 0:
                logger.error(f"Quantity {qty} not multiple of lot size {lot}")
                print(f"Quantity {qty} not multiple of lot size {lot}")
                return -1, None

            orderparams = {
                "variety": "NORMAL",
                "tradingsymbol": symbol,
                "symboltoken": token,
                "transactiontype": buy_sell,
                "exchange": "NFO",
                "ordertype": "MARKET",
                "producttype": "CARRYFORWARD",
                "duration": "DAY",
                "quantity": qty,
                "price": 0
            }

            # 4. PLACE ORDER - With retry
            for attempt in range(2):  # Try twice
                try:
                    orderid = self.obj.placeOrder(orderparams)
                    logger.info(f"Order placed successfully: {orderid}")
                    return orderid, tokenInfo['expiry']
                except requests.exceptions.Timeout:
                    logger.warning(f"Order placement timeout (attempt {attempt+1})")
                    time.sleep(2)
                except Exception as e:
                    logger.error(f"Order placement error (attempt {attempt+1}): {e}")
                    if attempt == 0:  # Only retry once
                        time.sleep(2)
                        # Send telegram alert on first failure
                        try:
                            TelegramSend.telegram_send_api().send_message(
                                "-4008545231", 
                                f"Warning angel one {symbol} order failed: {str(e)[:100]}"
                            )
                        except Exception as telegram_error:
                            logger.debug(f"Failed to send Telegram alert: {telegram_error}")
                            # Don't let telegram errors affect main flow

            return -1, None

        except Exception as e:
            logger.error(f"Fatal error in place_order_sythetic_future: {e}")
            traceback.print_exc()
            return -1, None



    def get_order_status(self, order_id):
        try:
            # Convert orderid which is <class 'numpy.float64'> to int
            order_id = int(order_id)

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

            # Line 633 - add validation before DataFrame access
            try:
                # Check if order exists in orderbook
                matching_orders = orderbook[orderbook.orderid == order_id]

                if matching_orders.empty:
                    print(f"Order ID {order_id} not found in orderbook")
                    return "NotFound", -1

                order_status = matching_orders['orderstatus'].values[0]
                averageprice = matching_orders['averageprice'].values[0]

                # Rest of the function logic...
            except Exception as e:
                print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                print(f"Error executing get_order_status: {e}")
                logger.error(f"Error executing get_order_status: {e}")
                return -1, -1

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
            print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
            print(f"Error executing get_order_status: {e}")
            logger.error(f"Error executing get_order_status: {e}")
            return -1, -1


'''
print("Starting")
angel_obj = angelone_api()
print(angel_obj)
print("Object created")
angel_obj.intializeSymbolTokenMap()
print("Initialized")
orderid = angel_obj.place_order_sythetic_future('BANKNIFTY', 30, 'BUY', 54500, 'PE')
print(orderid)
'''



'''

print("Starting")
angel_obj = angelone_api()
print(angel_obj)
print("Object created")
angel_obj.intializeSymbolTokenMap()
print("Initialized")
orderid = angel_obj.place_order_option_buy('NIFTY', 50, 'BUY', 23100, 'PE')
#orderid = angel_obj.place_order('NIFTY', 50, 'BUY', 23100, 'PE')
print(f"Order id: {orderid}")


orderid = angel_obj.place_order_cash('SBIN', 1, 'BUY')

# Print timestamp with seconds
print("Starting")
angel_obj = angelone_api()
print(angel_obj)
print("Object created")
angel_obj.intializeSymbolTokenMap()
print("Initialized")

orderid, expiry = angel_obj.place_order_commodity('GOLD', 1, 'SELL')

print(f"Order id: {orderid}, Expiry: {expiry}")

orderid, expiry = angel_obj.place_order_commodity('GOLD', 1, 'BUY', '2024-12-05')

print(f"Order id: {orderid}, Expiry: {expiry}")
"""
orderid = angel_obj.place_order_commodity('SILVER', 1, 'SELL')

orderid = angel_obj.place_order_commodity('CRUDEOIL', 1, 'SELL')

orderid = angel_obj.place_order_commodity('LEAD', 1, 'SELL')

orderid = angel_obj.place_order_commodity('ZINC', 1, 'SELL')

orderid = angel_obj.place_order_commodity('ALUMINIUM', 1, 'SELL')

orderid = angel_obj.place_order_commodity('COPPER', 1, 'SELL')

orderid = angel_obj.place_order_commodity('NATURALGAS', 1, 'SELL')
'''


'''
# Print timestamp with seconds
print("Starting")
angel_obj = angelone_api()
print(angel_obj)
print("Object created")
angel_obj.intializeSymbolTokenMap()
print("Initialized")

#orderid = angel_obj.place_order('NIFTY', 50, 'SELL', 21300, 'PE')

# Print orderid type
#print(type(orderid))


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
