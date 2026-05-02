"""Module providing Nifty Positional Strategy"""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=C0302
# pylint: disable=W0105
# pylint: disable=W0621

import traceback
from datetime import datetime, time, timedelta
import os
import logging
import time as t
import pandas as pd
import TelegramSend
import configuration
import brokrage_calculator
from exchange_state import ExchangeData
from OptionChainData import OptionChainData
logger = logging.getLogger(__name__)

INDEX_SEQ = ["NIFTY", "SENSEX"]  # Use list to maintain order

STRATERGY_SEQ = {
    "fr",
    "as"
}

class NiftyPositionalStrategy:
    def __init__(self, accounts):
        self.accounts = accounts
        self.symbol = "NIFTY"  # Fixed to NIFTY only
        self.nso_open = None
        # Add new tracking variables
        self.last_execution_time = None
        self.EXECUTION_INTERVAL = timedelta(minutes=5)  # 10 minutes interval
        self.TRADE_COOLDOWN = timedelta(minutes=30)  # 30 minutes cooldown
        self.MAX_TRADES_PER_EXPIRY = 2
        self.nifty_date_pd = None
        self.sensex_date_pd = None
        self.stratergy = "fr"  # Fixed to 'fr' strategy
        # Error message throttling - track last sent time for each account+error combination
        self.last_error_sent = {}  # Format: {f"{account}_{error_hash}": datetime}
        # Order retry tracking - track retry attempts for each order
        self.order_retry_count = {}  # Format: {f"{account}_{symbol}_{order_type}": count}
        self.MAX_RETRY_ATTEMPTS = 3  # Maximum retry attempts for rejected orders
        self.get_nift_sensex_expiry()

    def loss_limit(self):
        return -700  # Fixed for NIFTY


    def get_next_nifty_expiry(self):
        # self.nifty_date_pd is self.nifty_date_pd, want to return in format self.nifty_date_pd
        if self.symbol == "NIFTY":
            if self.nifty_date_pd is not None and len(self.nifty_date_pd) > 0:
                return self.nifty_date_pd[0]
            logging.error("Nifty expiry date not found.")
            return None
        elif self.symbol == "SENSEX":
            if self.sensex_date_pd is not None and len(self.sensex_date_pd) > 0:
                return self.sensex_date_pd[0]
            logging.error("SENSEX expiry date not found.")
            return None
        else:
            logging.error("Invalid symbol: %s. Expected 'NIFTY' or 'SENSEX'.", self.symbol)
            return None

    def is_entry_time(self) -> bool:
        """Check if it's entry time (around 12 PM, 2 days before expiry)"""
        current_time = datetime.now().time()
        current_date = datetime.now().date()

        # Get next expiry (using cached value)
        next_expiry = self.get_next_nifty_expiry()
        if next_expiry is None:
            logging.error("Could not get next expiry date, skipping entry time check")
            return False
        expiry_date = next_expiry.date()

        # Calculate days until expiry
        days_to_expiry = (expiry_date - current_date).days

        # Define entry days based on symbol (use explicit symbol names instead of set indexing)
        dates_to_expiry = 0
        if self.symbol == "SENSEX":
            dates_to_expiry = 2  # Enter 1 day before expiry
        elif self.symbol == "NIFTY":
            dates_to_expiry = 1  # Enter 2 days before expiry
        else:
            logging.error("Unknown symbol: %s", self.symbol)
            return False

        logging.debug("Entry time check for %s: days_to_expiry=%s, required_days=%s, current_time=%s", self.symbol, days_to_expiry, dates_to_expiry, current_time)

        # Check if it's the correct number of days before expiry
        if days_to_expiry == dates_to_expiry:
            # Entry window is true if after 11 AM
            is_entry_window = current_time >= time(11, 0)

            if is_entry_window:
                logging.info("Entry window active. Current time: %s, "
                           "Days to expiry: %d, "
                           "Next expiry: %s", current_time, days_to_expiry, expiry_date)
            else:
                logging.info("Entry window not active yet. Current time: %s, "
                           "Entry starts at 11:00 AM", current_time)
            return is_entry_window

        # If fewer days than required, allow entry (catch-up logic)
        if days_to_expiry < dates_to_expiry:
            logging.info("Allowing entry for %s as we're past the ideal entry window (days_to_expiry=%s < required=%s)", self.symbol, days_to_expiry, dates_to_expiry)
            return True

        logging.debug("Not entry time for %s: days_to_expiry=%s, required_days=%s", self.symbol, days_to_expiry, dates_to_expiry)
        return False

    def should_exit_trade(self, option_chain_analyzer, sold_options_info, account):
        """
        Check exit conditions:
        1. Exit if ATM strike + ATM straddle sum is greater than our sold CE strike
        2. Exit if ATM strike - ATM straddle sum is less than our sold PE strike
        """
        try:
            # Check if option_chain_analyzer is None
            if option_chain_analyzer is None:
                logging.warning("Option chain analyzer is None, cannot check exit conditions")
                return False

            # Safely access required dictionary keys with defaults
            atm_ce_price = option_chain_analyzer.get('atm_current_ce_price')
            atm_pe_price = option_chain_analyzer.get('atm_current_pe_price')
            atm_strike = option_chain_analyzer.get('atm_strike')

            # Validate all required data is present
            if atm_ce_price is None or atm_pe_price is None or atm_strike is None:
                logging.warning("Missing required price data for exit check: CE: %s, PE: %s, ATM: %s", atm_ce_price, atm_pe_price, atm_strike)
                return False

            # Check if DataFrame is empty or lacks required data
            if sold_options_info.empty:
                logging.warning("No trade data available to check exit conditions")
                return False

            # Get our sold strangle strikes
            strangle_ce_strike = sold_options_info.iloc[-1]['strangle_ce_strike']
            strangle_pe_strike = sold_options_info.iloc[-1]['strangle_pe_strike']

            # Check if we even have both legs open
            ce_is_active = sold_options_info.iloc[-1]['strangle_ce_price'] != -1
            pe_is_active = sold_options_info.iloc[-1]['strangle_pe_price'] != -1

            # Calculate exit boundaries
            atm_straddle_sum = atm_ce_price + atm_pe_price
            upper_boundary = atm_strike + atm_straddle_sum
            lower_boundary = atm_strike - atm_straddle_sum

            # if stratergy is as then upper_boundary and lower_boundary will be 75 %
            if self.stratergy == "as":
                upper_boundary = atm_strike + (atm_straddle_sum * 0.75)
                lower_boundary = atm_strike - (atm_straddle_sum * 0.75)

            # Log boundary information for debugging
            logging.debug("Exit check - Upper: %s, CE Strike: %s, Lower: %s, PE Strike: %s", upper_boundary, strangle_ce_strike, lower_boundary, strangle_pe_strike)

            if self.stratergy == "fr":
                # Only check relevant boundaries based on which legs are active
                if ce_is_active and upper_boundary > strangle_ce_strike:
                    logging.info(
                        f"Exiting trade - Upper boundary breach: "
                        f"ATM {atm_strike} + Straddle {atm_straddle_sum} = {upper_boundary} > "
                        f"CE Strike {strangle_ce_strike}"
                    )
                    return True

                if pe_is_active and lower_boundary < strangle_pe_strike:
                    logging.info(
                        f"Exiting trade - Lower boundary breach: "
                        f"ATM {atm_strike} - Straddle {atm_straddle_sum} = {lower_boundary} < "
                        f"PE Strike {strangle_pe_strike}"
                    )
                    return True
            elif self.stratergy == "as":
                print(strangle_ce_strike, strangle_pe_strike, upper_boundary, lower_boundary)
                # For 'as' strategy, check if either boundary is breached
                if pe_is_active and upper_boundary < strangle_ce_strike:
                    logging.info(
                        f"Exiting trade - Upper boundary breach: "
                        f"ATM {atm_strike} + Straddle {atm_straddle_sum} * 0.75 = {upper_boundary} > "
                        f"CE Strike {strangle_ce_strike}"
                    )
                    return True

                if ce_is_active and lower_boundary > strangle_pe_strike:
                    logging.info(
                        f"Exiting trade - Lower boundary breach: "
                        f"ATM {atm_strike} - Straddle {atm_straddle_sum} * 0.75 = {lower_boundary} < "
                        f"PE Strike {strangle_pe_strike}"
                    )
                    return True

            # Check stop loss exit condition
            if self.should_exit_on_stop_loss(sold_options_info, option_chain_analyzer, account):
                logging.info("Stop loss triggered for stratergy: %s account: %s symbol: %s", self.stratergy, account, self.symbol)
                return True

            return False

        except Exception as e:
            logging.error("Error in should_exit_trade: %s", e)
            logging.error(traceback.format_exc())
            return False

    def compute_current_profit_loss(self, sold_options_info, option_chain_analyzer):
        """
        Compute current unrealized P/L points for open positions.
        
        P/L = (current_ltp - sell_price) for each active leg
        Positive = loss (option value increased from when we sold)
        Negative = profit (option value decreased from when we sold)
        
        Args:
            sold_options_info: DataFrame with current trade info
            option_chain_analyzer: Dictionary with current LTP values
            
        Returns:
            float: Current P/L in points (negative = profit, positive = loss)
        """
        try:
            if sold_options_info.empty:
                return 0

            trade = sold_options_info.iloc[-1]
            ce_loss = 0
            pe_loss = 0

            # Get current LTP for our sold strikes from option_chain_analyzer
            current_ce_ltp = option_chain_analyzer.get('prev_ce_strangle_price', 0)
            current_pe_ltp = option_chain_analyzer.get('prev_pe_strangle_price', 0)

            # For CE leg
            if trade['strangle_ce_price'] != -1:
                sell_price_ce = trade['strangle_ce_price']
                ce_loss = current_ce_ltp - sell_price_ce
                logging.info("CE P/L: sell=%s, current=%s, loss=%s", sell_price_ce, current_ce_ltp, ce_loss)

            # For PE leg
            if trade['strangle_pe_price'] != -1:
                sell_price_pe = trade['strangle_pe_price']
                pe_loss = current_pe_ltp - sell_price_pe
                logging.info("PE P/L: sell=%s, current=%s, loss=%s", sell_price_pe, current_pe_ltp, pe_loss)

            # Sum up the losses for active legs
            total_loss_points = ce_loss + pe_loss

            logging.info("Total loss points: %.2f", total_loss_points)
            return total_loss_points

        except Exception as e:
            logging.error("Error computing current P/L points: %s", e)
            logging.error(traceback.format_exc())
            return 0

    def get_last_trade_strikes(self, symbol, strategy):
        """
        Get existing trade strikes from any account for this symbol/strategy.
        
        Args:
            symbol: index symbol
            strategy: fr or as
            
        Returns:
            tuple: (atm_strike, ce_strike, pe_strike)
        """
        for account in self.accounts:
            # Construct file path manually as we haven't set self.stratergy/symbol yet globally
            expiry_date = self.get_next_nifty_expiry().strftime("%Y-%m-%d")
            file_path = f"csv/nifty_pos_options_info_{expiry_date}_{account}_{symbol}_{strategy}.csv"

            if os.path.exists(file_path):
                df = self.read_existing_sold_options_info(file_path)
                if df is not None and not df.empty:
                    last_row = df.iloc[-1]
                    if last_row['trade_state'] == 'open':
                        atm_strike = last_row.get('atm_strike', 0)
                        ce_strike = last_row.get('strangle_ce_strike', 0)
                        pe_strike = last_row.get('strangle_pe_strike', 0)
                        return atm_strike, ce_strike, pe_strike

        logging.info("No existing open trade found for symbol: %s strategy: %s", symbol, strategy)
        return 0, 0, 0

    def should_exit_on_stop_loss(self, sold_options_info, option_chain_analyzer, account):
        """
        Check if current loss exceeds stop loss limits per lot.
        
        Stop loss thresholds (per lot):
        - fr strategy: 400
        - as strategy: 2000
        
        Args:
            sold_options_info: DataFrame with current trade info
            option_chain_analyzer: Dictionary with current LTP values
            
        Returns:
            bool: True if stop loss triggered, False otherwise
        """
        try:
            if sold_options_info.empty:
                return False

            # Only check open trades
            if sold_options_info.iloc[-1]['trade_state'] != 'open':
                return False

            # Define stop loss limits per lot for each strategy
            stop_loss_limits = {
                'fr': 400,
                'as': 2000
            }

            stop_loss_limit = stop_loss_limits.get(self.stratergy, 400)

            # Compute current loss in points
            loss_points = self.compute_current_profit_loss(sold_options_info, option_chain_analyzer)

            # Convert points to money based on lot size
            lot_size = 65 if self.symbol == "NIFTY" else 20
            current_loss_amount = loss_points * lot_size

            logging.info("Stop loss check - Account: %s, Strategy: %s, Symbol: %s, "
                         "Loss pts: %.2f, Loss amt: %.2f, Limit: %s",
                         account, self.stratergy, self.symbol, loss_points, current_loss_amount, stop_loss_limit)

            # If loss exceeds threshold, trigger exit
            if current_loss_amount > stop_loss_limit:
                logging.info(
                    "Stop loss triggered - Strategy: %s, Symbol: %s, "
                    "Current loss: %.2f (pts: %.2f) > Limit: %s",
                    self.stratergy, self.symbol, current_loss_amount, loss_points, stop_loss_limit
                )
                return True

            return False

        except Exception as e:
            logging.error("Error in stop loss check: %s", e)
            logging.error(traceback.format_exc())
            return False

    def execute_strategy(self, place_order_obj, account_details):
        """
        Execute strategy for all accounts

        Args:
            place_order_obj: Order placement object
            account_details: DataFrame containing account-specific trading details with columns
                            ['Account', 'Symbol', 'quantity']

        Returns:
            bool: True if execution was successful, False otherwise
        """
        try:
            # Validate input parameters
            if account_details is None or account_details.empty:
                logging.error("Account details DataFrame is empty or None")
                return False

            required_columns = ['Account', 'Symbol', 'quantity', 'stratergy']
            if not all(col in account_details.columns for col in required_columns):
                logging.error("Account details missing required columns: %s", required_columns)
                return False

            current_time = datetime.now()

            # Check execution interval
            if self.last_execution_time and \
               (current_time - self.last_execution_time) < self.EXECUTION_INTERVAL:
                return False

            self.last_execution_time = current_time

            # Check NFO market status
            if not self._check_market_status():
                logging.info("Market is closed, skipping execution")
                return False

            for INDEX_SEQ_KEY in INDEX_SEQ:
                self.symbol = INDEX_SEQ_KEY

                # Create OptionChainData object
                try:
                    option_chain_obj = OptionChainData(INDEX_SEQ_KEY)
                    option_chain_obj.set_bse_expiry_date_pd(self.sensex_date_pd)
                except Exception as e:
                    logging.error("Failed to create OptionChainData: %s", e)
                    return False

                # Loop through strategies
                execution_results = []
                for STRATERGY_SEQ_KEY in STRATERGY_SEQ:
                    self.stratergy = STRATERGY_SEQ_KEY

                    # Try to get existing strikes to fetch LTP
                    prev_atm, prev_ce, prev_pe = self.get_last_trade_strikes(INDEX_SEQ_KEY, STRATERGY_SEQ_KEY)

                    try:
                        # Fetch analyzer with specific strikes if we have an open trade
                        option_chain_analyzer = option_chain_obj.get_option_chain_info(prev_atm, prev_ce, prev_pe, INDEX_SEQ_KEY)
                    except Exception as e:
                        logging.error("Failed to get data from OptionChainData: %s", e)
                        continue

                    # Loop through all accounts
                    for account in self.accounts:
                        try:
                            logging.debug("Executing strategy for account: %s %s %s", account, INDEX_SEQ_KEY, STRATERGY_SEQ_KEY)
                            account_data = account_details[
                                (account_details['Account'] == account) &
                                (account_details['Symbol'] == INDEX_SEQ_KEY) &
                                 (account_details['stratergy'] == STRATERGY_SEQ_KEY)
                            ]

                            if account_data.empty:
                                logging.warning("No trading data found for account %s  %s", account, self.symbol)
                                continue

                            quantity = account_data['quantity'].values[0]

                            result = self._execute_for_account(
                                account=account,
                                option_chain_analyzer=option_chain_analyzer,
                                quantity=quantity,
                                symbol=INDEX_SEQ_KEY,
                                place_order_obj=place_order_obj
                            )
                            execution_results.append(result)

                        except Exception as acc_error:
                            logging.error("Error executing strategy for account %s: %s", account, acc_error)
                            self.send_error_message(account, str(acc_error))
                            execution_results.append(False)
                            continue

            return all(execution_results)

        except Exception as e:
            logging.error("Error in strategy execution: %s", e)
            logging.error(traceback.format_exc())
            return False

    def _execute_for_account(self, account: str, option_chain_analyzer: dict,
                            quantity: int, symbol: str, place_order_obj):
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

        sold_options_file_path = self.get_sold_options_file_path(account, symbol)

        if os.path.exists(sold_options_file_path):
            existing_sold_options_info = self.read_existing_sold_options_info(sold_options_file_path)

            # Check and update order status for any pending orders
            self.check_if_trade_is_executed(account, place_order_obj)

            # IMPORTANT: Reload the file to get updated trade states
            existing_sold_options_info = self.read_existing_sold_options_info(sold_options_file_path)

            # Define expiry_trades BEFORE using it in any code path
            current_expiry = self.get_next_nifty_expiry().strftime("%Y-%m-%d")
            expiry_trades = existing_sold_options_info[
                existing_sold_options_info['expiry'] == current_expiry
            ]

            # Now check for expiry day closing time
            if self.is_expiry_day_closing_time():
                if not existing_sold_options_info.empty and existing_sold_options_info.iloc[-1]['trade_state'] == 'open':
                    # Check if we've already processed expiry day closing for this account today
                    expiry_closing_flag_file = f"/tmp/expiry_closing_processed_{datetime.now().strftime('%Y-%m-%d')}_{account}_{self.symbol}_{self.stratergy}.flag"

                    if os.path.exists(expiry_closing_flag_file):
                        logging.info(f"Expiry day closing already processed for account {account} today")
                        return True

                    logging.info(f"Closing positions at expiry day 3:27 PM for account {account}")

                    spot_price = option_chain_analyzer['spot_price']

                    # Set close prices to 0 for expiry day closing
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_ce_close_price'] = \
                        max(spot_price - existing_sold_options_info.iloc[-1]['strangle_ce_strike'], 0 )
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_pe_close_price'] = \
                        max(existing_sold_options_info.iloc[-1]['strangle_pe_strike'] - spot_price, 0 )

                    # Change the tarde state to closed
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'trade_state'] = 'closed'
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'close_time'] = datetime.now()
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'ce_close_state'] = 'closed'
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'pe_close_state'] = 'closed'
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'ce_close_order_id'] = -1
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'pe_close_order_id'] = -1

                    # Create flag file to indicate processing is complete for today
                    with open(expiry_closing_flag_file, 'w', encoding='utf-8') as f:
                        f.write(f"Processed at {datetime.now()}")

                # Compute and send P/L (now expiry_trades is defined)
                total_ce_pl = 0
                total_pe_pl = 0
                total_hedge_pl = 0
                total_pl = 0

                # Loop through all trades for this expiry
                for _, trade in expiry_trades.iterrows():
                    # Include both closed and open trades for expiry day P/L
                    if trade['trade_state'] not in ['closed', 'open']:
                        continue

                    # Use quantity from each trade record
                    trade_qty = trade['quantity']

                    # For CE leg (sold option: profit = sell_price - close_price)
                    if trade['strangle_ce_price'] != -1:
                        ce_close_price = trade['strangle_ce_close_price']
                        # On expiry day, if no close price (None or NaN), assume 0 (expired worthless)
                        if pd.isna(ce_close_price):
                            ce_close_price = 0
                        ce_pl = (trade['strangle_ce_price'] - ce_close_price) * trade_qty
                        total_ce_pl += ce_pl

                    # For PE leg (sold option: profit = sell_price - close_price)
                    if trade['strangle_pe_price'] != -1:
                        pe_close_price = trade['strangle_pe_close_price']
                        # On expiry day, if no close price (None or NaN), assume 0 (expired worthless)
                        if pd.isna(pe_close_price):
                            pe_close_price = 0
                        pe_pl = (trade['strangle_pe_price'] - pe_close_price) * trade_qty
                        total_pe_pl += pe_pl

                    # For hedge CE (bought option: profit = close_price - buy_price)
                    hedge_ce_price = trade.get('hedge_ce_price', -1)
                    if hedge_ce_price is not None and hedge_ce_price != -1 and not pd.isna(hedge_ce_price):
                        hedge_ce_close = trade.get('hedge_ce_close_price', 0)
                        if pd.isna(hedge_ce_close):
                            hedge_ce_close = 0  # Expired worthless
                        hedge_ce_pl = (hedge_ce_close - hedge_ce_price) * trade_qty
                        total_hedge_pl += hedge_ce_pl

                    # For hedge PE (bought option: profit = close_price - buy_price)
                    hedge_pe_price = trade.get('hedge_pe_price', -1)
                    if hedge_pe_price is not None and hedge_pe_price != -1 and not pd.isna(hedge_pe_price):
                        hedge_pe_close = trade.get('hedge_pe_close_price', 0)
                        if pd.isna(hedge_pe_close):
                            hedge_pe_close = 0  # Expired worthless
                        hedge_pe_pl = (hedge_pe_close - hedge_pe_price) * trade_qty
                        total_hedge_pl += hedge_pe_pl

                total_pl = total_ce_pl + total_pe_pl + total_hedge_pl
                if self.symbol == "NIFTY":
                    total_pl = total_pl * 65
                    total_hedge_pl = total_hedge_pl * 65
                    total_ce_pl = total_ce_pl * 65
                    total_pe_pl = total_pe_pl * 65
                elif self.symbol == "SENSEX":
                    total_pl = total_pl * 20
                    total_hedge_pl = total_hedge_pl * 20
                    total_ce_pl = total_ce_pl * 20
                    total_pe_pl = total_pe_pl * 20

                # Only send P/L message if we haven't already processed expiry closing today
                pl_flag_file = f"/tmp/pl_message_sent_{datetime.now().strftime('%Y-%m-%d')}_{account}_{self.symbol}_{self.stratergy}.flag"
                if not os.path.exists(pl_flag_file):
                    # Send consolidated P/L information via Telegram
                    pl_message = (
                        f"Expiry Day Consolidated P/L for {account}:\n"
                        f"Symbol: {self.symbol}\n"
                        f"Strategy: {self.stratergy}\n"
                        f"CE P/L: {total_ce_pl:.2f}\n"
                        f"PE P/L: {total_pe_pl:.2f}\n"
                        f"Hedge P/L: {total_hedge_pl:.2f}\n"
                        f"Total P/L: {total_pl:.2f}\n"
                        f"Number of trades: {len(expiry_trades)}"
                    )
                    telegram_api = TelegramSend.telegram_send_api()
                    telegram_group = account + "_telegram"
                    chat_id = configuration.ConfigurationLoader.get_configuration().get(telegram_group)
                    telegram_api.send_message(chat_id, pl_message)

                    telegram_api.send_file(chat_id, sold_options_file_path)

                    # Compute brokerage
                    lot_size = 65 if self.symbol == "NIFTY" else 20
                    total_brokerage = 0
                    for _, trade in expiry_trades.iterrows():
                        if trade['trade_state'] not in ['closed', 'open']:
                            continue
                        trade_qty = trade['quantity']
                        if trade['strangle_ce_price'] != -1:
                            ce_close = trade['strangle_ce_close_price'] if not pd.isna(trade['strangle_ce_close_price']) else 0
                            total_brokerage += brokrage_calculator.calculate_equity_options(
                                ce_close, trade['strangle_ce_price'], trade_qty * lot_size)['total_charges']
                        if trade['strangle_pe_price'] != -1:
                            pe_close = trade['strangle_pe_close_price'] if not pd.isna(trade['strangle_pe_close_price']) else 0
                            total_brokerage += brokrage_calculator.calculate_equity_options(
                                pe_close, trade['strangle_pe_price'], trade_qty * lot_size)['total_charges']

                    # Save to consolidated PNL
                    pl_dict = {
                        'Date': datetime.now().strftime("%Y-%m-%d"),
                        'Account': account,
                        'Symbol': self.symbol,
                        'Quantity': quantity,
                        'NumberofTrade': len(expiry_trades),
                        'TotalPNL': total_pl,
                        'Brokarge': total_brokerage,
                        'CloseTime': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        'Stratergy': 'NiftyPositional',
                        'NetPNL': total_pl - total_brokerage,
                    }
                    current_month = datetime.now().strftime("%m")
                    file_name = f"pnl/consolidated_pnl_{current_month}.csv"
                    if os.path.exists(file_name):
                        df_pnl = pd.read_csv(file_name)
                        df_pnl = pd.concat([df_pnl, pd.DataFrame([pl_dict])], ignore_index=True)
                        df_pnl.to_csv(file_name, index=False)
                    else:
                        pd.DataFrame([pl_dict]).to_csv(file_name, index=False)

                    # Create flag file to prevent duplicate P/L messages
                    with open(pl_flag_file, 'w', encoding='utf-8') as f:
                        f.write(f"P/L message sent at {datetime.now()}")
                return True

            # Check number of trades for current expiry
            current_expiry = self.get_next_nifty_expiry().strftime("%Y-%m-%d")
            expiry_trades = existing_sold_options_info[
                existing_sold_options_info['expiry'] == current_expiry
            ]

            # Handle existing open positions
            # Check for ANY active trade state - not just 'open'
            active_states = ['open', 'open_pending', 'closing']
            if not expiry_trades.empty and expiry_trades.iloc[-1]['trade_state'] in active_states:
                if expiry_trades.iloc[-1]['trade_state'] == 'open':
                    # Only manage positions that are fully open
                    self._manage_open_position(
                        existing_sold_options_info,
                        option_chain_analyzer,
                        account,
                        quantity,
                        place_order_obj
                    )
                else:
                    logging.info(f"Trade for account {account} is in {expiry_trades.iloc[-1]['trade_state']} state. Waiting for completion.")
            else:
                if len(expiry_trades) >= self.MAX_TRADES_PER_EXPIRY:
                    logging.info(f"Maximum trades ({self.MAX_TRADES_PER_EXPIRY}) reached for account {account}, expiry {current_expiry}")
                    return True

                # Check if last trade was closed recently (30-min cooldown)
                if not expiry_trades.empty and expiry_trades.iloc[-1]['trade_state'] == 'closed':
                    last_close_time = pd.to_datetime(expiry_trades.iloc[-1]['close_time'])

                    # Don't enter into new trade on expiry day
                    if self.is_expiry_day():
                        logging.info(f"Skipping execution for account {account}: Today is expiry day")
                        return True

                    if (datetime.now() - last_close_time) < self.TRADE_COOLDOWN:
                        logging.info(f"Skipping execution for account {account}: Within 30-minute cooldown after previous trade")
                        return True
                self._enter_new_position(
                    option_chain_analyzer,
                    account,
                    quantity,
                    place_order_obj,
                    existing_sold_options_info
                )
        else:
            # First trade for this account/expiry
            if self.is_entry_time():
                logging.info(f"File not found for account {account}, creating new position")
                self._enter_new_position(
                    option_chain_analyzer,
                    account,
                    quantity,
                    place_order_obj,
                    None
                )

        return True

    def _check_market_status(self):
        """Check if NFO market is open"""
        current_time = datetime.now().time()

        if current_time < time(9, 15):
            # Market is closed before 9 AM
            return False

        # Special case for expiry day closing
        if self.is_expiry_day() and current_time >= time(15, 25) and current_time <= time(15, 35):
            # Allow execution during expiry closing window
            logging.info("Allowing execution for expiry day closing window")
            return True

        if current_time > time(15, 30):
            self.nso_open = False
            return False

        # Normal market hours check
        if self.nso_open is None:
            exchange_data = ExchangeData()
            self.nso_open = exchange_data.is_nfo_open()
            if not self.nso_open:
                logging.info("NFO market is closed")
                return False

        return self.nso_open

    def is_expiry_day(self) -> bool:
        """Check if today is expiry day"""
        current_date = datetime.now().date()
        expiry_date = self.get_next_nifty_expiry().date()
        return current_date == expiry_date

    def is_hedge_time(self) -> bool:
        """Check if current time is after 2:50 PM for end-of-day hedge placement"""
        current_time = datetime.now().time()
        return current_time >= time(14, 50)

    def _enter_hedge_for_open_position(self, existing_sold_options_info, account, quantity, place_order_obj):
        """Place hedge orders for an existing open position at end of day (after 2:50 PM)"""
        try:
            last_trade = existing_sold_options_info.iloc[-1]

            # Check if CE main position is open and CE hedge is not yet placed
            ce_needs_hedge = (last_trade['ce_open_state'] == 'open' and
                            last_trade.get('hedge_ce_open_state', 'None') == 'None')

            # Check if PE main position is open and PE hedge is not yet placed
            pe_needs_hedge = (last_trade['pe_open_state'] == 'open' and
                            last_trade.get('hedge_pe_open_state', 'None') == 'None')

            if not ce_needs_hedge and not pe_needs_hedge:
                return  # No hedges needed

            hedge_ce_strike = last_trade.get('hedge_ce_strike')
            hedge_pe_strike = last_trade.get('hedge_pe_strike')

            # Place CE hedge if needed
            if ce_needs_hedge and hedge_ce_strike is not None:
                logging.info(f"EOD: Placing CE hedge BUY order at strike {hedge_ce_strike} for {account}")
                hedge_ce_order_id = place_order_obj.buy_hedge_orders(
                    account, hedge_ce_strike, 'CE', self.symbol, quantity, False)

                if hedge_ce_order_id == -1:
                    error_message = f"EOD Hedge CE Order Failed | Strike: {hedge_ce_strike} | Qty: {quantity}"
                    self.send_error_message(account, error_message)
                else:
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'hedge_ce_order_id'] = hedge_ce_order_id
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'hedge_ce_open_state'] = 'open_pending'
                    logging.info(f"EOD: CE hedge order placed for {account}, order ID: {hedge_ce_order_id}")

            t.sleep(1)

            # Place PE hedge if needed
            if pe_needs_hedge and hedge_pe_strike is not None:
                logging.info(f"EOD: Placing PE hedge BUY order at strike {hedge_pe_strike} for {account}")
                hedge_pe_order_id = place_order_obj.buy_hedge_orders(
                    account, hedge_pe_strike, 'PE', self.symbol, quantity, False)

                if hedge_pe_order_id == -1:
                    error_message = f"EOD Hedge PE Order Failed | Strike: {hedge_pe_strike} | Qty: {quantity}"
                    self.send_error_message(account, error_message)
                else:
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'hedge_pe_order_id'] = hedge_pe_order_id
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'hedge_pe_open_state'] = 'open_pending'
                    logging.info(f"EOD: PE hedge order placed for {account}, order ID: {hedge_pe_order_id}")

            # Save updated info
            self.store_sold_options_info(existing_sold_options_info, account)

        except Exception as e:
            logging.error(f"Error placing end-of-day hedges: {str(e)}")
            logging.error(traceback.format_exc())

    def _manage_open_position(self, existing_sold_options_info, option_chain_analyzer,
                            account, quantity, place_order_obj):
        """Manage existing open positions"""
        try:
            # Add defensive check for option_chain_analyzer
            if option_chain_analyzer is None:
                logging.warning(f"Option chain analyzer is None for account {account}. Skipping price updates.")
                return

            # Note: Close prices should only be set when positions are actually closed
            # Current market prices are available in option_chain_analyzer for exit condition checks
            # No need to update close prices here as they should remain None until actual closing

            # Check exit conditions
            if self.should_exit_trade(option_chain_analyzer, existing_sold_options_info, account):
                self._close_position(
                    existing_sold_options_info,
                    account,
                    quantity,
                    place_order_obj
                )
                return  # Position closed, no need to place hedges

            # Check if it's time to place end-of-day hedges (after 2:50 PM, but not on expiry day)
            if self.is_hedge_time() and not self.is_expiry_day():
                self._enter_hedge_for_open_position(
                    existing_sold_options_info,
                    account,
                    quantity,
                    place_order_obj
                )

        except Exception as e:
            logging.error(f"Error in managing open position: {str(e)}")
            logging.error(traceback.format_exc())
            raise

    def _enter_new_position(self, option_chain_analyzer, account, quantity, place_order_obj, existing_sold_options_info):
        """Enter new position if conditions are met"""
        sold_options_info = self.create_new_position(
            account,
            option_chain_analyzer['spot_price'],
            option_chain_analyzer,
            quantity,
            place_order_obj
        )

        if sold_options_info:
            if existing_sold_options_info is None or existing_sold_options_info.empty:
                existing_sold_options_info = pd.DataFrame([sold_options_info])
            else:
                existing_sold_options_info = pd.concat([existing_sold_options_info, pd.DataFrame([sold_options_info])], ignore_index=True)
            self.store_sold_options_info(existing_sold_options_info, account)

    def get_sold_options_file_path(self, account, symbol):
        """Get file path using expiry date instead of current date"""
        expiry_date = self.get_next_nifty_expiry().strftime("%Y-%m-%d")
        return f"csv/nifty_pos_options_info_{expiry_date}_{account}_{symbol}_{self.stratergy}.csv"

    def get_error_options_file_path(self, account, symbol):
        """Get error file path using expiry date instead of current date"""
        expiry_date = self.get_next_nifty_expiry().strftime("%Y-%m-%d")
        return f"csv/nifty_pos_options_info_error_{expiry_date}_{account}_{symbol}_{self.stratergy}.csv"

    def get_option_price(self, option_chain_analyzer, option_type):
        """
        Get option price based on option type and market conditions

        Args:
            option_chain_analyzer: Dictionary containing option chain data
            option_type: String indicating option type ('CE' or 'PE')

        Returns:
            float: Option price
        """
        if self.stratergy == 'fr':
            if option_type == 'CE':
                return option_chain_analyzer['ce_strangle_price']
            if option_type == 'PE':
                return option_chain_analyzer['pe_strangle_price']
        elif self.stratergy == 'as':
            if option_type == 'CE':
                return option_chain_analyzer['atm_current_ce_price']
            if option_type == 'PE':
                return option_chain_analyzer['atm_current_pe_price']
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
        if self.stratergy == 'fr':
            if option_type == 'CE':
                return option_chain_analyzer['ce_strangle_strike']
            elif option_type == 'PE':
                return option_chain_analyzer['pe_strangle_strike']
        elif self.stratergy == 'as':
            if option_type == 'CE':
                return option_chain_analyzer['atm_strike']
            elif option_type == 'PE':
                return option_chain_analyzer['atm_strike']
        return 0  # Return 0 for invalid option type

    def get_hedge_offset(self):
        """Get hedge offset based on symbol: NIFTY=500, SENSEX=1500"""
        return 500 if self.symbol == "NIFTY" else 1500

    def create_new_position(self, account, spot_price, option_chain_analyzer,
                           quantity, place_order_obj):
        ce_strike = self.get_option_strike(option_chain_analyzer, 'CE')
        pe_strike = self.get_option_strike(option_chain_analyzer, 'PE')
        hedge_offset = self.get_hedge_offset()

        sold_options_info = {
            'account': account,
            'symbol': self.symbol,
            'spot_price': spot_price,
            'quantity': quantity,
            'strangle_ce_price': self.get_option_price(option_chain_analyzer, 'CE'),
            'strangle_pe_price': self.get_option_price(option_chain_analyzer, 'PE'),
            'trade_state': 'open_pending',
            'open_time': datetime.now(),
            'close_time': None,
            'expiry': self.get_next_nifty_expiry().strftime("%Y-%m-%d"),
            'strangle_ce_strike': ce_strike,
            'strangle_pe_strike': pe_strike,
            'atm_strike': option_chain_analyzer['atm_strike'],
            'strangle_ce_close_price': None,
            'strangle_pe_close_price': None,
            'pe_open_order_id': -1,
            'ce_open_order_id': -1,
            'pe_close_order_id': -1,
            'ce_close_order_id': -1,
            'pe_open_state': 'open_pending',
            'ce_open_state': 'open_pending',
            'pe_close_state': 'None',
            'ce_close_state': 'None',
            # Hedge fields - OTM options to protect short strangle
            'hedge_ce_strike': ce_strike + hedge_offset,  # Buy CE further OTM
            'hedge_pe_strike': pe_strike - hedge_offset,  # Buy PE further OTM
            'hedge_ce_price': -1,         # Buy price for CE hedge
            'hedge_pe_price': -1,         # Buy price for PE hedge
            'hedge_ce_order_id': -1,
            'hedge_pe_order_id': -1,
            'hedge_ce_open_state': 'None',
            'hedge_pe_open_state': 'None',
            'hedge_ce_close_price': None,
            'hedge_pe_close_price': None,
            'hedge_ce_close_order_id': -1,
            'hedge_pe_close_order_id': -1,
            'hedge_ce_close_state': 'None',
            'hedge_pe_close_state': 'None',
        }

        # Get PE/CE ratio for decision
        pe_to_ce_ratio = option_chain_analyzer['pe_to_ce_ratio']

        # Log decision parameters
        logging.info(f"Nifty Positional: {account} {self.symbol} ({self.stratergy}) - "
                    f"PE/CE Ratio: {pe_to_ce_ratio:.2f}, ATM: {option_chain_analyzer['atm_strike']}, "
                    f"CE Strike: {ce_strike}, PE Strike: {pe_strike}, Qty: {quantity}")

        # Place orders based on PE/CE ratio
        if pe_to_ce_ratio < 0.7:
            # Bearish - Place only CE
            logging.info(f"Nifty Positional: {account} {self.symbol} ({self.stratergy}) - "
                        f"BEARISH (ratio {pe_to_ce_ratio:.2f} < 0.7) → Placing CE only at {ce_strike}")
            sold_options_info = self.place_ce_only(
                sold_options_info, account, ce_strike, quantity, place_order_obj
            )
        elif pe_to_ce_ratio > 1.4:
            # Bullish - Place only PE
            logging.info(f"Nifty Positional: {account} {self.symbol} ({self.stratergy}) - "
                        f"BULLISH (ratio {pe_to_ce_ratio:.2f} > 1.4) → Placing PE only at {pe_strike}")
            sold_options_info = self.place_pe_only(
                sold_options_info, account, pe_strike, quantity, place_order_obj
            )
        else:
            # Neutral - Place both
            logging.info(f"Nifty Positional: {account} {self.symbol} ({self.stratergy}) - "
                        f"NEUTRAL (0.7 <= ratio {pe_to_ce_ratio:.2f} <= 1.4) → Placing both legs")
            sold_options_info = self.place_both_legs(
                sold_options_info, account, ce_strike, pe_strike, quantity, place_order_obj
            )

        return sold_options_info

    def place_ce_only(self, sold_options_info, account, ce_strike, quantity, place_order_obj):
        """Place only CE order (hedge will be placed at end of day)"""
        sold_options_info['strangle_pe_price'] = -1

        # Sell CE main position
        logging.info(f"Placing CE SELL order at strike {ce_strike}")
        sold_options_info['ce_open_order_id'] = place_order_obj.place_orders(
            account, ce_strike, 'CE', self.symbol, quantity, False)

        if sold_options_info['ce_open_order_id'] == -1:
            error_message = f"Entry CE Order Failed | Strike: {ce_strike} | Qty: {quantity} | Bearish"
            self.send_error_message(account, error_message)
            return None

        sold_options_info['pe_open_order_id'] = -1
        sold_options_info['pe_open_state'] = 'closed'
        sold_options_info['ce_open_state'] = 'open_pending'
        # Hedges will be placed at end of day (after 2:50 PM)
        sold_options_info['hedge_ce_open_state'] = 'None'
        sold_options_info['hedge_pe_open_state'] = 'closed'  # PE hedge not needed since we're only selling CE
        sold_options_info['hedge_pe_price'] = -1
        return sold_options_info

    def place_pe_only(self, sold_options_info, account, pe_strike, quantity, place_order_obj):
        """Place only PE order (hedge will be placed at end of day)"""
        sold_options_info['strangle_ce_price'] = -1

        # Sell PE main position
        logging.info(f"Placing PE SELL order at strike {pe_strike}")
        sold_options_info['pe_open_order_id'] = place_order_obj.place_orders(
            account, pe_strike, 'PE', self.symbol, quantity, False)

        if sold_options_info['pe_open_order_id'] == -1:
            error_message = f"Entry PE Order Failed | Strike: {pe_strike} | Qty: {quantity} | Bullish"
            self.send_error_message(account, error_message)
            return None

        sold_options_info['ce_open_order_id'] = -1
        sold_options_info['ce_open_state'] = 'closed'
        sold_options_info['pe_open_state'] = 'open_pending'
        # Hedges will be placed at end of day (after 2:50 PM)
        sold_options_info['hedge_pe_open_state'] = 'None'
        sold_options_info['hedge_ce_open_state'] = 'closed'  # CE hedge not needed since we're only selling PE
        sold_options_info['hedge_ce_price'] = -1
        return sold_options_info

    def place_both_legs(self, sold_options_info, account, ce_strike, pe_strike, quantity, place_order_obj):
        """Place both CE and PE orders (hedges will be placed at end of day)"""
        # Sell CE main position
        logging.info(f"Placing CE SELL order at strike {ce_strike}")
        sold_options_info['ce_open_order_id'] = place_order_obj.place_orders(
            account, ce_strike, 'CE', self.symbol, quantity, False)
        if sold_options_info['ce_open_order_id'] == -1:
            error_message = f"Entry CE Order Failed | Strike: {ce_strike} | Qty: {quantity} | Neutral"
            self.send_error_message(account, error_message)
            return None

        t.sleep(1)

        # Sell PE main position
        logging.info(f"Placing PE SELL order at strike {pe_strike}")
        sold_options_info['pe_open_order_id'] = place_order_obj.place_orders(
            account, pe_strike, 'PE', self.symbol, quantity, False)
        if sold_options_info['pe_open_order_id'] == -1:
            error_message = f"Entry PE Order Failed | Strike: {pe_strike} | Qty: {quantity} | Neutral"
            self.send_error_message(account, error_message)
            return None

        sold_options_info['ce_open_state'] = 'open_pending'
        sold_options_info['pe_open_state'] = 'open_pending'
        # Hedges will be placed at end of day (after 2:50 PM)
        sold_options_info['hedge_ce_open_state'] = 'None'
        sold_options_info['hedge_pe_open_state'] = 'None'
        return sold_options_info

    def retry_rejected_order(self, account, order_type, strike, quantity, place_order_obj):
        """
        Retry placing a rejected order

        Args:
            account: Trading account
            order_type: 'PE' or 'CE'
            strike: Strike price
            quantity: Order quantity
            place_order_obj: Order placement object

        Returns:
            tuple: (new_order_id, success_flag)
        """
        retry_key = f"{account}_{self.symbol}_{order_type}"

        # Get current retry count
        current_retries = self.order_retry_count.get(retry_key, 0)

        if current_retries >= self.MAX_RETRY_ATTEMPTS:
            logging.error(f"Maximum retry attempts ({self.MAX_RETRY_ATTEMPTS}) reached for {account} {self.symbol} {order_type} order")
            return -1, False

        # Increment retry count
        self.order_retry_count[retry_key] = current_retries + 1

        logging.info(f"Retrying {order_type} order for {account} {self.symbol} (attempt {current_retries + 1}/{self.MAX_RETRY_ATTEMPTS})")

        # Place new order
        new_order_id = place_order_obj.place_orders(account, strike, order_type, self.symbol, quantity, False)

        if new_order_id == -1:
            logging.error(f"Retry failed for {account} {self.symbol} {order_type} order")
            return -1, False

        logging.info(f"Successfully retried {order_type} order for {account} {self.symbol}, new order ID: {new_order_id}")
        return new_order_id, True

    def reset_retry_count(self, account, order_type):
        """Reset retry count for successful orders"""
        retry_key = f"{account}_{self.symbol}_{order_type}"
        if retry_key in self.order_retry_count:
            del self.order_retry_count[retry_key]

    def check_if_trade_is_executed(self, account, place_order_obj):
        error_in_order = False
        error_message = ""
        sold_options_file_path = self.get_sold_options_file_path(account, self.symbol)

        logging.debug(f"Checking if trade is executed for account {account}")

        if os.path.exists(sold_options_file_path):
            existing_sold_options_info = self.read_existing_sold_options_info(sold_options_file_path)

            logging.debug("Checking pe order is executed or not")

            # Check PE open order
            if existing_sold_options_info.iloc[-1]['pe_open_state'] == 'open_pending':
                order_status, price = place_order_obj.order_status(account,
                            existing_sold_options_info.iloc[-1]['pe_open_order_id'],
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_pe_price'])
                if order_status == 'Complete':
                    logging.info(f"PE order executed for account {account}")
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'pe_open_state'] = 'open'
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_pe_price'] = price
                    # Reset retry count on successful completion
                    self.reset_retry_count(account, 'PE')
                elif order_status == 'Open':
                    # Order is still pending, keep waiting
                    logging.info(f"PE open order {existing_sold_options_info.iloc[-1]['pe_open_order_id']} still pending for account {account}")
                elif order_status == 'Rejected':
                    # Order was rejected, try to retry
                    pe_strike = existing_sold_options_info.iloc[-1]['strangle_pe_strike']
                    # Get quantity from existing sold options info
                    quantity = existing_sold_options_info.iloc[-1]['quantity']

                    new_order_id, retry_success = self.retry_rejected_order(
                        account, 'PE', pe_strike, quantity, place_order_obj
                    )

                    if retry_success:
                        # Update with new order ID and keep in open_pending state
                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'pe_open_order_id'] = new_order_id
                        logging.info(f"PE order retry successful for account {account}, new order ID: {new_order_id}")
                    else:
                        # Retry failed, mark as error
                        error_in_order = True
                        error_message = error_message + f"PE open order rejected and retry failed for account {account} ({self.stratergy}) "
                elif order_status in [-1, 'NotFound']:
                    # API error or order not found, mark as error
                    error_in_order = True
                    error_message = error_message + f"PE open order API error or not found for account {account} ({self.stratergy}) "
                else:
                    # Unknown status, log and continue waiting
                    logging.warning(f"Unknown PE open order status '{order_status}' for account {account}, continuing to wait")
                self.store_sold_options_info(existing_sold_options_info, account)

            # Check CE open order
            if existing_sold_options_info.iloc[-1]['ce_open_state'] == 'open_pending':
                t.sleep(3)
                logging.debug("Checking ce order is executed or not")
                order_status, price = place_order_obj.order_status(account,
                            existing_sold_options_info.iloc[-1]['ce_open_order_id'],
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_ce_price'])
                if order_status == 'Complete':
                    logging.info(f"CE order executed for account {account}")
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'ce_open_state'] = 'open'
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_ce_price'] = price
                    # Reset retry count on successful completion
                    self.reset_retry_count(account, 'CE')
                elif order_status == 'Open':
                    # Order is still pending, keep waiting
                    logging.info(f"CE open order {existing_sold_options_info.iloc[-1]['ce_open_order_id']} still pending for account {account}")
                elif order_status == 'Rejected':
                    # Order was rejected, try to retry
                    ce_strike = existing_sold_options_info.iloc[-1]['strangle_ce_strike']
                    # Get quantity from existing sold options info
                    quantity = existing_sold_options_info.iloc[-1]['quantity']

                    new_order_id, retry_success = self.retry_rejected_order(
                        account, 'CE', ce_strike, quantity, place_order_obj
                    )

                    if retry_success:
                        # Update with new order ID and keep in open_pending state
                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'ce_open_order_id'] = new_order_id
                        logging.info(f"CE order retry successful for account {account}, new order ID: {new_order_id}")
                    else:
                        # Retry failed, mark as error
                        error_in_order = True
                        error_message = error_message + f"CE open order rejected and retry failed for account {account} ({self.stratergy}) "
                elif order_status in [-1, 'NotFound']:
                    # API error or order not found, mark as error
                    error_in_order = True
                    error_message = error_message + f"CE open order API error or not found for account {account} ({self.stratergy}) "
                else:
                    # Unknown status, log and continue waiting
                    logging.warning(f"Unknown CE open order status '{order_status}' for account {account}, continuing to wait")
                self.store_sold_options_info(existing_sold_options_info, account)

            # Check PE close order - only if close order ID is valid
            if (existing_sold_options_info.iloc[-1]['pe_close_state'] == 'close_pending' and
                existing_sold_options_info.iloc[-1]['pe_close_order_id'] != -1):
                order_status, price = place_order_obj.order_status(account,
                        existing_sold_options_info.iloc[-1]['pe_close_order_id'],
                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_pe_close_price'])
                if order_status == 'Complete':
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'pe_close_state'] = 'closed'
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_pe_close_price'] = price
                elif order_status == 'Open':
                    # Order is still pending, keep waiting
                    logging.info(f"PE close order {existing_sold_options_info.iloc[-1]['pe_close_order_id']} still pending for account {account}")
                elif order_status == 'Rejected':
                    # Order was rejected, mark as error
                    error_in_order = True
                    error_message = error_message + f"PE close order rejected for account {account} ({self.stratergy}) "
                elif order_status in [-1, 'NotFound']:
                    # API error or order not found, mark as error
                    error_in_order = True
                    error_message = error_message + f"PE close order API error or not found for account {account} ({self.stratergy}) "
                else:
                    # Unknown status, log and continue waiting
                    logging.warning(f"Unknown PE close order status '{order_status}' for account {account}, continuing to wait")
                self.store_sold_options_info(existing_sold_options_info, account)

            # Check CE close order - only if close order ID is valid
            if (existing_sold_options_info.iloc[-1]['ce_close_state'] == 'close_pending' and
                existing_sold_options_info.iloc[-1]['ce_close_order_id'] != -1):
                t.sleep(3)
                order_status, price = place_order_obj.order_status(account,
                        existing_sold_options_info.iloc[-1]['ce_close_order_id'],
                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_ce_close_price'])
                if order_status == 'Complete':
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'ce_close_state'] = 'closed'
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_ce_close_price'] = price
                elif order_status == 'Open':
                    # Order is still pending, keep waiting
                    logging.info(f"CE close order {existing_sold_options_info.iloc[-1]['ce_close_order_id']} still pending for account {account}")
                elif order_status == 'Rejected':
                    # Order was rejected, mark as error
                    error_in_order = True
                    error_message = error_message + f"CE close order rejected for account {account} ({self.stratergy}) "
                elif order_status in [-1, 'NotFound']:
                    # API error or order not found, mark as error
                    error_in_order = True
                    error_message = error_message + f"CE close order API error or not found for account {account} ({self.stratergy}) "
                else:
                    # Unknown status, log and continue waiting
                    logging.warning("Unknown CE close order status '%s' for account %s, continuing to wait", order_status, account)
                self.store_sold_options_info(existing_sold_options_info, account)

            # Check hedge CE open order
            if existing_sold_options_info.iloc[-1].get('hedge_ce_open_state') == 'open_pending':
                hedge_ce_order_id = existing_sold_options_info.iloc[-1].get('hedge_ce_order_id', -1)
                if hedge_ce_order_id != -1:
                    order_status, price = place_order_obj.order_status(account, hedge_ce_order_id,
                                existing_sold_options_info.iloc[-1].get('hedge_ce_price', 0))
                    if order_status == 'Complete':
                        logging.info(f"Hedge CE order executed for account {account}")
                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'hedge_ce_open_state'] = 'open'
                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'hedge_ce_price'] = price
                    elif order_status == 'Open':
                        logging.info(f"Hedge CE open order {hedge_ce_order_id} still pending for account {account}")
                    elif order_status in ['Rejected', -1, 'NotFound']:
                        error_in_order = True
                        error_message = error_message + f"Hedge CE open order failed for account {account} ({self.stratergy}) "
                    self.store_sold_options_info(existing_sold_options_info, account)

            # Check hedge PE open order
            if existing_sold_options_info.iloc[-1].get('hedge_pe_open_state') == 'open_pending':
                hedge_pe_order_id = existing_sold_options_info.iloc[-1].get('hedge_pe_order_id', -1)
                if hedge_pe_order_id != -1:
                    order_status, price = place_order_obj.order_status(account, hedge_pe_order_id,
                                existing_sold_options_info.iloc[-1].get('hedge_pe_price', 0))
                    if order_status == 'Complete':
                        logging.info(f"Hedge PE order executed for account {account}")
                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'hedge_pe_open_state'] = 'open'
                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'hedge_pe_price'] = price
                    elif order_status == 'Open':
                        logging.info(f"Hedge PE open order {hedge_pe_order_id} still pending for account {account}")
                    elif order_status in ['Rejected', -1, 'NotFound']:
                        error_in_order = True
                        error_message = error_message + f"Hedge PE open order failed for account {account} ({self.stratergy}) "
                    self.store_sold_options_info(existing_sold_options_info, account)

            # Check hedge CE close order
            if (existing_sold_options_info.iloc[-1].get('hedge_ce_close_state') == 'close_pending' and
                existing_sold_options_info.iloc[-1].get('hedge_ce_close_order_id', -1) != -1):
                order_status, price = place_order_obj.order_status(account,
                        existing_sold_options_info.iloc[-1]['hedge_ce_close_order_id'],
                        existing_sold_options_info.iloc[-1].get('hedge_ce_close_price'))
                if order_status == 'Complete':
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'hedge_ce_close_state'] = 'closed'
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'hedge_ce_close_price'] = price
                elif order_status == 'Open':
                    logging.info(f"Hedge CE close order still pending for account {account}")
                elif order_status in ['Rejected', -1, 'NotFound']:
                    error_in_order = True
                    error_message = error_message + f"Hedge CE close order failed for account {account} ({self.stratergy}) "
                self.store_sold_options_info(existing_sold_options_info, account)

            # Check hedge PE close order
            if (existing_sold_options_info.iloc[-1].get('hedge_pe_close_state') == 'close_pending' and
                existing_sold_options_info.iloc[-1].get('hedge_pe_close_order_id', -1) != -1):
                order_status, price = place_order_obj.order_status(account,
                        existing_sold_options_info.iloc[-1]['hedge_pe_close_order_id'],
                        existing_sold_options_info.iloc[-1].get('hedge_pe_close_price'))
                if order_status == 'Complete':
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'hedge_pe_close_state'] = 'closed'
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'hedge_pe_close_price'] = price
                elif order_status == 'Open':
                    logging.info(f"Hedge PE close order still pending for account {account}")
                elif order_status in ['Rejected', -1, 'NotFound']:
                    error_in_order = True
                    error_message = error_message + f"Hedge PE close order failed for account {account} ({self.stratergy}) "
                self.store_sold_options_info(existing_sold_options_info, account)

            # Add this section before storing results:
            # Check if we should change trade_state from open_pending to open
            if existing_sold_options_info.iloc[-1]['trade_state'] == 'open_pending':
                is_ce_ready = (existing_sold_options_info.iloc[-1]['strangle_ce_price'] == -1 or
                              existing_sold_options_info.iloc[-1]['ce_open_state'] == 'open')
                is_pe_ready = (existing_sold_options_info.iloc[-1]['strangle_pe_price'] == -1 or
                              existing_sold_options_info.iloc[-1]['pe_open_state'] == 'open')

                # Check hedge orders too
                hedge_ce_ready = (existing_sold_options_info.iloc[-1].get('hedge_ce_price', -1) == -1 or
                                  existing_sold_options_info.iloc[-1].get('hedge_ce_open_state') in ['open', 'closed', 'None'])
                hedge_pe_ready = (existing_sold_options_info.iloc[-1].get('hedge_pe_price', -1) == -1 or
                                  existing_sold_options_info.iloc[-1].get('hedge_pe_open_state') in ['open', 'closed', 'None'])

                logging.info("CE ready: %s, PE ready: %s, Hedge CE ready: %s, Hedge PE ready: %s",
                            is_ce_ready, is_pe_ready, hedge_ce_ready, hedge_pe_ready)

                if is_ce_ready and is_pe_ready and hedge_ce_ready and hedge_pe_ready:
                    logging.info("All orders (including hedges) executed for account %s, changing state to open", account)
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'trade_state'] = 'open'
                    self.store_sold_options_info(existing_sold_options_info, account)

            # Check transition from closing to closed (as we already implemented)
            if existing_sold_options_info.iloc[-1]['trade_state'] == 'closing':
                # For CE leg
                ce_was_opened = existing_sold_options_info.iloc[-1]['strangle_ce_price'] != -1
                ce_closed = not ce_was_opened or existing_sold_options_info.iloc[-1]['ce_close_state'] == 'closed'

                # For PE leg
                pe_was_opened = existing_sold_options_info.iloc[-1]['strangle_pe_price'] != -1
                pe_closed = not pe_was_opened or existing_sold_options_info.iloc[-1]['pe_close_state'] == 'closed'

                # For hedge CE leg
                hedge_ce_was_opened = existing_sold_options_info.iloc[-1].get('hedge_ce_price', -1) != -1
                hedge_ce_closed = not hedge_ce_was_opened or existing_sold_options_info.iloc[-1].get('hedge_ce_close_state') == 'closed'

                # For hedge PE leg
                hedge_pe_was_opened = existing_sold_options_info.iloc[-1].get('hedge_pe_price', -1) != -1
                hedge_pe_closed = not hedge_pe_was_opened or existing_sold_options_info.iloc[-1].get('hedge_pe_close_state') == 'closed'

                # If all legs are closed (or weren't opened), update trade state to 'closed'
                if ce_closed and pe_closed and hedge_ce_closed and hedge_pe_closed:
                    logging.info("Trade for account %s is now fully closed (including hedges)", account)
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'trade_state'] = 'closed'

            logging.debug("Trade state updated for account %s", account)

            if error_in_order:
                self.store_sold_options_info(existing_sold_options_info, account)
                self.send_error_message(account, error_message)
                return False

            self.store_sold_options_info(existing_sold_options_info, account)
            return True

        return True

    def close_trade(self, account, pe_strike, ce_strike, strangle_pe_price, strangle_ce_price,
                    place_order_obj, qty, hedge_ce_strike=None, hedge_pe_strike=None,
                    hedge_ce_price=-1, hedge_pe_price=-1):
        """Close the trade including hedge positions"""
        logging.info("Closing the trade for account %s %s (%s)", account, self.symbol, self.stratergy)
        ce_order_id = -1
        pe_order_id = -1
        hedge_ce_close_order_id = -1
        hedge_pe_close_order_id = -1

        # Close main positions first (buy back sold options)
        if strangle_pe_price != -1:
            pe_order_id = place_order_obj.close_orders(account, pe_strike, 'PE', self.symbol, qty, False)
            if pe_order_id == -1:
                error_message = f"Exit PE Order Failed | Strike: {pe_strike} | Qty: {qty}"
                self.send_error_message(account, error_message)
                return -1, -1, -1, -1

        if strangle_ce_price != -1:
            ce_order_id = place_order_obj.close_orders(account, ce_strike, 'CE', self.symbol, qty, False)
            if ce_order_id == -1:
                error_message = f"Exit CE Order Failed | Strike: {ce_strike} | Qty: {qty}"
                self.send_error_message(account, error_message)
                return -1, -1, -1, -1

        # Close hedge positions (sell the bought options)
        if hedge_ce_price != -1 and hedge_ce_strike is not None:
            t.sleep(1)
            logging.info(f"Closing hedge CE position at strike {hedge_ce_strike}")
            hedge_ce_close_order_id = place_order_obj.close_hedge_orders(account, hedge_ce_strike, 'CE', self.symbol, qty, False)
            if hedge_ce_close_order_id == -1:
                error_message = f"Exit Hedge CE Order Failed | Strike: {hedge_ce_strike} | Qty: {qty}"
                self.send_error_message(account, error_message)
                # Don't fail the whole trade if hedge close fails
                logging.warning("Hedge CE close failed, but continuing with main position close")

        if hedge_pe_price != -1 and hedge_pe_strike is not None:
            t.sleep(1)
            logging.info(f"Closing hedge PE position at strike {hedge_pe_strike}")
            hedge_pe_close_order_id = place_order_obj.close_hedge_orders(account, hedge_pe_strike, 'PE', self.symbol, qty, False)
            if hedge_pe_close_order_id == -1:
                error_message = f"Exit Hedge PE Order Failed | Strike: {hedge_pe_strike} | Qty: {qty}"
                self.send_error_message(account, error_message)
                # Don't fail the whole trade if hedge close fails
                logging.warning("Hedge PE close failed, but continuing with main position close")

        return ce_order_id, pe_order_id, hedge_ce_close_order_id, hedge_pe_close_order_id

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
                logging.warning("File not found: %s", file_path)
                return pd.DataFrame()  # Return empty DataFrame if file doesn't exist

        except Exception as e:
            logging.error("Error reading trade information: %s", e)
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
            file_path = self.get_sold_options_file_path(account, self.symbol)
            info.to_csv(file_path, index=False)
            logging.debug("Trade information stored in %s", file_path)
        except Exception as e:
            logging.error("Error storing trade information: %s", e)
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
            logging.error("Error updating trade information: %s", e)
            logging.error(traceback.format_exc())
            raise

    def _close_position(self, existing_sold_options_info, account, quantity, place_order_obj):
        """Close open positions including hedges"""
        try:
            # Check if it's expiry day closing
            is_expiry_closing = self.is_expiry_day_closing_time()

            # Get the current states for main positions
            pe_was_opened = existing_sold_options_info.iloc[-1]['strangle_pe_price'] != -1
            ce_was_opened = existing_sold_options_info.iloc[-1]['strangle_ce_price'] != -1

            # Get hedge info
            hedge_ce_strike = existing_sold_options_info.iloc[-1].get('hedge_ce_strike')
            hedge_pe_strike = existing_sold_options_info.iloc[-1].get('hedge_pe_strike')
            hedge_ce_price = existing_sold_options_info.iloc[-1].get('hedge_ce_price', -1)
            hedge_pe_price = existing_sold_options_info.iloc[-1].get('hedge_pe_price', -1)

            ce_close_id, pe_close_id, hedge_ce_close_id, hedge_pe_close_id = self.close_trade(
                account,
                existing_sold_options_info.iloc[-1]['strangle_pe_strike'],
                existing_sold_options_info.iloc[-1]['strangle_ce_strike'],
                existing_sold_options_info.iloc[-1]['strangle_pe_price'],
                existing_sold_options_info.iloc[-1]['strangle_ce_price'],
                place_order_obj,
                quantity,
                hedge_ce_strike=hedge_ce_strike,
                hedge_pe_strike=hedge_pe_strike,
                hedge_ce_price=hedge_ce_price,
                hedge_pe_price=hedge_pe_price
            )

            updates = {
                'trade_state': 'closing',
                'close_time': datetime.now()
            }

            # Only update CE states if CE was opened
            if ce_was_opened:
                updates['ce_close_order_id'] = ce_close_id
                updates['ce_close_state'] = 'close_pending' if ce_close_id != -1 else 'closed'

            # Only update PE states if PE was opened
            if pe_was_opened:
                updates['pe_close_order_id'] = pe_close_id
                updates['pe_close_state'] = 'close_pending' if pe_close_id != -1 else 'closed'

            # Update hedge close states
            if hedge_ce_price != -1:
                updates['hedge_ce_close_order_id'] = hedge_ce_close_id
                updates['hedge_ce_close_state'] = 'close_pending' if hedge_ce_close_id != -1 else 'closed'

            if hedge_pe_price != -1:
                updates['hedge_pe_close_order_id'] = hedge_pe_close_id
                updates['hedge_pe_close_state'] = 'close_pending' if hedge_pe_close_id != -1 else 'closed'

            # If it's expiry day closing, set close prices to 0 for opened legs
            if is_expiry_closing:
                if ce_was_opened:
                    updates['strangle_ce_close_price'] = 0
                if pe_was_opened:
                    updates['strangle_pe_close_price'] = 0
                if hedge_ce_price != -1:
                    updates['hedge_ce_close_price'] = 0
                if hedge_pe_price != -1:
                    updates['hedge_pe_close_price'] = 0

            self.update_and_store(
                existing_sold_options_info,
                account,
                existing_sold_options_info.index[-1],
                updates
            )

        except Exception as e:
            logging.error("Error in closing position: %s", e)
            logging.error(traceback.format_exc())
            raise

    def send_error_message(self, account: str, error_message: str):
        """
        Send error message via Telegram and handle error file creation
        Throttles messages to once per hour for the same error

        Args:
            account: Trading account identifier
            error_message: Error message to be sent
        """
        try:
            # Create a unique key for this account+error combination
            error_key = f"{account}_{self.symbol}_{hash(error_message)}"
            current_time = datetime.now()

            # Check if we've sent this error recently (within 1 hour)
            if error_key in self.last_error_sent:
                time_since_last = current_time - self.last_error_sent[error_key]
                if time_since_last < timedelta(hours=1):
                    # Skip sending, but still log
                    logging.info("Throttling error message for %s %s: %s (last sent %s ago)", account, self.symbol, error_message, time_since_last)
                    return

            # Get file paths
            sold_options_file_path = self.get_sold_options_file_path(account, self.symbol)
            error_file_path = self.get_error_options_file_path(account, self.symbol)

            # Initialize Telegram API
            telegram_api = TelegramSend.telegram_send_api()
            telegram_group = account + "_telegram"
            chat_id = configuration.ConfigurationLoader.get_configuration().get(telegram_group)

            # Send error message via Telegram
            error_msg = f"❌ Nifty Positional: {self.symbol} ({self.stratergy}) | {error_message}"
            telegram_api.send_message(chat_id, error_msg)

            # Update the last sent time
            self.last_error_sent[error_key] = current_time
            logging.info("Error message sent for %s %s: %s", account, self.symbol, error_message)

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
            logging.error("Error in sending error message: %s", e)
            logging.error(traceback.format_exc())

    def is_expiry_day_closing_time(self) -> bool:
        """Check if it's near expiry closing time on expiry day"""
        current_time = datetime.now()
        current_date = current_time.date()
        expiry_date = self.get_next_nifty_expiry().date()

        # Check if today is expiry day
        if current_date == expiry_date:
            # Check if time is at or after 3:15 PM (giving more time)
            closing_time = current_time.time() >= time(15, 22)
            if closing_time:
                logging.info("Expiry day closing condition met. Current time: %s", current_time.time())
            return closing_time
        return False

    def get_nift_sensex_expiry(self):
        """
        Get the next Nifty expiry date

        Returns:
            datetime: Next Nifty expiry date
        """
        fileurl = 'https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz'
        symboldf = pd.read_json(fileurl)

        # filter for BSE options
        symboldf = symboldf[symboldf['exchange'] == 'BSE']
        symboldf = symboldf[symboldf['segment'] == 'BSE_FO']

        # Extract unique expiry dates
        expiry_dates = symboldf['expiry'].unique()

        # sort expiry_dates
        expiry_dates = sorted(expiry_dates)

        # keep only nearest 1 date
        expiry_dates = expiry_dates[:1]

        self.sensex_date_pd = pd.to_datetime(expiry_dates, unit='ms')

        fileurl = 'https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz'
        symboldf = pd.read_json(fileurl)

        # filter for BSE options
        symboldf = symboldf[symboldf['exchange'] == 'NSE']
        symboldf = symboldf[symboldf['segment'] == 'NSE_FO']

        # Extract unique expiry dates
        expiry_dates = symboldf['expiry'].unique()

        # sort expiry_dates
        expiry_dates = sorted(expiry_dates)

        # keep only nearest 1 date
        expiry_dates = expiry_dates[:1]

        self.nifty_date_pd = pd.to_datetime(expiry_dates, unit='ms')


#commodity_path = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vQn_xcX-C2JGmkNQAj_DmrHhpfj0d0EESIN-JiE0zsrQ4guej5Y8FwHvDSCks7pdMMyE0UtkdTR_-bZ/pub?output=csv'
#commodity_account_details = pd.read_csv(commodity_path)
#commodity_stratergy = NiftyPositionalStrategy(commodity_account_details['Account'].unique())
#date = commodity_stratergy.get_next_nifty_expiry()
#print(f"Next Nifty expiry date: {date}")
