"""Module providing Nifty Positional Strategy"""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=W0105
# pylint: disable=W0621

import traceback
from datetime import datetime, time, timedelta
import os
import logging
import time as t
from typing import Optional
import pandas as pd
import TelegramSend
import configuration
from exchange_state import ExchangeData
logger = logging.getLogger(__name__)

ORDER_STATES = {
    'OPEN': {
        'INITIAL': 'open_pending',
        'COMPLETED': 'open',
        'FAILED': 'closed'
    },
    'CLOSE': {
        'INITIAL': 'close_pending',
        'COMPLETED': 'closed',
        'FAILED': 'open'  # Remains open if close fails
    }
}

class NiftyPositionalStrategy:
    def __init__(self, accounts):
        self.accounts = accounts
        self.symbol = "NIFTY"  # Fixed to NIFTY only
        self.nso_open = None
        self._cached_expiry: Optional[datetime] = None
        self._last_expiry_check: Optional[datetime] = None
        # Add new tracking variables
        self.last_execution_time = None
        self.EXECUTION_INTERVAL = timedelta(minutes=10)  # 10 minutes interval
        self.TRADE_COOLDOWN = timedelta(minutes=30)  # 30 minutes cooldown
        self.MAX_TRADES_PER_EXPIRY = 3

    def loss_limit(self):
        return -700  # Fixed for NIFTY


    def get_next_nifty_expiry(self):
        """
        Returns the next NIFTY expiry date, accounting for holidays.
        If Thursday is a holiday, uses Wednesday; if Wednesday is also a holiday, uses Tuesday, etc.
        Caches the result for the current expiry week.
        """
        today = datetime.now().date()

        # If we have a cached expiry and it's still valid for this week, return it
        if self._cached_expiry and self._last_expiry_check:
            # If today is before or on the cached expiry, and the cache was checked this week, use it
            if today <= self._cached_expiry.date() and (today - self._last_expiry_check.date()).days < 7:
                return self._cached_expiry

        # Find the next Thursday (expiry week)
        days_ahead = (3 - today.weekday()) % 7  # 3 = Thursday
        expiry_candidate = today + timedelta(days=days_ahead)
        ex = ExchangeData()  # Make sure to import ExchangeData

        # Check Thursday, then Wednesday, then Tuesday, then Monday
        for offset in range(0, 4):
            check_date = expiry_candidate - timedelta(days=offset)
            if not ex.is_nfo_holiday(check_date):
                expiry_datetime = datetime.combine(check_date, datetime.min.time())
                # Cache the result
                self._cached_expiry = expiry_datetime
                self._last_expiry_check = today
                return expiry_datetime

        # Fallback: if all are holidays, use Thursday
        expiry_datetime = datetime.combine(expiry_candidate, datetime.min.time())
        self._cached_expiry = expiry_datetime
        self._last_expiry_check = today
        return expiry_datetime

    def is_entry_time(self) -> bool:
        """Check if it's entry time (around 12 PM, 2 days before expiry)"""
        current_time = datetime.now().time()
        current_date = datetime.now().date()

        # Get next expiry (using cached value)
        next_expiry = self.get_next_nifty_expiry()
        expiry_date = next_expiry.date()

        # Calculate days until expiry
        days_to_expiry = (expiry_date - current_date).days

        # Check if it's 2 days before expiry
        if days_to_expiry == 2:
            # Entry window is true if after 11 AM
            is_entry_window = current_time >= time(11, 0)

            if is_entry_window:
                logging.info("Entry window active. Current time: %s, "
                           "Days to expiry: %d, "
                           "Next expiry: %s", current_time, days_to_expiry, expiry_date)
            return is_entry_window

        if days_to_expiry < 2:
            return True

        return False

    def should_exit_trade(self, option_chain_analyzer, sold_options_info):
        """
        Check exit conditions:
        1. Exit if ATM strike + ATM straddle sum is greater than our sold CE strike
        2. Exit if ATM strike - ATM straddle sum is less than our sold PE strike
        """
        try:
            # Get ATM straddle price and ATM strike
            atm_ce_price = option_chain_analyzer['atm_ce_price']
            atm_pe_price = option_chain_analyzer['atm_pe_price']
            atm_straddle_sum = atm_ce_price + atm_pe_price
            atm_strike = option_chain_analyzer['atm_strike']

            # Get our actually sold strangle strikes from the trade entry
            strangle_ce_strike = sold_options_info.iloc[-1]['strangle_ce_strike']
            strangle_pe_strike = sold_options_info.iloc[-1]['strangle_pe_strike']

            # Calculate the boundaries
            upper_boundary = atm_strike + atm_straddle_sum
            lower_boundary = atm_strike - atm_straddle_sum

            # Check if our sold strikes are breached by the boundaries
            if upper_boundary > strangle_ce_strike:
                logging.info(
                    f"Exiting trade - Upper boundary breach: "
                    f"ATM {atm_strike} + Straddle {atm_straddle_sum} = {upper_boundary} > "
                    f"CE Strike {strangle_ce_strike}"
                )
                return True

            if lower_boundary < strangle_pe_strike:
                logging.info(
                    f"Exiting trade - Lower boundary breach: "
                    f"ATM {atm_strike} - Straddle {atm_straddle_sum} = {lower_boundary} < "
                    f"PE Strike {strangle_pe_strike}"
                )
                return True

            return False

        except Exception as e:
            logging.error(f"Error in should_exit_trade: {str(e)}")
            traceback.print_exc()
            return False

    def execute_strategy(self, option_chain_analyzer, quantity, place_order_obj):
        """
        Execute strategy for all accounts

        Args:
            option_chain_analyzer: Dictionary containing option chain data
            quantity: Trade quantity
            place_order_obj: Order placement object
        """
        try:
            current_time = datetime.now()

            # Check execution interval (10 minutes)
            if self.last_execution_time and \
               (current_time - self.last_execution_time) < self.EXECUTION_INTERVAL:
                #logging.info("Skipping execution: Within 10-minute interval")
                return

            self.last_execution_time = current_time

            # Check NFO market status
            if not self._check_market_status():
                return

            # Loop through all accounts
            for account in self.accounts:
                try:
                    logging.info(f"Executing strategy for account: {account}")
                    self._execute_for_account(
                        account=account,
                        option_chain_analyzer=option_chain_analyzer,
                        quantity=quantity,
                        place_order_obj=place_order_obj
                    )
                except Exception as acc_error:
                    logging.error(f"Error executing strategy for account {account}: {str(acc_error)}")
                    self.send_error_message(account, str(acc_error))
                    continue  # Continue with next account even if one fails

        except Exception as e:
            logging.error(f"Error in strategy execution: {str(e)}")
            logging.error(traceback.format_exc())

    def _execute_for_account(self, account: str, option_chain_analyzer: dict,
                            quantity: int, place_order_obj):
        """
        Execute strategy for a single account

        Args:
            account: Trading account identifier
            option_chain_analyzer: Dictionary containing option chain data
            quantity: Trade quantity
            place_order_obj: Order placement object
        """
        if account not in self.accounts:
            raise ValueError(f"Error: Account '{account}' not valid. Choose from {self.accounts}")

        sold_options_file_path = self.get_sold_options_file_path(account)

        if os.path.exists(sold_options_file_path):
            existing_sold_options_info = self.read_existing_sold_options_info(sold_options_file_path)

            # Check for expiry day closing time
            if self.is_expiry_day_closing_time():
                if not existing_sold_options_info.empty and existing_sold_options_info.iloc[-1]['trade_state'] == 'open':
                    logging.info(f"Closing positions at expiry day 3:27 PM for account {account}")

                    # Set close prices to 0 for expiry day closing
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_ce_close_price'] = 0
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_pe_close_price'] = 0

                    # Close the positions
                    self._close_position(
                        existing_sold_options_info,
                        account,
                        quantity,
                        place_order_obj
                    )

                    # Compute and send P/L
                    ce_pl = (existing_sold_options_info.iloc[-1]['strangle_ce_price'] - 0) * quantity
                    pe_pl = (existing_sold_options_info.iloc[-1]['strangle_pe_price'] - 0) * quantity
                    total_pl = ce_pl + pe_pl

                    # Send P/L information via Telegram
                    pl_message = (
                        f"Expiry Day Closing P/L for {account}:\n"
                        f"CE P/L: {ce_pl:.2f}\n"
                        f"PE P/L: {pe_pl:.2f}\n"
                        f"Total P/L: {total_pl:.2f}"
                    )
                    telegram_api = TelegramSend.telegram_send_api()
                    telegram_group = account + "_telegram"
                    chat_id = configuration.ConfigurationLoader.get_configuration().get(telegram_group)
                    telegram_api.send_message(chat_id, pl_message)

                    return

            # Check number of trades for current expiry
            current_expiry = self.get_next_nifty_expiry().strftime("%Y-%m-%d")
            expiry_trades = existing_sold_options_info[
                existing_sold_options_info['expiry'] == current_expiry
            ]

            # Handle existing open positions
            if not expiry_trades.empty and expiry_trades.iloc[-1]['trade_state'] == 'open':
                # Update current prices and check exit conditions
                self._manage_open_position(
                    existing_sold_options_info,
                    option_chain_analyzer,
                    account,
                    quantity,
                    place_order_obj
                )
            else:
                if len(expiry_trades) >= self.MAX_TRADES_PER_EXPIRY:
                    logging.info(f"Maximum trades ({self.MAX_TRADES_PER_EXPIRY}) reached for account {account}, expiry {current_expiry}")
                    return

                # Check if last trade was closed recently (30-min cooldown)
                if not expiry_trades.empty and expiry_trades.iloc[-1]['trade_state'] == 'closed':
                    last_close_time = pd.to_datetime(expiry_trades.iloc[-1]['close_time'])
                    if (datetime.now() - last_close_time) < self.TRADE_COOLDOWN:
                        logging.info(f"Skipping execution for account {account}: Within 30-minute cooldown after previous trade")
                        return
                self._enter_new_position(
                    option_chain_analyzer,
                    account,
                    quantity,
                    place_order_obj
                )
        else:
            # First trade for this account/expiry
            if self.is_entry_time():
                self._enter_new_position(
                    option_chain_analyzer,
                    account,
                    quantity,
                    place_order_obj
                )

    def _check_market_status(self):
        """Check if NFO market is open"""
        if self.nso_open is None:
            exchange_data = ExchangeData()
            self.nso_open = exchange_data.is_nfo_open()
            if not self.nso_open:
                logging.info("NFO market is closed")
                return False
        return self.nso_open

    def _manage_open_position(self, existing_sold_options_info, option_chain_analyzer,
                            account, quantity, place_order_obj):
        """Manage existing open positions"""
        try:
            # Update current prices
            updates = {
                'strangle_ce_close_price': option_chain_analyzer['prev_ce_strangle_price'],
                'strangle_pe_close_price': option_chain_analyzer['prev_pe_strangle_price']
            }

            existing_sold_options_info = self.update_and_store(
                existing_sold_options_info,
                account,
                existing_sold_options_info.index[-1],
                updates
            )

            # Check exit conditions
            if self.should_exit_trade(option_chain_analyzer, existing_sold_options_info):
                self._close_position(
                    existing_sold_options_info,
                    account,
                    quantity,
                    place_order_obj
                )

        except Exception as e:
            logging.error(f"Error in managing open position: {str(e)}")
            logging.error(traceback.format_exc())
            raise

    def _enter_new_position(self, option_chain_analyzer, account, quantity, place_order_obj):
        """Enter new position if conditions are met"""
        sold_options_info = self.create_new_position(
            account,
            option_chain_analyzer['spot_price'],
            option_chain_analyzer,
            option_chain_analyzer['ce_strangle_strike'],
            option_chain_analyzer['pe_strangle_strike'],
            quantity,
            place_order_obj
        )

        if sold_options_info:
            existing_sold_options_info = pd.DataFrame([sold_options_info])
            self.store_sold_options_info(existing_sold_options_info, account)

    def get_sold_options_file_path(self, account):
        """Get file path using expiry date instead of current date"""
        expiry_date = self.get_next_nifty_expiry().strftime("%Y-%m-%d")
        return f"csv/nifty_pos_options_info_{expiry_date}_{account}.csv"

    def get_error_options_file_path(self, account):
        """Get error file path using expiry date instead of current date"""
        expiry_date = self.get_next_nifty_expiry().strftime("%Y-%m-%d")
        return f"csv/nifty_pos_options_info_error_{expiry_date}_{account}.csv"

    def get_option_price(self, option_chain_analyzer, option_type):
        """
        Get option price based on option type and market conditions

        Args:
            option_chain_analyzer: Dictionary containing option chain data
            option_type: String indicating option type ('CE' or 'PE')

        Returns:
            float: Option price
        """
        if option_type == 'CE':
            if option_chain_analyzer['pe_to_ce_ratio'] < 0.7:
                return option_chain_analyzer['ce_strangle_price']
            else:
                return option_chain_analyzer['ce_strangle_price']
        elif option_type == 'PE':
            if option_chain_analyzer['pe_to_ce_ratio'] > 1.4:
                return option_chain_analyzer['pe_strangle_price']
            else:
                return option_chain_analyzer['pe_strangle_price']
        return 0  # Return 0 for invalid option type

    def get_option_strike(self, option_chain_analyzer, option_type):
        """
        Get strike price based on option type

        Args:
            option_chain_analyzer: Dictionary containing option chain data
            option_type: String indicating option type ('CE' or 'PE')

        Returns:
            float: Strike price
        """
        if option_type == 'CE':
            return option_chain_analyzer['ce_strangle_strike']
        elif option_type == 'PE':
            return option_chain_analyzer['pe_strangle_strike']
        return 0  # Return 0 for invalid option type

    def create_new_position(self, account, spot_price, option_chain_analyzer,
                           ce_strike, pe_strike, quantity, place_order_obj):
        sold_options_info = {
            'account': account,
            'symbol': self.symbol,
            'spot_price': spot_price,
            'quantity': quantity,
            'strangle_ce_price': self.get_option_price(option_chain_analyzer, 'CE'),
            'strangle_pe_price': self.get_option_price(option_chain_analyzer, 'PE'),
            'trade_state': 'open',
            'open_time': datetime.now(),
            'close_time': None,
            'expiry': self.get_next_nifty_expiry().strftime("%Y-%m-%d"),
            'strangle_ce_strike': self.get_option_strike(option_chain_analyzer, 'CE'),
            'strangle_pe_strike': self.get_option_strike(option_chain_analyzer, 'PE'),
            'strangle_ce_close_price': self.get_option_price(option_chain_analyzer, 'CE'),
            'strangle_pe_close_price': self.get_option_price(option_chain_analyzer, 'PE'),
            'pe_open_order_id': -1,
            'ce_open_order_id': -1,
            'pe_close_order_id': -1,
            'ce_close_order_id': -1,
            'pe_open_state': 'open_pending',
            'ce_open_state': 'open_pending',
            'pe_close_state': 'None',
            'ce_close_state': 'None'
        }

        # Place orders based on PE/CE ratio
        if option_chain_analyzer['pe_to_ce_ratio'] < 0.7:
            # Bearish - Place only CE
            sold_options_info = self.place_ce_only(
                sold_options_info, account, ce_strike, quantity, place_order_obj
            )
        elif option_chain_analyzer['pe_to_ce_ratio'] > 1.4:
            # Bullish - Place only PE
            sold_options_info = self.place_pe_only(
                sold_options_info, account, pe_strike, quantity, place_order_obj
            )
        else:
            # Neutral - Place both
            sold_options_info = self.place_both_legs(
                sold_options_info, account, ce_strike, pe_strike, quantity, place_order_obj
            )

        return sold_options_info

    def place_ce_only(self, sold_options_info, account, ce_strike, quantity, place_order_obj):
        """Place only CE order"""
        sold_options_info['strangle_pe_price'] = -1
        sold_options_info['ce_open_order_id'] = place_order_obj.place_orders(
            account, ce_strike, 'CE', self.symbol, quantity, False)

        if sold_options_info['ce_open_order_id'] == -1:
            error_message = "Error in placing ce open order"
            self.send_error_message(account, error_message)
            return None

        sold_options_info['pe_open_order_id'] = -1
        sold_options_info['pe_open_state'] = 'closed'
        sold_options_info['ce_open_state'] = 'open_pending'
        return sold_options_info

    def place_pe_only(self, sold_options_info, account, pe_strike, quantity, place_order_obj):
        """Place only PE order"""
        sold_options_info['strangle_ce_price'] = -1
        sold_options_info['pe_open_order_id'] = place_order_obj.place_orders(
            account, pe_strike, 'PE', self.symbol, quantity, False)

        if sold_options_info['pe_open_order_id'] == -1:
            error_message = "Error in placing pe open order"
            self.send_error_message(account, error_message)
            return None

        sold_options_info['ce_open_order_id'] = -1
        sold_options_info['ce_open_state'] = 'closed'
        sold_options_info['pe_open_state'] = 'open_pending'
        return sold_options_info

    def place_both_legs(self, sold_options_info, account, ce_strike, pe_strike, quantity, place_order_obj):
        """Place both CE and PE orders"""
        # Place CE order
        sold_options_info['ce_open_order_id'] = place_order_obj.place_orders(
            account, ce_strike, 'CE', self.symbol, quantity, False)
        if sold_options_info['ce_open_order_id'] == -1:
            error_message = "Error in placing ce open order"
            self.send_error_message(account, error_message)
            return None

        t.sleep(1)

        # Place PE order
        sold_options_info['pe_open_order_id'] = place_order_obj.place_orders(
            account, pe_strike, 'PE', self.symbol, quantity, False)
        if sold_options_info['pe_open_order_id'] == -1:
            error_message = "Error in placing pe open order"
            self.send_error_message(account, error_message)
            return None

        sold_options_info['ce_open_state'] = 'open_pending'
        sold_options_info['pe_open_state'] = 'open_pending'
        return sold_options_info

    # Include other utility methods from FarSellStrategy with necessary modifications
    # Such as close_trade, store_sold_options_info, compute_profit_loss, etc.

    def update_trade_status(self, existing_sold_options_info, ce_close_id, pe_close_id, option_chain_analyzer):
        """Update trade status after closing orders"""
        if ce_close_id != -1:
            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'ce_close_state'] = 'close_pending'
            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'ce_close_order_id'] = ce_close_id

        if pe_close_id != -1:
            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'pe_close_state'] = 'close_pending'
            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'pe_close_order_id'] = pe_close_id

        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_ce_close_price'] = \
            option_chain_analyzer['prev_ce_strangle_price']
        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_pe_close_price'] = \
            option_chain_analyzer['prev_pe_strangle_price']

        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'trade_state'] = 'closing'
        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'close_time'] = datetime.now()

        # When closing positions
        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'pe_close_state'] = 'close_pending'
        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'ce_close_state'] = 'close_pending'

        if existing_sold_options_info.iloc[-1]['strangle_ce_price'] == -1:
            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'ce_close_state'] = 'closed'

        if existing_sold_options_info.iloc[-1]['strangle_pe_price'] == -1:
            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'pe_close_state'] = 'closed'

    def check_if_trade_is_executed(self, account, place_order_obj):
        error_path = self.get_error_options_file_path(account)
        if os.path.exists(error_path):
            return False

        error_in_order = False
        error_message = ""
        sold_options_file_path = self.get_sold_options_file_path(account)

        if os.path.exists(sold_options_file_path):
            existing_sold_options_info = self.read_existing_sold_options_info(sold_options_file_path)

            # Check PE open order
            if existing_sold_options_info.iloc[-1]['pe_open_state'] == 'open_pending':
                order_status, price = place_order_obj.order_status(account,
                            existing_sold_options_info.iloc[-1]['pe_open_order_id'],
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_pe_price'])
                if order_status == 'Complete':
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'pe_open_state'] = 'open'
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_pe_price'] = price
                else:
                    error_in_order = True
                    error_message = error_message + "Error in pe open order"

            # Check CE open order
            if existing_sold_options_info.iloc[-1]['ce_open_state'] == 'open_pending':
                t.sleep(3)
                order_status, price = place_order_obj.order_status(account,
                            existing_sold_options_info.iloc[-1]['ce_open_order_id'],
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_ce_price'])
                if order_status == 'Complete':
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'ce_open_state'] = 'open'
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_ce_price'] = price
                else:
                    error_in_order = True
                    error_message = error_message + "Error in ce open order"

            # Check PE close order
            if existing_sold_options_info.iloc[-1]['pe_close_state'] == 'close_pending':
                order_status, price = place_order_obj.order_status(account,
                        existing_sold_options_info.iloc[-1]['pe_close_order_id'],
                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_pe_close_price'])
                if order_status == 'Complete':
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'pe_close_state'] = 'closed'
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_pe_close_price'] = price
                else:
                    error_in_order = True
                    error_message = error_message + "Error in pe close order"

            # Check CE close order
            if existing_sold_options_info.iloc[-1]['ce_close_state'] == 'close_pending':
                t.sleep(3)
                order_status, price = place_order_obj.order_status(account,
                        existing_sold_options_info.iloc[-1]['ce_close_order_id'],
                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_ce_close_price'])
                if order_status == 'Complete':
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'ce_close_state'] = 'closed'
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_ce_close_price'] = price
                else:
                    error_in_order = True
                    error_message = error_message + "Error in ce close order"

            if error_in_order:
                self.store_sold_options_info(existing_sold_options_info, account)
                self.send_error_message(account, error_message)
                return False

            self.store_sold_options_info(existing_sold_options_info, account)
            return True

        return True

    def close_trade(self, account, pe_strike, ce_strike, strangle_pe_price, strangle_ce_price, place_order_obj, qty):
        """Close the trade"""
        logging.info(f"Closing the trade for account {account}")
        ce_order_id = -1
        pe_order_id = -1

        if strangle_pe_price != -1:
            pe_order_id = place_order_obj.close_orders(account, pe_strike, 'PE', self.symbol, qty)
            if pe_order_id == -1:
                error_message = "Error in placing pe close order"
                self.send_error_message(account, error_message)
                return -1, -1

        if strangle_ce_price != -1:
            ce_order_id = place_order_obj.close_orders(account, ce_strike, 'CE', self.symbol, qty)
            if ce_order_id == -1:
                error_message = "Error in placing ce close order"
                self.send_error_message(account, error_message)
                return -1, -1

        return ce_order_id, pe_order_id

    def read_existing_sold_options_info(self, file_path):
        """
        Read existing trade information from CSV file

        Args:
            file_path: Path to the CSV file

        Returns:
            pd.DataFrame: DataFrame containing trade information
        """
        try:
            if os.path.exists(file_path):
                df = pd.read_csv(file_path)

                # Convert timestamp strings back to datetime objects
                timestamp_columns = ['open_time', 'close_time']
                for col in timestamp_columns:
                    if col in df.columns:
                        df[col] = pd.to_datetime(df[col])

                return df
            else:
                logging.warning(f"File not found: {file_path}")
                return pd.DataFrame()  # Return empty DataFrame if file doesn't exist

        except Exception as e:
            logging.error(f"Error reading trade information: {str(e)}")
            logging.error(traceback.format_exc())
            return pd.DataFrame()

    def store_sold_options_info(self, info: pd.DataFrame, account: str):
        """
        Store trade information to CSV file

        Args:
            info: DataFrame containing trade information
            account: Trading account identifier
        """
        try:
            file_path = self.get_sold_options_file_path(account)
            info.to_csv(file_path, index=False)
            logging.info(f"Trade information stored in {file_path}")
        except Exception as e:
            logging.error(f"Error storing trade information: {str(e)}")
            logging.error(traceback.format_exc())
            raise

    def update_and_store(self, existing_sold_options_info: pd.DataFrame, account: str,
                         index: int, updates: dict):
        """
        Update trade information and persist to file

        Args:
            existing_sold_options_info: DataFrame containing all trades
            account: Trading account identifier
            index: Index of the trade to update
            updates: Dictionary of column-value pairs to update
        """
        try:
            # Update the specified columns
            for column, value in updates.items():
                existing_sold_options_info.loc[index, column] = value

            # Store updated information
            self.store_sold_options_info(existing_sold_options_info, account)

            return existing_sold_options_info
        except Exception as e:
            logging.error(f"Error updating trade information: {str(e)}")
            logging.error(traceback.format_exc())
            raise

    def _close_position(self, existing_sold_options_info, account, quantity,
                       place_order_obj):
        """Close open positions"""
        try:
            # Check if it's expiry day closing
            is_expiry_closing = self.is_expiry_day_closing_time()

            ce_close_id, pe_close_id = self.close_trade(
                account,
                existing_sold_options_info.iloc[-1]['strangle_pe_strike'],
                existing_sold_options_info.iloc[-1]['strangle_ce_strike'],
                existing_sold_options_info.iloc[-1]['strangle_pe_price'],
                existing_sold_options_info.iloc[-1]['strangle_ce_price'],
                place_order_obj,
                quantity
            )

            updates = {
                'ce_close_order_id': ce_close_id,
                'pe_close_order_id': pe_close_id,
                'ce_close_state': 'close_pending' if ce_close_id != -1 else 'closed',
                'pe_close_state': 'close_pending' if pe_close_id != -1 else 'closed',
                'trade_state': 'closing',
                'close_time': datetime.now()
            }

            # If it's expiry day closing, set close prices to 0
            if is_expiry_closing:
                updates['strangle_ce_close_price'] = 0
                updates['strangle_pe_close_price'] = 0

            self.update_and_store(
                existing_sold_options_info,
                account,
                existing_sold_options_info.index[-1],
                updates
            )

        except Exception as e:
            logging.error(f"Error in closing position: {str(e)}")
            logging.error(traceback.format_exc())
            raise

    def send_error_message(self, account: str, error_message: str):
        """
        Send error message via Telegram and handle error file creation

        Args:
            account: Trading account identifier
            error_message: Error message to be sent
        """
        try:
            # Get file paths
            sold_options_file_path = self.get_sold_options_file_path(account)
            error_file_path = self.get_error_options_file_path(account)

            # Initialize Telegram API
            telegram_api = TelegramSend.telegram_send_api()
            telegram_group = account + "_telegram"
            chat_id = configuration.ConfigurationLoader.get_configuration().get(telegram_group)

            # Send error message via Telegram
            error_msg = f"Nifty Positional Strategy critical error: {account} {self.symbol} {error_message}"
            telegram_api.send_message(chat_id, error_msg)

            # Handle error file creation and renaming
            if os.path.exists(sold_options_file_path):
                # Rename existing file to error file
                os.rename(
                    sold_options_file_path,
                    sold_options_file_path.replace("sold_options_info", "sold_options_info_error")
                )
            else:
                # Create empty error file
                with open(error_file_path, 'w', encoding='utf-8') as _:
                    pass

            logging.error(error_msg)

        except Exception as e:
            logging.error(f"Error in sending error message: {str(e)}")
            logging.error(traceback.format_exc())

    def is_expiry_day_closing_time(self) -> bool:
        """Check if it's 3:27 PM on expiry day"""
        current_time = datetime.now()
        current_date = current_time.date()
        expiry_date = self.get_next_nifty_expiry().date()

        # Check if today is expiry day
        if current_date == expiry_date:
            # Check if time is 3:27 PM
            return current_time.time() >= time(15, 27)
        return False

"""
import PlaceOrder

import os
from pathlib import Path
import logging_config  # This sets up the logging
from OptionChainData import OptionChainData

strike = {"NIFTY": 23000}

# Test code
if __name__ == '__main__':
    coomodity_path = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vQn_xcX-C2JGmkNQAj_DmrHhpfj0d0EESIN-JiE0zsrQ4guej5Y8FwHvDSCks7pdMMyE0UtkdTR_-bZ/pub?output=csv'
    commodity_account_details = pd.read_csv(coomodity_path)

    print(commodity_account_details)

    symbol = "NIFTY"

    place_order = PlaceOrder.PlaceOrder()  # Instantiate the PlaceOrder class
    place_order.init_account("deepti")
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

    commodity_stratergy = NiftyPositionalStrategy(commodity_account_details['Account'].unique())
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

    # Get option chain data for NIFTY
    symbol = "NIFTY"
    option_chain_analyzer = OptionChainData(symbol)
    option_chain_info = option_chain_analyzer.get_option_chain_info(0, 0, 0, symbol)

    # Execute strategy with correct parameters
    for account in commodity_account_details['Account'].unique():
        quantity = commodity_account_details[commodity_account_details['Account'] == account]['quantity'].values[0]
        commodity_stratergy.execute_strategy(option_chain_info, quantity, place_order)
"""
