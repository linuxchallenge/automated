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
from datetime import datetime, time


import time as t
# basic logging configuration
import logging
import pandas as pd
import configuration
import commodity_data
from alligator_api import alligator_api
from TelegramSend import telegram_send_api

logger = logging.getLogger(__name__)

symbol = ['CRUDEOIL', 'NATURALGAS', 'COPPER', 'GOLD', 'LEAD', 'ZINC', 'ALUMINIUM', 'SILVER']

# Map symbol to lot
symbol_to_lot = {
    'CRUDEOIL': 10,
    'NATURALGAS': 250,
    'COPPER': 2500,
    'GOLD': 10,
    'LEAD': 1000,
    'ZINC': 1000,
    'ALUMINIUM': 1000,
    'SILVER': 5,
}

class CommodityStratergy:
    def __init__(self, accounts):
        self.accounts = accounts

        # Read last_proccesed_symbol and last_executed_hour from file
        if os.path.exists('last_proccesed_symbol.txt'):
            with open('last_proccesed_symbol.txt', 'r', encoding='utf-8') as f:
                data = f.read().split(',')
                self.last_proccesed_symbol = data[0]
                self.last_executed_hour = int(data[1]) if len(data) > 1 else 0
        else:
            self.last_proccesed_symbol = None
            self.last_executed_hour = 0

        self.commodity_data = commodity_data.commodity_data()
        self.commodity_data.intializeSymbolAndGetExpiryData()

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


    def check_trade_executed(self, accounts, place_order):
        # For all accounts
        for account in accounts:
            file_name = f'csv/Commodity-{account}.csv'
            if os.path.exists(file_name):

                # read file
                current_trade = pd.read_csv(file_name)

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
                    row_number = current_trade.index.get_loc(current_trade[(current_trade['enter_order_state'] == 'close_pending')].index[0])

                    # Check order status
                    order_id = current_trade.loc[row_number, 'exit_orderid']
                    old_price = current_trade.loc[row_number, 'exit_price']

                    status, price = place_order.order_status(account, order_id, old_price)

                    if status == "Complete":
                        current_trade.loc[row_number, 'exit_order_state'] = 'close'
                        current_trade.loc[row_number, 'exit_price'] = price
                        current_trade.to_csv(file_name, index=False)
                    else:
                        # Send telegram message
                        self.send_message(account, current_trade.loc[row_number, 'Symbol'], f"Order status is {status}", 0)
                        current_trade.loc[row_number, 'exit_order_state'] = 'error'



    def execute_strategy(self, accounts, place_order, account_details):
        try:

            current_time_dt = datetime.now().time()

            if current_time_dt < time(8, 59):
                t.sleep(60)
                return

            # If last_executed_hour is same as current hour, then return
            if self.last_executed_hour == current_time_dt.hour:
                return

            start_loop_time = datetime.now()

            # Get the configuration
            configuration.ConfigurationLoader.load_configuration()

            self.check_trade_executed(accounts, place_order)

            # Loop for all symbol and start with the last processed symbol
            for s in symbol:
                if self.last_proccesed_symbol is not None and s != self.last_proccesed_symbol:
                    continue

                print(f"Processing symbol: {s}")
                logger.info(f"Processing symbol: {s}")

                # Get the historic data
                historic_data = self.commodity_data.historic_data(s)

                historic_data_daily = self.commodity_data.historic_data(s, daily=True)

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
                logger.info(f"Symbol: {s}, Alligator: {alligator}, Bullish: {bullish}, Bearish: {bearish}")

                print(f"Symbol: {s}, close: {historic_data.iloc[-1]['close']}")

                # Loop for all accounts
                for account in accounts:

                    print(f"Processing account: {account}")
                    logging.info(f"Processing account: {account}")

                    # Get cvs file with account name, month and year in the file name
                    file_name = f'csv/Commodity-{account}.csv'

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

                    if alligator_daily[0] == "uptrend":
                        if current_trade is None or row_number == -1:
                            if historic_data.iloc[-1]['close'] > bullish:
                                print("Enter long trade")
                                logging.info("Enter long trade")
                                order_id = place_order.place_buy_orders_commodity(account, s, \
                                                                       account_details.loc[(account_details['Account'] == account) &\
                                                                                            (account_details['Symbol'] == s)].shape[0])
                                new_row = {'Symbol': s, 'trade_type': ['long'], \
                                        'entry_time': datetime.now(), 'entry_price': historic_data.iloc[-1]['close'], \
                                        'enter_orderid' : order_id, 'enter_order_state': 'open_pending', 'exit_orderid': 0, 'exit_order_state': 'none', \
                                            'exit_order_id' : 0, 'exit_time': '', 'exit_price': '', 'state': 'open', 'profit': ''}                                
                                current_trade = pd.concat([current_trade, pd.DataFrame(new_row)], ignore_index=True)
                                trade_entered = True
                    elif alligator_daily[0] == "downtrend":
                        if current_trade is None or row_number == -1:
                            if historic_data.iloc[-1]['close'] < bearish:
                                print ("Enter short trade")
                                logging.info("Enter short trade")
                                order_id = place_order.place_sell_orders_commodity(account, s,
                                            account_details.loc[(account_details['Account'] == account) & \
                                                                (account_details['Symbol'] == s)].shape[0])
                                new_row = {'Symbol': s, 'trade_type': ['short'], \
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
                                logging.info("Exit long trade")
                                order_id = place_order.place_sell_orders_commodity(account, s,
                                            account_details.loc[(account_details['Account'] == account)\
                                                                 & (account_details['Symbol'] == s)].shape[0])
                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'exit_price'] - \
                                    current_trade.loc[row_number, 'entry_price']
                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'profit'] \
                                    * symbol_to_lot[s]
                                current_trade.loc[row_number, 'exit_orderid'] = order_id
                                current_trade.loc[row_number, 'exit_order_state'] = 'close_pending'
                                self.send_message(account, s, f"Long p/l is {current_trade.loc[row_number, 'profit']}", \
                                                current_trade.loc[row_number, 'profit'])
                    elif trade_entered is False and alligator[0] == "uptrend":
                        if current_trade is not None and row_number != -1 and current_trade.shape[0] != 0:
                            if current_trade.loc[row_number, 'trade_type'] == 'short':
                                print(historic_data.iloc[-1]['Date'])
                                current_trade.loc[row_number, 'exit_time'] = historic_data.iloc[-1]['Date']
                                current_trade.loc[row_number, 'exit_price'] = historic_data.iloc[-1]['close']
                                current_trade.loc[row_number, 'state'] = 'closed'

                                print ("Exit short trade " +  str(historic_data.iloc[-1]['close']) + str(current_trade.loc[row_number, 'exit_price']))
                                logging.info("Exit short trade")
                                order_id = place_order.place_buy_orders_commodity(account, s,
                                            account_details.loc[(account_details['Account'] == account) &
                                                                (account_details['Symbol'] == s)].shape[0])
                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'entry_price'] - \
                                    current_trade.loc[row_number, 'exit_price']
                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'profit'] \
                                    * symbol_to_lot[s]
                                current_trade.loc[row_number, 'exit_orderid'] = order_id
                                current_trade.loc[row_number, 'exit_order_state'] = 'close_pending'
                                self.send_message(account, s, f"Short p/l is {current_trade.loc[row_number, 'profit']}", \
                                                current_trade.loc[row_number, 'profit'])
                    else:
                        if current_trade is not None and row_number != -1 and current_trade.shape[0] != 0:
                            print(historic_data.iloc[-1]['Date'])
                            current_trade.loc[row_number, 'exit_time'] = historic_data.iloc[-1]['Date']
                            current_trade.loc[row_number, 'exit_price'] = historic_data.iloc[-1]['close']
                            current_trade.loc[row_number, 'state'] = 'closed'
                            if current_trade.loc[row_number, 'trade_type'] == 'short':
                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'entry_price'] - \
                                    current_trade.loc[row_number, 'exit_price']
                                order_id = place_order.place_buy_orders_commodity(account, s,
                                         account_details.loc[(account_details['Account'] == account) & \
                                                             (account_details['Symbol'] == s)].shape[0])
                            else:
                                order_id = place_order.place_sell_orders_commodity(account, s,
                                         account_details.loc[(account_details['Account'] == account) & \
                                                             (account_details['Symbol'] == s)].shape[0])
                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'exit_price'] - \
                                    current_trade.loc[row_number, 'entry_price']
                            current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'profit'] \
                                    * symbol_to_lot[s]
                            current_trade.loc[row_number, 'exit_orderid'] = order_id
                            current_trade.loc[row_number, 'exit_order_state'] = 'close_pending'
                            if current_trade.loc[row_number, 'trade_type'] == 'short':
                                self.send_message(account, s, f"Short p/l is {current_trade.loc[row_number, 'profit']}", \
                                                current_trade.loc[row_number, 'profit'])
                                print ("Exit short trade " +  str(historic_data.iloc[-1]['close']) + str(current_trade.loc[row_number, 'exit_price']))
                                logging.info("Exit short trade")
                            else:
                                print ("Exit long trade " +  str(historic_data.iloc[-1]['close']) + str(current_trade.loc[row_number, 'exit_price']))
                                logging.info("Exit long trade")
                                self.send_message(account, s, f"Long p/l is {current_trade.loc[row_number, 'profit']}", \
                                                current_trade.loc[row_number, 'profit'])

                    if current_trade is not None:
                        current_trade.to_csv(file_name, index=False)

                    print(f"Processed account: {account}")
                    logging.info(f"Processed account: {account}")

                print(f"Processing symbol: {s}")
                logging.info(f"Processing symbol: {s}")

                after_loop_time = datetime.now()

                # If symbol is last assign self.last_proccesed_symbol to first symbol
                if s == symbol[-1]:
                    self.last_executed_hour = current_time_dt.hour
                    self.last_proccesed_symbol = symbol[0]

                    # Save to file
                    with open('last_proccesed_symbol.txt', 'w', encoding='utf-8') as f:
                        f.write(f"{self.last_proccesed_symbol},{self.last_executed_hour}")

                # Assign next symbol to self.last_proccesed_symbol
                for i in range(len(symbol)):
                    if symbol[i] == s:
                        if i == len(symbol) - 1:
                            self.last_proccesed_symbol = symbol[0]
                        else:
                            self.last_proccesed_symbol = symbol[i+1]

                time_difference = (after_loop_time - start_loop_time).total_seconds()

                print(f"Time taken for symbol: {s} is {time_difference}")

                if time_difference > 30:
                    print("Excedding 30 seconds so exit")
                    return

        except Exception as e:
            logging.error(f"Error executing execute_strategy: {e}")
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
            'Stratergy': 'Commodity',
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
    coomodity_path = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vSW7PvQv8xTthnXTbsRByR09G5Ny9g523F0PP8jKjcQ2cXL2oVqfJvdmdepjjGe_urDKJjj9WnquAuk/pub?output=csv'
    commodity_account_details = pd.read_csv(coomodity_path)

    # add deepti GOLD and 1 to commodity_account_details
    commodity_account_details = commodity_account_details.append({'Account': 'deepti', 'Symbol': 'GOLD', 'Quantity': 1}, ignore_index=True)

    place_order = PlaceOrder.PlaceOrder()  # Instantiate the PlaceOrder class
    place_order.init_account("deepti")

    # Get home directory
    cur_dir = Path.home()
    # Add /temp/data_collection to the home directory
    cur_dir = cur_dir / 'temp' / 'data_collection'
    # Create the directory if it does not exist
    cur_dir.mkdir(parents=True, exist_ok=True)

    #Change the current working directory to the directory
    os.chdir(cur_dir)

    commodity_stratergy = CommodityStratergy(['dummy', 'deepti'])
    print("Starting")
    commodity_stratergy.execute_strategy(['deepti'], place_order, commodity_account_details)
    print("Exiting 1    ")
    commodity_stratergy.execute_strategy(['deepti'], place_order, commodity_account_details)
    print("Exiting 2    ")
    commodity_stratergy.execute_strategy(['dummy'], place_order, commodity_account_details)
    print("Exiting 3    ")
    commodity_stratergy.execute_strategy(['dummy'], place_order, commodity_account_details)
    print("Exiting 4    ")
    commodity_stratergy.execute_strategy(['dummy'], place_order, commodity_account_details)
    print("Exiting 5    ")
    commodity_stratergy.execute_strategy(['dummy'], place_order, commodity_account_details)
    print("Exiting 6    ")
    commodity_stratergy.execute_strategy(['dummy'], place_order, commodity_account_details)
    print("Exiting 7    ")
    commodity_stratergy.execute_strategy(['dummy'], place_order, commodity_account_details)
    print("Exiting 8    ")
    commodity_stratergy.execute_strategy(['dummy'], place_order, commodity_account_details)
    print("Exiting 9    ")
    commodity_stratergy.execute_strategy(['dummy'], place_order, commodity_account_details)
    print("Exiting 10    ")
    commodity_stratergy.execute_strategy(['dummy'], place_order, commodity_account_details)
    print("Exiting 11    ")
    commodity_stratergy.execute_strategy(['dummy'], place_order, commodity_account_details)

"""