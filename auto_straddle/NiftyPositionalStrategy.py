"""Module providing Nifty Positional Strategy"""

import traceback
from datetime import datetime, time, timedelta
import os
import logging
import time as t
import pandas as pd
import TelegramSend
import configuration
from exchange_state import ExchangeData
import brokrage_calculator
import requests
from typing import Optional
logger = logging.getLogger(__name__)

class NiftyPositionalStrategy:
    def __init__(self, accounts):
        self.accounts = accounts
        self.symbol = "NIFTY"  # Fixed to NIFTY only
        self.nso_open = None
        self._cached_expiry: Optional[datetime] = None
        self._last_expiry_check: Optional[datetime] = None

    def loss_limit(self):
        return -700  # Fixed for NIFTY
    

    def get_next_nifty_expiry(self) -> datetime:
        """Calculate the next NIFTY expiry date"""
        current_date = datetime.now()
        
        # If we have a cached expiry and it's still valid, return it
        if (self._cached_expiry and 
            self._last_expiry_check and 
            current_date.date() == self._last_expiry_check.date()):
            return self._cached_expiry

        # Calculate next expiry
        current_day = current_date.weekday()
        days_to_thursday = (3 - current_day) % 7  # 3 represents Thursday
        
        next_thursday = current_date + timedelta(days=days_to_thursday)
        
        # If today is Thursday and it's past market hours, get next Thursday
        if current_day == 3 and current_date.time() > time(15, 30):
            next_thursday += timedelta(days=7)
            
        # Cache the result
        self._cached_expiry = next_thursday
        self._last_expiry_check = current_date
        
        return next_thursday

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
            # Check if time is around 12 PM (giving 15-minute window)
            is_entry_window = time(11, 45) <= current_time <= time(12, 15)
            
            if is_entry_window:
                logging.info(f"Entry window active. Current time: {current_time}, "
                           f"Days to expiry: {days_to_expiry}, "
                           f"Next expiry: {expiry_date}")
            return is_entry_window
            
        return False

    def should_exit_trade(self, option_chain_analyzer, sold_options_info):
        """
        Check exit conditions:
        Exit when CE/PE strike is within 1x of sum of ATM straddle price
        """
        try:
            # Get ATM straddle price
            atm_ce_price = option_chain_analyzer['atm_ce_price']
            atm_pe_price = option_chain_analyzer['atm_pe_price']
            atm_straddle_sum = atm_ce_price + atm_pe_price
            
            # Get current prices of our positions
            ce_current_price = option_chain_analyzer['prev_ce_strangle_price']
            pe_current_price = option_chain_analyzer['prev_pe_strangle_price']
            
            # Check if either strike is within 1x of straddle sum
            if (ce_current_price >= atm_straddle_sum or 
                pe_current_price >= atm_straddle_sum):
                logging.info(
                    f"Exiting trade as strike price reached straddle sum threshold. "
                    f"Straddle sum: {atm_straddle_sum}, "
                    f"CE: {ce_current_price}, PE: {pe_current_price}"
                )
                return True
                
            return False

        except Exception as e:
            logging.error(f"Error in should_exit_trade: {str(e)}")
            return False

    def execute_strategy(self, option_chain_analyzer, account, quantity, place_order_obj):
        try:
            if account not in self.accounts:
                raise ValueError(f"Error: Account '{account}' not valid. Choose from {self.accounts}")

            # Check if NFO market is open
            if self.nso_open is None:
                exchange_data = ExchangeData()
                exchange_data_var = exchange_data.is_nfo_open()
                if not exchange_data_var:
                    print("NFO market is closed")
                    self.nso_open = False
                    return
                self.nso_open = True
            elif not self.nso_open:
                return

            # Extract relevant information
            spot_price = option_chain_analyzer['spot_price']
            ce_strangle_strike = option_chain_analyzer['ce_strangle_strike']
            pe_strangle_strike = option_chain_analyzer['pe_strangle_strike']

            sold_options_file_path = self.get_sold_options_file_path(account)
            
            if os.path.exists(sold_options_file_path):
                # Manage existing position
                existing_sold_options_info = self.read_existing_sold_options_info(sold_options_file_path)
                
                if existing_sold_options_info.iloc[-1]['trade_state'] == 'open':
                    # Update current prices
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_ce_close_price'] = \
                        option_chain_analyzer['prev_ce_strangle_price']
                    existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'strangle_pe_close_price'] = \
                        option_chain_analyzer['prev_pe_strangle_price']

                    # Check exit conditions
                    if self.should_exit_trade(option_chain_analyzer, existing_sold_options_info.iloc[-1]):
                        # Close the trade
                        ce_close_id, pe_close_id = self.close_trade(
                            account,
                            existing_sold_options_info.iloc[-1]['strangle_pe_strike'],
                            existing_sold_options_info.iloc[-1]['strangle_ce_strike'],
                            existing_sold_options_info.iloc[-1]['strangle_pe_price'],
                            existing_sold_options_info.iloc[-1]['strangle_ce_price'],
                            place_order_obj,
                            quantity
                        )
                        
                        # Update trade status
                        self.update_trade_status(
                            existing_sold_options_info,
                            ce_close_id,
                            pe_close_id,
                            option_chain_analyzer
                        )
                        
                        # Store updated information
                        self.store_sold_options_info(existing_sold_options_info, account)
                        
            else:
                # Check entry conditions
                if self.is_entry_time():
                    # Create new position based on PE/CE ratio
                    sold_options_info = self.create_new_position(
                        account,
                        spot_price,
                        option_chain_analyzer,
                        ce_strangle_strike,
                        pe_strangle_strike,
                        quantity,
                        place_order_obj
                    )
                    
                    if sold_options_info:
                        # Store new position
                        existing_sold_options_info = pd.DataFrame([sold_options_info])
                        self.store_sold_options_info(existing_sold_options_info, account)

        except Exception as e:
            logging.error("Error executing Nifty Positional Strategy: %s", e)
            logging.error(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
            self.send_error_notification(account, str(e))

    def create_new_position(self, account, spot_price, option_chain_analyzer, 
                          ce_strike, pe_strike, quantity, place_order_obj):
        """Create new position based on market conditions"""
        sold_options_info = {
            'account': account,
            'symbol': self.symbol,
            'spot_price': spot_price,
            'strangle_ce_price': get_option_price(option_chain_analyzer, 'CE'),
            'strangle_pe_price': get_option_price(option_chain_analyzer, 'PE'),
            'trade_state': 'open',
            'open_time': datetime.now(),
            'close_time': None,
            'strangle_ce_strike': ce_strike,
            'strangle_pe_strike': pe_strike,
            'strangle_ce_close_price': get_option_price(option_chain_analyzer, 'CE'),
            'strangle_pe_close_price': get_option_price(option_chain_analyzer, 'PE'),
            'pe_open_order_id': -1,
            'ce_open_order_id': -1,
            'pe_close_order_id': -1,
            'ce_close_order_id': -1,
            'pe_open_state': 'open',
            'ce_open_state': 'open',
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

    def get_sold_options_file_path(self, account):
        """Get file path using expiry date instead of current date"""
        expiry_date = self.get_next_nifty_expiry().strftime("%Y-%m-%d")
        return f"csv/nifty_pos_options_info_{expiry_date}_{account}.csv"

    def get_error_options_file_path(self, account):
        """Get error file path using expiry date instead of current date"""
        expiry_date = self.get_next_nifty_expiry().strftime("%Y-%m-%d")
        return f"csv/nifty_pos_options_info_error_{expiry_date}_{account}.csv"


    # Include other utility methods from FarSellStrategy with necessary modifications
    # Such as close_trade, store_sold_options_info, compute_profit_loss, etc.








