#pylint: disable=W0718
#pylint: disable=C0301
#pylint: disable=C0302
#pylint: disable=C0114
#pylint: disable=C0303

import glob
import logging
import os
import sys
from datetime import datetime, timedelta
from io import StringIO
from typing import Dict, List, Optional
import traceback
import pandas as pd
import requests

import brokrage_calculator
from TelegramSend import telegram_send_api
import configuration

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class LedgerCalculator:
    """
    Comprehensive Ledger Calculation System for All Trading Strategies.
    Calculates credit/debit for all accounts across different trading strategies.
    """
    def __init__(self, data_directory: str = "~/temp/data_collection/csv"):
        """
        Initialize the ledger calculator

        Args:
            data_directory (str): Path to directory containing CSV files
        """
        self.data_directory = os.path.expanduser(data_directory)
        self.accounts = set()
        self.target_date = None
        self.valid_accounts = ['deepti', 'avanthi', 'leelu']  # Only these 3 accounts
        self.symboldf = None  # To be initialized on first use
        self.sheet_cache = {}  # Cache for Google Sheet DataFrames
        self.ledger_csv_path = os.path.join(self.data_directory, "ledger_tracking.csv")
        self._initialize_ledger_csv()

        # Multiplication factors for different instruments (lot sizes)
        # Lot sizes/multiplication factors grouped by strategy
        self.lot_sizes = {
            'AutoStraddle': {
                'NIFTY': 65,
                'BANKNIFTY': 30,
                'FINNIFTY': 65,
                'MIDCPNIFTY': 50,
                'SENSEX': 10
            },
            'FarSell': {
                'NIFTY': 65,
                'BANKNIFTY': 30,
                'FINNIFTY': 65,
                'MIDCPNIFTY': 50,
                'SENSEX': 10
            },
            'IndexFuture': {
                'NIFTY': 65,
                'BANKNIFTY': 30,
                'FINNIFTY': 65
            },
            'Commodity': {
                'CRUDEOIL': 10,
                'NATURALGAS': 250,
                'COPPER': 2500,
                'GOLD': 10,
                'LEAD': 1000,
                'ZINC': 1000,
                'ALUMINIUM': 1000,
                'SILVER': 1
            },
            'NiftyPositional': {
                'NIFTY': 65,
                'SENSEX': 20
            }
        }

        # Google Sheets URLs for strategy account details
        self.strategy_urls = {
            'as': 'https://docs.google.com/spreadsheets/d/1Kndwbk4S9iSz9uZ4ZaMkPG2bHehjqRWU7RdJ595jwQg/export?format=csv',
            'fr': 'https://docs.google.com/spreadsheets/d/1Kndwbk4S9iSz9uZ4ZaMkPG2bHehjqRWU7RdJ595jwQg/export?format=csv',
            'Commodity': 'https://docs.google.com/spreadsheets/d/12hH-wMr36t7VGiyO08oAbaihyOCt6ZPLKj7FO9wNH6o/export?format=csv',
            'IndexFuture': 'https://docs.google.com/spreadsheets/d/1S2PO_tPjnCpq3LGWRUXJenWouSAJ8dxCuC_jTLSC07E/export?format=csv',
            'Positional': 'https://docs.google.com/spreadsheets/d/1Ncv-9eA52t6bMNIcI3kzAxTQlvjQ9dOvqZdFX0-JYCM/export?format=csv'
        }


    def set_target_date(self, date_str: str):
        """
        Set the target date for ledger calculation

        Args:
            date_str (str): Date in YYYY-MM-DD format
        """
        self.target_date = datetime.strptime(date_str, "%Y-%m-%d")
        # logger.info("Target date set to: %s", self.target_date.strftime('%Y-%m-%d'))

    def _initialize_ledger_csv(self):
        """Initialize the ledger tracking CSV if it doesn't exist"""
        try:
            if not os.path.exists(self.ledger_csv_path):
                df = pd.DataFrame(columns=['date', 'account', 'ledger_got', 'ledger_computed', 'ledger_change', 'difference'])
                df.to_csv(self.ledger_csv_path, index=False)
                logger.info("Created ledger tracking CSV at %s", self.ledger_csv_path)
        except Exception as e:
            logger.error("Error initializing ledger CSV: %s", e)

    def get_previous_ledger_got(self, account: str, target_date: str) -> float:
        """
        Get the ledger_got value from the previous trading day
        
        Args:
            account: Account name
            target_date: Target date in YYYY-MM-DD format
            
        Returns:
            Previous ledger_got value or 0.0 if not found
        """
        try:
            if not os.path.exists(self.ledger_csv_path):
                return 0.0
                
            df = pd.read_csv(self.ledger_csv_path)
            if df.empty:
                return 0.0
                
            df['date'] = pd.to_datetime(df['date'])
            target_dt = datetime.strptime(target_date, "%Y-%m-%d")
            
            # Filter for this account and dates before target
            account_data = df[(df['account'] == account) & (df['date'] < target_dt)]
            
            if account_data.empty:
                logger.info("No previous ledger data found for %s", account)
                return 0.0
            
            # Get the most recent entry
            latest = account_data.sort_values('date', ascending=False).iloc[0]
            previous_value = float(latest['ledger_got'])
            logger.info("Previous ledger for %s: %.2f", account, previous_value)
            return previous_value
        except Exception as e:
            logger.error("Error getting previous ledger for %s: %s", account, e)
            return 0.0

    def _is_entry_already_tracked(self, target_date: str, account: str) -> bool:
        """
        Check if an entry for this date and account already exists in the tracking CSV
        
        Args:
            target_date: Target date in YYYY-MM-DD format
            account: Account name
            
        Returns:
            True if entry exists, False otherwise
        """
        try:
            if not os.path.exists(self.ledger_csv_path):
                return False
                
            df = pd.read_csv(self.ledger_csv_path)
            if df.empty:
                return False
                
            # Filter for this date and account (case-insensitive)
            existing = df[(df['date'] == target_date) & (df['account'].str.lower() == account.lower())]
            
            return not existing.empty
        except Exception as e:
            logger.error("Error checking if entry exists: %s", e)
            return False

    def update_ledger_tracking(self, target_date: str, account: str, ledger_got: float, 
                              ledger_change: float, ledger_computed: float):
        """
        Update or add ledger tracking entry
        
        Args:
            target_date: Date in YYYY-MM-DD format
            account: Account name
            ledger_got: Actual balance from API
            ledger_change: Daily P&L change
            ledger_computed: Expected balance (previous + change)
        """
        try:
            df = pd.read_csv(self.ledger_csv_path)
            
            # Check if entry already exists
            existing = df[(df['date'] == target_date) & (df['account'] == account)]
            
            if not existing.empty:
                logger.info("Entry already exists for %s on %s, skipping update", account, target_date)
                return
            
            # Calculate difference
            difference = ledger_got - ledger_computed
            
            # Add new entry
            new_entry = pd.DataFrame([{
                'date': target_date,
                'account': account,
                'ledger_got': ledger_got,
                'ledger_computed': ledger_computed,
                'ledger_change': ledger_change,
                'difference': difference
            }])
            
            df = pd.concat([df, new_entry], ignore_index=True)
            df.to_csv(self.ledger_csv_path, index=False)
            
            logger.info("Updated ledger tracking for %s: got=%.2f, computed=%.2f, diff=%.2f", account, ledger_got, ledger_computed, difference)
            
        except Exception as e:
            logger.error("Error updating ledger tracking: %s", e)
            logger.error(traceback.format_exc())

    def generate_ledger_with_balance_check(self, target_date: str, place_order) -> Dict[str, Dict]:
        """
        Generate ledger report with actual balance verification
        
        Args:
            target_date: Date in YYYY-MM-DD format
            place_order: PlaceOrder instance to fetch actual balances
            
        Returns:
            Dict with account -> {computed, actual, difference, strategies}
        """
        logger.info("Generating ledger with balance check for %s", target_date)
        
        # Calculate comprehensive ledger (computed changes)
        ledger = self.calculate_comprehensive_ledger(target_date)
        
        result = {}
        
        for account in self.accounts:
            if account == 'dummy':
                logger.info("Skipping dummy account")
                continue
                
            # Get computed total for this account
            computed_change = ledger.get(account, {}).get('Total', 0)
            
            # Get previous day's ledger_got
            previous_ledger = self.get_previous_ledger_got(account, target_date)
            
            # Compute expected ledger
            ledger_computed = previous_ledger + computed_change
            
            # Check if entry already exists before fetching balance and updating
            if self._is_entry_already_tracked(target_date, account):
                logger.info("Entry already exists for %s on %s, skipping balance fetch and notification", account, target_date)
                continue
            
            # Get actual balance from API
            try:
                if place_order:
                    ledger_got = place_order.get_ledger_balance(account)
                else:
                    ledger_got = 0.0
                logger.info("Fetched actual balance for %s: %.2f", account, ledger_got)
            except Exception as e:
                logger.error("Error fetching balance for %s: %s", account, e)
                ledger_got = 0.0
            
            # Update CSV
            self.update_ledger_tracking(target_date, account, ledger_got, computed_change, ledger_computed)
            
            result[account] = {
                'previous_balance': previous_ledger,
                'computed_change': computed_change,
                'computed_balance': ledger_computed,
                'actual_balance': ledger_got,
                'difference': ledger_got - ledger_computed,
                'strategies': ledger.get(account, {})
            }
        
        # Send Telegram notifications
        self._send_telegram_notifications(target_date, result)
        
        return result

    def _send_telegram_notifications(self, target_date: str, results: Dict[str, Dict]):
        """Send Telegram notifications for all accounts"""
        try:
            telegram = telegram_send_api()
            
            for account, data in results.items():
                message = f"""📊 Ledger Report - {target_date}
Account: {account.upper()}

💰 Previous Balance: ₹{data['previous_balance']:,.2f}
📈 Computed Change: ₹{data['computed_change']:,.2f}
🧮 Expected Balance: ₹{data['computed_balance']:,.2f}
✅ Actual Balance: ₹{data['actual_balance']:,.2f}
⚠️ Difference: ₹{data['difference']:,.2f}

Strategy Breakdown:"""
                
                for strategy, amount in data['strategies'].items():
                    if strategy != 'Total':
                        message += f"\n  {strategy}: ₹{amount:,.2f}"
                
                # Get telegram group ID for this account
                telegram_group = account + "_telegram"
                group_id = configuration.ConfigurationLoader.get_configuration().get(telegram_group)
                
                if group_id:
                    telegram.send_message(group_id, message)
                    logger.info("Sent ledger report to %s", account)
                else:
                    logger.warning("No telegram group configured for %s", account)
                    
        except Exception as e:
            logger.error("Error sending Telegram notifications: %s", e)
            logger.error(traceback.format_exc())

    def fetch_account_details_from_google_sheets(self, url: str) -> pd.DataFrame:
        """
        Fetch account details from a specific Google Sheet URL with caching
        Returns DataFrame with Account, Symbol, Stratergy, quantity columns
        """
        if url in self.sheet_cache:
            return self.sheet_cache[url]

        try:
            logger.debug("Fetching account details from Google Sheets: %s", url)
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            # Parse CSV content
            csv_content = StringIO(response.text)
            df = pd.read_csv(csv_content)

            logger.debug("Successfully fetched %d rows from Google Sheets", len(df))
            self.sheet_cache[url] = df
            return df

        except requests.exceptions.RequestException as e:
            logger.error("Error fetching account details from Google Sheets (%s): %s", url, e)
            return pd.DataFrame()  # Return empty DataFrame to trigger fallback

    def get_quantity_for_account_symbol(self, account: str, symbol: str, strategy: str) -> int:
        """
        Get quantity for a specific account/symbol/strategy combination
        Fetches from Google Sheets following the same pattern as AutoStraddle.py

        Args:
            account (str): Account name (deepti, avanthi, leelu)
            symbol (str): Instrument symbol (NIFTY, BANKNIFTY, etc.)
            strategy (str): Strategy type ('as', 'fr', etc.)

        Returns:
            int: Quantity for this combination
        """
        try:
            # Fetch account details for the specific strategy
            url = self.strategy_urls.get(strategy) or self.strategy_urls.get(strategy.lower())
            
            if not url:
                logger.warning("No Google Sheet URL defined for strategy: %s", strategy)
                account_details = pd.DataFrame()
            else:
                account_details = self.fetch_account_details_from_google_sheets(url)

            if not account_details.empty:
                # Filter for the specific account, symbol, and strategy
                # Note: strategy name in the sheet might differ from the key, 
                # but we'll try matching it or assuming all entries in that sheet are for that category
                filtered = account_details[
                    (account_details['Account'].str.lower() == account.lower()) &
                    (account_details['Symbol'].str.upper() == symbol.upper())
                ]

                if not filtered.empty:
                    quantity = int(filtered['quantity'].iloc[0])
                    #logger.info("Quantity from Google Sheets for %s/%s/%s: %s",
                    #            account, symbol, strategy, quantity)
                    return quantity
                else:
                    logger.warning("No matching entry found in Google Sheets for %s/%s/%s",
                                   account, symbol, strategy)

            # Fallback to default quantities if Google Sheets fetch fails or no match found
            default_quantities = {
                ('deepti', 'NIFTY', 'as'): 8,  # Updated based on Google Sheets data
                ('deepti', 'BANKNIFTY', 'as'): 1,
                ('deepti', 'FINNIFTY', 'as'): 2,
                ('avanthi', 'NIFTY', 'as'): 1,
                ('avanthi', 'BANKNIFTY', 'as'): 1,
                ('avanthi', 'FINNIFTY', 'as'): 1,
                ('leelu', 'NIFTY', 'as'): 3,  # Confirmed from Google Sheets data
                ('leelu', 'BANKNIFTY', 'as'): 2,
                ('leelu', 'FINNIFTY', 'as'): 2,
                ('deepti', 'NIFTY', 'fr'): 2,
                ('deepti', 'BANKNIFTY', 'fr'): 1,
                ('deepti', 'FINNIFTY', 'fr'): 2,
                ('avanthi', 'NIFTY', 'fr'): 1,
                ('avanthi', 'BANKNIFTY', 'fr'): 1,
                ('avanthi', 'FINNIFTY', 'fr'): 1,
                ('leelu', 'NIFTY', 'fr'): 3,
                ('leelu', 'BANKNIFTY', 'fr'): 2,
                ('leelu', 'FINNIFTY', 'fr'): 2,
            }

            key = (account.lower(), symbol.upper(), strategy.lower())
            quantity = default_quantities.get(key, 1)  # Default to 1 if not found

            logger.debug("Quantity from fallback for %s/%s/%s: %s", \
                account, symbol, strategy, quantity)
            return quantity

        except (ValueError, TypeError, KeyError) as e:
            logger.error("Error getting quantity for %s/%s/%s: %s", account, symbol, strategy, e)
            return 1  # Safe fallback

    def get_previous_day(self, date_str: str) -> str:
        """Get previous trading day date string (skips weekends)"""
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        prev_day = date_obj - timedelta(days=1)
        while prev_day.weekday() >= 5:  # Skip Saturday (5) and Sunday (6)
            prev_day -= timedelta(days=1)
        return prev_day.strftime("%Y-%m-%d")

    def find_files_by_pattern(self, pattern: str) -> List[str]:
        """
        Find files matching a specific pattern

        Args:
            pattern (str): Glob pattern to search for

        Returns:
            List[str]: List of matching file paths
        """
        search_path = os.path.join(self.data_directory, pattern)
        files = glob.glob(search_path)
        logger.debug("Found %d files matching pattern: %s", len(files), pattern)
        return files

    def extract_account_from_filename(self, filename: str) -> Optional[str]:
        """Extract account name from filename - only consider valid accounts"""
        valid_accounts = ['deepti', 'avanthi', 'leelu']
        parts = os.path.basename(filename).split('_')
        for part in parts:
            if part.lower() in valid_accounts:
                return part.lower()
        return None

    def calculate_autostraddle_ledger(self, target_date: str) -> Dict[str, float]:
        """
        Calculate ledger for AutoStraddle strategy (as_sold_options_info_closed_*.csv)
        For AutoStraddle, we need to get PREVIOUS DAY files

        Args:
            target_date (str): Target date in YYYY-MM-DD format

        Returns:
            Dict[str, float]: Account-wise ledger amounts
        """
        logger.info("Calculating AutoStraddle ledger...")
        ledger = {}
        # For AutoStraddle, get PREVIOUS DAY files
        previous_date = self.get_previous_day(target_date)
        # logger.info("Looking for AutoStraddle files from previous day: %s", previous_date)

        # Find all AutoStraddle closed files for the previous date
        pattern = f"as_sold_options_info_closed_{previous_date}_*.csv"
        files = self.find_files_by_pattern(pattern)

        for file_path in files:
            try:
                # Extract account and instrument from filename
                filename = os.path.basename(file_path)
                parts = filename.replace('.csv', '').split('_')

                if len(parts) >= 8:
                    # as_sold_options_info_closed_2025-09-26_deepti_BANKNIFTY.csv
                    # parts: [as, sold, options, info, closed, 2025-09-26, deepti, BANKNIFTY]
                    account = parts[6].lower()  # deepti, avanthi, leelu
                    instrument = parts[7]  # BANKNIFTY, NIFTY, etc.

                    # Only process valid accounts
                    if account not in self.valid_accounts:
                        logger.debug("Skipping AutoStraddle file for invalid account: %s", account)
                        continue

                    self.accounts.add(account)

                    # Read the CSV file
                    df = pd.read_csv(file_path)

                    if df.empty:
                        continue

                    # Get quantity for this account/instrument combination
                    # For now, default to 1 - this should be configurable or
                    # read from account details
                    quantity = self.get_quantity_for_account_symbol(account, instrument, 'as')

                    # Calculate profit/loss using AutoStraddle logic (per unit)
                    pnl_per_unit = self.compute_autostraddle_profit_loss(df, instrument)

                    # Apply quantity to get total P&L
                    total_pnl = pnl_per_unit * quantity

                    # Calculate brokerage
                    total_brokerage = self.compute_autostraddle_brokerage(df, instrument, quantity)

                    # Net ledger amount (profit - brokerage)
                    net_amount = total_pnl - total_brokerage

                    # Add to account ledger
                    if account not in ledger:
                        ledger[account] = 0
                    ledger[account] += net_amount

                    logger.debug("AutoStraddle - %s %s: P&L_per_unit=%.2f, Quantity=%s, \
                        Total_P&L=%.2f, Brokerage=%.2f, Net=%.2f",
                                account, instrument, pnl_per_unit, quantity, total_pnl,
                                total_brokerage, net_amount)

            except (IOError, pd.errors.ParserError) as e:
                logger.error("Error processing AutoStraddle file %s: %s", file_path, e)
                continue

        return ledger

    def calculate_farsell_ledger(self, target_date: str) -> Dict[str, float]:
        """
        Calculate ledger for FarSell strategy (fr_sold_options_info_closed_*.csv)
        For FarSell, we need to get PREVIOUS DAY files

        Args:
            target_date (str): Target date in YYYY-MM-DD format

        Returns:
            Dict[str, float]: Account-wise ledger amounts
        """
        logger.info("Calculating FarSell ledger...")
        ledger = {}

        # For FarSell, get PREVIOUS DAY files
        previous_date = self.get_previous_day(target_date)
        # logger.info("Looking for FarSell files from previous day: %s", previous_date)

        # Find all FarSell closed files for the previous date
        pattern = f"fr_sold_options_info_closed_{previous_date}_*.csv"
        files = self.find_files_by_pattern(pattern)

        for file_path in files:
            try:
                # Extract account and instrument from filename
                filename = os.path.basename(file_path)
                parts = filename.replace('.csv', '').split('_')

                if len(parts) >= 8:
                    # fr_sold_options_info_closed_2025-09-26_deepti_BANKNIFTY.csv
                    # parts: [fr, sold, options, info, closed, 2025-09-26, deepti, BANKNIFTY]
                    account = parts[6].lower()  # deepti, avanthi, leelu
                    instrument = parts[7]  # BANKNIFTY, NIFTY, etc.

                    # Only process valid accounts
                    if account not in self.valid_accounts:
                        logger.debug("Skipping FarSell file for invalid account: %s", account)
                        continue

                    self.accounts.add(account)

                    # Read the CSV file
                    df = pd.read_csv(file_path)

                    if df.empty:
                        continue

                    # Get quantity for this account/instrument combination
                    quantity = self.get_quantity_for_account_symbol(account, instrument, 'fr')

                    # Calculate profit/loss using FarSell logic (per unit)
                    pnl_per_unit = self.compute_farsell_profit_loss(df, instrument)

                    # Apply quantity to get total P&L
                    total_pnl = pnl_per_unit * quantity

                    # Calculate brokerage
                    total_brokerage = self.compute_farsell_brokerage(df, instrument, quantity)

                    # Net ledger amount (profit - brokerage)
                    net_amount = total_pnl - total_brokerage

                    # Add to account ledger
                    if account not in ledger:
                        ledger[account] = 0
                    ledger[account] += net_amount

                    logger.debug("FarSell - %s %s: P&L=%.2f, Brokerage=%.2f, Net=%.2f",
                                account, instrument, total_pnl, total_brokerage, net_amount)

            except (IOError, pd.errors.ParserError) as e:
                logger.error("Error processing FarSell file %s: %s", file_path, e)
                continue

        return ledger

    def calculate_nifty_positional_ledger(self, target_date: str) -> Dict[str, float]:
        """
        Calculate ledger for Nifty Positional strategy (nifty_pos_options_info_*.csv)
        For Positional, we need to get PREVIOUS DAY trades (same as other strategies)
        Searches for files within previous_date to previous_date + 3 days.
        """
        logger.info("Calculating Nifty Positional ledger...")
        ledger = {}
        
        # For Positional, get PREVIOUS DAY trades
        previous_date = self.get_previous_day(target_date)
        logger.info("Looking for Positional trades from previous day: %s", previous_date)
        previous_dt = datetime.strptime(previous_date, "%Y-%m-%d")
        
        # Combinations to check
        symbols = ['NIFTY', 'SENSEX']
        strategies = ['as', 'fr']
        
        for account in self.valid_accounts:
            for symbol in symbols:
                for strategy in strategies:
                    best_file = None
                    max_expiry = None
                    
                    # Search window: previous_date to previous_date + 3 days
                    for i in range(4):
                        expiry_date = (previous_dt + timedelta(days=i)).strftime("%Y-%m-%d")
                        pattern = f"nifty_pos_options_info_{expiry_date}_{account}_{symbol}_{strategy}.csv"
                        files = self.find_files_by_pattern(pattern)
                        
                        if files:
                            # We expect at most one file per specific expiry/account/symbol/strategy
                            current_expiry = datetime.strptime(expiry_date, "%Y-%m-%d")
                            if max_expiry is None or current_expiry > max_expiry:
                                max_expiry = current_expiry
                                best_file = files[0]

                    if best_file:
                        try:
                            logger.debug("Processing positional file: %s (expiry: %s)", 
                                        os.path.basename(best_file), max_expiry.strftime("%Y-%m-%d"))
                            
                            df = pd.read_csv(best_file)
                            if df.empty:
                                continue

                            self.accounts.add(account)
                            
                            # Calculate positional P&L (Credit for Open, Debit for Close) for PREVIOUS date
                            net_amount = self.compute_positional_pnl(df, symbol, previous_date)
                            
                            if net_amount != 0:
                                if account not in ledger:
                                    ledger[account] = 0
                                ledger[account] += net_amount
                                
                                logger.debug("Positional - %s %s %s: Net=%.2f", 
                                            account, symbol, strategy, net_amount)
                                        
                        except Exception as e:
                            logger.error("Error processing Positional file %s: %s", best_file, e)
        
        return ledger

    def calculate_commodity_ledger(self, target_date: str) -> Dict[str, float]:
        """
        Calculate ledger for Commodity futures (Commodity-account.csv)
        For Commodity, we need to get PREVIOUS DAY trades (same as other strategies)

        Args:
            target_date (str): Target date in YYYY-MM-DD format

        Returns:
            Dict[str, float]: Account-wise ledger amounts
        """
        logger.info("Calculating Commodity ledger...")
        ledger = {}

        # For Commodity, get PREVIOUS DAY trades
        previous_date = self.get_previous_day(target_date)
        logger.info("Looking for Commodity trades from previous day: %s", previous_date)

        # Find all Commodity files
        pattern = "Commodity-*.csv"
        files = self.find_files_by_pattern(pattern)

        for file_path in files:
            try:
                # Extract account from filename
                filename = os.path.basename(file_path)
                account = filename.replace('Commodity-', '').replace('.csv', '').lower()

                # Only process valid accounts
                if account not in self.valid_accounts:
                    logger.debug("Skipping Commodity file for invalid account: %s", account)
                    continue

                self.accounts.add(account)

                # Read the CSV file
                df = pd.read_csv(file_path)

                if df.empty:
                    continue

                # Filter trades for previous date using entry_time or exit_time
                if 'entry_time' in df.columns:
                    df['entry_date'] = pd.to_datetime(df['entry_time']).dt.strftime('%Y-%m-%d')
                if 'exit_time' in df.columns:
                    df['exit_date'] = pd.to_datetime(df['exit_time']).dt.strftime('%Y-%m-%d')
                
                # Check if trade was entered or exited on PREVIOUS date
                # We need the full DF for compute_commodity_mtm to check both dates per row
                net_amount = self.compute_commodity_mtm(df, previous_date, account)

                # Add to account ledger
                if account not in ledger:
                    ledger[account] = 0
                ledger[account] += net_amount

                logger.debug("Commodity - %s: Net MTM=%.2f", account, net_amount)

            except (IOError, pd.errors.ParserError, ValueError) as e:
                logger.error("Error processing Commodity file %s: %s", file_path, e)
                continue

        return ledger

    def calculate_index_future_ledger(self, target_date: str) -> Dict[str, float]:
        """
        Calculate ledger for Index Future synthetic strategy (IndexFuture-account.csv)
        For IndexFuture, we need to get PREVIOUS DAY trades (same as AutoStraddle/FarSell)

        Args:
            target_date (str): Target date in YYYY-MM-DD format

        Returns:
            Dict[str, float]: Account-wise ledger amounts
        """
        logger.info("Calculating Index Future ledger...")
        ledger = {}

        # For IndexFuture, get PREVIOUS DAY trades
        previous_date = self.get_previous_day(target_date)
        logger.info("Looking for IndexFuture trades from previous day: %s", previous_date)

        # Find all Index Future files
        pattern = "IndexFuture-*.csv"
        files = self.find_files_by_pattern(pattern)

        for file_path in files:
            try:
                # Extract account from filename
                filename = os.path.basename(file_path)
                account = filename.replace('IndexFuture-', '').replace('.csv', '').lower()

                # Only process valid accounts
                if account not in self.valid_accounts:
                    logger.debug("Skipping IndexFuture file for invalid account: %s", account)
                    continue

                self.accounts.add(account)

                # Read the CSV file
                df = pd.read_csv(file_path)

                if df.empty:
                    continue

                # Calculate synthetic future P&L for PREVIOUS date
                net_amount = self.compute_synthetic_future_pnl(df, previous_date, account)

                # Add to account ledger
                if account not in ledger:
                    ledger[account] = 0
                ledger[account] += net_amount

                logger.debug("IndexFuture - %s: Net=%.2f", account, net_amount)

            except (IOError, pd.errors.ParserError, ValueError) as e:
                logger.error("Error processing IndexFuture file %s: %s", file_path, e)
                continue

        return ledger

    def compute_autostraddle_profit_loss(self, df: pd.DataFrame, symbol: str) -> float:
        """
        Compute profit/loss for AutoStraddle strategy
        Based on AutoStraddleStrategy.compute_profit_loss method
        """
        try:
            # Use AutoStraddle strategy lot sizes
            multiplier = self.lot_sizes.get('AutoStraddle', {}).get(symbol, 75)

            total_profit_loss = 0

            for _, row in df.iterrows():
                atm_ce_price = row.get('atm_ce_price', 0)
                atm_pe_price = row.get('atm_pe_price', 0)
                atm_ce_close_price = row.get('atm_ce_close_price', 0)
                atm_pe_close_price = row.get('atm_pe_close_price', 0)

                profit_loss_ce = 0
                profit_loss_pe = 0

                if atm_ce_price != -1 and atm_ce_price > 0:
                    profit_loss_ce = atm_ce_price - atm_ce_close_price

                if atm_pe_price != -1 and atm_pe_price > 0:
                    profit_loss_pe = atm_pe_price - atm_pe_close_price

                total_profit_loss += profit_loss_ce + profit_loss_pe

            return total_profit_loss * multiplier

        except (KeyError, AttributeError, TypeError) as e:
            logger.error("Error computing AutoStraddle P&L: %s", e)
            return 0

    def compute_autostraddle_brokerage(self, df: pd.DataFrame, symbol: str, quantity: int) -> float:
        """
        Compute brokerage for AutoStraddle strategy
        Based on AutoStraddleStrategy.compute_brokrage method
        """
        try:
            # Use AutoStraddle strategy lot sizes
            multiplier = self.lot_sizes.get('AutoStraddle', {}).get(symbol, 75)

            total_brokerage = 0

            for _, row in df.iterrows():
                atm_ce_price = row.get('atm_ce_price', 0)
                atm_pe_price = row.get('atm_pe_price', 0)
                atm_ce_close_price = row.get('atm_ce_close_price', 0)
                atm_pe_close_price = row.get('atm_pe_close_price', 0)

                if atm_ce_price != -1 and atm_ce_price > 0:
                    brokerage_ce = brokrage_calculator.calculate_equity_options(
                        atm_ce_close_price, atm_ce_price, quantity * multiplier
                    )['total_charges']
                    total_brokerage += brokerage_ce

                if atm_pe_price != -1 and atm_pe_price > 0:
                    brokerage_pe = brokrage_calculator.calculate_equity_options(
                        atm_pe_close_price, atm_pe_price, quantity * multiplier
                    )['total_charges']
                    total_brokerage += brokerage_pe

            return total_brokerage

        except (KeyError, AttributeError, TypeError) as e:
            logger.error("Error computing AutoStraddle brokerage: %s", e)
            return 0

    def compute_farsell_profit_loss(self, df: pd.DataFrame, symbol: str) -> float:
        """
        Compute profit/loss for FarSell strategy
        Based on FarSellStratergy.compute_profit_loss method
        """
        try:
            # Use FarSell strategy lot sizes
            multiplier = self.lot_sizes.get('FarSell', {}).get(symbol, 75)

            total_profit_loss = 0

            for _, row in df.iterrows():
                strangle_ce_price = row.get('strangle_ce_price', 0)
                strangle_pe_price = row.get('strangle_pe_price', 0)
                strangle_ce_close_price = row.get('strangle_ce_close_price', 0)
                strangle_pe_close_price = row.get('strangle_pe_close_price', 0)

                profit_loss_ce = 0
                profit_loss_pe = 0

                if strangle_ce_price != -1 and strangle_ce_price > 0:
                    profit_loss_ce = strangle_ce_price - strangle_ce_close_price

                if strangle_pe_price != -1 and strangle_pe_price > 0:
                    profit_loss_pe = strangle_pe_price - strangle_pe_close_price

                total_profit_loss += profit_loss_ce + profit_loss_pe

            return total_profit_loss * multiplier

        except (KeyError, AttributeError, TypeError) as e:
            logger.error("Error computing FarSell P&L: %s", e)
            return 0

    def compute_farsell_brokerage(self, df: pd.DataFrame, symbol: str, quantity: int) -> float:
        """
        Compute brokerage for FarSell strategy
        Similar to AutoStraddle but with strangle columns
        """
        try:
            # Use FarSell strategy lot sizes
            multiplier = self.lot_sizes.get('FarSell', {}).get(symbol, 75)

            total_brokerage = 0

            for _, row in df.iterrows():
                strangle_ce_price = row.get('strangle_ce_price', 0)
                strangle_pe_price = row.get('strangle_pe_price', 0)
                strangle_ce_close_price = row.get('strangle_ce_close_price', 0)
                strangle_pe_close_price = row.get('strangle_pe_close_price', 0)

                if strangle_ce_price != -1 and strangle_ce_price > 0:
                    brokerage_ce = brokrage_calculator.calculate_equity_options(
                        strangle_ce_close_price, strangle_ce_price, quantity * multiplier
                    )['total_charges']
                    total_brokerage += brokerage_ce

                if strangle_pe_price != -1 and strangle_pe_price > 0:
                    brokerage_pe = brokrage_calculator.calculate_equity_options(
                        strangle_pe_close_price, strangle_pe_price, quantity * multiplier
                    )['total_charges']
                    total_brokerage += brokerage_pe

            return total_brokerage

        except (KeyError, AttributeError, TypeError) as e:
            logger.error("Error computing FarSell brokerage: %s", e)
            return 0

    def compute_positional_pnl(self, df: pd.DataFrame, symbol: str,
                                target_date: str) -> float:
        """
        Compute P&L for positional trades based on open/close on target date.
        Open = Credit, Close = Debit. Uses strangle price columns and row quantity.
        """
        try:
            total_pnl = 0
            multiplier = self.lot_sizes.get('NiftyPositional', {}).get(symbol, 75)
            
            # Ensure date columns are present
            if 'open_time' in df.columns:
                df['open_date'] = pd.to_datetime(df['open_time']).dt.strftime('%Y-%m-%d')
            if 'close_time' in df.columns:
                df['close_date'] = pd.to_datetime(df['close_time']).dt.strftime('%Y-%m-%d')

            for _, row in df.iterrows():
                # Correct quantity for this specific trade row
                qty_val = row.get('quantity')
                if pd.isna(qty_val) or float(qty_val) <= 0:
                    continue
                qty = float(qty_val)

                # Check if trade was opened on target date (Credit)
                # Entry = Sell strangle = Credit; Hedge = Buy options = Debit
                open_date = row.get('open_date')
                if pd.notna(open_date) and open_date == target_date:
                    ce_price = float(row.get('strangle_ce_price') or 0)
                    pe_price = float(row.get('strangle_pe_price') or 0)

                    # Handle cases where one side might be -1 or 0 (if only one side traded)
                    credit_per_unit = 0
                    if ce_price > 0:
                        credit_per_unit += ce_price
                    if pe_price > 0:
                        credit_per_unit += pe_price

                    total_pnl += credit_per_unit * multiplier * qty

                    # Hedge options are BOUGHT on the same day (debit)
                    # hedge_ce_price / hedge_pe_price are -1 when not yet placed
                    hedge_ce_price = float(row.get('hedge_ce_price') or 0)
                    hedge_pe_price = float(row.get('hedge_pe_price') or 0)
                    hedge_debit = 0
                    if hedge_ce_price > 0:
                        hedge_debit += hedge_ce_price
                    if hedge_pe_price > 0:
                        hedge_debit += hedge_pe_price
                    total_pnl -= hedge_debit * multiplier * qty

                # Check if trade was closed on target date (Debit)
                # Exit = Buy to close strangle = Debit; Hedge close = Sell/expiry = Credit
                close_date = row.get('close_date')
                if pd.notna(close_date) and close_date == target_date:
                    ce_close_price = float(row.get('strangle_ce_close_price') or 0)
                    pe_close_price = float(row.get('strangle_pe_close_price') or 0)

                    # Skip if both close prices are 0/empty (incomplete data)
                    if ce_close_price == 0 and pe_close_price == 0:
                        continue

                    debit_per_unit = 0
                    if ce_close_price > 0:
                        debit_per_unit += ce_close_price
                    if pe_close_price > 0:
                        debit_per_unit += pe_close_price

                    total_pnl -= debit_per_unit * multiplier * qty

                    # Hedge options are SOLD (or expired worthless) on close date (credit)
                    # NaN means expired worthless → 0 credit, which is correct
                    hedge_ce_close = row.get('hedge_ce_close_price')
                    hedge_pe_close = row.get('hedge_pe_close_price')
                    hedge_ce_close = float(hedge_ce_close) if pd.notna(hedge_ce_close) else 0
                    hedge_pe_close = float(hedge_pe_close) if pd.notna(hedge_pe_close) else 0
                    hedge_credit = 0
                    if hedge_ce_close > 0:
                        hedge_credit += hedge_ce_close
                    if hedge_pe_close > 0:
                        hedge_credit += hedge_pe_close
                    total_pnl += hedge_credit * multiplier * qty

            return total_pnl

        except Exception as e:
            logger.error("Error computing positional P&L: %s", e)
            return 0

    def fetch_mcx_symbols(self):
        """Fetch and process MCX symbols from Upstox"""
        if self.symboldf is not None:
            return
        
        try:
            logger.info("Fetching MCX symbols from Upstox...")
            file_url = 'https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz'
            df = pd.read_csv(file_url)
            df['expiry'] = pd.to_datetime(df['expiry']).dt.date
            df = df[df.exchange == 'MCX_FO']
            df = df[df.strike == 0]
            self.symboldf = df
            logger.info("Successfully fetched %d MCX symbols", len(df))
        except Exception as e:
            logger.error("Error fetching MCX symbols: %s", e)
            self.symboldf = pd.DataFrame()

    def get_commodity_close_prices(self, symbol: str, target_date: str, previous_date: str) -> Dict[str, float]:
        """Fetch close prices for target and previous date from Upstox"""
        self.fetch_mcx_symbols()
        
        prices = {'target': None, 'previous': None}
        
        try:
            # Map symbol names if necessary
            upstox_symbol = 'CRUDE OIL' if symbol == 'CRUDEOIL' else symbol
            
            # Find the correct instrument_key (nearest expiry)
            token_df = self.symboldf[self.symboldf.name == upstox_symbol]
            
            if symbol == 'GOLD':
                token_df = token_df[~token_df.tradingsymbol.str.contains('PETAL|GUINEA|GOLDTEN', regex=True)]
            elif symbol in ['LEAD', 'ZINC']:
                token_df = token_df[~token_df.tradingsymbol.str.contains('MINI')]
            elif symbol == 'ALUMINIUM':
                token_df = token_df[~token_df.tradingsymbol.str.contains('ALUMINIUM')] # Wait, logic in commodity_data was a bit weird here

            if token_df.empty:
                logger.warning("No token found for symbol %s", symbol)
                return prices

            token_df = token_df.sort_values(by='expiry', ascending=True)
            
            # Simple heuristic: for most commodities, use 0th. For some, 1st as per commodity_data.py
            index_to_use = 1 if symbol in ['LEAD', 'ZINC', 'ALUMINIUM'] and len(token_df) > 1 else 0
            token = token_df.iloc[index_to_use]['instrument_key']
            
            # Fetch daily candles
            today_str = datetime.now().strftime("%Y-%m-%d")
            # We need enough data to cover previous_date as well
            from_date = (datetime.strptime(previous_date, "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d")
            
            url = f'https://api.upstox.com/v2/historical-candle/{token}/day/{today_str}/{from_date}'
            headers = {
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'application/json'
            }
            
            logger.debug("Fetching daily candles for %s (%s)", symbol, token)
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code != 200:
                logger.warning("Failed to fetch data from Upstox for %s: %s", symbol, res.status_code)
                return prices
                
            data = res.json()
            if 'data' in data and 'candles' in data['data']:
                candles = data['data']['candles']
                # Candles are [date, open, high, low, close, volume, oi]
                for candle in candles:
                    candle_date = candle[0].split('T')[0]
                    if candle_date == target_date:
                        prices['target'] = float(candle[4])
                    elif candle_date == previous_date:
                        prices['previous'] = float(candle[4])
            
            logger.debug("Prices for %s: Target=%s, Previous=%s", symbol, prices['target'], prices['previous'])
            return prices
            
        except Exception as e:
            logger.error("Error getting close prices for %s: %s", symbol, e)
            logger.debug(traceback.format_exc())
            return prices

    def compute_commodity_mtm(self, df: pd.DataFrame, target_date: str, account: str) -> float:
        """
        Compute MTM for commodity trades using daily close prices.
        
        Cases:
        1: Opened today, not closed -> Close - Open
        2: Opened and closed today -> Exit - Open
        3: Previously opened, closed today -> Exit - PrevClose
        4: Open all day -> Close - PrevClose
        """
        try:
            total_mtm = 0
            previous_date = self.get_previous_day(target_date)
            
            # We'll cache close prices by symbol to avoid repeated API calls
            symbol_prices = {}

            for _, row in df.iterrows():
                symbol = row.get('Symbol') or row.get('symbol', 'GOLD')
                multiplier = self.lot_sizes.get('Commodity', {}).get(symbol, 1)
                trade_type = row.get('trade_type', 'long').lower()
                quantity = self.get_quantity_for_account_symbol(account, symbol, 'Commodity')
                
                entry_date = pd.to_datetime(row.get('entry_time')).strftime('%Y-%m-%d') if pd.notna(row.get('entry_time')) else None
                exit_date = pd.to_datetime(row.get('exit_time')).strftime('%Y-%m-%d') if pd.notna(row.get('exit_time')) else None
                
                # Check status and relevance to target_date
                # If closed before today or opened after today, skip
                if exit_date and exit_date < target_date:
                    continue
                if entry_date and entry_date > target_date:
                    continue
                
                # Fetch prices if not in cache
                if symbol not in symbol_prices:
                    symbol_prices[symbol] = self.get_commodity_close_prices(symbol, target_date, previous_date)
                
                prices = symbol_prices[symbol]
                target_close = prices['target']
                prev_close = prices['previous']
                
                entry_price = float(row.get('entry_price')) if pd.notna(row.get('entry_price')) else 0
                exit_price = float(row.get('exit_price')) if pd.notna(row.get('exit_price')) else 0
                
                mtm_per_unit = 0
                
                # Logic cases
                if entry_date == target_date:
                    if exit_date == target_date:
                        # Case 2: Opened and closed same day
                        mtm_per_unit = exit_price - entry_price
                        case = 2
                    else:
                        # Case 1: Opened today, not closed
                        if target_close:
                            mtm_per_unit = target_close - entry_price
                        case = 1
                else:
                    # entry_date < target_date
                    if exit_date == target_date:
                        # Case 3: Opened previously, closed today
                        if prev_close:
                            mtm_per_unit = exit_price - prev_close
                        case = 3
                    else:
                        # Case 4: Open whole day
                        if target_close and prev_close:
                            mtm_per_unit = target_close - prev_close
                        case = 4
                
                # Adjust for short trades
                if trade_type == 'short':
                    mtm_per_unit = -mtm_per_unit
                
                total_mtm += mtm_per_unit * multiplier * quantity
                
                logger.debug("Commodity %s Case %d: Type=%s, Qty=%d, MTM=%.2f (per unit=%.2f)", 
                            symbol, case, trade_type, quantity, mtm_per_unit * multiplier * quantity, mtm_per_unit)

            return total_mtm

        except Exception as e:
            logger.error("Error computing commodity MTM: %s", e)
            logger.debug(traceback.format_exc())
            return 0

    def compute_synthetic_future_pnl(self, df: pd.DataFrame, target_date: str, account: str) -> float:
        """
        Compute P&L for synthetic futures based on trade type.
        Short Entry: PE Buy (Debit), CE Sell (Credit)
        Long Entry: CE Buy (Debit), PE Sell (Credit)
        Exit: Opposite of entry.
        
        Only processes rows where orders are successfully opened or closed,
        and filters out unrealistic prices (e.g. index price recorded instead of option price).
        """
        try:
            total_pnl = 0

            # Prepare date columns
            if 'entry_time' in df.columns:
                df['entry_date'] = pd.to_datetime(df['entry_time']).dt.strftime('%Y-%m-%d')
            if 'exit_time' in df.columns:
                df['exit_date'] = pd.to_datetime(df['exit_time']).dt.strftime('%Y-%m-%d')

            for _, row in df.iterrows():
                # Check order states - skip if still pending
                enter_state = str(row.get('enter_order_state', '')).lower()
                exit_state = str(row.get('exit_order_state', '')).lower()
                
                # If it's a target date entry, we only care if it's successfully opened
                # (either 'open' or already 'closed')
                if enter_state not in ['open', 'close', 'closed']:
                    continue

                entry_date = row.get('entry_date')
                exit_date = row.get('exit_date')
                
                entry_price_ce = float(row.get('entry_price_ce') or 0)
                entry_price_pe = float(row.get('entry_price_pe') or 0)
                exit_price_ce = float(row.get('exit_price_ce') or 0)
                exit_price_pe = float(row.get('exit_price_pe') or 0)
                
                trade_type = row.get('trade_type', 'long').lower()
                symbol = row.get('Symbol') or row.get('symbol', 'NIFTY')
                multiplier = self.lot_sizes.get('IndexFuture', {}).get(symbol, 65)
                quantity = self.get_quantity_for_account_symbol(account, symbol, 'IndexFuture')

                # Heuristic to detect Index price recorded as Option price
                # Options for NIFTY/BANKNIFTY are rarely above 5000, 
                # while index is 20000+.
                if entry_price_ce > 5000 or entry_price_pe > 5000 or \
                   exit_price_ce > 5000 or exit_price_pe > 5000:
                    logger.warning("IndexFuture %s %s: Skipping row with unrealistic prices (CE=%.2f, PE=%.2f)",
                                   account, symbol, entry_price_ce, entry_price_pe)
                    continue

                row_pnl = 0

                # Entry on target date
                if pd.notna(entry_date) and entry_date == target_date:
                    if trade_type == 'short':
                        # Short Entry: PE Buy (Debit), CE Sell (Credit)
                        row_pnl += (entry_price_ce - entry_price_pe) * multiplier * quantity
                    else:
                        # Long Entry: CE Buy (Debit), PE Sell (Credit)
                        row_pnl += (entry_price_pe - entry_price_ce) * multiplier * quantity
                    logger.debug("IndexFuture Entry %s %s: CE=%.2f, PE=%.2f, Type=%s, Qty=%d, PnL=%.2f",
                                symbol, target_date, entry_price_ce, entry_price_pe, trade_type, quantity, row_pnl)

                # Exit on target date
                # Only check exit if it actually closed today
                if pd.notna(exit_date) and exit_date == target_date and exit_state in ['close', 'closed']:
                    if trade_type == 'short':
                        # Short Exit: PE Sell (Credit), CE Buy (Debit)
                        exit_pnl = (exit_price_pe - exit_price_ce) * multiplier * quantity
                    else:
                        # Long Exit: CE Sell (Credit), PE Buy (Debit)
                        exit_pnl = (exit_price_ce - exit_price_pe) * multiplier * quantity
                    row_pnl += exit_pnl
                    logger.debug("IndexFuture Exit %s %s: CE=%.2f, PE=%.2f, Type=%s, Qty=%d, PnL=%.2f",
                                symbol, target_date, exit_price_ce, exit_price_pe, trade_type, quantity, exit_pnl)

                total_pnl += row_pnl

            return total_pnl

        except Exception as e:
            logger.error("Error computing synthetic future P&L: %s", e)
            return 0

    def calculate_comprehensive_ledger(self, target_date: str) -> Dict[str, Dict[str, float]]:
        """
        Calculate comprehensive ledger for all strategies and accounts

        Args:
            target_date (str): Target date in YYYY-MM-DD format

        Returns:
            Dict[str, Dict[str, float]]: Nested dict with account -> strategy -> amount
        """
        logger.info("Calculating comprehensive ledger for date: %s", target_date)

        self.set_target_date(target_date)

        # Calculate ledger for each strategy
        autostraddle_ledger = self.calculate_autostraddle_ledger(target_date)
        farsell_ledger = self.calculate_farsell_ledger(target_date)
        positional_ledger = self.calculate_nifty_positional_ledger(target_date)
        commodity_ledger = self.calculate_commodity_ledger(target_date)
        index_future_ledger = self.calculate_index_future_ledger(target_date)

        # Combine all ledgers by account
        comprehensive_ledger = {}

        for account in self.accounts:
            comprehensive_ledger[account] = {
                'AutoStraddle': autostraddle_ledger.get(account, 0),
                'FarSell': farsell_ledger.get(account, 0),
                'Positional': positional_ledger.get(account, 0),
                'Commodity': commodity_ledger.get(account, 0),
                'IndexFuture': index_future_ledger.get(account, 0),
                'Total': (
                    autostraddle_ledger.get(account, 0) +
                    farsell_ledger.get(account, 0) +
                    positional_ledger.get(account, 0) +
                    commodity_ledger.get(account, 0) +
                    index_future_ledger.get(account, 0)
                )
            }

        return comprehensive_ledger

    def generate_ledger_report(self, target_date: str) -> str:
        """
        Generate a formatted ledger report

        Args:
            target_date (str): Target date in YYYY-MM-DD format

        Returns:
            str: Formatted report string
        """
        ledger = self.calculate_comprehensive_ledger(target_date)

        report = f"\n{'='*80}\n"
        report += f"COMPREHENSIVE LEDGER REPORT - {target_date}\n"
        report += f"{'='*80}\n\n"

        grand_total = 0

        for account, strategies in ledger.items():
            report += f"Account: {account.upper()}\n"
            report += f"{'-'*50}\n"

            for strategy, amount in strategies.items():
                if strategy != 'Total':
                    report += f"{strategy:<20}: ₹{amount:>12,.2f}\n"

            report += f"{'-'*50}\n"
            report += f"{'TOTAL':<20}: ₹{strategies['Total']:>12,.2f}\n\n"

            grand_total += strategies['Total']

        report += f"{'='*50}\n"
        report += f"{'GRAND TOTAL':<20}: ₹{grand_total:>12,.2f}\n"
        report += f"{'='*50}\n"

        return report

def main():
    """Main function to run ledger calculation"""

    if len(sys.argv) != 2:
        print("Usage: python ledger_calculation.py YYYY-MM-DD")
        sys.exit(1)

    target_date = sys.argv[1]

    try:
        # Validate date format
        datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        print("Error: Date must be in YYYY-MM-DD format")
        sys.exit(1)

    # Initialize calculator
    calculator = LedgerCalculator()

    # Generate and print report
    #report = calculator.generate_ledger_with_balance_check(target_date, None)
    report = calculator.generate_ledger_report(target_date)
    print(report)
    logger.info(report)


if __name__ == "__main__":
    main()
