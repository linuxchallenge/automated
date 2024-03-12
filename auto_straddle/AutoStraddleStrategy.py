"""Module providing a function for far cell"""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=W0105



import traceback
from datetime import datetime, time, timedelta
import os
import logging
import time as t
#from PlaceOrder import PlaceOrder
import pandas as pd
import TelegramSend

#from OptionChainData import OptionChainData
#from pathlib import Path
#from PlaceOrder import PlaceOrder


logging.basicConfig(filename='/tmp/autostraddle.log', filemode='w',
                    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] %(message)s')

logging.getLogger().setLevel(logging.INFO)


# For nifty return 50, for bank nifty return 100, for finnifty return 50
def get_strike_interval(symbol):
    if symbol == "NIFTY":
        return 50
    if symbol == "BANKNIFTY":
        return 100
    if symbol == "FINNIFTY":
        return 50
    return 0


class AutoStraddleStrategy:
    def __init__(self, accounts, symbols):
        self.accounts = accounts
        self.symbols = symbols

    def send_error_message(self, account, symbol, error_message):
        sold_options_file_path = self.get_sold_options_file_path(account, symbol)

        x = TelegramSend.telegram_send_api()

        # Send profit loss over telegramsend send_message
        x.send_message("-4008545231", f"Auto straddle Critical error far sell {account} {symbol} {error_message}")

        # Since trade is closed rename the file to sold_options_info_error
        os.rename(sold_options_file_path,
                sold_options_file_path.replace("sold_options_info", "sold_options_info_error"))

    def check_if_trade_is_executed(self, account, symbol, place_order_obj, option_chain_analyzer):

        error_path = self.get_error_options_file_path(account, symbol)
        if os.path.exists(error_path):
            logging.info(f"Critical error far sell so returing {account} {symbol}")
            return False

        # Check if the trade is executed
        # Example: Check if the order is executed by calling get_order_status
        # order_id = place_order_obj.place_orders(account, atm_ce_strike, pe_ce, symbol, qty)
        # status, price = place_order_obj.get_order_status(order_id)
        # return True if the order is executed, else False
        error_in_order = False
        error_message = ""
        sold_options_file_path = self.get_sold_options_file_path(account, symbol)
        if os.path.exists(sold_options_file_path):
            # If the file exists, read its contents and populate sold_options_info
            existing_sold_options_info = self.read_existing_sold_options_info(sold_options_file_path)
            if existing_sold_options_info.iloc[-1]['pe_open_state'] == 'open':
                order_status, price = place_order_obj.order_status(account,
                            existing_sold_options_info.iloc[-1]['pe_open_order_id'],
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_pe_close_price'])
                if order_status == 'Complete':
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'pe_open_state'] = 'closed'
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_pe_price'] = price
                else:
                    error_in_order = True
                    error_message = error_message + "Error in pe open order"

            if existing_sold_options_info.iloc[-1]['ce_open_state'] == 'open':

                t.sleep(3) # Sleep for 3 seconds
                order_status, price = place_order_obj.order_status(account,
                            existing_sold_options_info.iloc[-1]['ce_open_order_id'],
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_ce_close_price'])
                if order_status == 'Complete':
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'ce_open_state'] = 'closed'
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_ce_price'] = price
                else:
                    error_in_order = True
                    error_message = error_message + "Error in ce open order"

            if existing_sold_options_info.iloc[-1]['pe_close_state'] == 'open':
                order_status, price = place_order_obj.order_status(account,
                        existing_sold_options_info.iloc[-1]['pe_close_order_id'],
                        get_option_price(option_chain_analyzer, 'PE'))
                if order_status == 'Complete':
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'pe_close_state'] = 'closed'
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_pe_close_price'] = price
                else:
                    error_in_order = True
                    error_message = error_message + "Error in pe close order"

            if existing_sold_options_info.iloc[-1]['ce_close_state'] == 'open':
                t.sleep(3) # Sleep for 3 seconds
                order_status, price = place_order_obj.order_status(account,
                        existing_sold_options_info.iloc[-1]['ce_close_order_id'],
                        get_option_price(option_chain_analyzer, 'CE'))
                if order_status == 'Complete':
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'ce_close_state'] = 'closed'
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_ce_close_price'] = price
                else:
                    error_in_order = True
                    error_message = error_message + "Error in ce close order"

            if error_in_order:

                self.store_sold_options_info(existing_sold_options_info, account, symbol)

                self.send_error_message(account, symbol, error_message)
                return False

            self.store_sold_options_info(existing_sold_options_info, account, symbol)
            return True

        return True

    def execute_strategy(self, option_chain_analyzer, symbol, account, quantity, place_order_obj):
        try:
            if account not in self.accounts:
                raise ValueError(f"Error: Account '{account}' not valid. Choose from {self.accounts}")

            # Extract relevant information from option_chain_analyzer
            spot_price = option_chain_analyzer['spot_price']
            atm_ce_strike = option_chain_analyzer['atm_strike']
            atm_pe_strike = option_chain_analyzer['atm_strike']

            # Implement your auto straddle strategy logic here
            # Example: Sell ATM call and put options after 9:30 AM
            current_time = datetime.now().time()

            check_if_trade_is_executed = self.check_if_trade_is_executed(account, symbol, place_order_obj, option_chain_analyzer)
            if not check_if_trade_is_executed:
                print(f"Trade is not executed for account {account} {symbol}")
                return

            if current_time > time(15, 10):
                sold_options_file_path = self.get_sold_options_file_path(account, symbol)
                if os.path.exists(sold_options_file_path):
                    # If the file exists, read its contents and populate sold_options_info
                    existing_sold_options_info = self.read_existing_sold_options_info(sold_options_file_path)

                    # Close all trade as market is about to close
                    if existing_sold_options_info.iloc[-1]['trade_state'] == 'open':

                        # Close the trade
                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'ce_close_order_id'], \
                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'pe_close_order_id'] = \
                        self.close_trade(account, existing_sold_options_info.iloc[-1]['atm_pe_strike'], \
                                         existing_sold_options_info.iloc[-1]['atm_ce_strike'],\
                                              existing_sold_options_info.iloc[-1]['atm_pe_price'],
                                         existing_sold_options_info.iloc[-1]['atm_ce_price'], symbol, place_order_obj, quantity)

                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'pe_close_state'] = 'open'
                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'ce_close_state'] = 'open'

                        if existing_sold_options_info.iloc[-1]['atm_ce_price'] == -1:
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'ce_close_state'] = 'closed'

                        if existing_sold_options_info.iloc[-1]['atm_pe_price'] == -1:
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'pe_close_state'] = 'closed'

                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_ce_close_price'] = \
                            option_chain_analyzer['prev_atm_next_ce_price']
                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_pe_close_price'] = \
                            option_chain_analyzer['prev_atm_next_pe_price']

                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'trade_state'] = \
                            'closed'
                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'close_time'] = \
                            datetime.now()
                        print(f"Auto Straddle trade closed for account {account}")
                        logging.info(
                            f"Auto Straddle trade closed for account {account} {symbol} {option_chain_analyzer['spot_price']}\
                                {option_chain_analyzer['pe_to_ce_ratio']}")

                        # Store the information in a file with account and symbol in the name
                        self.store_sold_options_info(existing_sold_options_info, account, symbol)

                    else:
                        compute_profit_loss = self.compute_profit_loss(existing_sold_options_info, symbol)

                        x = TelegramSend.telegram_send_api()

                        # Send profit loss over telegramsend send_message
                        x.send_message("-4008545231", f"Profit or loss for {account} {symbol} is {compute_profit_loss}")

                        # Store the information in a file with account and symbol in the name
                        self.store_sold_options_info(existing_sold_options_info, account, symbol)
                        x.send_file("-4008545231", sold_options_file_path)

                        # Since trade is closed rename the file to sold_options_info_closed
                        os.rename(sold_options_file_path,
                                  sold_options_file_path.replace("sold_options_info", "sold_options_info_closed"))

                return
            elif current_time > time(9, 35):
                # Execute strategy only after 9:30 AM

                # Example: Print a message for demonstration purposes
                # logging.info(f"Selling ATM call and put options for account {account} and symbol {symbol}")

                # Check if the file exists for the given account, symbol, and date
                sold_options_file_path = self.get_sold_options_file_path(account, symbol)
                if os.path.exists(sold_options_file_path):
                    # If the file exists, read its contents and populate sold_options_info
                    existing_sold_options_info = self.read_existing_sold_options_info(sold_options_file_path)

                    # If the trade is closed, check if the conditions to re-enter the trade are met
                    if existing_sold_options_info.iloc[-1]['trade_state'] == 'open':
                        # logging.info(f"Auto Straddle CE trade is still open for account {option_chain_analyzer['given_ce_strike']}")
                        # logging.info(f"Auto Straddle PE trade is still open for account {option_chain_analyzer['given_pe_strike']}")
                        if existing_sold_options_info.iloc[-1]['atm_ce_price'] == -1 or \
                                existing_sold_options_info.iloc[-1]['atm_pe_price'] == -1:
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_ce_close_price'] = \
                                option_chain_analyzer['prev_atm_next_ce_price']
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_pe_close_price'] = \
                                option_chain_analyzer['prev_atm_next_pe_price']

                        else:
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_ce_close_price'] = \
                                option_chain_analyzer['prev_atm_ce_price']
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_pe_close_price'] = \
                                option_chain_analyzer['prev_atm_pe_price']

                        profit_or_loss = self.compute_profit_loss(existing_sold_options_info, symbol)

                        # Check if the conditions to close the trade are met
                        if self.should_close_trade(option_chain_analyzer, existing_sold_options_info.iloc[-1], symbol) \
                                or profit_or_loss < -3000:
                            # Close the trade
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'ce_close_order_id'], \
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'pe_close_order_id'] = \
                                self.close_trade(account, existing_sold_options_info.iloc[-1]['atm_pe_strike'], \
                                             existing_sold_options_info.iloc[-1]['atm_ce_strike'], \
                                                existing_sold_options_info.iloc[-1]['atm_pe_price'],
                                             existing_sold_options_info.iloc[-1]['atm_ce_price'], symbol, place_order_obj, quantity)

                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'pe_close_state'] = 'open'
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'ce_close_state'] = 'open'

                            if existing_sold_options_info.iloc[-1]['atm_ce_price'] == -1:
                                existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'ce_close_state'] = 'closed'

                            if existing_sold_options_info.iloc[-1]['atm_pe_price'] == -1:
                                existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'pe_close_state'] = 'closed'

                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'trade_state'] = \
                                'closed'
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'close_time'] = \
                                datetime.now()
                            logging.info(f"Auto Straddle trade closed for account {account} {symbol} \
                                {option_chain_analyzer['spot_price']} {option_chain_analyzer['pe_to_ce_ratio']}")
                    elif existing_sold_options_info.iloc[-1]['trade_state'] == 'closed':
                        # Check if the conditions to re-enter the trade are met
                        if self.should_reenter_trade(existing_sold_options_info):
                            # Re-enter the trade
                            sold_options_info = {
                                'account': account,
                                'symbol': symbol,
                                'atm_strike': spot_price,
                                'atm_ce_price': get_option_price(option_chain_analyzer, 'CE'),
                                'atm_pe_price': get_option_price(option_chain_analyzer, 'PE'),
                                'trade_state': 'open',
                                'open_time': datetime.now(),
                                'close_time': None,
                                'atm_ce_strike': get_option_strike(option_chain_analyzer, 'CE', symbol),
                                'atm_pe_strike': get_option_strike(option_chain_analyzer, 'PE', symbol),
                                'atm_ce_close_price': get_option_price(option_chain_analyzer, 'CE'),
                                'atm_pe_close_price': get_option_price(option_chain_analyzer, 'PE'),
                                'pe_open_order_id': -1,
                                'ce_open_order_id': -1,
                                'pe_close_order_id': -1,
                                'ce_close_order_id': -1,
                                'pe_open_state': 'open',
                                'ce_open_state': 'open',
                                'pe_close_state': 'None',
                                'ce_close_state': 'None'
                            }

                            # Place orders for ATM CE and ATM PE, if pe is less than 0.7 place only CE order and\
                            # if pe greater than 1.4 place only PE order else place both orders
                            if option_chain_analyzer['pe_to_ce_ratio'] < 0.7:
                                # Place only CE order
                                sold_options_info['atm_pe_price'] = -1
                                sold_options_info['ce_open_order_id'] = place_order_obj.place_orders(account, atm_ce_strike + get_strike_interval(symbol), 'CE', symbol, quantity)

                                if sold_options_info['ce_open_order_id'] == -1:
                                    error_message = "Error in placing ce open order"
                                    self.send_error_message(account, symbol, error_message)
                                    return
                                sold_options_info['pe_open_order_id'] = -1
                                sold_options_info['pe_open_state'] = 'closed'

                            elif option_chain_analyzer['pe_to_ce_ratio'] > 1.4:
                                # Place only PE order
                                sold_options_info['atm_ce_price'] = -1
                                sold_options_info['pe_open_order_id'] = place_order_obj.place_orders(account, atm_pe_strike - get_strike_interval(symbol), 'PE', symbol, quantity)

                                if sold_options_info['pe_open_order_id'] == -1:
                                    error_message = "Error in placing pe open order"
                                    self.send_error_message(account, symbol, error_message)
                                    return
                                sold_options_info['ce_open_order_id'] = -1
                                sold_options_info['ce_open_state'] = 'closed'

                            else:
                                # Place both CE and PE orders
                                sold_options_info['ce_open_order_id'] = place_order_obj.place_orders(account, atm_ce_strike, 'CE', symbol, quantity)

                                if sold_options_info['ce_open_order_id'] == -1:
                                    error_message = "Error in placing ce open order"
                                    self.send_error_message(account, symbol, error_message)
                                    return
                                t.sleep(2) # Sleep for 2 seconds

                                sold_options_info['pe_open_order_id'] = place_order_obj.place_orders(account, atm_pe_strike, 'PE', symbol, quantity)
                                if sold_options_info['pe_open_order_id'] == -1:
                                    error_message = "Error in placing pe open order"
                                    self.send_error_message(account, symbol, error_message)
                                    return

                            print(f"Auto Straddle trade re-entered for account {account}")
                            logging.info(f"Auto Straddle trade re-entered for account {account} \
                                         {option_chain_analyzer['pe_to_ce_ratio']} {symbol} {option_chain_analyzer['spot_price']}")
                            logging.info(f"Auto Straddle {get_option_price(option_chain_analyzer, 'CE')} \
                                    {get_option_price(option_chain_analyzer, 'PE')}")
                            existing_sold_options_info = pd.concat(
                                [existing_sold_options_info, pd.DataFrame([sold_options_info])], ignore_index=True)

                else:
                    # If the file doesn't exist, create a new sold_options_info
                    existing_sold_options_info = pd.DataFrame()
                    sold_options_info = {
                        'account': account,
                        'symbol': symbol,
                        'atm_strike': spot_price,
                        'atm_ce_price': get_option_price(option_chain_analyzer, 'CE'),
                        'atm_pe_price': get_option_price(option_chain_analyzer, 'PE'),
                        'trade_state': 'open',
                        'open_time': datetime.now(),
                        'close_time': None,
                        'atm_ce_strike': get_option_strike(option_chain_analyzer, 'CE', symbol),
                        'atm_pe_strike': get_option_strike(option_chain_analyzer, 'PE', symbol),
                        'atm_ce_close_price': get_option_price(option_chain_analyzer, 'CE'),
                        'atm_pe_close_price': get_option_price(option_chain_analyzer, 'PE'),
                        'pe_open_order_id': 0,
                        'ce_open_order_id': 0,
                        'pe_close_order_id': 0,
                        'ce_close_order_id': 0,
                        'pe_open_state': 'open',
                        'ce_open_state': 'open',
                        'pe_close_state': 'None',
                        'ce_close_state': 'None'
                    }

                    # Place orders for ATM CE and ATM PE, if pe is less than 0.7 place only CE order
                    # and if pe greater than 1.4 place only PE order else place both orders
                    logging.info(f"Auto Straddle {symbol} {get_option_price(option_chain_analyzer, 'CE')} {get_option_price(option_chain_analyzer, 'PE')}")
                    logging.info(f"Auto Straddle {option_chain_analyzer['pe_to_ce_ratio']} {symbol} {option_chain_analyzer['spot_price']}")
                    if option_chain_analyzer['pe_to_ce_ratio'] < 0.7:
                        # Place only CE order
                        sold_options_info['atm_pe_price'] = -1
                        sold_options_info['ce_open_order_id'] = place_order_obj.place_orders(account, atm_ce_strike + get_strike_interval(symbol), 'CE', symbol, quantity)
                        if sold_options_info['ce_open_order_id'] == -1:
                            error_message = "Error in placing ce open order"
                            self.send_error_message(account, symbol, error_message)
                            return
                        sold_options_info['pe_open_order_id'] = -1
                        sold_options_info['pe_open_state'] = 'closed'
                    elif option_chain_analyzer['pe_to_ce_ratio'] > 1.4:
                        # Place only PE order
                        sold_options_info['atm_ce_price'] = -1
                        sold_options_info['pe_open_order_id'] = place_order_obj.place_orders(account, atm_pe_strike - get_strike_interval(symbol), 'PE', symbol, quantity)
                        if sold_options_info['pe_open_order_id'] == -1:
                            error_message = "Error in placing pe open order"
                            self.send_error_message(account, symbol, error_message)
                            return
                        sold_options_info['ce_open_order_id'] = -1
                        sold_options_info['ce_open_state'] = 'closed'
                    else:
                        # Place both CE and PE orders
                        sold_options_info['pe_open_order_id'] = place_order_obj.place_orders(account, atm_pe_strike, 'PE', symbol, quantity)
                        if sold_options_info['pe_open_order_id'] == -1:
                            error_message = "Error in placing pe open order"
                            self.send_error_message(account, symbol, error_message)
                            return
                        t.sleep(2) # Sleep for 2 seconds

                        sold_options_info['ce_open_order_id'] = place_order_obj.place_orders(account, atm_ce_strike, 'CE', symbol, quantity)
                        if sold_options_info['ce_open_order_id'] == -1:
                            error_message = "Error in placing ce open order"
                            self.send_error_message(account, symbol, error_message)
                            return

                    # convert store_sold_options_info to dataframe
                    existing_sold_options_info = pd.concat([existing_sold_options_info,
                                                            pd.DataFrame([sold_options_info])], ignore_index=True)

                # Store the information in a file with account and symbol in the name
                self.store_sold_options_info(existing_sold_options_info, account, symbol)

        except Exception as e:
            print(''.join(traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)))
            logging.error(''.join(traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)))
            print(f"Error executing Auto Straddle Strategy: {e}")
            logging.error(f"Error executing Auto Straddle Strategy: {e}")

    def get_sold_options_file_path(self, account, symbol):
        current_date = datetime.now().strftime("%Y-%m-%d")
        file_name = f"as_sold_options_info_{current_date}_{account}_{symbol}.csv"
        return file_name

    def get_error_options_file_path(self, account, symbol):
        current_date = datetime.now().strftime("%Y-%m-%d")
        file_name = f"as_sold_options_info_error_{current_date}_{account}_{symbol}.csv"
        return file_name

    # Function computes profit or loss of existing_sold_options_info by subtracting each row of
    # atm_ce_price and atm_pe_price from atm_ce_close_price and atm_pe_close_price respectively
    def compute_profit_loss(self, existing_sold_options_info, symbol):
        try:
            # Define multiplication factors based on the symbol
            multiplication_factor = {
                'NIFTY': 50,
                'BANKNIFTY': 15,
                'FINNIFTY': 40
            }
            total_profit_loss = 0

            # Iterate over all rows and compute profit/loss for each row
            for _, row in existing_sold_options_info.iterrows():
                # Extract relevant columns from the current row
                atm_ce_price = row['atm_ce_price']
                atm_pe_price = row['atm_pe_price']
                atm_ce_close_price = row['atm_ce_close_price']
                atm_pe_close_price = row['atm_pe_close_price']
                profit_loss_ce = 0
                profit_loss_pe = 0

                # Compute profit or loss for the current row
                if atm_ce_price != -1:
                    # CE order was not placed
                    profit_loss_ce = atm_ce_price - atm_ce_close_price

                if atm_pe_price != -1:
                    # PE order was not placed
                    profit_loss_pe = atm_pe_price - atm_pe_close_price

                # Sum up the profit or loss for the current row
                total_profit_loss = total_profit_loss + profit_loss_ce + profit_loss_pe

            # Multiply the total profit or loss by the factor based on the symbol
            total_profit_loss *= multiplication_factor.get(symbol, 1)

            print(f"Total profit or loss: {total_profit_loss}")
            logging.info(f"{symbol} Current total profit or loss: {total_profit_loss}")

            # Check if the total loss is more than 3000
            return total_profit_loss

        except Exception as e:
            print(f"Error computing profit or loss: {e}")
            logging.error(f"Error computing profit or loss: {e}")
            return None, None

    def read_existing_sold_options_info(self, file_path):
        try:
            # Read existing content from the CSV file into a DataFrame
            if os.path.exists(file_path):
                df = pd.read_csv(file_path)
                return df
            else:
                print(f"Warning: File '{file_path}' does not exist.")
                logging.warning(f"Warning: File '{file_path}' does not exist.")
                return None

        except Exception as e:
            print(f"Error reading sold options information: {e}")
            logging.error(f"Error reading sold options information: {e}")
            return None

    def should_close_trade(self, option_chain_analyzer, sold_options_info, symbol):
        # Implement conditions to close the trade based on spot price movement
        # Example: Close the trade if NIFTY spot_price has moved by 60, FINNIFTY by 60, and BANKNIFTY by 120
        nifty_movement = 60
        finnifty_movement = 60
        banknifty_movement = 120

        if (
                symbol == "NIFTY"
                and abs(option_chain_analyzer['spot_price'] - sold_options_info['atm_strike']) >= nifty_movement
        ) or (
                symbol == "FINNIFTY"
                and abs(option_chain_analyzer['spot_price'] - sold_options_info['atm_strike']) >= finnifty_movement
        ) or (
                symbol == "BANKNIFTY"
                and abs(option_chain_analyzer['spot_price'] - sold_options_info['atm_strike']) >= banknifty_movement
        ):
            logging.info(
                f"Closing the trade for account {symbol} {option_chain_analyzer['spot_price']} from {sold_options_info['atm_strike']}")
            return True
        return False

    def close_trade(self, account, pe_strike, ce_strike, pe_price, ce_price, symbol, place_order_obj, qty):
        # Close the trade logic goes here
        print(f"Closing the trade for account {account}" , str(symbol) , str(pe_price) , str(ce_price) , str(ce_strike) , str(pe_strike))
        logging.info(f"Closing the trade for account {account}")
        ce_order_id = -1
        pe_order_id = -1
        if pe_price != -1:
            pe_order_id = place_order_obj.close_orders(account, pe_strike, 'PE', symbol, qty)
            if pe_order_id == -1:
                error_message = "Error in placing pe close order"
                self.send_error_message(account, symbol, error_message)
                return -1, -1
        if ce_price != -1:
            ce_order_id = place_order_obj.close_orders(account, ce_strike, 'CE', symbol, qty)
            if ce_order_id == -1:
                error_message = "Error in placing ce close order"
                self.send_error_message(account, symbol, error_message)
                return -1, -1
        # Update the trade state
        return ce_order_id, pe_order_id


    def store_sold_options_info(self, info, account, symbol):
        try:
            file_name = self.get_sold_options_file_path(account, symbol)

            # Save data_frame to CSV with the current date appended to the symbol
            info.to_csv(file_name, index=False)


        except Exception as e:
            print(f"Error storing sold options information: {e}")
            logging.error(f"Error storing sold options information: {e}")

    def get_strike_price(self, account, symbol):
        # logging.info(f"Getting strike price for account {account} and symbol {symbol}")
        file_name = self.get_sold_options_file_path(account, symbol)
        if os.path.exists(file_name):
            existing_sold_options_info = self.read_existing_sold_options_info(file_name)
            if existing_sold_options_info.iloc[-1]['trade_state'] == 'open':
                if existing_sold_options_info.iloc[-1]['atm_pe_price'] == -1:
                    return existing_sold_options_info.iloc[-1]['atm_pe_strike']
                elif existing_sold_options_info.iloc[-1]['atm_ce_price'] == -1:
                    return existing_sold_options_info.iloc[-1]['atm_ce_strike']
                else:
                    return existing_sold_options_info.iloc[-1]['atm_ce_strike']
            else:
                return 0
        else:
            return 0

    def should_reenter_trade(self, sold_options_info):

        profit_amount = self.compute_profit_loss(sold_options_info, sold_options_info.iloc[-1]['symbol'])
        if profit_amount < -3000:
            print(f"Profit amount: {profit_amount} is greater than 3000")
            logging.info(f"Profit amount: {profit_amount} is greater than 3000")
            return False

        # sold_options_info has more than 1 row
        if sold_options_info.shape[0] >= 5:
            return False

        # Check if the required 5-minute interval has passed since the trade close time
        if (
                sold_options_info.iloc[-1]['close_time'] and sold_options_info.iloc[-1]['trade_state'] == 'closed'
                and (
                datetime.now() - datetime.strptime(sold_options_info.iloc[-1]['close_time'], '%Y-%m-%d %H:%M:%S.%f'))
                >= timedelta(minutes=5)
        ):
            return True
        return False


def get_option_price(option_chain_analyzer, option_type):
    # Implement your logic to get the option price based on strike price and type (CE/PE)
    # You can extract this information from option_chain_analyzer
    # For example, option_chain_analyzer['CE'] and option_chain_analyzer['PE']
    # return option_price from option_chain_analyzer
    if option_type == 'CE':
        if option_chain_analyzer['pe_to_ce_ratio'] < 0.7:
            return option_chain_analyzer['atm_next_ce_price']
        else:
            return option_chain_analyzer['atm_current_ce_price']
    elif option_type == 'PE':
        if option_chain_analyzer['pe_to_ce_ratio'] > 1.4:
            return option_chain_analyzer['atm_next_pe_price']
        else:
            return option_chain_analyzer['atm_current_pe_price']


def get_option_strike(option_chain_analyzer, option_type, symbol):
    # Implement your logic to get the option price based on strike price and type (CE/PE)
    # You can extract this information from option_chain_analyzer
    # For example, option_chain_analyzer['CE'] and option_chain_analyzer['PE']
    # return option_price from option_chain_analyzer
    if option_type == 'CE':
        if option_chain_analyzer['pe_to_ce_ratio'] < 0.7:
            return option_chain_analyzer['atm_strike'] + get_strike_interval(symbol)
        else:
            return option_chain_analyzer['atm_strike']
    elif option_type == 'PE':
        if option_chain_analyzer['pe_to_ce_ratio'] > 1.4:
            return option_chain_analyzer['atm_strike'] - get_strike_interval(symbol)
        else:
            return option_chain_analyzer['atm_strike']


"""
accounts = ["dummy", "deepti", "leelu"]
symbols = ["BANKNIFTY"]

place_order = PlaceOrder()

# Get home directory
dir = Path.home()
# Add /temp/data_collection to the home directory
dir = dir / 'temp' / 'data_collection'
# Create the directory if it does not exist
dir.mkdir(parents=True, exist_ok=True)

#Change the current working directory to the directory
os.chdir(dir)


# Initalize all accounts
for account in accounts:
    place_order.init_account(account)

for symbol in symbols:
    option_chain_analyzer = OptionChainData(symbol)

    auto_straddle_strategy = AutoStraddleStrategy(accounts, symbol)
    strike_data = auto_straddle_strategy.get_strike_price(accounts[0], symbol)
    print(f"Strike data: {strike_data}")
    # If symbol is nifty, use the following line to get the option chain data
    option_chain_info = option_chain_analyzer.get_option_chain_info(strike_data, 0, 0)
    #print(f"Option chain info: {option_chain_info}")

    if option_chain_info is not None:
        print(f"pe_to_ce_ratio: {option_chain_info['pe_to_ce_ratio']}")
        option_chain_info['pe_to_ce_ratio'] = 0.4
        auto_straddle_strategy.execute_strategy(option_chain_info, symbol, "deepti", 1, place_order)
        auto_straddle_strategy.execute_strategy(option_chain_info, symbol, "dummy", 1, place_order)
        auto_straddle_strategy.execute_strategy(option_chain_info, symbol, "leelu", 1, place_order)
    else:
        print(f"Option chain information not available for symbol {symbol}")
"""
