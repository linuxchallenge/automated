import os
import time
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import pandas as pd
from tvDatafeed import Interval, TvDatafeed

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
            current = datetime.now()
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


"""
nifty_200_df = pd.read_csv('price_action/ind_nifty500list.csv')
x = DataFetcher()
# Iterate all rows using DataFrame.iterrows()
# current = datetime.now()
# ten_days_before = current - timedelta(days=20)

for index, row in nifty_200_df.iterrows():
    print(row["Symbol"])
    df = x.OHLCHistoricData(row["Symbol"])
    print(df)
"""