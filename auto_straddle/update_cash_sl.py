"""
Cash Strategy Stop-Loss Updater

This script updates stop-loss values in cash_stratergy.csv based on Williams Fractal analysis.
It finds the lowest of the 2 previous bullish fractals (support levels) below the current LTP
and sets the new SL to 3% below that level (only if it would increase the SL, never decrease).

Usage:
    python update_cash_sl.py [--dry-run] [--symbol SYMBOL]

    --dry-run: Show proposed changes without modifying the CSV
    --symbol: Only process a specific symbol (for testing)
"""
#pylint: disable=W1203
#pylint: disable=W0718
#pylint: disable=C0301
import os
import time
import argparse
import logging
import traceback
import pandas as pd
from tvDatafeed import TvDatafeed, Interval
from alligator_api import alligator_api

# Set up logging
logger = logging.getLogger(__name__)


class CashSLUpdater:
    """Class to update stop-loss values based on Williams Fractal analysis"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CashSLUpdater, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        # Initialize attributes here to satisfy Pylint's 'Attribute defined outside __init__'
        # Using a guard because __init__ is called every time CashSLUpdater() is invoked
        if not hasattr(self, 'tv_obj'):
            self.tv_obj = None

    def init_tvdatafeed(self):
        """Initialize TvDatafeed without credentials (anonymous access)"""
        try:
            logger.info("Initializing TvDatafeed (no credentials required)...")
            self.tv_obj = TvDatafeed()
            logger.info("TvDatafeed initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize TvDatafeed: {e}")
            return False

    @staticmethod
    def get_bullish_fractals_below_ltp(data, fractals, ltp):
        """
        Get bullish fractal prices (local lows/support levels) that are below the current LTP.

        Bullish fractals mark local lows which are support levels - ideal for stop-loss placement.

        Args:
            data: OHLC DataFrame with 'low' column
            fractals: DataFrame with 'BullishFractal' boolean column
            ltp: Last traded price (current close)

        Returns:
            Series of bullish fractal prices (lows) below LTP, sorted by date (most recent first)
        """
        # Merge fractals with data to get the low prices
        data_with_fractals = data.copy()
        data_with_fractals['BullishFractal'] = fractals['BullishFractal']

        # Filter to bullish fractals only (local lows = support levels)
        bullish = data_with_fractals[data_with_fractals['BullishFractal']]

        # Get the low prices (bullish fractals are local lows)
        bullish_prices = bullish['low']

        # Filter to those below LTP
        bullish_below_ltp = bullish_prices[bullish_prices < ltp]

        # Sort by index (date) descending to get most recent first
        bullish_below_ltp = bullish_below_ltp.sort_index(ascending=False)

        return bullish_below_ltp

    def calculate_new_sl(self, symbol, exchange, current_sl, verbose=True):
        """
        Calculate new stop-loss based on Williams Fractal analysis.

        Args:
            symbol: Stock symbol
            exchange: Exchange (NSE or BSE)
            current_sl: Current stop-loss value
            verbose: Log detailed info

        Returns:
            tuple: (new_sl, lowest_fractal, ltp) or (None, None, None) on error
        """
        try:
            # Fetch daily historical data (4000 bars)
            if verbose:
                logger.info(f"Fetching daily data for {symbol} from {exchange}...")

            data = self.tv_obj.get_hist(
                symbol=symbol,
                exchange=exchange,
                interval=Interval.in_daily,
                n_bars=4000
            )

            if data is None or data.empty:
                logger.error(f"No data received for {symbol}")
                return None, None, None

            if verbose:
                logger.debug(f"Received {len(data)} bars of data for {symbol}")

            # Get current LTP (last close price)
            ltp = data['close'].iloc[-1]
            if verbose:
                logger.debug(f"{symbol} Current LTP: {ltp:.2f}")

            # Compute Williams Fractal with period=5
            alligator = alligator_api()
            fractals = alligator.WILLIAMS_FRACTAL(data, period=5)

            # Drop NaN values
            fractals = fractals.dropna()

            if verbose:
                bullish_count = fractals['BullishFractal'].sum()
                logger.debug(f"{symbol}: Found {bullish_count} bullish fractals (support levels)")

            # Get bullish fractals below LTP (support levels)
            bullish_below_ltp = self.get_bullish_fractals_below_ltp(data, fractals, ltp)

            if len(bullish_below_ltp) < 2:
                logger.warning(f"{symbol}: Not enough bullish fractals below LTP ({len(bullish_below_ltp)} found, need 2)")
                return None, None, ltp

            # Take the 2 most recent bullish fractals below LTP
            recent_2_fractals = bullish_below_ltp.head(2)

            if verbose:
                fractal_str = ", ".join([f"{price:.2f}" for price in recent_2_fractals.values])
                logger.debug(f"{symbol}: 2 most recent support fractals: {fractal_str}")

            # Find the lowest of the 2
            lowest_fractal = recent_2_fractals.min()

            # Calculate new SL (3% below the lowest fractal)
            new_sl = lowest_fractal * 0.97

            # Only return new SL if it's higher than current (never reduce SL)
            if new_sl > current_sl:
                logger.info(f"{symbol}: New SL {new_sl:.2f} > Current SL {current_sl:.2f} - WILL UPDATE")
                return new_sl, lowest_fractal, ltp
            else:
                logger.debug(f"{symbol}: New SL {new_sl:.2f} <= Current SL {current_sl:.2f} - NO CHANGE")
                return None, lowest_fractal, ltp

        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}")
            logger.error(traceback.format_exc())
            return None, None, None

    def update_cash_sl(self, dry_run=False, symbol_filter=None):
        """
        Main function to update stop-loss values in cash_stratergy.csv

        Args:
            dry_run: If True, only show proposed changes without modifying CSV
            symbol_filter: If provided, only process this symbol

        Returns:
            int: Number of positions updated
        """
        logger.info("=" * 50)
        logger.info("Cash Strategy Stop-Loss Updater - Starting")
        logger.info("=" * 50)

        # Initialize TvDatafeed if not already done
        if self.tv_obj is None:
            if not self.init_tvdatafeed():
                logger.error("Could not initialize TvDatafeed")
                return 0

        # Read cash_stratergy.csv
        csv_path = os.path.expanduser('~/temp/data_collection/cash_stratergy.csv')
        logger.info(f"Reading {csv_path}...")

        try:
            data = pd.read_csv(csv_path)
        except Exception as e:
            logger.error(f"Could not read CSV: {e}")
            return 0

        # Filter to open positions
        open_positions = data[data['status'] == 'open'].copy()
        logger.info(f"Found {len(open_positions)} open positions")

        if symbol_filter:
            open_positions = open_positions[open_positions['symbol'] == symbol_filter]
            logger.info(f"Filtered to symbol '{symbol_filter}': {len(open_positions)} positions")

        if len(open_positions) == 0:
            logger.info("No positions to process")
            return 0

        # Process each open position
        updates = []

        for idx, row in open_positions.iterrows():
            symbol = row['symbol']
            exchange = row.get('exchange', 'NSE')
            current_sl = row['sl']

            logger.info(f"Processing: {symbol} ({exchange}), Current SL: {current_sl}")

            # Handle symbol mapping if needed (e.g., M_M -> M&M)
            tv_symbol = symbol.replace('_', '&')

            new_sl, lowest_fractal, ltp = self.calculate_new_sl(
                tv_symbol, exchange, current_sl, verbose=True
            )

            if new_sl is not None:
                updates.append({
                    'idx': idx,
                    'symbol': symbol,
                    'exchange': exchange,
                    'current_sl': current_sl,
                    'new_sl': new_sl,
                    'lowest_fractal': lowest_fractal,
                    'ltp': ltp
                })

            # Small delay to avoid rate limiting
            time.sleep(1)

        # Summary
        logger.info("=" * 50)
        logger.info("SUMMARY")
        logger.info("=" * 50)

        if len(updates) == 0:
            logger.info("No updates needed")
            return 0

        logger.info(f"Proposed updates ({len(updates)} positions):")
        for u in updates:
            logger.info(f"  {u['symbol']} ({u['exchange']}): SL {u['current_sl']:.2f} -> {u['new_sl']:.2f} (Fractal: {u['lowest_fractal']:.2f}, LTP: {u['ltp']:.2f})")

        if dry_run:
            logger.info("[DRY RUN] No changes made to CSV")
        else:
            # Apply updates
            for u in updates:
                data.loc[u['idx'], 'sl'] = round(u['new_sl'], 2)

            # Save CSV
            data.to_csv(csv_path, index=False)
            logger.info(f"Updated {len(updates)} positions in {csv_path}")

        return len(updates)


def get_updater():
    """Get or create the singleton updater instance"""
    return CashSLUpdater()


def run_cash_sl_update(dry_run=False):
    """
    Convenience function to run the SL update from AutoStraddle.py

    Args:
        dry_run: If True, only show proposed changes without modifying CSV

    Returns:
        int: Number of positions updated
    """
    updater = get_updater()
    return updater.update_cash_sl(dry_run=dry_run)


def test_single_symbol(symbol, exchange='NSE'):
    """Test function for a single symbol"""
    logger.info(f"Testing {symbol} on {exchange}")

    updater = get_updater()
    if not updater.init_tvdatafeed():
        return

    # Use a dummy current_sl of 0 to see all fractal info
    new_sl, lowest_fractal, _ = updater.calculate_new_sl(symbol, exchange, 0, verbose=True)

    if lowest_fractal:
        logger.info(f"Result: Lowest of 2 previous fractals: {lowest_fractal:.2f}")
    if new_sl:
        logger.info(f"Result: Proposed SL (fractal - 3%): {new_sl:.2f}")


def main():
    """Main entry point for command-line usage"""
    # Configure logging for command-line usage
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] (%(name)s): %(message)s'
    )

    parser = argparse.ArgumentParser(description='Update cash strategy stop-loss values')
    parser.add_argument('--dry-run', action='store_true', help='Show changes without modifying CSV')
    parser.add_argument('--symbol', type=str, help='Only process a specific symbol')
    parser.add_argument('--test', type=str, help='Test mode: analyze a single symbol (e.g., --test BHARTIARTL)')
    parser.add_argument('--exchange', type=str, default='NSE', help='Exchange for test mode (default: NSE)')

    args = parser.parse_args()

    if args.test:
        test_single_symbol(args.test, args.exchange)
    else:
        updater = CashSLUpdater()
        updater.update_cash_sl(dry_run=args.dry_run, symbol_filter=args.symbol)


if __name__ == "__main__":
    main()
