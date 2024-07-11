"""Module providing a function for main function """

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=W0105
# pylint: disable=C0200
# pylint: disable=C0413
# pylint: disable=W0718

import time
from datetime import datetime, timedelta
import pandas as pd
import requests

class commodity_data:

    # define different comodity data
    symbol = ['CRUDEOIL', 'NATURALGAS', 'COPPER', 'GOLD', 'LEAD', 'ZINC', 'ALUMINIUM', 'SILVER']

    def __init__(self):
        self.symbolTokenMap = {}  # Add this line

    def intializeSymbolAndGetExpiryData(self):
        try:
            url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
            d = requests.get(url, timeout=50).json()
            token_df = pd.DataFrame.from_dict(d)
            token_df['expiry'] = pd.to_datetime(token_df['expiry'])
            token_df = token_df.astype({'strike': float})
            token_df.to_csv('token_map_angelone.csv')

            # Get only exch_seg is mcx
            token_df = token_df[token_df['exch_seg'] == 'MCX']

            # Get instrumenttype is FUTCOM
            token_df = token_df[token_df['instrumenttype'] == 'FUTCOM']

            # Loop fpr all symbol
            for s in self.symbol:
                df = token_df[token_df['name'] == s]
                df = df.sort_values(by='expiry')

                # Save to dictionary
                self.symbolTokenMap[s] = df.iloc[0]['expiry'].strftime("%Y-%m-%d")  # Modified format to "yyyy-mm-dd"

        except Exception as e:
            print(f"Error executing intializeSymbolTokenMap: {e}")


    def datetotimestamp(self, date):
        time_tuple = date.timetuple()
        timestamp = round(time.mktime(time_tuple))
        return timestamp

    def timestamptodate(self, timestamp):
        return datetime.fromtimestamp(timestamp)

    def historic_data(self, symbol, daily = False):
        todate = datetime.now()
        fdate = todate - timedelta(days=60)

        end = self.datetotimestamp(todate)
        start = self.datetotimestamp(fdate)

        expiry_date = self.symbolTokenMap[symbol]

        if not daily:
            url = 'https://priceapi.moneycontrol.com/techCharts/commodity/history?symbol=' \
                + symbol + '_' + expiry_date + '_mcx&resolution=60&from=' + str(start) + \
                    '&to=' + str(end) + '&currencyCode=INR'
        else:
            url = 'https://priceapi.moneycontrol.com/techCharts/commodity/history?symbol=' \
                + symbol + '_' + expiry_date + '_mcx&resolution=1D&from=' + str(start) + \
                    '&to=' + str(end) + '&currencyCode=INR'

        hdr = {'User-Agent': 'Mozilla/5.0'}

        try:
            resp = requests.get(url, headers=hdr, timeout=50).json()
        except Exception as e:
            print(f"Error executing historic_data: {e}")
            time.sleep(3)

            # retry
            try:
                resp = requests.get(url, headers=hdr, timeout=50).json()
            except Exception as e1:
                print(f"Error executing historic_data: {e1}")
                return None

        data = pd.DataFrame(resp)
        date = []
        for dt in data['t']:
            date.append({'Date': self.timestamptodate(dt)})

        dt = pd.DataFrame(date)
        intraday_data = pd.concat([dt, data['o'], data['h'], data['l'], data['c']], axis=1). \
            rename(columns={'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close'})

        intraday_data.dropna(inplace=True)

        return intraday_data

"""
# test code
cd = commodity_data()
#expiry_date = cd.get_expiry_date('CRUDEOIL')
cd.intializeSymbolAndGetExpiryData()

print("=====  GOLD ======")
data = cd.historic_data('GOLD', daily=True)
print(data)
print("=====  GOLD ======")

print("=====  CRUDEOIL ======")
data = cd.historic_data('CRUDEOIL', daily=True)
print(data)
print("=====  CRUDEOIL ======")


print("=====  NATURAL GAS ======")
data = cd.historic_data('NATURALGAS', daily=True)
print(data)
print("=====  NATURAL GAS ======")

print("=====  COPPER ======")
data = cd.historic_data('COPPER', daily=True)
print(data)
print("=====  COPPER ======")

print("=====  LEAD ======")
data = cd.historic_data('LEAD', daily=True)
print(data)
print("=====  LEAD ======")


print("=====  ZINC ======")
data = cd.historic_data('ZINC', daily=True)
print(data)
print("=====  ZINC ======")


print("=====  ALUMINIUM ======")
data = cd.historic_data('ALUMINIUM', daily=True)
print(data)
print("=====  ALUMINIUM ======")


print("=====  SILVER ======")
data = cd.historic_data('SILVER')
print(data)
print("=====  SILVER ======")

#print(expiry_date)
"""
