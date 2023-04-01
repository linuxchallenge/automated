import requests
import time
import pandas as pd
from datetime import datetime, timedelta

import logging
import os


class usd_api(object):

    def __init__(self):
        pass

    # create object of call
    def datetotimestamp(self, date):
        time_tuple = date.timetuple()
        timestamp = round(time.mktime(time_tuple))
        return timestamp

    def timestamptodate(self, timestamp):
        return datetime.fromtimestamp(timestamp)

    def OHLCHistoricData(self, time_frame):
        end = self.datetotimestamp(datetime.today())

        if time_frame == 15:
            start = self.datetotimestamp(datetime.today() - timedelta(days=50))
            start = start - (start % 3600)
            url = 'https://query1.finance.yahoo.com/v8/finance/chart/USDINR=X?symbol=USDINR%3DX&period1=' + str(
                start) + "&period2=" + str(
                end) + '&useYfid=true&interval=15m&includePrePost=true&events=div%7Csplit%7Cearn&lang=en-US&region=US&crumb=LR5Y7Gosvof&corsDomain=finance.yahoo.com'

        else:
            start = self.datetotimestamp(datetime.today() - timedelta(days=59))
            print (start)
            start = start - (start % 86400)
            start = start - 19800
            print(start)
            url = 'https://query1.finance.yahoo.com/v8/finance/chart/USDINR=X?symbol=USDINR%3DX&period1=' + str(
                start) + '&period2=' + str(
                end) + '&useYfid=true&interval=30m&includePrePost=true&events=div%7Csplit%7Cearn&lang=en-US&region=US&crumb=LR5Y7Gosvof&corsDomain=finance.yahoo.com'
            print(url)

        hdr = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(url, headers=hdr).json()
        quote = resp["chart"]["result"][0]["indicators"]["quote"][0]
        data = pd.DataFrame(quote)
        timestamp = quote = resp["chart"]["result"][0]["timestamp"]
        date = []
        for dt in timestamp:
            date.append({'Date': self.timestamptodate(dt)})
        dt = pd.DataFrame(date)

        intraday_data = pd.concat([dt, data['open'], data['high'], data['low'], data['close'], data['volume']],
                                  axis=1). \
            rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'})

        intraday_data = intraday_data.dropna()

        intraday_data = intraday_data[
            (intraday_data.Date.dt.time >= datetime(year=2023, month=1, day=1, hour=9, minute=0).time()) &
            (intraday_data.Date.dt.time <= datetime(year=2023, month=1, day=1, hour=17, minute=0).time())]

        return intraday_data


#x = usd_api()

#df = x.OHLCHistoricData(60)
#print(df)
