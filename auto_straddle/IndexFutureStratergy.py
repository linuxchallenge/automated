"""Module providing a function for far cell"""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=W0105
# pylint: disable=C0200

import os
import traceback
import time
from datetime import datetime, time, timedelta, date


# basic logging configuration
import logging
import pandas as pd
import requests
import configuration
from alligator_api import alligator_api
from TelegramSend import telegram_send_api
from exchange_state import ExchangeData

logger = logging.getLogger(__name__)

symbol = ['NIFTY', 'BANKNIFTY', 'FINNIFTY']

# Map symbol to lot
symbol_to_lot = {
    'NIFTY': 75,
    'BANKNIFTY': 15,
    'FINNIFTY': 25
}

class IndexFutureStratergy:
    def __init__(self, accounts):
        if not accounts:
            raise ValueError("Accounts list cannot be empty")

        # Initialize logger first
        self.logger = logging.getLogger(__name__)
        self.accounts = accounts
        self.last_executed_time = None
        self.last_processed_symbol = None

        # Load saved state
        loaded_time, loaded_symbol = self._load_execution_state()
        if loaded_time and loaded_symbol:
            self.last_executed_time = loaded_time
            self.last_processed_symbol = loaded_symbol

    def _load_execution_state(self):
        """Load last execution time and symbol from file
        
        Returns:
            tuple: (datetime | None, str | None) - Last execution time and symbol
        """
        filepath = 'last_processed_symbol_index.txt'

        # Create file if it doesn't exist
        if not os.path.exists(filepath):
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write('')  # Create empty file
            self.logger.info(f"Created new state file at {filepath}")
            return None, None

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                line = f.read().strip()
                if line:
                    time_str, symbol_par = line.split(',')
                    try:
                        # Try parsing as complete ISO format first
                        dt = datetime.fromisoformat(time_str)
                    except ValueError:
                        # If it's just a time string, combine with today's date
                        current_date = datetime.now().date()
                        dt = datetime.combine(current_date, datetime.strptime(time_str, '%H:%M:%S.%f').time())
                    return dt, symbol_par
                return None, None
        except Exception as e:
            self.logger.error(f"Error reading state file: {e}")
            return None, None

    def _save_execution_state(self, execution_time, symbol_par):
        """Save execution time and symbol to file
        
        Args:
            execution_time (datetime): Current execution time
            symbol (str): Current symbol being processed
        """
        filepath = 'last_processed_symbol_index.txt'
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"{execution_time.isoformat()},{symbol_par}")
        except Exception as e:
            self.logger.error(f"Failed to save state to {filepath}: {e}")
            raise

    def is_same_time_block(self, current_time, symbol_par):
        """Check if current time is in same 15-minute block as last execution
        
        Args:
            current_time (datetime): Current time to check
            symbol (str): Current symbol being processed
            
        Returns:
            bool: True if in same time block, False otherwise
        """
        if not self.last_executed_time:
            return False

        same_block = (
            self.last_executed_time.hour == current_time.hour and
            (self.last_executed_time.minute // 15) == (current_time.minute // 15)
        )

        # if same_block, continue only if current symbol is next in sequence after last_processed_symbol
        if same_block:
            try:
                last_symbol_index = symbol.index(self.last_processed_symbol)
                if last_symbol_index == 0:
                    return False  # If we processed last symbol, start new block
                return True  # Continue with next symbol in same block
            except ValueError:
                return False  # If symbol not found in list, start new block

        if not same_block:
            self._save_execution_state(current_time, symbol_par)
            self.last_executed_time = current_time
            self.last_processed_symbol = symbol_par

        return same_block

    def datetotimestamp(self, date_obj):
        """Convert datetime/date object to Unix timestamp
        
        Args:
            date_obj (Union[datetime, date]): Date object to convert
            
        Returns:
            int: Unix timestamp
        """
        try:
            if isinstance(date_obj, datetime):
                return int(date_obj.timestamp())
            elif isinstance(date_obj, date):
                return int(datetime.combine(date_obj, datetime.min.time()).timestamp())
            else:
                raise ValueError(f"Expected datetime/date object, got {type(date_obj)}")
        except Exception as e:
            self.logger.error(f"Error converting date to timestamp: {e}")
            raise

    def timestamptodate(self, timestamp):
        """Convert Unix timestamp to datetime
        
        Args:
            timestamp (int): Unix timestamp
            
        Returns:
            datetime: Datetime object
        """
        try:
            return datetime.fromtimestamp(int(timestamp))
        except Exception as e:
            self.logger.error(f"Error converting timestamp to date: {e}")
            raise

    def convert15m_to_75m(self, data):
        data = data.set_index('Date')
        data = data.groupby(data.index.date) \
            .apply(lambda d: d.resample(rule='75T', closed='left', label='left', origin=d.index.min())
                   .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna())
        data.reset_index(inplace=True)
        data = data.drop(['level_0'], axis=1)
        return data

    def OHLCHistoricData(self, symbol_parse):

        fdate = datetime.now()
        todate = fdate - timedelta(days=70)

        start = self.datetotimestamp(todate)
        end = self.datetotimestamp(fdate)
        if symbol_parse == "NIFTY":
            url = 'https://priceapi.moneycontrol.com/techCharts/history?symbol=9&resolution=15&from=' + str(
                start) + '&to=' + str(end) + ''
        elif symbol_parse == "BANKNIFTY":
            url = 'https://priceapi.moneycontrol.com/techCharts/history?symbol=23&resolution=15&from=' + str(
                start) + '&to=' + str(end) + ''
        elif symbol_parse == "FINNIFTY":
            url = 'https://priceapi.moneycontrol.com/techCharts/history?symbol=47&resolution=15&from=' + str(
                start) + '&to=' + str(end) + ''
        else:
            return None

        hdr = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(url, timeout=5, headers=hdr).json()
        data = pd.DataFrame(resp)

        date_time = []
        for dt in data['t']:
            date_time.append({'Date': self.timestamptodate(dt)})

        dt = pd.DataFrame(date_time)
        intraday_data = pd.concat([dt, data['o'], data['h'], data['l'], data['c'], data['v']], axis=1). \
            rename(columns={'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume'})

        return intraday_data

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


    def check_trade_executed(self, accounts, place_order, account_details):
        # For all accounts
        for account in accounts:
            file_name = f'csv/IndexFuture-{account}.csv'
            if os.path.exists(file_name):

                # read file
                current_trade = pd.read_csv(file_name)

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
                        current_trade.to_csv(file_name, index=False)
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

                        if current_trade.loc[row_number, 'trade_type'] == 'short':
                            current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'entry_price'] - \
                                current_trade.loc[row_number, 'exit_price']
                            current_trade.loc[row_number, 'profit'] = (current_trade.loc[row_number, 'profit'] \
                                    * symbol_to_lot[current_trade.loc[row_number, 'Symbol']]) * quantity
                            self.send_message(account, current_trade.loc[row_number, 'Symbol'], \
                                              f"Short p/l is {current_trade.loc[row_number, 'profit']}", \
                                            current_trade.loc[row_number, 'profit'])
                        else:
                            current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'exit_price'] - \
                                current_trade.loc[row_number, 'entry_price']
                            current_trade.loc[row_number, 'profit'] = (current_trade.loc[row_number, 'profit'] \
                                    * symbol_to_lot[current_trade.loc[row_number, 'Symbol']]) * quantity
                            self.send_message(account, current_trade.loc[row_number, 'Symbol'], \
                                              f"Long p/l is {current_trade.loc[row_number, 'profit']}", \
                                            current_trade.loc[row_number, 'profit'])

                        current_trade.to_csv(file_name, index=False)
                    else:
                        # Send telegram message
                        self.send_message(account, current_trade.loc[row_number, 'Symbol'], f"Order status is {status}", 0)
                        current_trade.loc[row_number, 'exit_order_state'] = 'error'

    def execute_strategy(self, accounts, place_order, account_details):
        try:

            current_time_dt = datetime.now().time()

            if current_time_dt < time(9, 15):
                return

            # return if time is more than 3:30
            if current_time_dt > time(15, 30):
                return

            self.check_trade_executed(accounts, place_order, account_details)

            # If last_executed_hour is same as current hour, then return
            if self.last_executed_time and self.is_same_time_block(current_time_dt, self.last_processed_symbol):
                return

            start_loop_time = datetime.now()

            # Get the configuration
            configuration.ConfigurationLoader.load_configuration()

            # Loop for all symbol and start with the last processed symbol
            for s in symbol:
                if self.last_processed_symbol != 'None' and self.last_processed_symbol is not None and s != self.last_processed_symbol:
                    continue

                print(f"Processing symbol: {s}")
                self.logger.info(f"Processing symbol: {s}")

                # Check if NSE is open
                exchange_data = ExchangeData()
                exchange_data_var = exchange_data.is_nfo_open()
                if exchange_data_var is False:
                    self.last_executed_time = current_time_dt
                    self.logger.info("NSE is closed")
                    print("NSE is closed")
                    return

                # Get the historic data
                historic_data = self.OHLCHistoricData(s)

                historic_data_daily = self.convert15m_to_75m(historic_data)

                # drop last row
                historic_data_daily = historic_data_daily.drop(historic_data_daily.tail(1).index)

                if historic_data is None:
                    print(f"Error getting historic data for symbol: {s}")
                    return

                if historic_data_daily is None:
                    print(f"Error getting historic daily data for symbol: {s}")
                    return

                # Get alligator and fractal
                alligator, bullish, bearish = self.get_alligator_fractal(historic_data)

                alligator_daily, _, _ = self.get_alligator_fractal(historic_data_daily)

                print(f"Symbol: {s}, Alligator: {alligator}, Bullish: {bullish}, Bearish: {bearish}")
                self.logger.info(f"Symbol: {s}, Alligator: {alligator}, Bullish: {bullish}, Bearish: {bearish}")

                print(f"Symbol: {s}, close: {historic_data.iloc[-1]['close']}")

                # Loop for all accounts
                for account in accounts:

                    print(f"Processing account: {account}")
                    self.logger.info(f"Processing account: {account}")

                    # Get cvs file with account name, month and year in the file name
                    file_name = f'csv/IndexFuture-{account}.csv'

                    row_number = -1

                    if account_details.loc[(account_details['Account'] == account) & (account_details['Symbol'] == s)].shape[0] == 0:
                        continue

                    if os.path.exists(file_name):
                        current_trade = pd.read_csv(file_name)
                        try:
                            row_number = current_trade.index.get_loc(current_trade[(current_trade['Symbol'] == s) & \
                                                                            (current_trade['state'] == 'open')].index[0])
                        except Exception:
                            row_number = -1
                    else:
                        current_trade = None
                        row_number = -1

                    trade_entered = False

                    quantity = account_details.loc[(account_details['Account'] == account) & (account_details['Symbol'] == s)]['quantity'].values[0]

                    if alligator_daily[0] == "uptrend":
                        if current_trade is None or row_number == -1:
                            if historic_data.iloc[-1]['close'] > bullish and alligator[0] == "uptrend":
                                print("Enter long trade")
                                self.logger.info("Enter long trade")
                                order_id, expiry = place_order.place_buy_orders_commodity(account, s, quantity, None, False)
                                new_row = {'Symbol': s, 'expiry': expiry, 'trade_type': ['long'],
                                           'entry_time': datetime.now(), 'entry_price': historic_data.iloc[-1]['close'], 
                                           'enter_orderid': order_id, 'enter_order_state': 'open_pending', 'exit_orderid': 0, 'exit_order_state': 'none', 
                                           'exit_order_id': 0, 'exit_time': '', 'exit_price': '', 'state': 'open', 'profit': ''}
                                current_trade = pd.concat([current_trade, pd.DataFrame(new_row)], ignore_index=True)
                                trade_entered = True
                    elif alligator_daily[0] == "downtrend":
                        if current_trade is None or row_number == -1:
                            if historic_data.iloc[-1]['close'] < bearish and alligator[0] == "downtrend":
                                print ("Enter short trade")
                                self.logger.info("Enter short trade")
                                order_id, expiry = place_order.place_sell_orders_commodity(account, s, quantity, None, False)
                                new_row = {'Symbol': s, 'expiry': expiry, 'trade_type': ['short'], \
                                        'entry_time': datetime.now(), 'entry_price': historic_data.iloc[-1]['close'], \
                                        'enter_orderid' : order_id, 'enter_order_state': 'open_pending', 'exit_orderid': 0, 'exit_order_state': 'none', \
                                            'exit_order_id' : 0, 'exit_time': '', 'exit_price': '', 'state': 'open', 'profit': ''}
                                current_trade = pd.concat([current_trade, pd.DataFrame(new_row)], ignore_index=True)
                                trade_entered = True

                    # Exit the trade.
                    if trade_entered is False and alligator[0] == "downtrend":
                        if current_trade is not None and row_number != -1 and current_trade.shape[0] != 0:
                            if current_trade.loc[row_number, 'trade_type'] == 'long':
                                print(historic_data.iloc[-1]['Date'])
                                current_trade.loc[row_number, 'exit_time'] = historic_data.iloc[-1]['Date']
                                current_trade.loc[row_number, 'exit_price'] = historic_data.iloc[-1]['close']
                                current_trade.loc[row_number, 'state'] = 'closed'

                                print ("Exit long trade " +  str(historic_data.iloc[-1]['close']) + str(current_trade.loc[row_number, 'exit_price']))
                                self.logger.info("Exit long trade")
                                order_id, expiry = place_order.place_sell_orders_commodity(account, s, quantity, current_trade.loc[row_number, 'expiry'], False)
                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'exit_price'] - \
                                    current_trade.loc[row_number, 'entry_price']
                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'profit'] \
                                    * symbol_to_lot[s]
                                current_trade.loc[row_number, 'exit_orderid'] = order_id
                                current_trade.loc[row_number, 'exit_order_state'] = 'close_pending'
                    elif trade_entered is False and alligator[0] == "uptrend":
                        if current_trade is not None and row_number != -1 and current_trade.shape[0] != 0:
                            if current_trade.loc[row_number, 'trade_type'] == 'short':
                                print(historic_data.iloc[-1]['Date'])
                                current_trade.loc[row_number, 'exit_time'] = historic_data.iloc[-1]['Date']
                                current_trade.loc[row_number, 'exit_price'] = historic_data.iloc[-1]['close']
                                current_trade.loc[row_number, 'state'] = 'closed'

                                print ("Exit short trade " +  str(historic_data.iloc[-1]['close']) + str(current_trade.loc[row_number, 'exit_price']))
                                self.logger.info("Exit short trade")
                                order_id, expiry = place_order.place_buy_orders_commodity(account, s, quantity, current_trade.loc[row_number, 'expiry'], False)
                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'entry_price'] - \
                                    current_trade.loc[row_number, 'exit_price']
                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'profit'] \
                                    * symbol_to_lot[s]
                                current_trade.loc[row_number, 'exit_orderid'] = order_id
                                current_trade.loc[row_number, 'exit_order_state'] = 'close_pending'
                    else:
                        if current_trade is not None and row_number != -1 and current_trade.shape[0] != 0:
                            print(historic_data.iloc[-1]['Date'])
                            current_trade.loc[row_number, 'exit_time'] = historic_data.iloc[-1]['Date']
                            current_trade.loc[row_number, 'exit_price'] = historic_data.iloc[-1]['close']
                            current_trade.loc[row_number, 'state'] = 'closed'
                            if current_trade.loc[row_number, 'trade_type'] == 'short':
                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'entry_price'] - \
                                    current_trade.loc[row_number, 'exit_price']
                                order_id, expiry = place_order.place_buy_orders_commodity(account, s, quantity, current_trade.loc[row_number, 'expiry'], False)
                            else:
                                order_id, expiry = place_order.place_sell_orders_commodity(account, s, quantity, current_trade.loc[row_number, 'expiry'], False)
                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'exit_price'] - \
                                    current_trade.loc[row_number, 'entry_price']
                            current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'profit'] \
                                    * symbol_to_lot[s]
                            current_trade.loc[row_number, 'exit_orderid'] = order_id
                            current_trade.loc[row_number, 'exit_order_state'] = 'close_pending'
                            if current_trade.loc[row_number, 'trade_type'] == 'short':
                                print ("Exit short trade " +  str(historic_data.iloc[-1]['close']) + str(current_trade.loc[row_number, 'exit_price']))
                                self.logger.info("Exit short trade")
                            else:
                                print ("Exit long trade " +  str(historic_data.iloc[-1]['close']) + str(current_trade.loc[row_number, 'exit_price']))
                                self.logger.info("Exit long trade")

                    if current_trade is not None:
                        current_trade.to_csv(file_name, index=False)

                    print(f"Processed account: {account}")
                    self.logger.info(f"Processed account: {account}")

                print(f"Processing symbol: {s}")
                self.logger.info(f"Processing symbol: {s}")

                after_loop_time = datetime.now()

                # Assign next symbol to self.last_processed_symbol
                for i in range(len(symbol)):
                    if symbol[i] == s:
                        if i == len(symbol) - 1:
                            self.last_processed_symbol = symbol[0]
                        else:
                            self.last_processed_symbol = symbol[i+1]
                
                self.last_executed_time = after_loop_time

                # Save to file
                self._save_execution_state(self.last_executed_time, self.last_processed_symbol)                            

                time_difference = (after_loop_time - start_loop_time).total_seconds()

                print(f"Time taken for symbol: {s} is {time_difference}")

                if time_difference > 30:
                    print("Excedding 30 seconds so exit")
                    return

        except Exception as e:
            self.logger.error(f"Error executing execute_strategy: {e}")
            traceback.print_exc()

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

"""

import PlaceOrder

import os
from pathlib import Path
import logging_config  # This sets up the logging

# Test code
if __name__ == '__main__':
    coomodity_path = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vSt9M_2rCWQqiDtbBY4hn7oCfRLpWpbdHonYbqiQmDznXWSK_0DTgtV3q2TtK1fnslRDjd0NpccSDZU/pub?output=csv'
    commodity_account_details = pd.read_csv(coomodity_path)

    print(commodity_account_details)

    # add deepti GOLD and 1 to commodity_account_details
    #commodity_account_details = commodity_account_details.append({'Account': 'deepti', 'Symbol': 'GOLD', 'Quantity': 1}, ignore_index=True)

    place_order = PlaceOrder.PlaceOrder()  # Instantiate the PlaceOrder class
    place_order.init_account("deepti")
    place_order.init_account("leelu")
    place_order.init_account("avanthi")

    # Get home directory
    cur_dir = Path.home()
    # Add /temp/data_collection to the home directory
    cur_dir = cur_dir / 'temp' / 'data_collection'
    # Create the directory if it does not exist
    cur_dir.mkdir(parents=True, exist_ok=True)

    #Change the current working directory to the directory
    os.chdir(cur_dir)

    commodity_stratergy = IndexFutureStratergy(['dummy', 'deepti', 'leelu'])
    print("Starting")
    commodity_stratergy.execute_strategy(['dummy', 'deepti', 'leelu'], place_order, commodity_account_details)
    print("Exiting 1    ")
    commodity_stratergy.execute_strategy(['dummy', 'deepti', 'leelu'], place_order, commodity_account_details)
    print("Exiting 2    ")
    commodity_stratergy.execute_strategy(['dummy', 'deepti', 'leelu'], place_order, commodity_account_details)
    print("Exiting 3    ")
    commodity_stratergy.execute_strategy(['dummy', 'deepti', 'leelu'], place_order, commodity_account_details)
    print("Exiting 4    ")
    commodity_stratergy.execute_strategy(['deepti'], place_order, commodity_account_details)
    print("Exiting 5    ")
    commodity_stratergy.execute_strategy(['dummy'], place_order, commodity_account_details)
    print("Exiting 6    ")
    commodity_stratergy.execute_strategy(['leelu'], place_order, commodity_account_details)
    print("Exiting 7    ")    
    commodity_stratergy.execute_strategy(['leelu'], place_order, commodity_account_details)
    print("Exiting 8    ")    
"""