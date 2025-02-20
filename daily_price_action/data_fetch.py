import os
import time
import yfinance as yf
import pandas as pd
from datetime import timedelta
import datetime
import pandas as pd
from tvDatafeed import Interval, TvDatafeed
import requests


headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "en-US,en;q=0.9,en-IN;q=0.8,en-GB;q=0.7",
            "cache-control": "max-age=0",
            "priority": "u=0, i",
            "sec-ch-ua": '"Microsoft Edge";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0"
        }

# define class for fetching data
class DataFetcher:
    def __init__(self):
        self.datafeed = TvDatafeed()

    def fetch_data(self, symbol, interval):
        """Fetch historical data from TradingView."""

        try:
            tv_data = self.datafeed.get_hist(symbol=symbol, exchange='NSE', interval=Interval.in_daily, n_bars=5000)

            # Drop symbol column
            tv_data = tv_data.drop(columns=['symbol'])

            # datetime column make non index
            tv_data['datetime'] = tv_data.index

            # Covert datetime from UTC to IST
            tv_data['datetime'] = tv_data['datetime'].dt.tz_localize(None)

            # Reverse the data to have the latest data at the end
            tv_data = tv_data.iloc[::-1].reset_index(drop=True)
        except Exception as e:
            print(f"Error fetching data for symbol: {symbol}")
            print(e)
            tv_data = pd.DataFrame()

        return tv_data
    

    def OHLCHistoricData(self, symbol):
        try:
            current = datetime.datetime.now()
            yestday_date = current - timedelta(days=0)
            symbol = symbol + ".NS"
            fdate = current - timedelta(days=1500)
            data_fut = yf.download(tickers=symbol, start=fdate, end=yestday_date)
            data_fut = data_fut[['Open', 'High', 'Low', 'Close']]

            # rename 'Open', 'High', 'Low', 'Close' columns to 'open', 'high', 'low', 'close'
            data_fut.columns = ['open', 'high', 'low', 'close']

            # Raname Date column to datetime
            data_fut['datetime'] = data_fut.index

            # Reverse the data to have the latest data at the end
            data_fut = data_fut.iloc[::-1].reset_index(drop=True)

            return data_fut
        except Exception as e:
            print("Historic API failed: {}".format(e))    

    def nsefetch(self, payload):
        try:
            output = requests.get(payload,headers=headers).json()
            #print(output)
        except ValueError:
            s =requests.Session()
            output = s.get("http://nseindia.com",headers=headers, timeout=10)
            output = s.get(payload,headers=headers).json()
        return output            

    def equity_history_virgin(self, symbol,series,start_date,end_date):
        #url="https://www.nseindia.com/api/historical/cm/equity?symbol="+symbol+"&series=[%22"+series+"%22]&from="+str(start_date)+"&to="+str(end_date)+""
        url = 'https://www.nseindia.com/api/historical/cm/equity?symbol=' + symbol + '&series=["' + series + '"]&from=' + start_date + '&to=' + end_date

        payload = self.nsefetch(url)
        return pd.DataFrame.from_records(payload["data"])

    def equity_history(self, symbol,series,start_date,end_date):
        #We are getting the input in text. So it is being converted to Datetime object from String.
        start_date = datetime.datetime.strptime(start_date, "%d-%m-%Y")
        end_date = datetime.datetime.strptime(end_date, "%d-%m-%Y")

        #We are calculating the difference between the days
        diff = end_date-start_date

        total=pd.DataFrame()
        for i in range (0,int(diff.days/40)):

            temp_date = (start_date+datetime.timedelta(days=(40))).strftime("%d-%m-%Y")
            start_date = datetime.datetime.strftime(start_date, "%d-%m-%Y")

            total = pd.concat([total, self.equity_history_virgin(symbol, series, start_date, temp_date)])

            #Preparation for the next loop
            start_date = datetime.datetime.strptime(temp_date, "%d-%m-%Y")


        start_date = datetime.datetime.strftime(start_date, "%d-%m-%Y")
        end_date = datetime.datetime.strftime(end_date, "%d-%m-%Y")


        #total=total.append(equity_history_virgin(symbol,series,start_date,end_date))
        #total=total.concat(equity_history_virgin(symbol,series,start_date,end_date))
        total = pd.concat([total, self.equity_history_virgin(symbol, series, start_date, end_date)])


        payload = total.iloc[::-1].reset_index(drop=True)
        return payload


    def OHLCHistricData_nseweb(self, symbol):

        end_date = datetime.datetime.now().strftime("%d-%m-%Y")
        end_date = str(end_date)

        start_date = (datetime.datetime.now()- datetime.timedelta(days=200)).strftime("%d-%m-%Y")
        start_date = str(start_date)

        series = "EQ"

        df = self.equity_history(symbol,series,start_date,end_date)

        #filter the columns 'CH_TIMESTAMP' as date, 'CH_OPENING_PRICE' as open, 'CH_TRADE_HIGH_PRICE' as high, 'CH_TRADE_LOW_PRICE' as low, 'CH_CLOSING_PRICE' as close
        df = df[['CH_TIMESTAMP','CH_OPENING_PRICE','CH_TRADE_HIGH_PRICE','CH_TRADE_LOW_PRICE','CH_CLOSING_PRICE']]
        df.columns = ['datetime','open','high','low','close']
        df['datetime'] = pd.to_datetime(df['datetime'])
        df = df.set_index('datetime')
        df = df.sort_index()
        return df




"""
nifty_200_df = pd.read_csv('price_action/ind_nifty500list.csv')
x = DataFetcher()
# Iterate all rows using DataFrame.iterrows()
# current = datetime.now()
# ten_days_before = current - timedelta(days=20)

for index, row in nifty_200_df.iterrows():
    print(row["Symbol"])
    df = x.OHLCHistricData_nseweb(row["Symbol"])
    print(df)
"""