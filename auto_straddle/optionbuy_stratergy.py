"""Module providing a function for far cell"""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=W0105
# pylint: disable=C0200
# pylint: disable=W0621

from datetime import datetime, timedelta, time
import os
import logging
from pathlib import Path
import traceback
import pandas as pd
import requests
import configuration
from alligator_api import alligator_api
from TelegramSend import telegram_send_api
from exchange_state import ExchangeData


symbol_list = ['NIFTY', 'BANKNIFTY']

logger = logging.getLogger(__name__)

# Map symbol to lot
symbol_to_lot = {
    'NIFTY': 75,
    'BANKNIFTY': 15,
    'FINNIFTY': 25
}

class OptionBuyStrategy:
    # Add file path constant at class level
    FILE_PATH_TEMPLATE = "csv/OptionBuy-{account}.csv"

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        fileUrl ='https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz'
        self.symboldf = pd.read_csv(fileUrl)
        self.symboldf['expiry'] = pd.to_datetime(self.symboldf['expiry']).apply(lambda x: x.date())
        self.symboldf = self.symboldf[self.symboldf.exchange == 'NSE_FO']
        self.symboldf = self.symboldf[self.symboldf.instrument_type == 'OPTIDX']

        # Get holiday list from url https://api.upstox.com/v2/market/holidays
        url = 'https://api.upstox.com/v2/market/holidays'
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': 'https://api.upstox.com',
            'Connection': 'keep-alive'
        }
        payload = {}

        response = requests.request("GET", url, headers=headers, data=payload, timeout=10)
        holidays = response.json()['data']
        holidaydf = pd.DataFrame(holidays)
        holidaydf['date'] = pd.to_datetime(holidaydf['date']).apply(lambda x: x.date())
        holidaydf = holidaydf[holidaydf['holiday_type'] == 'TRADING_HOLIDAY']

        # Check if previous day was holiday
        today = datetime.now().date()
        self.yesterday = today - timedelta(days=1)
        while (self.yesterday in holidaydf['date'].values and
               holidaydf[holidaydf['date'] == self.yesterday]['closed_exchanges'].str.contains('NSE').iloc[0] or
               self.yesterday.weekday() in [5, 6]):  # 5 is Saturday, 6 is Sunday
            self.yesterday = self.yesterday - timedelta(days=1)

        self.nso_open = None

    def send_message(self, account, symbol_msg, error_message, compute_profit_loss):
        x = telegram_send_api()

        telegram_group = account + "_telegram"

        id3 = configuration.ConfigurationLoader.get_configuration().get(telegram_group)

        # Send profit loss over telegramsend send_message
        x.send_message(id3, f"{account} {symbol_msg} {error_message}")

        pl_dict = {
            'Date': datetime.now().strftime("%Y-%m-%d"),
            'Account': account,
            'Symbol': symbol_msg,
            'Quantity': 1,
            'NumberofTrade': 1,
            'TotalPNL': compute_profit_loss * 1,
            'Brokarge': 60,
            'CloseTime': datetime.now().strftime("%H:%M:%S"),
            'Stratergy': 'IndexFuture',
            'NetPNL': compute_profit_loss - 60
        }

        current_month = datetime.now().strftime("%m")
        file_name = f"pnl/consolidated_pnl_{current_month}.csv"
        if os.path.exists(file_name):
            df = pd.read_csv(file_name)
            df = pd.concat([df, pd.DataFrame([pl_dict])], ignore_index=True)
            df.to_csv(file_name, index=False)
        else:
            df = pd.DataFrame([pl_dict])
            df.to_csv(file_name, index=False)

    # Wanted function to convert 1m to 3m
    def convert1m_to_3m(self, historic_data):
        # Ensure historic_data is not None
        if historic_data is None:
            return None

        # Set the datetime as index
        historic_data = historic_data.set_index('date')

        # Resample to 3-minute intervals, starting at market open (9:15)
        offset = pd.Timedelta(minutes=3)

        resampled_data = historic_data.resample('3T', offset=offset).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last'
        }).dropna()

        # Reset index to make date a column again
        resampled_data = resampled_data.reset_index()
        return resampled_data

    def get_option_data(self, symbol_get, ce_pe, strike):
        # Get current date and last day of current month
        today = datetime.now().date()
        next_month = today.replace(day=28) + timedelta(days=4)
        last_day = next_month - timedelta(days=next_month.day)  # Remove .date() call

        # Filter for current month expiries
        current_month_expiries = self.symboldf[
            (self.symboldf.name == symbol_get) &
            (self.symboldf.strike == strike) &
            (self.symboldf.option_type == ce_pe) &
            (self.symboldf.expiry <= last_day)
        ]

        # Check if current month expiry is available, if not get next month's last expiry
        if current_month_expiries.empty:
            next_month_expiries = self.symboldf[
                (self.symboldf.name == symbol_get) &
                (self.symboldf.strike == strike) &
                (self.symboldf.option_type == ce_pe) &
                (self.symboldf.expiry > last_day)
            ]
            instrument_key = next_month_expiries.sort_values('expiry', ascending=True).iloc[-1]['instrument_key']
        else:
            # Get the last expiry date's instrument key
            instrument_key = current_month_expiries.sort_values('expiry', ascending=True).iloc[-1]['instrument_key']

        # need to generate url like https://api.upstox.com/v2/historical-candle/NSE_FO%7C45676/1minute/2025-01-14/2025-01-13
        # where NSE_FO%7C45676 is instrument_key
        # 1minute is candle interval
        # 2025-01-14 is today's date
        # 2025-01-13 is previous day
        # Format the instrument key for URL
        formatted_instrument_key = instrument_key.replace('|', '%7C')

        # Get today and yesterday's dates in YYYY-MM-DD format
        today_str = datetime.now().strftime('%Y-%m-%d')
        yesterday = self.yesterday
        yesterday_str = yesterday.strftime('%Y-%m-%d')

        url = f'https://api.upstox.com/v2/historical-candle/{formatted_instrument_key}/1minute/{today_str}/{yesterday_str}'

        headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'X-Requested-With': 'XMLHttpRequest',
                'Referer': 'https://api.upstox.com',
                'Connection': 'keep-alive'
            }

        # Get the data from the URL
        res = requests.get(url, headers=headers, timeout=10)

        candleRes = res.json()

        if 'data' in candleRes and 'candles' in candleRes['data'] and  candleRes['data']['candles']:
            candleData = pd.DataFrame(candleRes['data']['candles'])
            candleData.columns = ['date','open','high','low', 'close','vol','oi']
            candleData['date'] = pd.to_datetime(candleData['date']).dt.tz_convert('Asia/Kolkata')

            # Drop vol and oi columns
            candleData = candleData.drop(['vol','oi'], axis=1)

            # From candleData['date'] remove time zone info
            if candleData is not None:
                candleData = candleData.assign(date=candleData['date'].dt.tz_localize(None))

                # Sort by date
                candleData = candleData.sort_values(by='date', ascending=True)
            # Reverse the index order
            candleData = candleData.reset_index(drop=True)

        else:
            print('No data',candleRes)
            candleData = None

        # Similary get pesent day data where url will be like https://api.upstox.com/v2/historical-candle/intraday/NSE_FO%7C45676/1minute/
        url = f'https://api.upstox.com/v2/historical-candle/intraday/{formatted_instrument_key}/1minute'

        res = requests.get(url, headers=headers, timeout=10)
        candleResIntra = res.json()

        candleDataIntra = None
        if 'data' in candleResIntra and 'candles' in candleResIntra['data'] and candleResIntra['data']['candles']:
            candleDataIntra = pd.DataFrame(candleResIntra['data']['candles'])
            candleDataIntra.columns = ['date','open','high','low', 'close','vol','oi']
            candleDataIntra['date'] = pd.to_datetime(candleDataIntra['date']).dt.tz_convert('Asia/Kolkata')

            # Drop vol and oi columns
            candleDataIntra = candleDataIntra.drop(['vol','oi'], axis=1)

            # Remove timezone info
            if candleDataIntra is not None:
                candleDataIntra = candleDataIntra.assign(date=candleDataIntra['date'].dt.tz_localize(None))

            # Sort and reset index
            candleDataIntra = candleDataIntra.sort_values(by='date', ascending=True)
            candleDataIntra = candleDataIntra.reset_index(drop=True)

            # Combine historical and intraday data
            candleData = pd.concat([candleData, candleDataIntra])
        else:
            print('No intraday data', candleResIntra)

        return self.convert1m_to_3m(candleData)

    # Write function which accepts data frame and retuen alligator and fractal
    def get_alligator_fractal(self, data):
        # Initialize alligator_api
        alligator = alligator_api()

        # Compute alligator values
        processed_data = alligator.compute_alligator(data)

        trend = alligator.compute_trend(processed_data)

        # Compute Williams Fractal
        fractals = alligator.WILLIAMS_FRACTAL(data, period=5)

        # Remove NaN values
        fractals = fractals.dropna()

        # Get only latest fractal
        bearish = fractals.loc[fractals.BearishFractal == 1]
        bullish = fractals.loc[fractals.BullishFractal == 1]

        # Check if bearish and bullish fractal exists
        if bearish.empty or bullish.empty:
            return trend, 0, 0
        return trend, data.loc[bearish.index[-1]]['high'], data.loc[bullish.index[-1]]['low']

    def convert15m_to_75m(self, historic_data):
        # Ensure historic_data is not None
        if historic_data is None:
            return None

        # Set the datetime as index
        historic_data = historic_data.set_index('date')

        # Resample to 15-minute intervals, starting at market open (9:15)
        offset = pd.Timedelta(minutes=15)

        resampled_data = historic_data.resample('15T', offset=offset).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last'
        }).dropna()

        # Reset index to make date a column again
        resampled_data = resampled_data.reset_index()
        return resampled_data

    def check_trade_executed(self, accounts, place_order, account_details):
        # For all accounts
        for account in accounts:
            file_path = Path(self.FILE_PATH_TEMPLATE.format(account=account))
            file_path.parent.mkdir(exist_ok=True)

            if file_path.exists():

                # read file
                current_trade = pd.read_csv(file_path)

                # current_trade is empty return
                if current_trade.shape[0] == 0:
                    return

                # check if any enter_order_state is open_pending
                if current_trade.loc[current_trade['enter_order_state'] == 'open_pending'].shape[0] != 0:
                    row_number = current_trade.index.get_loc(current_trade[(current_trade['enter_order_state'] == 'open_pending')].index[0])

                    # Check order status
                    order_id = current_trade.loc[row_number, 'enter_orderid']
                    old_price = current_trade.loc[row_number, 'entry_price']

                    status, price = place_order.order_status(account, order_id, old_price)

                    if status == "Complete":
                        current_trade.loc[row_number, 'enter_order_state'] = 'open'
                        current_trade.loc[row_number, 'entry_price'] = price
                        current_trade.to_csv(file_path, index=False)
                    else:
                        # Send telegram message
                        self.send_message(account, current_trade.loc[row_number, 'Symbol'], f"Order status is {status}", 0)
                        current_trade.loc[row_number, 'enter_order_state'] = 'error'

                # check if any exit_order_state is close_pending
                if current_trade.loc[current_trade['exit_order_state'] == 'close_pending'].shape[0] != 0:
                    row_number = current_trade.index.get_loc(current_trade[(current_trade['exit_order_state'] == 'close_pending')].index[0])

                    # Check order status
                    order_id = current_trade.loc[row_number, 'exit_orderid']
                    old_price = current_trade.loc[row_number, 'exit_price']

                    status, price = place_order.order_status(account, order_id, old_price)

                    quantity = account_details.loc[(account_details['Account'] == account) \
                                                   & (account_details['Symbol'] == current_trade.loc[row_number, 'Symbol'])]['quantity'].values[0]

                    if status == "Complete":
                        current_trade.loc[row_number, 'exit_order_state'] = 'close'
                        current_trade.loc[row_number, 'exit_price'] = price

                        current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'exit_price'] - \
                            current_trade.loc[row_number, 'entry_price']
                        current_trade.loc[row_number, 'profit'] = (current_trade.loc[row_number, 'profit'] \
                                * symbol_to_lot[current_trade.loc[row_number, 'Symbol']]) * quantity
                        self.send_message(
                            account,
                            current_trade.loc[row_number, 'Symbol'],
                            f"option buy p/l is {current_trade.loc[row_number, 'option-type']}, \
                                {current_trade.loc[row_number, 'strike']}, \
                                    {current_trade.loc[row_number, 'profit']}",
                            current_trade.loc[row_number, 'profit']
                        )

                        current_trade.to_csv(file_path, index=False)
                    else:
                        # Send telegram message
                        self.send_message(account, current_trade.loc[row_number, 'Symbol'], f"option buy order status is {status}", 0)
                        current_trade.loc[row_number, 'exit_order_state'] = 'error'

    def close_open_trades(self, accounts, place_order, account_details):
        """Close any open trades based on conditions"""
        try:
            for account in accounts:
                file_path = self.FILE_PATH_TEMPLATE.format(account=account)

                # Check if file exists
                if not os.path.exists(file_path):
                    continue

                # Read existing trades
                current_trade = pd.read_csv(file_path)
                if current_trade.empty:
                    continue

                # Filter open trades
                open_trades = current_trade[current_trade['state'] == 'open']
                if open_trades.empty:
                    continue

                for idx, trade in open_trades.iterrows():
                    try:
                        symbol_name = trade['Symbol']
                        option_type = trade['option-type']
                        strike_price = trade['strike']
                        historic_data = self.get_option_data(symbol_name, option_type, strike_price)

                        if historic_data is not None and not historic_data.empty:
                            current_trade.loc[idx, 'exit_time'] = historic_data.iloc[-1]['date']
                            current_trade.loc[idx, 'exit_price'] = historic_data.iloc[-1]['close']
                            current_trade.loc[idx, 'state'] = 'closed'

                            print(f"Exit long trade {option_type} at price {historic_data.iloc[-1]['close']}")
                            self.logger.info(f"Exit long trade {option_type}")

                            quantity = account_details.loc[(account_details['Account'] == account) &
                                                        (account_details['Symbol'] == symbol_name)]['quantity'].values[0]

                            # def place_orders_option_buy(self, account, atm_ce_strike, pe_ce, symbol, qty, buy_sell):
                            order_id = place_order.place_orders_option_buy(account, strike_price, option_type, symbol_name, quantity, "SELL")

                            current_trade.loc[idx, 'profit'] = (current_trade.loc[idx, 'exit_price'] -
                                                              current_trade.loc[idx, 'entry_price']) * symbol_to_lot[symbol_name]
                            current_trade.loc[idx, 'exit_orderid'] = order_id
                            current_trade.loc[idx, 'exit_order_state'] = 'close_pending'

                            current_trade.to_csv(file_path, index=False)

                    except Exception as e:
                        self.logger.error(f"Error closing trade for {symbol_name}: {e}")
                        continue

        except Exception as e:
            self.logger.error(f"Error in close_open_trades: {e}")

    def execute_strategy(self, accounts, place_order, account_details, strike):
        try:

            # Get the configuration
            configuration.ConfigurationLoader.load_configuration()

            current_time_dt = datetime.now().time()

            if current_time_dt < time(9, 33):
                return

            # return if time is more than 3:30
            if current_time_dt > time(15, 30):
                return

            # Check if NSE is open
            if self.nso_open is None:
                exchange_data = ExchangeData()
                exchange_data_var = exchange_data.is_nfo_open()
                if exchange_data_var is False:
                    print("NFO market is closed")
                    self.nso_open = False
                    return
                else:
                    self.nso_open = True

            elif self.nso_open is False:
                return

            self.check_trade_executed(accounts, place_order, account_details)

            # if current time is more than 3:15 close all open trades
            if current_time_dt > time(15, 15):
                self.close_open_trades(accounts, place_order, account_details)
                return

            # Check if all symbols have been executed in this 3-minute block
            for symbol_name in symbol_list:
                file_name = f'csv/OptionBuy-last_execution_{symbol_name}.txt'

                # If file exists, check last execution time
                if os.path.exists(file_name):
                    with open(file_name, 'r', encoding='utf-8') as f:
                        last_execution = f.read().strip()
                        # Get the minutes portion of both times and divide by 3 to get the block number
                        last_block = int(datetime.strptime(last_execution, "%H:%M").minute / 3)
                        current_block = int(current_time_dt.minute / 3)

                        if last_block == current_block:
                            continue

                # Loop for both CE and PE
                for option_type in ['CE', 'PE']:

                    # Check for existing trades in dummy.csv
                    dummy_file = 'csv/OptionBuy-dummy.csv'
                    strike_process = strike[symbol_name]
                    if os.path.exists(dummy_file):
                        dummy_trades = pd.read_csv(dummy_file)
                        open_trade = dummy_trades[(dummy_trades['Symbol'] == symbol_name) &
                                                (dummy_trades['option-type'] == option_type) &
                                                (dummy_trades['state'] == 'open')]
                        if not open_trade.empty:
                            strike_process = open_trade.iloc[0]['strike']

                    # Get the historic data
                    historic_data = self.get_option_data(symbol_name, option_type, strike_process)

                    try:
                        historic_data_15min = self.convert15m_to_75m(historic_data)
                        if historic_data_15min is None:
                            self.logger.error(f"Failed to convert data for {symbol_name}")
                            continue
                    except Exception as e:
                        self.logger.error(f"Error processing {symbol_name}: {e}")
                        continue

                    # drop last row
                    historic_data_15min = historic_data_15min.drop(historic_data_15min.tail(1).index)

                    if historic_data is None:
                        print(f"Error getting historic data for symbol: {symbol_name} {option_type}")
                        continue

                    if historic_data_15min is None:
                        print(f"Error getting historic daily data for symbol: {symbol_name} {option_type}")
                        continue

                    # Get alligator and fractal
                    alligator, bullish, bearish = self.get_alligator_fractal(historic_data)
                    alligator_daily, _, _ = self.get_alligator_fractal(historic_data_15min)

                    print(f"Symbol: {symbol_name} {option_type}, Alligator: {alligator}, Bullish: {bullish}, Bearish: {bearish}")
                    self.logger.info(f"Symbol: {symbol_name} {option_type} {strike_process}, Alligator: {alligator}, Bullish: {bullish}, Bearish: {bearish}")

                    print(f"Symbol: {symbol_name} {option_type}, close: {historic_data.iloc[-1]['close']}")
                    self.logger.info(f"Symbol: {symbol_name} {option_type}, close: {historic_data.iloc[-1]['close']}")

                    # Loop for all accounts
                    for account in accounts:
                        print(f"Processing account: {account}")
                        self.logger.info(f"Processing account: {account}")

                        # Get cvs file with account name, month and year in the file name
                        file_path = Path(self.FILE_PATH_TEMPLATE.format(account=account))
                        file_path.parent.mkdir(exist_ok=True)

                        row_number = -1

                        if account_details.loc[(account_details['Account'] == account) & (account_details['Symbol'] == symbol_name)].shape[0] == 0:
                            continue

                        if file_path.exists():
                            current_trade = pd.read_csv(file_path)
                            try:
                                row_number = current_trade.index.get_loc(current_trade[(current_trade['Symbol'] == symbol_name) & \
                                                                                (current_trade['option-type'] == option_type) & \
                                                                                (current_trade['state'] == 'open')].index[0])
                            except Exception:
                                row_number = -1
                        else:
                            current_trade = None
                            row_number = -1

                        trade_entered = False

                        quantity = account_details.loc[(account_details['Account'] == account) & (account_details['Symbol'] == symbol_name)]['quantity'].values[0]

                        # Replace the problematic comparison with:
                        if current_trade is None or row_number == -1:
                            # Convert Series to scalar value before comparison
                            close_price = historic_data.iloc[-1]['close'].item()
                            if close_price > bullish and alligator[0] == "uptrend" \
                                and alligator_daily[0] == "uptrend":
                                print(f"Enter long trade {option_type}")
                                self.logger.info(f"Enter long trade {option_type}")
                                order_id = place_order.place_orders_option_buy(account, strike_process, option_type, symbol_name, quantity, "BUY")
                                expiry = datetime.now().date()
                                new_row = {'Symbol': symbol_name,
                                           'option-type': option_type,
                                           'strike': strike_process,
                                           'expiry': expiry,
                                           'trade_type': ['long'],
                                           'entry_time': datetime.now(),
                                           'entry_price': close_price,  # Use scalar value here too
                                           'enter_orderid': order_id,
                                           'enter_order_state': 'open_pending',
                                           'exit_orderid': 0,
                                           'exit_order_state': 'none',
                                           'exit_order_id': 0,
                                           'exit_time': '',
                                           'exit_price': '',
                                           'state': 'open',
                                           'profit': ''}
                                current_trade = pd.concat([current_trade, pd.DataFrame(new_row)], ignore_index=True)
                                trade_entered = True

                        # Exit the trade
                        if trade_entered is False and alligator[0] != "uptrend":
                            if current_trade is not None and row_number != -1 and current_trade.shape[0] != 0:
                                print(historic_data.iloc[-1]['date'])
                                current_trade.loc[row_number, 'exit_time'] = historic_data.iloc[-1]['date']
                                current_trade.loc[row_number, 'exit_price'] = historic_data.iloc[-1]['close']
                                current_trade.loc[row_number, 'state'] = 'closed'

                                print(f"Exit long trade {option_type} " + str(historic_data.iloc[-1]['close']) + str(current_trade.loc[row_number, 'exit_price']))
                                self.logger.info(f"Exit long trade {option_type}")
                                order_id = place_order.place_orders_option_buy(account, strike_process, option_type, symbol_name, quantity, "SELL")
                                expiry = datetime.now().date()
                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'exit_price'] - \
                                    current_trade.loc[row_number, 'entry_price']
                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'profit'] \
                                    * symbol_to_lot[symbol_name]
                                current_trade.loc[row_number, 'exit_orderid'] = order_id
                                current_trade.loc[row_number, 'exit_order_state'] = 'close_pending'

                        if current_trade is not None:
                            current_trade.to_csv(file_path, index=False)

                        print(f"Processed account: {account}")
                        self.logger.info(f"Processed account: {account}")

                    # After processing, save current time
                    with open(file_name, 'w', encoding='utf-8') as f:
                        f.write(current_time_dt.strftime("%H:%M"))

                # Check if executed time has more than 20 seconds return, check using current_time_dt
                after_execution_time = datetime.now().time()
                # Getting current date for conversion
                current_date = datetime.now().date()

                # Convert time objects to datetime objects for comparison
                current_datetime = datetime.combine(current_date, current_time_dt)
                after_execution_datetime = datetime.combine(current_date, after_execution_time)

                # Now perform the comparison
                if current_datetime - after_execution_datetime > timedelta(seconds=20):
                    return
        except Exception as e:
            print("Error in execute_strategy", e)
            logger.error(f"Error in execute_strategy: {e}")

            #print trace back
            print(traceback.format_exc())
            return



"""
import PlaceOrder

import os
from pathlib import Path
import logging_config  # This sets up the logging
from OptionChainData import OptionChainData

strike = {"NIFTY": 23000, "BANKNIFTY": 49000, "FINNIFTY": 15000}

# Test code
if __name__ == '__main__':
    coomodity_path = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vQXfDbzC7lWCbDgVa6VwTJVViYo_EXl3ZMgTdFcsTbshjS38hWzwYf93VtddOhY4nfkR4aTdpfCiGRT/pub?output=csv'
    commodity_account_details = pd.read_csv(coomodity_path)

    print(commodity_account_details)

    symbol = "NIFTY"

    # add deepti GOLD and 1 to commodity_account_details
    #commodity_account_details = commodity_account_details.append({'Account': 'deepti', 'Symbol': 'GOLD', 'Quantity': 1}, ignore_index=True)

    place_order = PlaceOrder.PlaceOrder()  # Instantiate the PlaceOrder class
    #place_order.init_account("deepti")
    #place_order.init_account("leelu")
    #place_order.init_account("avanthi")

    # Get home directory
    cur_dir = Path.home()
    # Add /temp/data_collection to the home directory
    cur_dir = cur_dir / 'temp' / 'data_collection'
    # Create the directory if it does not exist
    cur_dir.mkdir(parents=True, exist_ok=True)

    #Change the current working directory to the directory
    os.chdir(cur_dir)

    commodity_stratergy = OptionBuyStrategy()
    print("Starting")

    option_chain_analyzer = OptionChainData(symbol)

    #print("Before calling get_option_chain_info", strike_data, pe_strike, ce_strike)

    # Get option chain data for the specified symbol
    option_chain_info = option_chain_analyzer.get_option_chain_info(0, 0, 0, symbol)

    strike[symbol] = option_chain_info['atm_strike']

    symbol = "BANKNIFTY"

    # Get option chain data for the specified symbol
    option_chain_info = option_chain_analyzer.get_option_chain_info(0, 0, 0, symbol)

    strike[symbol] = option_chain_info['atm_strike']

    print("After calling get_option_chain_info", strike)

    commodity_stratergy.execute_strategy(commodity_account_details['Account'].unique(), place_order, commodity_account_details, strike)
"""
