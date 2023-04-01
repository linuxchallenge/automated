import requests, time
import pandas as pd
from datetime import datetime, timedelta

import logging
import os
class intraday_api(object):

    def __init__(self):
        pass
        # create object of call

    def datetotimestamp(self, date):
        time_tuple = date.timetuple()
        timestamp = round(time.mktime(time_tuple))
        return timestamp

    def timestamptodate(self, timestamp):
        return datetime.fromtimestamp(timestamp)

    def OHLCHistoricData(self, symbol, fdate, todate):
        end = self.datetotimestamp(todate)
        start = self.datetotimestamp(fdate)
        if symbol == "Nifty":
            url = 'https://priceapi.moneycontrol.com/techCharts/history?symbol=9&resolution=15&from=' + str(
                start) + '&to=' + str(end) + ''
        if symbol == "Bnf":
            url = 'https://priceapi.moneycontrol.com/techCharts/history?symbol=23&resolution=15&from=' + str(
                start) + '&to=' + str(end) + ''
        if symbol == "Finnifty":
            url = 'https://priceapi.moneycontrol.com/techCharts/history?symbol=47&resolution=15&from=' + str(
                start) + '&to=' + str(end) + ''

        hdr = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(url, headers=hdr).json()
        data = pd.DataFrame(resp)

        date = []
        for dt in data['t']:
            date.append({'Date': self.timestamptodate(dt)})

        dt = pd.DataFrame(date)
        intraday_data = pd.concat([dt, data['o'], data['h'], data['l'], data['c'], data['v']], axis=1). \
            rename(columns={'o': 'Open', 'h': 'High', 'l': 'Low', 'c': 'Close', 'v': 'Volume'})

        return intraday_data

    def convert15m_to_75m(self, data):
        data = data.set_index('Date')
        data = data.groupby(data.index.date) \
            .apply(lambda d: d.resample(rule='75T', closed='left', label='left', origin=d.index.min())
                   .agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna())
        data.reset_index(inplace=True)
        data = data.drop(['level_0'], axis=1)
        return data


#x = intraday_api()
#current = datetime.now()
#ten_days_before = current - timedelta(days=70)

#df = x.OHLCHistoricData("Bnf", ten_days_before, current)
#print(df)

#df = x.convert15m_to_75m(df)
#print(df)






