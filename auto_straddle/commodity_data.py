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

import random
import time
from datetime import datetime, timedelta
import pandas as pd
import requests
from tvDatafeed import Interval, TvDatafeed

class commodity_data:

    # define different comodity data
    symbol = ['CRUDEOIL', 'NATURALGAS', 'COPPER', 'GOLD', 'LEAD', 'ZINC', 'ALUMINIUM', 'SILVER']

    use_source = "tv"  # tv or mc

    def __init__(self):
        self.symbolTokenMap = {}  # Add this line

        self.tv_obj = None

        #username = 'cool_adi52002@rediffmail.com'
        #password = 'CrazyTrading12@'
        #username = 'demand_adi3890@rediffmail.com'
        #password = 'CrazyTradingToday12@'

        # Have multiple set of credentials stored in a list
        credentials = [
            {'username': 'bocaki6537@tiervio.com', 'password': 'TradingIsAmazing1@'},
            {'username': 'muhume@citmo.net', 'password': 'WhatAWorldThisIs1@'},
            {'username': 'mifxda4u6w@hellomailo.net', 'password': 'TheCruelTradingWord3$'},
            {'username': '3mvzbkoy61@gonetor.com', 'password': 'TodayIsAmazingDay1@'},
            {'username': 'hgggg', 'password': 'TodayWasAmazingDay7@'},
            {'username': 'cool_adi52002@rediffmail.com', 'password':'CrazyTrading12@'},
            {'username': 'demand_adi3890@rediffmail.com', 'password':'CrazyTradingToday12@'}
        ]
        
        # Initialize the tv datafeed
        # Randomly choose a set of credentials
        for _ in range(5):
            credentials12 = random.choice(credentials)
            username = credentials12['username']
            password = credentials12['password']
            print(f"Using credentials: {username}")
            print(f"Using credentials: {password}")

            self.tv_obj = TvDatafeed(username, password)

            if self.tv_obj.token != 'unauthorized_user_token':
                break

            # sleep for 3 seconds before retrying
            time.sleep(5)

            # If it fails 5 times, then switch self.use_source to mc
            if _ == 4:
                self.use_source = "mc"
                print("Switching to MC as TV Datafeed failed")
                break

        print("TV Datafeed initialized " + self.tv_obj.token)

    def change_source(self, source):
        self.use_source = source

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
        if self.use_source == "tv":
            return self.historic_data_tv(symbol, daily)
        return self.historic_data_investing(symbol, daily)

    def historic_data_tv(self, symbol, daily = False):
        if not daily:
            tv_data = self.tv_obj.get_hist(symbol=symbol, exchange='MCX', interval=Interval.in_1_hour, n_bars=500, fut_contract=1)
        else:
            tv_data = self.tv_obj.get_hist(symbol=symbol, exchange='MCX', interval=Interval.in_daily, n_bars=500, fut_contract=1)

        # Drop symbol column
        tv_data = tv_data.drop(columns=['symbol'])

        # datetime column make non index
        tv_data['datetime'] = tv_data.index

        # Rename datetime column to Date
        tv_data = tv_data.rename(columns={'datetime': 'Date'})

        # Covert datetime from UTC to IST
        tv_data['Date'] = tv_data['Date'].dt.tz_localize(None)

        tv_data = tv_data.drop(columns='volume')

        # Drop datetime as index
        tv_data = tv_data.reset_index(drop=True)

        # Put Date at first column
        tv_data = tv_data[['Date', 'open', 'high', 'low', 'close']]

        return tv_data


    def historic_data_investing(self, symbol, daily = False):
        # Map symbol to ID
        symbol_id_map = {
            'CRUDEOIL': '49774',
            'NATURALGAS': '49787',
            'COPPER': '40015',
            'GOLD': '49778',
            'LEAD': '49784',
            'ZINC': '49794',
            'ALUMINIUM': '40015',
            'SILVER': '49791'
        }

        # Define headers
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': 'https://www.investing.com',
            'Connection': 'keep-alive',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'en-US,en;q=0.9',
            'Origin': 'https://www.investing.com',
            'Host': 'api.investing.com'
        }

        # Define URL
        if not daily:
            url = f'https://api.investing.com/api/financialdata/{symbol_id_map[symbol]}/historical/chart/?interval=PT30M&pointscount=160'
        else:
            url = f'https://api.investing.com/api/financialdata/{symbol_id_map[symbol]}/historical/chart/?interval=PT1D&pointscount=160'

        print(f"Fetching data from URL: {url}")

        # Send a GET request to the URL with headers
        response = requests.get(url, headers=headers, timeout=50)

        # Check if the request was successful
        if response.status_code == 200:
            # Parse the response JSON
            data = response.json()

            # Extract relevant data for the DataFrame
            if 'data' in data:

                # Convert JSON data to DataFrame
                df = pd.DataFrame(data['data'])

                # Print the columns to inspect
                print("Original columns:", df.columns)

                # Drop the last column
                df = df.iloc[:, :-1]

                # Ensure the DataFrame has the expected number of columns
                if len(df.columns) == 6:
                    # Rename columns
                    df.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']

                    # Convert timestamp from epoch to datetime
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

                    # Convert timestamp from UTC to IST
                    df['timestamp'] = df['timestamp'] + timedelta(hours=5, minutes=30)

                    # Rename timestamp to Date
                    df = df.rename(columns={'timestamp': 'Date'})

                    # Drop volume column
                    df = df.drop(columns='volume')

                    df.dropna(inplace=True)

                    if not daily:
                        ohlc = {
                            'open': 'first',
                            'high': 'max',
                            'low': 'min',
                            'close': 'last',
                        }

                        df.set_index('Date', inplace=True)
                        df = df.resample('60min', origin=00).apply(ohlc)
                        df.dropna(inplace=True)
                        df.reset_index(inplace=True)
                else:
                    print(f"Unexpected number of columns: {len(df.columns)}")
            else:
                print("No 'data' field in the JSON response.")
                return self.historic_data_mc(symbol, daily)
        else:
            print(f"Failed to fetch data. Status code: {response.status_code}")
            return self.historic_data_mc(symbol, daily)

        return df


    def historic_data_mc(self, symbol, daily = False):
        todate = datetime.now()
        fdate = todate - timedelta(days=60)

        end = self.datetotimestamp(todate)
        start = self.datetotimestamp(fdate)

        expiry_date = self.symbolTokenMap[symbol]

        if not daily:
            url = 'https://priceapi.moneycontrol.com/techCharts/commodity/history?symbol=' \
                + symbol + '_' + expiry_date + '_mcx&resolution=30&from=' + str(start) + \
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

        if not daily:
            ohlc = {
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
            }

            intraday_data.set_index('Date', inplace=True)
            intraday_data = intraday_data.resample('60min', origin=00).apply(ohlc)
            intraday_data.dropna(inplace=True)          
            intraday_data.reset_index(inplace=True)  


        return intraday_data

"""
# test code
cd = commodity_data()
#expiry_date = cd.get_expiry_date('CRUDEOIL')
cd.intializeSymbolAndGetExpiryData()

print("=====  GOLD ======")
data = cd.historic_data('GOLD')
print(data)
print("=====  GOLD ======")

print("=====  CRUDEOIL ======")
data = cd.historic_data('CRUDEOIL')
print(data)
print("=====  CRUDEOIL ======")


print("=====  NATURAL GAS ======")
data = cd.historic_data('NATURALGAS')
print(data)
print("=====  NATURAL GAS ======")

print("=====  COPPER ======")
data = cd.historic_data('COPPER')
print(data)
print("=====  COPPER ======")

print("=====  LEAD ======")
data = cd.historic_data('LEAD')
print(data)
print("=====  LEAD ======")


print("=====  ZINC ======")
data = cd.historic_data('ZINC')
print(data)
print("=====  ZINC ======")


print("=====  ALUMINIUM ======")
data = cd.historic_data('ALUMINIUM')
print(data)
print("=====  ALUMINIUM ======")


print("=====  SILVER ======")
data = cd.historic_data('SILVER')
print(data)
print("=====  SILVER ======")

#print(expiry_date)
"""
