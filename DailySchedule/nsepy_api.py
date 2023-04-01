from nsepy import get_history
from datetime import datetime, timedelta
import pandas as pd
from jugaad_data.nse import NSELive

class nse_pi_api(object):

    def __init__(self):
        pass
        # create object of call

    def OHLCHistoricData(self, symbol, fdate, todate):
        try:
            yestday_date = todate - timedelta(days=1)
            data_fut = get_history(symbol=symbol, start=fdate, end=yestday_date)
            data_fut = data_fut[['Open', 'High', 'Low', 'Close', 'Volume']]
            n = NSELive()
            q = n.stock_quote(symbol)
            new_row = pd.Series(data={'Open': q['priceInfo']['open'], 'High': q['priceInfo']['intraDayHighLow']['max'],
                                      'Low': q['priceInfo']['intraDayHighLow']['min'],
                                      'Close': q['priceInfo']['intraDayHighLow']['value'], 'Volume': 0},
                                name=todate.date())

            data_fut = data_fut.append(new_row, ignore_index=False)
            data_fut.drop_duplicates()

            return data_fut
        except Exception as e:
            print("Historic API failed: {}".format(e))

#nifty_200_df = pd.read_csv('ind_nifty200list.csv')
#x = nse_pi_api()
# Iterate all rows using DataFrame.iterrows()
#current = datetime.now()
#ten_days_before = current - timedelta(days=2)

#for index, row in nifty_200_df.iterrows():
#    print(row["Symbol"])
#    df = x.OHLCHistoricData(row["Symbol"], ten_days_before, current)
#    print(df)

