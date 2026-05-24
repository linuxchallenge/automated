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
from datetime import datetime, time, timedelta


import time as t
# basic logging configuration
import logging
import pandas as pd
import configuration
import commodity_data
from alligator_api import alligator_api
from TelegramSend import telegram_send_api
from exchange_state import ExchangeData
import brokrage_calculator

logger = logging.getLogger(__name__)

symbol = ['COPPER', 'GOLD', 'SILVER']

# Map symbol to lot
symbol_to_lot = {
    'CRUDEOIL': 10,
    'NATURALGAS': 250,
    'COPPER': 2500,
    'GOLD': 10,
    'LEAD': 1000,
    'ZINC': 1000,
    'ALUMINIUM': 1000,
    'SILVER': 1,
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

        # Error message throttling - track last sent time for each account+error combination
        self.last_error_sent = {}  # Format: {f"{account}_{symbol}_{error_hash}": datetime}
        # Order retry tracking - track retry attempts for each order
        self.order_retry_count = {}  # Format: {f"{account}_{symbol}_{order_type}": count}
        # Track the hour when the last retry was attempted per key
        self.order_retry_hour = {}  # Format: {f"{account}_{symbol}_{order_type}": hour}
        self.MAX_RETRY_ATTEMPTS = 3  # Maximum retry attempts for rejected orders
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

    def retry_rejected_order(self, account, trading_symbol, order_type, place_order, account_details, current_trade=None, row_number=None):
        """
        Retry placing a rejected order

        Args:
            account: Trading account
            trading_symbol: Trading symbol (e.g., 'GOLD', 'COPPER')
            order_type: 'entry' or 'exit'
            place_order: Order placement object
            account_details: DataFrame with account configuration (Account, Symbol, quantity)
            current_trade: DataFrame with trade data (needed to get trade details)
            row_number: Row index in the DataFrame

        Returns:
            tuple: (new_order_id, success_flag)
        """
        retry_key = f"{account}_{trading_symbol}_{order_type}"

        current_hour = datetime.now().hour

        # Get current retry count
        current_retries = self.order_retry_count.get(retry_key, 0)

        if current_retries >= self.MAX_RETRY_ATTEMPTS:
            logging.error(f"Maximum retry attempts ({self.MAX_RETRY_ATTEMPTS}) reached for {account} {trading_symbol} {order_type} order")
            return -1, False

        # Only retry if the hour has changed since last retry attempt
        last_retry_hour = self.order_retry_hour.get(retry_key, -1)
        if last_retry_hour == current_hour:
            logging.info(f"Already retried {order_type} order for {account} {trading_symbol} this hour (hour={current_hour}), waiting for next hour")
            return -1, False

        # Record this hour as the last retry hour and increment retry count
        self.order_retry_hour[retry_key] = current_hour
        self.order_retry_count[retry_key] = current_retries + 1

        logging.info(f"Retrying {order_type} order for {account} {trading_symbol} (attempt {current_retries + 1}/{self.MAX_RETRY_ATTEMPTS})")

        try:
            # Get quantity from account details
            try:
                quantity = account_details.loc[(account_details['Account'] == account) &
                                             (account_details['Symbol'] == trading_symbol)]['quantity'].values[0]
                logging.info(f"Retrieved quantity {quantity} for {account} {trading_symbol} from account_details")
            except (IndexError, KeyError) as e:
                # Fallback to default quantity if not found in account_details
                quantity = 1
                logging.warning(f"Could not find quantity for {account} {trading_symbol} in account_details, using default: {quantity}. Error: {e}")

            if order_type == 'entry':
                # For entry orders, we need to know if it's a buy or sell based on trade_type
                if current_trade is not None and row_number is not None:
                    trade_type = current_trade.loc[row_number, 'trade_type']
                    if trade_type == 'long':
                        # Place buy order for long entry
                        order_id, expiry = place_order.place_buy_orders_commodity(account, trading_symbol, quantity, None)
                    elif trade_type == 'short':
                        # Place sell order for short entry
                        order_id, expiry = place_order.place_sell_orders_commodity(account, trading_symbol, quantity, None)
                    else:
                        logging.error(f"Unknown trade_type '{trade_type}' for {account} {trading_symbol}")
                        return -1, False
                else:
                    logging.error(f"Missing trade data for entry order retry: {account} {trading_symbol}")
                    return -1, False

            elif order_type == 'exit':
                # For exit orders, we need to do the opposite of the entry
                if current_trade is not None and row_number is not None:
                    trade_type = current_trade.loc[row_number, 'trade_type']
                    expiry = current_trade.loc[row_number, 'expiry']
                    if trade_type == 'long':
                        # Place sell order to exit long position
                        order_id, _ = place_order.place_sell_orders_commodity(account, trading_symbol, quantity, expiry)
                    elif trade_type == 'short':
                        # Place buy order to exit short position
                        order_id, _ = place_order.place_buy_orders_commodity(account, trading_symbol, quantity, expiry)
                    else:
                        logging.error(f"Unknown trade_type '{trade_type}' for {account} {trading_symbol}")
                        return -1, False
                else:
                    logging.error(f"Missing trade data for exit order retry: {account} {trading_symbol}")
                    return -1, False
            else:
                logging.error(f"Unknown order_type '{order_type}' for {account} {trading_symbol}")
                return -1, False

            if order_id == -1:
                logging.error(f"Retry failed for {account} {trading_symbol} {order_type} order")
                return -1, False

            logging.info(f"Successfully retried {order_type} order for {account} {trading_symbol}, new order ID: {order_id}")
            return order_id, True

        except Exception as e:
            logging.error(f"Exception during retry for {account} {trading_symbol} {order_type}: {str(e)}")
            return -1, False

    def reset_retry_count(self, account, trading_symbol, order_type):
        """Reset retry count for successful orders"""
        retry_key = f"{account}_{trading_symbol}_{order_type}"
        if retry_key in self.order_retry_count:
            del self.order_retry_count[retry_key]
        if retry_key in self.order_retry_hour:
            del self.order_retry_hour[retry_key]

    def check_trade_executed(self, accounts, place_order, account_details):
        # For all accounts
        for account in accounts:
            file_name = f'csv/Commodity-{account}.csv'
            if os.path.exists(file_name):

                # read file
                current_trade = pd.read_csv(file_name)

                # current_trade is empty, skip to the next account
                if current_trade.shape[0] == 0:
                    continue

                # check if any enter_order_state is open_pending
                open_pending_rows = current_trade[current_trade['enter_order_state'] == 'open_pending']
                if not open_pending_rows.empty:
                    row_number = current_trade.index.get_loc(open_pending_rows.index[0])

                    # Now safe to access row_number
                    order_id = current_trade.loc[row_number, 'enter_orderid']
                    old_price = current_trade.loc[row_number, 'entry_price']

                    status, price = place_order.order_status(account, order_id, old_price)

                    logging.info(f"Order status for order id {order_id} is {status} and price is {price}")

                    if status == "Complete":
                        current_trade.loc[row_number, 'enter_order_state'] = 'open'
                        if price != 0:
                            current_trade.loc[row_number, 'entry_price'] = price
                        current_trade.to_csv(file_name, index=False)
                        logging.info(f"Entry order completed for {account} {current_trade.loc[row_number, 'Symbol']}")
                        # Reset retry count on successful completion
                        self.reset_retry_count(account, current_trade.loc[row_number, 'Symbol'], 'entry')
                    elif status == "Open":
                        # Order is still pending, keep waiting
                        logging.info(f"Entry order {order_id} still pending for {account} {current_trade.loc[row_number, 'Symbol']}")
                    elif status in ("Rejected", "InvalidID"):
                        # Rejected or initial placement returned -1 (InvalidID).
                        # Before retrying, check if the position already exists at the broker
                        # (the order may have been partially processed despite the error).
                        trading_symbol = current_trade.loc[row_number, 'Symbol']
                        trade_type = current_trade.loc[row_number, 'trade_type']

                        pos_type, pos_price = place_order.get_commodity_position(account, trading_symbol, trade_type)
                        if pos_type is not None:
                            # Position exists — the order was actually executed
                            logging.info(f"Position found at broker for {account} {trading_symbol} despite status={status}. Marking entry as open.")
                            current_trade.loc[row_number, 'enter_order_state'] = 'open'
                            if pos_price > 0:
                                current_trade.loc[row_number, 'entry_price'] = pos_price
                            current_trade.to_csv(file_name, index=False)
                            self.reset_retry_count(account, trading_symbol, 'entry')
                        else:
                            # No position — retry the order
                            new_order_id, retry_success = self.retry_rejected_order(
                                account, trading_symbol, 'entry', place_order, account_details, current_trade, row_number
                            )

                            if retry_success:
                                current_trade.loc[row_number, 'enter_orderid'] = new_order_id
                                logging.info(f"Entry order retry successful for {account} {trading_symbol}, new order ID: {new_order_id}")
                                current_trade.to_csv(file_name, index=False)
                            else:
                                retry_key = f"{account}_{trading_symbol}_entry"
                                current_retries = self.order_retry_count.get(retry_key, 0)
                                if current_retries >= self.MAX_RETRY_ATTEMPTS:
                                    self.send_message(account, trading_symbol, "Entry order rejected and all retries failed", 0)
                                    current_trade.loc[row_number, 'enter_order_state'] = 'error'
                                    current_trade.to_csv(file_name, index=False)
                                else:
                                    logging.info(f"Entry order retry deferred to next hour for {account} {trading_symbol}")
                    elif status == "NotFound":
                        # Order not in orderbook — could mean it was never placed or was already filled.
                        # Check position to disambiguate.
                        trading_symbol = current_trade.loc[row_number, 'Symbol']
                        trade_type = current_trade.loc[row_number, 'trade_type']

                        pos_type, pos_price = place_order.get_commodity_position(account, trading_symbol, trade_type)
                        if pos_type is not None:
                            logging.info(f"Order {order_id} not in orderbook but position exists for {account} {trading_symbol}. Marking entry as open.")
                            current_trade.loc[row_number, 'enter_order_state'] = 'open'
                            if pos_price > 0:
                                current_trade.loc[row_number, 'entry_price'] = pos_price
                            current_trade.to_csv(file_name, index=False)
                            self.reset_retry_count(account, trading_symbol, 'entry')
                        else:
                            self.send_message(account, trading_symbol, f"Entry order not found in orderbook and no position exists (order_id={order_id})", 0)
                            current_trade.loc[row_number, 'enter_order_state'] = 'error'
                            current_trade.to_csv(file_name, index=False)
                    elif status == -1:
                        # API error — log but don't mark as error yet; will retry next cycle
                        logging.warning(f"API error checking entry order {order_id} for {account} {current_trade.loc[row_number, 'Symbol']}, will retry next cycle")
                    else:
                        # Unknown status, log warning but don't mark as error yet
                        logging.warning(f"Unknown entry order status '{status}' for {account} {current_trade.loc[row_number, 'Symbol']}, continuing to wait")

                # check if any exit_order_state is close_pending
                if current_trade.loc[current_trade['exit_order_state'] == 'close_pending'].shape[0] != 0:
                    row_number = current_trade.index.get_loc(current_trade[(current_trade['exit_order_state'] == 'close_pending')].index[0])

                    # Check order status
                    order_id = current_trade.loc[row_number, 'exit_orderid']
                    old_price = current_trade.loc[row_number, 'exit_price']

                    try:
                        status, price = place_order.order_status(account, order_id, old_price)
                        logging.info(f"Order status for order id {order_id} is {status} and price is {price}")

                        quantity = account_details.loc[(account_details['Account'] == account) \
                                                   & (account_details['Symbol'] == current_trade.loc[row_number, 'Symbol'])]['quantity'].values[0]

                        if status == "Complete":
                            current_trade.loc[row_number, 'exit_order_state'] = 'close'
                            current_trade.loc[row_number, 'state'] = 'closed'
                            if price != 0:
                                current_trade.loc[row_number, 'exit_price'] = price

                            brokarage_dict = brokrage_calculator.calculate_equity_futures(current_trade.loc[row_number, 'entry_price']
                                                                                    , current_trade.loc[row_number, 'exit_price'],
                                                                                 symbol_to_lot[current_trade.loc[row_number, 'Symbol']] * quantity)
                            brokarage = brokarage_dict['total_charges']

                            if current_trade.loc[row_number, 'trade_type'] == 'short':
                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'entry_price'] - \
                                    current_trade.loc[row_number, 'exit_price']
                                current_trade.loc[row_number, 'profit'] = (current_trade.loc[row_number, 'profit'] \
                                        * symbol_to_lot[current_trade.loc[row_number, 'Symbol']]) * quantity
                                self.send_message(account, current_trade.loc[row_number, 'Symbol'], \
                                                  f"Commodity Strategy 🎯: Short p/l 💰 is {current_trade.loc[row_number, 'profit']:.2f} | Brokerage 💸 is {brokarage:.2f}", \
                                                current_trade.loc[row_number, 'profit'], brokarage, quantity)
                            else:
                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'exit_price'] - \
                                    current_trade.loc[row_number, 'entry_price']
                                current_trade.loc[row_number, 'profit'] = (current_trade.loc[row_number, 'profit'] \
                                        * symbol_to_lot[current_trade.loc[row_number, 'Symbol']]) * quantity
                                self.send_message(account, current_trade.loc[row_number, 'Symbol'], \
                                                  f"Commodity Strategy 🎯: Long p/l 💰 is {current_trade.loc[row_number, 'profit']:.2f} | Brokerage 💸 is {brokarage:.2f}", \
                                                current_trade.loc[row_number, 'profit'], brokarage, quantity)

                            current_trade.to_csv(file_name, index=False)
                            # Reset retry count on successful completion
                            self.reset_retry_count(account, current_trade.loc[row_number, 'Symbol'], 'exit')
                        elif status == "Open":
                            # Order is still pending, keep waiting
                            logging.info(f"Exit order {order_id} still pending for {account} {current_trade.loc[row_number, 'Symbol']}")
                        elif status in ("Rejected", "InvalidID"):
                            # Rejected or invalid order ID.
                            # Check if the position is already gone (exit was executed despite the error).
                            trading_symbol = current_trade.loc[row_number, 'Symbol']
                            trade_type = current_trade.loc[row_number, 'trade_type']

                            pos_type, _ = place_order.get_commodity_position(account, trading_symbol, trade_type)
                            if pos_type is None:
                                # Position is gone — exit was executed successfully
                                logging.info(f"Exit order status={status} but position is gone for {account} {trading_symbol}. Marking as closed.")
                                current_trade.loc[row_number, 'exit_order_state'] = 'close'
                                current_trade.loc[row_number, 'state'] = 'closed'
                                current_trade.to_csv(file_name, index=False)
                                self.reset_retry_count(account, trading_symbol, 'exit')
                            else:
                                # Position still exists — retry the exit order
                                new_order_id, retry_success = self.retry_rejected_order(
                                    account, trading_symbol, 'exit', place_order, account_details, current_trade, row_number
                                )

                                if retry_success:
                                    current_trade.loc[row_number, 'exit_orderid'] = new_order_id
                                    logging.info(f"Exit order retry successful for {account} {trading_symbol}, new order ID: {new_order_id}")
                                    current_trade.to_csv(file_name, index=False)
                                else:
                                    retry_key = f"{account}_{trading_symbol}_exit"
                                    current_retries = self.order_retry_count.get(retry_key, 0)
                                    if current_retries >= self.MAX_RETRY_ATTEMPTS:
                                        self.send_message(account, trading_symbol, "Exit order rejected and all retries failed", 0)
                                        current_trade.loc[row_number, 'exit_order_state'] = 'error'
                                        current_trade.to_csv(file_name, index=False)
                                    else:
                                        logging.info(f"Exit order retry deferred to next hour for {account} {trading_symbol}")
                        elif status == "NotFound":
                            # Order not in orderbook — check if position was already closed.
                            trading_symbol = current_trade.loc[row_number, 'Symbol']
                            trade_type = current_trade.loc[row_number, 'trade_type']

                            pos_type, _ = place_order.get_commodity_position(account, trading_symbol, trade_type)
                            if pos_type is None:
                                logging.info(f"Exit order {order_id} not in orderbook and position is gone for {account} {trading_symbol}. Marking as closed.")
                                current_trade.loc[row_number, 'exit_order_state'] = 'close'
                                current_trade.loc[row_number, 'state'] = 'closed'
                                current_trade.to_csv(file_name, index=False)
                                self.reset_retry_count(account, trading_symbol, 'exit')
                            else:
                                # Position still open — original order never went through, retry next cycle
                                logging.warning(f"Exit order {order_id} not in orderbook but position still open for {account} {trading_symbol}. Retrying.")
                                new_order_id, retry_success = self.retry_rejected_order(
                                    account, trading_symbol, 'exit', place_order, account_details, current_trade, row_number
                                )
                                if retry_success:
                                    current_trade.loc[row_number, 'exit_orderid'] = new_order_id
                                    logging.info(f"Exit order retry successful for {account} {trading_symbol}, new order ID: {new_order_id}")
                                    current_trade.to_csv(file_name, index=False)
                                else:
                                    retry_key = f"{account}_{trading_symbol}_exit"
                                    current_retries = self.order_retry_count.get(retry_key, 0)
                                    if current_retries >= self.MAX_RETRY_ATTEMPTS:
                                        self.send_message(account, trading_symbol, "Exit order not found and all retries failed. Manual check required.", 0)
                                        current_trade.loc[row_number, 'exit_order_state'] = 'error'
                                        current_trade.to_csv(file_name, index=False)
                                    else:
                                        logging.info(f"Exit order retry deferred to next hour for {account} {trading_symbol}")
                        elif status == -1:
                            # API error — log but don't mark as error yet; will retry next cycle
                            logging.warning(f"API error checking exit order {order_id} for {account} {current_trade.loc[row_number, 'Symbol']}, will retry next cycle")
                        else:
                            # Unknown status, log warning but don't mark as error yet
                            logging.warning(f"Unknown exit order status '{status}' for {account} {current_trade.loc[row_number, 'Symbol']}, continuing to wait")
                    except Exception as e:
                        logging.error(f"Exception in check_trade_executed: {str(e)}")



    def execute_strategy(self, accounts, place_order, account_details):
        try:

            current_time_dt = datetime.now().time()

            if current_time_dt < time(9, 4):
                t.sleep(20)
                return

            self.check_trade_executed(accounts, place_order, account_details)

            # If last_executed_hour is same as current hour, then return
            if self.last_executed_hour == current_time_dt.hour:
                return

            start_loop_time = datetime.now()

            # Get the configuration
            #configuration.ConfigurationLoader.load_configuration()


            # Loop for all symbol and start with the last processed symbol
            for s in symbol:
                if self.last_proccesed_symbol is not None and s != self.last_proccesed_symbol:
                    continue

                print(f"Processing symbol: {s}")
                logger.info(f"Processing symbol: {s}")

                # Check if MCX is open
                exchange_data = ExchangeData()
                exchange_data_var = exchange_data.is_mcx_open()
                if exchange_data_var is False:
                    self.last_executed_hour = current_time_dt.hour
                    logger.info("MCX is closed")
                    print("MCX is closed")
                    return

                # Get the historic data
                historic_data = self.commodity_data.historic_data(s)

                historic_data_daily = self.commodity_data.historic_data(s, daily=True)

                # drop last row
                historic_data_daily = historic_data_daily.drop(historic_data_daily.tail(1).index)

                if historic_data is None:
                    logging.error(f"Error getting historic data for symbol: {s}")
                    print(f"Error getting historic data for symbol: {s}")
                    return

                if historic_data_daily is None:
                    logging.error(f"Error getting historic daily data for symbol: {s}")
                    print(f"Error getting historic daily data for symbol: {s}")
                    return

                # Get alligator and fractal
                alligator, bullish, bearish = self.get_alligator_fractal(historic_data)

                alligator_daily, _, _ = self.get_alligator_fractal(historic_data_daily)

                print(f"Symbol: {s}, Alligator: {alligator}, Bullish: {bullish}, Bearish: {bearish}")
                logger.info(f"Symbol: {s}, Alligator: {alligator}, Bullish: {bullish}, Bearish: {bearish}")
                logger.info(f"Symbol: {s}, Alligator Daily: {alligator_daily}")

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

                    quantity = account_details.loc[(account_details['Account'] == account) & (account_details['Symbol'] == s)]['quantity'].values[0]

                    if alligator_daily[0] == "uptrend":
                        if current_trade is None or row_number == -1:
                            if historic_data.iloc[-1]['close'] > bullish and alligator[0] == "uptrend":
                                print("Enter long trade")
                                logging.info("Enter long trade")
                                order_id, expiry = place_order.place_buy_orders_commodity(account, s, quantity, None)
                                new_row = {'Symbol': s, 'expiry': expiry, 'trade_type': 'long',
                                           'entry_time': datetime.now(), 'entry_price': historic_data.iloc[-1]['close'],
                                           'enter_orderid': order_id, 'enter_order_state': 'open_pending' if order_id != -1 else 'error', 'exit_orderid': 0,
                                           'exit_order_state': 'none', 'exit_time': '', 'exit_price': '', 'state': 'open', 'profit': ''}
                                current_trade = pd.concat([current_trade, pd.DataFrame([new_row])], ignore_index=True)  # Note the square brackets
                                trade_entered = True
                    elif alligator_daily[0] == "downtrend":
                        if current_trade is None or row_number == -1:
                            if historic_data.iloc[-1]['close'] < bearish and alligator[0] == "downtrend":
                                print ("Enter short trade")
                                logging.info("Enter short trade")
                                order_id, expiry = place_order.place_sell_orders_commodity(account, s, quantity, None)
                                new_row = {'Symbol': s, 'expiry': expiry, 'trade_type': 'short', \
                                        'entry_time': datetime.now(), 'entry_price': historic_data.iloc[-1]['close'], \
                                        'enter_orderid' : order_id, 'enter_order_state': 'open_pending' if order_id != -1 else 'error', 'exit_orderid': 0, 'exit_order_state': 'none', \
                                            'exit_time': '', 'exit_price': '', 'state': 'open', 'profit': ''}
                                current_trade = pd.concat([current_trade, pd.DataFrame([new_row])], ignore_index=True)  # Note the square brackets
                                trade_entered = True

                    # Exit the trade.
                    if trade_entered is False and alligator[0] == "downtrend":
                        if current_trade is not None and row_number != -1 and current_trade.shape[0] != 0:
                            if current_trade.loc[row_number, 'trade_type'] == 'long':
                                print(historic_data.iloc[-1]['Date'])
                                current_trade.loc[row_number, 'exit_time'] = historic_data.iloc[-1]['Date']
                                current_trade.loc[row_number, 'exit_price'] = historic_data.iloc[-1]['close']
                                current_trade.loc[row_number, 'state'] = 'closed'

                                # Fake close if previous error
                                if current_trade.loc[row_number, 'enter_order_state'] == 'error' or \
                                   current_trade.loc[row_number, 'exit_order_state'] == 'error':
                                    logging.info(f"Fake closing long trade for {account} {s} due to previous error state")
                                    current_trade.loc[row_number, 'exit_order_state'] = 'fake_closed'
                                else:
                                    print ("Exit long trade " +  str(historic_data.iloc[-1]['close']) + str(current_trade.loc[row_number, 'exit_price']))
                                    logging.info("Exit long trade")
                                    order_id, expiry = place_order.place_sell_orders_commodity(account, s, quantity, current_trade.loc[row_number, 'expiry'])
                                    current_trade.loc[row_number, 'exit_orderid'] = order_id
                                    current_trade.loc[row_number, 'exit_order_state'] = 'close_pending'

                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'exit_price'] - \
                                    current_trade.loc[row_number, 'entry_price']
                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'profit'] \
                                    * symbol_to_lot[s]

                    elif trade_entered is False and alligator[0] == "uptrend":
                        if current_trade is not None and row_number != -1 and current_trade.shape[0] != 0:
                            if current_trade.loc[row_number, 'trade_type'] == 'short':
                                print(historic_data.iloc[-1]['Date'])
                                current_trade.loc[row_number, 'exit_time'] = historic_data.iloc[-1]['Date']
                                current_trade.loc[row_number, 'exit_price'] = historic_data.iloc[-1]['close']
                                current_trade.loc[row_number, 'state'] = 'closed'

                                # Fake close if previous error
                                if current_trade.loc[row_number, 'enter_order_state'] == 'error' or \
                                   current_trade.loc[row_number, 'exit_order_state'] == 'error':
                                    logging.info(f"Fake closing short trade for {account} {s} due to previous error state")
                                    current_trade.loc[row_number, 'exit_order_state'] = 'fake_closed'
                                else:
                                    print ("Exit short trade " +  str(historic_data.iloc[-1]['close']) + str(current_trade.loc[row_number, 'exit_price']))
                                    logging.info("Exit short trade")
                                    order_id, expiry = place_order.place_buy_orders_commodity(account, s, quantity, current_trade.loc[row_number, 'expiry'])
                                    current_trade.loc[row_number, 'exit_orderid'] = order_id
                                    current_trade.loc[row_number, 'exit_order_state'] = 'close_pending'

                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'entry_price'] - \
                                    current_trade.loc[row_number, 'exit_price']
                                current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'profit'] \
                                    * symbol_to_lot[s]
                    else:
                        if current_trade is not None and row_number != -1 and current_trade.shape[0] != 0:
                            print(historic_data.iloc[-1]['Date'])
                            current_trade.loc[row_number, 'exit_time'] = historic_data.iloc[-1]['Date']
                            current_trade.loc[row_number, 'exit_price'] = historic_data.iloc[-1]['close']
                            current_trade.loc[row_number, 'state'] = 'closed'

                            # Fake close if previous error
                            if current_trade.loc[row_number, 'enter_order_state'] == 'error' or \
                               current_trade.loc[row_number, 'exit_order_state'] == 'error':
                                logging.info(f"Fake closing trade for {account} {s} due to previous error state")
                                current_trade.loc[row_number, 'exit_order_state'] = 'fake_closed'
                            else:
                                if current_trade.loc[row_number, 'trade_type'] == 'short':
                                    current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'entry_price'] - \
                                        current_trade.loc[row_number, 'exit_price']
                                    order_id, expiry = place_order.place_buy_orders_commodity(account, s, quantity, current_trade.loc[row_number, 'expiry'])
                                    print ("Exit short trade " +  str(historic_data.iloc[-1]['close']) + str(current_trade.loc[row_number, 'exit_price']))
                                    logging.info("Exit short trade")
                                else:
                                    order_id, expiry = place_order.place_sell_orders_commodity(account, s, quantity, current_trade.loc[row_number, 'expiry'])
                                    current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'exit_price'] - \
                                        current_trade.loc[row_number, 'entry_price']
                                    print ("Exit long trade " +  str(historic_data.iloc[-1]['close']) + str(current_trade.loc[row_number, 'exit_price']))
                                    logging.info("Exit long trade")
                                current_trade.loc[row_number, 'exit_orderid'] = order_id
                                current_trade.loc[row_number, 'exit_order_state'] = 'close_pending'

                            current_trade.loc[row_number, 'profit'] = current_trade.loc[row_number, 'profit'] \
                                    * symbol_to_lot[s]

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

                if time_difference > 120:
                    print("Excedding 120 seconds so exit")
                    return

        except Exception as e:
            logging.error(f"Error executing execute_strategy: {e}")
            traceback.print_exc()

    def send_message(self, account, symbol_msg, error_message, compute_profit_loss, brokrage = 0, quantity = 0):
        # Create a unique key for this account+symbol+error combination
        error_key = f"{account}_{symbol_msg}_{hash(error_message)}"
        current_time = datetime.now()

        # Check if we've sent this error recently (within 1 hour) - only for error messages
        if compute_profit_loss == 0 and brokrage == 0:  # This indicates it's an error message
            if error_key in self.last_error_sent:
                time_since_last = current_time - self.last_error_sent[error_key]
                if time_since_last < timedelta(hours=1):
                    # Skip sending, but still log
                    logging.info(f"Throttling error message for {account} {symbol_msg}: {error_message} (last sent {time_since_last} ago)")
                    return

            # Update the last sent time for error messages
            self.last_error_sent[error_key] = current_time
            logging.info(f"Error message sent for {account} {symbol_msg}: {error_message}")

        x = telegram_send_api()

        telegram_group = account + "_telegram"

        id3 = configuration.ConfigurationLoader.get_configuration().get(telegram_group)

        # Send profit loss over telegramsend send_message
        x.send_message(id3, f"{account} {symbol_msg} {error_message}")

        if brokrage != 0:
            pl_dict = {
                'Date': datetime.now().strftime("%Y-%m-%d"),
                'Account': account,
                'Symbol': symbol_msg,
                'Quantity': quantity,
                'NumberofTrade': 1,
                'TotalPNL': compute_profit_loss * 1,
                'Brokarge': brokrage,
                'CloseTime': datetime.now().strftime("%H:%M:%S"),
                'Stratergy': 'Commodity',
                'NetPNL': compute_profit_loss - 60 - brokrage
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
    #commodity_account_details = commodity_account_details.append({'Account': 'deepti', 'Symbol': 'GOLD', 'Quantity': 1}, ignore_index=True)

    place_order = PlaceOrder.PlaceOrder()  # Instantiate the PlaceOrder class
    place_order.init_account("deepti")
    place_order.init_account("leelu")

    # Get home directory
    cur_dir = Path.home()
    # Add /temp/data_collection to the home directory
    cur_dir = cur_dir / 'temp' / 'data_collection'
    # Create the directory if it does not exist
    cur_dir.mkdir(parents=True, exist_ok=True)

    #Change the current working directory to the directory
    os.chdir(cur_dir)

    commodity_stratergy = CommodityStratergy(['dummy', 'deepti', 'leelu'])
    print("Starting")
    commodity_stratergy.execute_strategy(['deepti'], place_order, commodity_account_details)
    print("Exiting 1    ")
    commodity_stratergy.execute_strategy(['deepti'], place_order, commodity_account_details)
    print("Exiting 2    ")
    commodity_stratergy.execute_strategy(['dummy'], place_order, commodity_account_details)
    print("Exiting 3    ")
    commodity_stratergy.execute_strategy(['dummy'], place_order, commodity_account_details)
    print("Exiting 4    ")
    commodity_stratergy.execute_strategy(['leelu'], place_order, commodity_account_details)
    print("Exiting 5    ")
    commodity_stratergy.execute_strategy(['leelu'], place_order, commodity_account_details)
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
