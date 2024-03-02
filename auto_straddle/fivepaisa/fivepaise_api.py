from io import StringIO
import time
import traceback
import logging
import pandas as pd
from py5paisa import FivePaisaClient
#import py5paisa
import pyotp
import requests
import fivepaisa.credentials_2 as credentials_leelu


class fivepaise_api(object):

    def __init__(self):
        # create object of call
        cred={
            "APP_NAME":credentials_leelu.APP_NAME,
            "APP_SOURCE":credentials_leelu.APP_SOURCE,
            "USER_ID":credentials_leelu.USER_ID,
            "PASSWORD":credentials_leelu.PASSWORD,
            "USER_KEY":credentials_leelu.USER_KEY,
            "ENCRYPTION_KEY":credentials_leelu.ENCRYPTION_KEY
        }

        self.obj = FivePaisaClient(cred=cred)

        attempts = 5
        while attempts > 0:
            totp_pin = pyotp.TOTP(credentials_leelu.TOTP).now()

            self.session = self.obj.get_totp_session(credentials_leelu.EMAIL,totp_pin,credentials_leelu.PIN)
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
        url = "https://openapi.5paisa.com/VendorsAPI/Service1.svc/ScripMaster/segment/All"
        self.csv_data = self.download_csv(url)
        self.scrip_master_df = pd.read_csv(StringIO(self.csv_data))

    def getTokenInfo(self, symbol, strike_price, pe_ce):
        # script_master_df has Name column like FINNIFTY 13 Feb 2024 CE 23750.00
        # Match the symbol and strike price and pe_ce and get ScriptCode collumn value
        df = self.scrip_master_df
        
        df = df[(df['SymbolRoot'] == symbol) & (df['StrikeRate'] == strike_price) & (df['ScripType'] == pe_ce)]
        # Sort the df by expiry date and get the first row
        df = df.sort_values(by='Expiry')
        return df.iloc[0]
    
    def place_order(self, symbol, qty, buy_sell, strike_price, pe_ce):
        tokenInfo = self.getTokenInfo(symbol, strike_price, pe_ce)

        print("five paise place order")

        symbol = tokenInfo['SymbolRoot']
        token = tokenInfo['ScripCode']
        lot = int(tokenInfo['LotSize'])

        print(f"Symbol: {symbol}, Token: {token}, Lot: {lot}")

        if qty % lot != 0:
                return -1
        if buy_sell == 'BUY':
            buy_sell = 'B'
        else:
            buy_sell = 'S'
        try:
            order_id = self.obj.place_order(OrderType=buy_sell, Exchange='N', ExchangeType='D', ScripCode=int(token), Qty=int(qty), Price=0)
        except Exception as e1:
            try:
                print("Error placing order, trying again")
                time.sleep(2)
                order_id = self.obj.place_order(OrderType=buy_sell, Exchange='N', ExchangeType='D', ScripCode=int(token), Qty=int(qty), Price=0)
            except Exception as e2:
                print(''.join(traceback.format_exception(etype=type(e1), value=e1, tb=e2.__traceback__)))
                print(f"Error executing place_order: {e2}")
                logging.error("Error executing place_order: %s", e2)
                return -1
        print(f"Order id: {order_id['BrokerOrderID']}")
        return order_id['BrokerOrderID']
    
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
                    time.sleep(2)
                    orderbook = self.obj.order_book()
                except Exception as e1:
                    print(f"Error: {e1}")
                    return -1, -1

            orderbook = pd.DataFrame(orderbook)

            order_ret = "Rejected"
            order_status = orderbook.loc[orderbook.BrokerOrderId == order_id, 'OrderStatus'].values[0]
            if order_status == 'Fully Executed':
                order_ret = "Complete"
            elif order_status == 'Open':
                order_ret = "Open"
            elif order_status == 'Rejected By 5P':
                order_ret = "Rejected"
            average_price = orderbook.loc[orderbook.BrokerOrderId == order_id, 'AveragePrice'].values[0]

            return order_ret, average_price
        except Exception as e:
            print(''.join(traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)))
            print(f"Error executing get_order_status: {e}")
            return -1, -1

        
'''
print("Starting")
angel_obj = fivepaise_api()
print("Object created")
#orderid = angel_obj.place_order('BANKNIFTY', 15, 'SELL', 44800, 'PE')
#print(orderid)
status, price = angel_obj.get_order_status(196169270)
print(status, price)

'''





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