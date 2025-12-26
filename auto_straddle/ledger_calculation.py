import glob
import logging
import os
import sys
from datetime import datetime, timedelta
from io import StringIO
from typing import Dict, List, Optional

import pandas as pd
import requests

import brokrage_calculator

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

        # Multiplication factors for different instruments (lot sizes)
        # Lot sizes/multiplication factors grouped by strategy
        self.lot_sizes = {
            'AutoStraddle': {
                'NIFTY': 75,
                'BANKNIFTY': 35,
                'FINNIFTY': 65,
                'MIDCPNIFTY': 50,
                'SENSEX': 10
            },
            'FarSell': {
                'NIFTY': 75,
                'BANKNIFTY': 35,
                'FINNIFTY': 65,
                'MIDCPNIFTY': 50,
                'SENSEX': 10
            },
            'IndexFuture': {
                'NIFTY': 65,
                'BANKNIFTY': 35,
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
                'SILVER': 5
            },
            'NiftyPositional': {
                'NIFTY': 75,
                'SENSEX': 20
            }
        }

        logger.info("Initialized LedgerCalculator with data directory: %s", self.data_directory)

    def set_target_date(self, date_str: str):
        """
        Set the target date for ledger calculation

        Args:
            date_str (str): Date in YYYY-MM-DD format
        """
        self.target_date = datetime.strptime(date_str, "%Y-%m-%d")
        logger.info("Target date set to: %s", self.target_date.strftime('%Y-%m-%d'))

    def fetch_account_details_from_google_sheets(self) -> pd.DataFrame:
        """
        Fetch account details from Google Sheets
        Returns DataFrame with Account, Symbol, Stratergy, quantity columns
        """
        try:
            url = "https://docs.google.com/spreadsheets/d/\
                1Kndwbk4S9iSz9uZ4ZaMkPG2bHehjqRWU7RdJ595jwQg/export?format=csv"
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            # Parse CSV content
            csv_content = StringIO(response.text)
            df = pd.read_csv(csv_content)

            logger.info("Successfully fetched account details from Google Sheets. Shape: %s", \
                df.shape)
            logger.debug("Account details columns: %s", df.columns.tolist())

            return df

        except requests.exceptions.RequestException as e:
            logger.error("Error fetching account details from Google Sheets: %s", e)
            logger.info("Falling back to default quantities")
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
            # Fetch account details from Google Sheets
            account_details = self.fetch_account_details_from_google_sheets()

            if not account_details.empty:
                # Filter for the specific account, symbol, and strategy
                filtered = account_details[
                    (account_details['Account'].str.lower() == account.lower()) &
                    (account_details['Symbol'].str.upper() == symbol.upper()) &
                    (account_details['Stratergy'].str.lower() == strategy.lower())
                ]

                if not filtered.empty:
                    quantity = int(filtered['quantity'].iloc[0])
                    logger.info("Quantity from Google Sheets for %s/%s/%s: %s",
                                account, symbol, strategy, quantity)
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

            logger.info("Quantity from fallback for %s/%s/%s: %s", \
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
        logger.info("Found %d files matching pattern: %s", len(files), pattern)
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
        logger.info("Looking for AutoStraddle files from previous day: %s", previous_date)

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
                        logger.info("Skipping AutoStraddle file for invalid account: %s", account)
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

                    logger.info("AutoStraddle - %s %s: P&L_per_unit=%.2f, Quantity=%s, \
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
        logger.info("Looking for FarSell files from previous day: %s", previous_date)

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
                        logger.info("Skipping FarSell file for invalid account: %s", account)
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

                    logger.info("FarSell - %s %s: P&L=%.2f, Brokerage=%.2f, Net=%.2f",
                                account, instrument, total_pnl, total_brokerage, net_amount)

            except (IOError, pd.errors.ParserError) as e:
                logger.error("Error processing FarSell file %s: %s", file_path, e)
                continue

        return ledger

    def calculate_nifty_positional_ledger(self, target_date: str) -> Dict[str, float]:
        """
        Calculate ledger for Nifty Positional strategy (nifty_pos_options_info_*.csv)

        Args:
            target_date (str): Target date in YYYY-MM-DD format

        Returns:
            Dict[str, float]: Account-wise ledger amounts
        """
        logger.info("Calculating Nifty Positional ledger...")
        ledger = {}

        # Find all Nifty Positional files (check previous day, current day, and future expiry dates)
        patterns = [
            f"nifty_pos_options_info_{target_date}_*.csv",
            f"nifty_pos_options_info_{self.get_previous_day(target_date)}_*.csv"
        ]

        all_files = []
        for pattern in patterns:
            all_files.extend(self.find_files_by_pattern(pattern))

        for file_path in all_files:
            try:
                # Extract account and instrument from filename
                filename = os.path.basename(file_path)
                parts = filename.replace('.csv', '').split('_')

                if len(parts) >= 5:
                    expiry_date = parts[3]  # 2025-08-05
                    account = parts[4].lower()  # deepti, avanthi, leelu
                    instrument = parts[5] if len(parts) > 5 else 'NIFTY'  # SENSEX, NIFTY, etc.

                    # Only process valid accounts
                    if account not in self.valid_accounts:
                        logger.info("Skipping Positional file for invalid account: %s", account)
                        continue

                    self.accounts.add(account)

                    # Read the CSV file
                    df = pd.read_csv(file_path)

                    if df.empty:
                        continue

                    # Calculate positional P&L based on open/close trades on target date
                    net_amount = self.compute_positional_pnl(df, instrument, target_date, account)

                    # Add to account ledger
                    if account not in ledger:
                        ledger[account] = 0
                    ledger[account] += net_amount

                    logger.info("Positional - %s %s (exp: %s): Net=%.2f",
                                account, instrument, expiry_date, net_amount)

            except (IOError, pd.errors.ParserError, ValueError) as e:
                logger.error("Error processing Positional file %s: %s", file_path, e)
                continue

        return ledger

    def calculate_commodity_ledger(self, target_date: str) -> Dict[str, float]:
        """
        Calculate ledger for Commodity futures (Commodity-account.csv)

        Args:
            target_date (str): Target date in YYYY-MM-DD format

        Returns:
            Dict[str, float]: Account-wise ledger amounts
        """
        logger.info("Calculating Commodity ledger...")
        ledger = {}

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
                    logger.info("Skipping Commodity file for invalid account: %s", account)
                    continue

                self.accounts.add(account)

                # Read the CSV file
                df = pd.read_csv(file_path)

                if df.empty:
                    continue

                # Filter trades for target date using entry_time or exit_time
                if 'entry_time' in df.columns:
                    df['entry_date'] = pd.to_datetime(df['entry_time']).dt.strftime('%Y-%m-%d')
                if 'exit_time' in df.columns:
                    df['exit_date'] = pd.to_datetime(df['exit_time']).dt.strftime('%Y-%m-%d')
                
                # Check if trade was entered or exited on target date
                # We need the full DF for compute_commodity_mtm to check both dates per row
                net_amount = self.compute_commodity_mtm(df, target_date, account)

                # Add to account ledger
                if account not in ledger:
                    ledger[account] = 0
                ledger[account] += net_amount

                logger.info("Commodity - %s: Net MTM=%.2f", account, net_amount)

            except (IOError, pd.errors.ParserError, ValueError) as e:
                logger.error("Error processing Commodity file %s: %s", file_path, e)
                continue

        return ledger

    def calculate_index_future_ledger(self, target_date: str) -> Dict[str, float]:
        """
        Calculate ledger for Index Future synthetic strategy (IndexFuture-account.csv)

        Args:
            target_date (str): Target date in YYYY-MM-DD format

        Returns:
            Dict[str, float]: Account-wise ledger amounts
        """
        logger.info("Calculating Index Future ledger...")
        ledger = {}

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
                    logger.info("Skipping IndexFuture file for invalid account: %s", account)
                    continue

                self.accounts.add(account)

                # Read the CSV file
                df = pd.read_csv(file_path)

                if df.empty:
                    continue

                # Calculate synthetic future P&L for target date
                net_amount = self.compute_synthetic_future_pnl(df, target_date, account)

                # Add to account ledger
                if account not in ledger:
                    ledger[account] = 0
                ledger[account] += net_amount

                logger.info("IndexFuture - %s: Net=%.2f", account, net_amount)

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
                               target_date: str, account: str) -> float:
        """
        Compute P&L for positional trades based on open/close on target date
        """
        try:
            total_pnl = 0

            # Use NiftyPositional strategy lot sizes
            multiplier = self.lot_sizes.get('NiftyPositional', {}).get(symbol, 75)
            
            # Fetch quantity for this account/symbol/strategy
            quantity = self.get_quantity_for_account_symbol(account, symbol, 'fr')

            # Filter trades that were opened or closed on target date
            if 'open_time' in df.columns:
                df['open_date'] = pd.to_datetime(df['open_time']).dt.strftime('%Y-%m-%d')
            if 'close_time' in df.columns:
                df['close_date'] = pd.to_datetime(df['close_time']).dt.strftime('%Y-%m-%d')

            for _, row in df.iterrows():
                # Check if trade was opened on target date (credit)
                if row.get('open_date') == target_date:
                    # Opening trade - credit amount (selling options)
                    ce_price = row.get('ce_open_price', 0) or row.get('atm_ce_price', 0)
                    pe_price = row.get('pe_open_price', 0) or row.get('atm_pe_price', 0)
                    credit = float(ce_price) + float(pe_price)

                    # Calculate brokerage for opening
                    brokerage = 0
                    if ce_price > 0:
                        brokerage += brokrage_calculator.calculate_equity_options(
                            0, ce_price, multiplier * quantity)['total_charges']
                    if pe_price > 0:
                        brokerage += brokrage_calculator.calculate_equity_options(
                            0, pe_price, multiplier * quantity)['total_charges']

                    total_pnl += (credit * multiplier * quantity) - brokerage

                # Check if trade was closed on target date (debit)
                if row.get('close_date') == target_date:
                    # Closing trade - debit amount (buying back options)
                    ce_close_price = row.get('ce_close_price', 0) or \
                        row.get('atm_ce_close_price', 0)
                    pe_close_price = row.get('pe_close_price', 0) or \
                        row.get('atm_pe_close_price', 0)
                    debit = float(ce_close_price) + float(pe_close_price)

                    # Calculate brokerage for closing
                    brokerage = 0
                    if ce_close_price > 0:
                        brokerage += brokrage_calculator.calculate_equity_options(
                            ce_close_price, 0, multiplier * quantity)['total_charges']
                    if pe_close_price > 0:
                        brokerage += brokrage_calculator.calculate_equity_options(
                            pe_close_price, 0, multiplier * quantity)['total_charges']

                    total_pnl -= (debit * multiplier * quantity) + brokerage

            return total_pnl

        except (KeyError, AttributeError, TypeError, ValueError) as e:
            logger.error("Error computing positional P&L: %s", e)
            return 0

    def compute_commodity_mtm(self, df: pd.DataFrame, target_date: str, account: str) -> float:
        """
        Compute MTM for commodity trades using a cash-flow based approach.
        """
        try:
            total_amount = 0

            for _, row in df.iterrows():
                symbol = row.get('Symbol') or row.get('symbol', 'GOLD')
                # Use Commodity strategy lot sizes
                multiplier = self.lot_sizes.get('Commodity', {}).get(symbol, 1)
                trade_type = row.get('trade_type', 'long').lower()
                
                # Fetch quantity for this account/symbol/strategy
                quantity = self.get_quantity_for_account_symbol(account, symbol, 'Commodity')
                
                entry_date = row.get('entry_date')
                exit_date = row.get('exit_date')
                
                entry_price = row.get('entry_price') or row.get('open_price')
                exit_price = row.get('exit_price') or row.get('close_price')
                
                # Check for entry on target date
                if entry_date == target_date and pd.notna(entry_price):
                    cost = float(entry_price) * multiplier * quantity
                    if trade_type == 'long':
                        total_amount -= cost  # Buying: Debit
                    else:
                        total_amount += cost  # Selling: Credit
                
                # Check for exit on target date
                if exit_date == target_date and pd.notna(exit_price):
                    proceeds = float(exit_price) * multiplier * quantity
                    if trade_type == 'long':
                        total_amount += proceeds  # Selling long position: Credit
                    else:
                        total_amount -= proceeds  # Buying back short: Debit
                
                # If neither matched but it's a closed trade on this date, 
                # check if there's a profit column we can use as fallback
                elif exit_date == target_date and 'profit' in row and pd.notna(row['profit']):
                    total_amount += float(row['profit']) * quantity

            return total_amount

        except (KeyError, AttributeError, TypeError) as e:
            logger.error("Error computing commodity MTM: %s", e)
            return 0

    def compute_synthetic_future_pnl(self, df: pd.DataFrame, target_date: str, account: str) -> float:
        """
        Compute P&L for synthetic futures (CE buy + PE sell for long, CE sell + PE buy for short)
        """
        try:
            total_pnl = 0

            # Filter trades for target date
            if 'entry_time' in df.columns:
                df['entry_date'] = pd.to_datetime(df['entry_time']).dt.strftime('%Y-%m-%d')
            if 'exit_time' in df.columns:
                df['exit_date'] = pd.to_datetime(df['exit_time']).dt.strftime('%Y-%m-%d')

            for _, row in df.iterrows():
                # Check if trade was entered or exited on target date
                entry_date = row.get('entry_date')
                exit_date = row.get('exit_date')
                
                entry_price_ce = row.get('entry_price_ce')
                entry_price_pe = row.get('entry_price_pe')
                exit_price_ce = row.get('exit_price_ce')
                exit_price_pe = row.get('exit_price_pe')
                
                trade_type = row.get('trade_type', 'long').lower()
                symbol = row.get('Symbol') or row.get('symbol', 'NIFTY')
                # Use IndexFuture strategy lot sizes
                multiplier = self.lot_sizes.get('IndexFuture', {}).get(symbol, 75)
                
                # Fetch quantity for this account/symbol/strategy
                quantity = self.get_quantity_for_account_symbol(account, symbol, 'IndexFuture')

                # Entry on target date
                if entry_date == target_date and pd.notna(entry_price_ce) and pd.notna(entry_price_pe):
                    entry_cost = (float(entry_price_ce) + float(entry_price_pe)) * multiplier * quantity
                    if trade_type == 'long':
                        total_pnl -= entry_cost
                    else:
                        total_pnl += entry_cost
                
                # Exit on target date
                if exit_date == target_date and pd.notna(exit_price_ce) and pd.notna(exit_price_pe):
                    exit_proceeds = (float(exit_price_ce) + float(exit_price_pe)) * multiplier * quantity
                    if trade_type == 'long':
                        total_pnl += exit_proceeds
                    else:
                        total_pnl -= exit_proceeds
                
                # Fallback to profit column if available
                elif exit_date == target_date and 'profit' in row and pd.notna(row['profit']):
                    total_pnl += float(row['profit']) * quantity

            return total_pnl

        except (KeyError, AttributeError, TypeError, ValueError) as e:
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

    def save_ledger_to_csv(self, target_date: str, output_file: str = None) -> str:
        """
        Save ledger to CSV file

        Args:
            target_date (str): Target date in YYYY-MM-DD format
            output_file (str): Output CSV file path

        Returns:
            str: Path to saved CSV file
        """
        ledger = self.calculate_comprehensive_ledger(target_date)

        # Convert to DataFrame
        rows = []
        for account, strategies in ledger.items():
            for strategy, amount in strategies.items():
                rows.append({
                    'Date': target_date,
                    'Account': account,
                    'Strategy': strategy,
                    'Amount': amount
                })

        df = pd.DataFrame(rows)

        if output_file is None:
            output_file = f"ledger_report_{target_date}.csv"

        df.to_csv(output_file, index=False)
        logger.info("Ledger report saved to: %s", output_file)

        return output_file


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
    report = calculator.generate_ledger_report(target_date)
    print(report)

    # Save to CSV
    csv_file = calculator.save_ledger_to_csv(target_date)
    print(f"\nDetailed report saved to: {csv_file}")


if __name__ == "__main__":
    main()
