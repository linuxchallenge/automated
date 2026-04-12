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
import json
import logging
import os
import random
import socket
import time
from datetime import datetime, timedelta
import traceback
import pandas as pd
import requests
from tvDatafeed import Interval, TvDatafeed
import pytz
import websocket

# Set up logging
logger = logging.getLogger(__name__)

# Set socket timeout for websocket connections (in seconds)
# Balance between allowing slow connections and not blocking too long
DEFAULT_WEBSOCKET_TIMEOUT = 10  # 10 seconds - fail fast but allow for some latency
socket.setdefaulttimeout(DEFAULT_WEBSOCKET_TIMEOUT)
websocket.setdefaulttimeout(DEFAULT_WEBSOCKET_TIMEOUT)

class commodity_data:

    # define different comodity data
    symbol = ['CRUDEOIL', 'NATURALGAS', 'COPPER', 'GOLD', 'LEAD', 'ZINC', 'ALUMINIUM', 'SILVER']

    use_source = "tv"  # tv or mc

    def __init__(self):
        self.symbolTokenMap = {}  # Add this line
        logging.info("Initializing commodity_data")
        self.tv_obj = None
        self.tv_error = 0
        self.tv_timeout_retries = 0  # Track consecutive timeout errors
        self.max_tv_timeout_retries = 3  # Max retries before reconnecting

        # Load credentials from external file
        credentials_file = os.path.join(os.path.dirname(__file__), 'tv_credentials.json')
        try:
            with open(credentials_file, 'r', encoding='utf-8') as f:
                self.credentials = json.load(f)
            logging.info(f"Loaded {len(self.credentials)} credentials from {credentials_file}")
        except FileNotFoundError:
            logging.error(f"Credentials file not found: {credentials_file}")
            self.credentials = []
        except json.JSONDecodeError as e:
            logging.error(f"Error parsing credentials file: {e}")
            self.credentials = []

        # Initialize the tv datafeed
        self._init_tv_connection()

        fileUrl ='https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz'
        self.symboldf = pd.read_csv(fileUrl)
        self.symboldf['expiry'] = pd.to_datetime(self.symboldf['expiry']).apply(lambda x: x.date())
        self.symboldf = self.symboldf[self.symboldf.exchange == 'MCX_FO']
        self.symboldf = self.symboldf[self.symboldf.strike == 0]
        #self.use_source = "up"
        if self.tv_obj:
            print("TV Datafeed initialized " + self.tv_obj.token)
        logging.info(f"Fetching data from: {self.use_source}")

    def _init_tv_connection(self):
        """Initialize or reinitialize the TradingView connection"""
        # Randomly choose a set of credentials
        for attempt in range(5):
            if not self.credentials:
                logging.error("No credentials available for TV connection")
                self.use_source = "up"
                return

            credentials12 = random.choice(self.credentials)
            username = credentials12['username']
            password = credentials12['password']
            print(f"Using credentials: {username}")
            logging.info(f"TV connection attempt {attempt + 1}/5 with user: {username}")

            try:
                self.tv_obj = TvDatafeed(username, password, random_user_agent=True)  # pylint: disable=unexpected-keyword-arg

                if self.tv_obj.token != 'unauthorized_user_token':
                    logging.info(f"TV connection successful with token: {self.tv_obj.token}")
                    self.tv_timeout_retries = 0  # Reset timeout counter on successful connection
                    return
            except Exception as e:
                logging.error(f"Error creating TV connection: {e}")

            # sleep for 3 seconds before retrying
            time.sleep(5)

        # If it fails 5 times, then switch self.use_source to up
        self.use_source = "up"
        print("Switching to UP as TV Datafeed failed")
        logging.error("Switching to UP as TV Datafeed failed")

    def _reconnect_tv(self):
        """Reconnect TradingView when connection issues occur"""
        logging.warning("Reconnecting TradingView due to connection issues...")
        try:
            # Close existing websocket if any
            if self.tv_obj and hasattr(self.tv_obj, 'ws') and self.tv_obj.ws:
                try:
                    self.tv_obj.ws.close()
                except Exception:
                    pass
        except Exception as e:
            logging.debug(f"Error closing existing TV connection: {e}")

        # Reinitialize the connection
        self._init_tv_connection()

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
        try:
            logging.info(f"Fetching data for symbol: {symbol} {self.use_source}")
            if self.use_source == "tv":
                try:
                    logging.info(f"Fetching data from TV for symbol: {symbol}")
                    return self.historic_data_tv(symbol, daily)
                except Exception as e:
                    print(f"Error executing historic_data_tv: {e}")
                    logging.error(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                    logging.error(f"Error executing historic_data_tv: {e}")
                    return self.historic_data_upstox(symbol, daily)
            elif self.use_source == "up":
                logging.info(f"Fetching data from UP for symbol: {symbol}")
                return self.historic_data_upstox(symbol, daily)
            return self.historic_data_investing(symbol, daily)
        except Exception as e:
            print(f"Error executing historic_data: {e}")
            logging.error(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
            logging.error(f"Error executing historic_data: {e}")
            return None

    def historic_data_tv(self, symbol, daily = False):
        max_retries = 2  # Reduced from 3 for faster failure

        for retry in range(max_retries):
            try:
                if not daily:
                    tv_data = self.tv_obj.get_hist(symbol=symbol, exchange='MCX', interval=Interval.in_1_hour, n_bars=500, fut_contract=1)
                else:
                    tv_data = self.tv_obj.get_hist(symbol=symbol, exchange='MCX', interval=Interval.in_daily, n_bars=500, fut_contract=1)

                # Null check - TV can return None on connection issues
                if tv_data is None:
                    logging.warning(f"TV returned None for {symbol}, retry {retry + 1}/{max_retries}")
                    self.tv_timeout_retries += 1
                    if self.tv_timeout_retries >= self.max_tv_timeout_retries:
                        logging.warning("Multiple consecutive TV failures, reconnecting...")
                        self._reconnect_tv()
                        self.tv_timeout_retries = 0
                    time.sleep(1)  # Short 1s wait before retry
                    continue

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

                # Success - reset error counters
                self.tv_error = 0
                self.tv_timeout_retries = 0
                return tv_data

            except Exception as e:
                error_str = str(e).lower()
                is_timeout = 'timeout' in error_str or 'timed out' in error_str
                is_connection_lost = 'connection' in error_str and 'lost' in error_str

                logging.error(f"Error executing historic_data_tv (retry {retry + 1}/{max_retries}): {e}")

                if is_timeout or is_connection_lost:
                    self.tv_timeout_retries += 1
                    logging.warning(f"TV timeout/connection error #{self.tv_timeout_retries} for {symbol}")

                    # If we've had multiple consecutive timeouts, try reconnecting
                    if self.tv_timeout_retries >= self.max_tv_timeout_retries:
                        logging.warning("Multiple consecutive TV timeouts, reconnecting...")
                        self._reconnect_tv()
                        self.tv_timeout_retries = 0

                    # Short wait before retry (1 second)
                    time.sleep(1)
                    continue
                else:
                    # Non-timeout error - log full traceback
                    logging.error(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                    print(f"Error executing historic_data_tv: {e}")

                    # Maintain count - if error is more than 5 times switch to UP
                    self.tv_error = self.tv_error + 1
                    if self.tv_error > 5:
                        self.use_source = "up"
                        logging.warning("Switching to Upstox due to repeated TV errors")

                    return None

        # All retries exhausted - fall back to Upstox immediately
        logging.warning(f"TV retries exhausted for {symbol}, falling back to Upstox")
        return None  # Will trigger fallback in historic_data()


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

    def historic_data_upstox(self, symbol, isDaily = False):
        try:
            print("Fetching data from Upstox")
            TIME_ZONE = pytz.timezone('Asia/Kolkata')

            if symbol == 'CRUDEOIL':
                symbol = 'CRUDE OIL'

            # From self.symboldf get all the tokens for the given symbol
            token = self.symboldf[self.symboldf.name == symbol]

            if symbol == 'GOLD':
                # Remove all entires which has PETAL and GUINEA in tradingsymbol column
                token = token[~token.tradingsymbol.str.contains('PETAL')]
                token = token[~token.tradingsymbol.str.contains('GUINEA')]
                token = token[~token.tradingsymbol.str.contains('GOLDTEN')]

            if symbol == 'LEAD' or symbol == 'ZINC':
                # Remove all entires which has MINI in tradingsymbol column
                token = token[~token.tradingsymbol.str.contains('MINI')]

            if symbol == 'ALUMINIUM':
                # Remove all entires which has MINI in tradingsymbol column
                token = token[~token.tradingsymbol.str.contains('ALUMINIUM')]

            # Sort tokens by expiry date
            token = token.sort_values(by='expiry', ascending=True)

            if symbol == 'LEAD' or symbol == 'ZINC' or symbol == 'ALUMINIUM':
                # Get the first token
                token = token.iloc[1]['instrument_key']
            else:
                # Get the first token
                token = token.iloc[0]['instrument_key']

            # Get the first token
            #logging.info(f"Token: {token}")
            #print(f"Token: {token}")
            #token_id = token.iloc[0]['instrument_key']

            fromDate = (datetime.now(TIME_ZONE)  - timedelta(days=100 if isDaily else 20)) .strftime("%Y-%m-%d")
            todate = datetime.now(TIME_ZONE).strftime("%Y-%m-%d")

            if isDaily:
                url = f'https://api.upstox.com/v2/historical-candle/{token}/day/{todate}/{fromDate}'
                url_history = None  # Not used for daily data
            else:
                url = f'https://api.upstox.com/v2/historical-candle/intraday/{token}/30minute'
                url_history = f'https://api.upstox.com/v2/historical-candle/{token}/30minute/{todate}/{fromDate}'
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'X-Requested-With': 'XMLHttpRequest',
                'Referer': 'https://api.upstox.com',
                'Connection': 'keep-alive'
            }

            res = requests.get(url, headers=headers, params={}, timeout=5.0)
            logging.info("Response status code: %s", str(res.status_code))
            candleRes = res.json()

            if 'data' in candleRes and 'candles' in candleRes['data'] and  candleRes['data']['candles']:
                candleData = pd.DataFrame(candleRes['data']['candles'])
                candleData.columns = ['date','open','high','low', 'close','vol','oi']
                candleData['date'] = pd.to_datetime(candleData['date']).dt.tz_convert('Asia/Kolkata')

                # Drop vol and oi columns
                candleData = candleData.drop(['vol','oi'], axis=1)

                # From candleData['date'] remove time zone info
                candleData = candleData.assign(date=candleData['date'].dt.tz_localize(None))  # pylint: disable=unsubscriptable-object

                # Sort by date
                candleData = candleData.sort_values(by='date', ascending=True)
                # Reverse the index order
                candleData = candleData.reset_index(drop=True)

            else:
                print('No data',candleRes)
                candleData = None

            if isDaily is False and url_history is not None:
                res = requests.get(url_history, headers=headers, params={}, timeout=5.0)
                candleRes = res.json()

                if 'data' in candleRes and 'candles' in candleRes['data'] and  candleRes['data']['candles']:
                    candleData_min = pd.DataFrame(candleRes['data']['candles'])
                    candleData_min.columns = ['date','open','high','low', 'close','vol','oi']
                    candleData_min['date'] = pd.to_datetime(candleData_min['date']).dt.tz_convert('Asia/Kolkata')

                    # Drop vol and oi columns
                    candleData_min = candleData_min.drop(['vol','oi'], axis=1)

                    # From candleData['date'] remove time zone info
                    candleData_min = candleData_min.assign(date=candleData_min['date'].dt.tz_localize(None))  # pylint: disable=unsubscriptable-object

                    # Reverse the data
                    candleData_min = candleData_min.sort_values(by='date', ascending=True)

                    # Merge candleData_min and candleData
                    candleData = pd.concat([candleData_min, candleData], ignore_index=True)

                    ohlc = {
                        'open': 'first',
                        'high': 'max',
                        'low': 'min',
                        'close': 'last',
                    }

                    candleData.set_index('date', inplace=True)
                    candleData = candleData.resample('60min', origin=00).apply(ohlc)
                    candleData.dropna(inplace=True)
                    candleData.reset_index(inplace=True)

                else:
                    print('No data',candleRes)

        except Exception as e:
            print(f"Error executing historic_data_upstox: {e}")
            logging.error(''.join(traceback.format_exception(type(e), e, e.__traceback__)))

        # Rename date column to Date
        candleData = candleData.rename(columns={'date': 'Date'})
        return candleData

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
