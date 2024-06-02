"""Module providing a function for far cell"""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=W0105


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

logging.basicConfig(filename='/tmp/autostraddle.log', filemode='w',
                    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] %(message)s')

logging.getLogger().setLevel(logging.INFO)

symbol = ['NATURALGAS', 'CRUDEOIL', 'COPPER', 'GOLD', 'LEAD', 'ZINC', 'ALUMINIUM', 'SILVER']

class CommodityStratergy:
    def __init__(self, accounts):
        self.accounts = accounts
        self.last_executed_hour = 0
        self.last_proccesed_symbol = None
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

    def execute_strategy(self, accounts):
        try:

            current_time_dt = datetime.now().time()

            if current_time_dt < time(8, 59):
                t.sleep(60)
                return

            # If last_executed_hour is same as current hour, then return
            if self.last_executed_hour == current_time_dt.hour:
                return

            # Get the configuration
            configuration.ConfigurationLoader.load_configuration()

            # Loop for all symbol and start with the last processed symbol
            for s in symbol:
                if self.last_proccesed_symbol is not None and s != self.last_proccesed_symbol:
                    continue

                print(f"Processing symbol: {s}")

                # Get the historic data
                historic_data = self.commodity_data.historic_data(s)

                # Get alligator and fractal
                alligator, bullish, bearish = self.get_alligator_fractal(historic_data)

                print(f"Symbol: {s}, Alligator: {alligator}, Bullish: {bullish}, Bearish: {bearish}")

                print(f"Symbol: {s}, close: {historic_data.iloc[-1]['close']}")

                # Get the first entry of accounts
                account = accounts[0]

                # Get cvs file with account name, month and year in the file name
                file_name = f'Commodity-{account}_{datetime.now().strftime("%Y-%m")}.csv'

                row_number = -1

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

                if alligator[0] == "uptrend":
                    if current_trade is None or row_number == -1:
                        if historic_data.iloc[-1]['close'] > bullish:
                            print ("Enter long trade")
                            new_row = {'Symbol': s, 'trade_type': ['long'], \
                                    'entry_time': historic_data.iloc[-1]['datetime'], 'entry_price': historic_data.iloc[-1]['close'], \
                                    'enter_orderid' : 0, 'enter_order_state': 'open', 'exit_orderid': 0, 'exit_order_state': 'none', \
                                        'exit_order_id' : 0, 'exit_time': '', 'exit_price': '', 'state': 'open', 'profit': ''}
                            current_trade = pd.concat([current_trade, pd.DataFrame(new_row)], ignore_index=True)
                    else:
                        if current_trade.iloc[row_number]['trade_type'] == 'short':
                            print ("Exit short trade")
                            current_trade.loc[row_number, 'exit_time'] = historic_data.iloc[-1]['Date']
                            current_trade.loc[row_number, 'exit_price'] = historic_data.iloc[-1]['close']
                            current_trade.loc[row_number, 'exit_order_state'] = 'open'
                            current_trade.loc[row_number, 'state'] = 'closed'
                elif alligator[0] == "downtrend":
                    if current_trade is None or row_number == -1:
                        if historic_data.iloc[-1]['close'] < bearish:
                            print ("Enter short trade")
                            new_row = {'Symbol': s, 'trade_type': ['short'], \
                                    'entry_time': datetime.now(), 'entry_price': historic_data.iloc[-1]['close'], \
                                    'enter_orderid' : 0, 'enter_order_state': 'open', 'exit_orderid': 0, 'exit_order_state': 'none', \
                                        'exit_order_id' : 0, 'exit_time': '', 'exit_price': '', 'state': 'open', 'profit': ''}
                            current_trade = pd.concat([current_trade, pd.DataFrame(new_row)], ignore_index=True)
                    else:
                        if (current_trade.shape[0] != 0) and current_trade.iloc[row_number]['trade_type'] == 'long':
                            print ("Exit long trade")
                            current_trade.loc[row_number, 'exit_time']  = historic_data.iloc[-1]['Date']
                            current_trade.loc[row_number, 'exit_price'] = historic_data.iloc[-1]['close']
                            current_trade.loc[row_number, 'exit_order_state'] = 'open'
                            current_trade.loc[row_number, 'state'] = 'closed'
                else: # sideways
                    if current_trade is not None and row_number != -1 and current_trade.shape[0] != 0:
                        if current_trade.iloc[-1]['state'] == 'open':
                            print(historic_data.iloc[-1]['Date'])
                            current_trade.loc[row_number, 'exit_time'] = historic_data.iloc[-1]['Date']
                            current_trade.loc[row_number, 'exit_price'] = historic_data.iloc[-1]['close']
                            current_trade.loc[row_number, 'state'] = 'closed'
                            if current_trade.iloc[-1]['trade_type'] == 'long':
                                print ("Exit long trade")
                                current_trade.at[current_trade.index[-1], 'profit'] = current_trade.iloc[-1]['exit_price'] - current_trade.iloc[-1]['entry_price']
                            else:
                                print ("Exit short trade")
                                current_trade.at[current_trade.index[-1], 'profit'] = current_trade.iloc[-1]['entry_price'] - current_trade.iloc[-1]['exit_price']
                if current_trade is not None:
                    current_trade.to_csv(file_name, index=False)

        except Exception as e:
            logging.error(f"Error executing execute_strategy: {e}")
            traceback.print_exc()


'''
# Test code
if __name__ == '__main__':
    commodity_stratergy = CommodityStratergy(['dummy'])
    commodity_stratergy.execute_strategy(['dummy'])
'''    