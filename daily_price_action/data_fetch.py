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
        fileUrl ='https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz'
        self.symboldf = pd.read_csv(fileUrl)
        self.symboldf = self.symboldf[self.symboldf.instrument_type == 'EQUITY']
        self.symboldf = self.symboldf[self.symboldf.exchange == 'NSE_EQ']

    def fetch_data(self, symbol, interval):
        """Fetch historical data from TradingView."""

        if not hasattr(self, 'datafeed'):
            self.datafeed = TvDatafeed()

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
        print(url)

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

        start_date = (datetime.datetime.now()- datetime.timedelta(days=1500)).strftime("%d-%m-%Y")
        start_date = str(start_date)

        series = "EQ"

        tries = 0
        max_tries = 2
        while tries < max_tries:
            try:
                df = self.equity_history(symbol, series, start_date, end_date)
                break
            except Exception as e:
                tries += 1
                if tries == max_tries:
                    print(f"Failed to fetch data for {symbol} after {max_tries} attempts: {e}")
                    return pd.DataFrame()  # Return empty DataFrame on failure
                print(f"Attempt {tries} failed, retrying in 10 seconds...")
                time.sleep(10)

        #print(df)
        if not df.empty:
            #filter the columns 'CH_TIMESTAMP' as date, 'CH_OPENING_PRICE' as open, 'CH_TRADE_HIGH_PRICE' as high, 'CH_TRADE_LOW_PRICE' as low, 'CH_CLOSING_PRICE' as close
            df = df[['CH_TIMESTAMP','CH_OPENING_PRICE','CH_TRADE_HIGH_PRICE','CH_TRADE_LOW_PRICE','CH_CLOSING_PRICE']]
            df.columns = ['datetime','open','high','low','close']
            df['datetime'] = pd.to_datetime(df['datetime'])
            #df = df.set_index('datetime')
            #df = df.sort_index()
        return df
    
    def OHLCHistricData_upstox(self, symbol):
        token = self.symboldf[self.symboldf.tradingsymbol == symbol]

        if not token.empty:
            instrument_key = token['instrument_key'].values[0]
            all_data = []
            
            # Get data for last 4 years
            today = datetime.datetime.now()
            
            for year in range(8):
                end_date = today - datetime.timedelta(days=365*year)
                start_date = end_date - datetime.timedelta(days=365)
                
                # Format dates as YYYY-MM-DD
                to_date = end_date.strftime('%Y-%m-%d')
                from_date = start_date.strftime('%Y-%m-%d')
                
                url = f'https://api.upstox.com/v2/historical-candle/{instrument_key}/day/{to_date}/{from_date}'
                
                headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'X-Requested-With': 'XMLHttpRequest',
                'Referer': 'https://api.upstox.com',
                'Connection': 'keep-alive'
                }

                try:
                    res = requests.get(url, headers=headers, timeout=5.0)
                    candleRes = res.json()

                    if 'data' in candleRes and 'candles' in candleRes['data'] and candleRes['data']['candles']:
                        candleData = pd.DataFrame(candleRes['data']['candles'])
                        candleData.columns = ['date','open','high','low', 'close','vol','oi']
                        candleData['date'] = pd.to_datetime(candleData['date']).dt.tz_convert('Asia/Kolkata')
                        candleData = candleData.drop(['vol','oi'], axis=1)
                        candleData = candleData.assign(date=candleData['date'].dt.tz_localize(None))
                        all_data.append(candleData)
                    
                except Exception as e:
                    print(f"Error fetching data for period {from_date} to {to_date}: {e}")
                    continue

            if all_data:
                final_df = pd.concat(all_data, ignore_index=True)
                final_df = final_df.drop_duplicates(subset=['date'])
                final_df = final_df.sort_values(by='date', ascending=True)
                # Reverse the data to have the latest data at the end
                final_df = final_df.iloc[::-1].reset_index(drop=True)

                final_df.rename(columns={'date': 'datetime'}, inplace=True)
                return final_df
            
            return None
        else:
            print("Token not found")
            return None





"""
nifty_200_df = pd.read_csv('price_action/ind_nifty500list.csv')
x = DataFetcher()
# Iterate all rows using DataFrame.iterrows()
# current = datetime.now()
# ten_days_before = current - timedelta(days=20)

for index, row in nifty_200_df.iterrows():
    print(row["Symbol"])
    df = x.OHLCHistricData_upstox(row["Symbol"])
    print(df)
"""


