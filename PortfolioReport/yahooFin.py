import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta


class yahooFin_api(object):

    def __init__(self):
        pass
        # create object of call

    def OHLCHistoricData(self, symbol, fdate, todate):
        try:
            yestday_date = todate - timedelta(days=0)
            symbol = symbol + ".NS"
            print(symbol)
            data_fut = yf.download(tickers=symbol, start=fdate, end=yestday_date)
            data_fut = data_fut[['Open', 'High', 'Low', 'Close', 'Volume']]

            return data_fut
        except Exception as e:
            print("Historic API failed: {}".format(e))

# nifty_200_df = pd.read_csv('ind_nifty200list.csv')
# x = yahooFin_api()
# Iterate all rows using DataFrame.iterrows()
# current = datetime.now()
# ten_days_before = current - timedelta(days=20)

# for index, row in nifty_200_df.iterrows():
#    print(row["Symbol"])
#    df = x.OHLCHistoricData(row["Symbol"], ten_days_before, current)
#    print(df)
