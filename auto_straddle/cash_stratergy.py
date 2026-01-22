"""Module providing a function for far cell"""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=W0105


from datetime import datetime, timedelta
import os
import traceback
import logging
from time import sleep
import threading
import time
import requests
#from PlaceOrder import PlaceOrder
import pandas as pd
import TelegramSend
import configuration
from exchange_state import ExchangeData
import brokrage_calculator


headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "en-US,en;q=0.9,en-IN;q=0.8,en-GB;q=0.7",
            "cache-control": "max-age=0",
            "priority": "u=0, i",
            "sec-ch-ua": '"Microsoft Edge";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0"
        }

#from OptionChainData import OptionChainData
#from pathlib import Path
#from PlaceOrder import PlaceOrder

# Set up logging
logger = logging.getLogger(__name__)


class NSEPriceCache:
    """Thread-safe cache for NSE price data with TTL and rate limiting"""

    def __init__(self, ttl_seconds=60, rate_limit_calls=10, rate_limit_period=60):
        """
        Initialize cache with TTL and rate limiting

        Args:
            ttl_seconds: Time-to-live for cached entries (default: 60s)
            rate_limit_calls: Maximum API calls allowed in the period
            rate_limit_period: Time period for rate limiting in seconds
        """
        self._cache = {}  # {symbol: {'price': float, 'timestamp': datetime}}
        self._lock = threading.Lock()
        self.ttl = timedelta(seconds=ttl_seconds)

        # Rate limiting
        self._call_timestamps = []  # List of recent API call timestamps
        self.rate_limit_calls = rate_limit_calls
        self.rate_limit_period = timedelta(seconds=rate_limit_period)

    def get(self, symbol):
        """Get cached price if available and not expired"""
        with self._lock:
            if symbol in self._cache:
                entry = self._cache[symbol]
                if datetime.now() - entry['timestamp'] < self.ttl:
                    logger.debug(f"Cache hit for {symbol}: {entry['price']}")
                    return entry['price']
                else:
                    logger.debug(f"Cache expired for {symbol}")
                    del self._cache[symbol]
            return None

    def set(self, symbol, price):
        """Store price in cache with current timestamp"""
        with self._lock:
            self._cache[symbol] = {
                'price': price,
                'timestamp': datetime.now()
            }
            logger.debug(f"Cached price for {symbol}: {price}")

    def can_make_call(self):
        """Check if we can make an API call within rate limits"""
        with self._lock:
            now = datetime.now()
            # Remove old timestamps outside the rate limit window
            self._call_timestamps = [
                ts for ts in self._call_timestamps
                if now - ts < self.rate_limit_period
            ]

            if len(self._call_timestamps) >= self.rate_limit_calls:
                oldest_call = self._call_timestamps[0]
                wait_time = (oldest_call + self.rate_limit_period - now).total_seconds()
                logger.warning(f"Rate limit reached. Need to wait {wait_time:.1f}s")
                return False, wait_time

            return True, 0

    def record_call(self):
        """Record that an API call was made"""
        with self._lock:
            self._call_timestamps.append(datetime.now())

    def clear(self):
        """Clear all cached entries"""
        with self._lock:
            self._cache.clear()
            logger.info("Price cache cleared")


class TelegramNotifier:
    """Centralized handler for Telegram notifications"""

    def __init__(self, telegram_api):
        self.telegram_api = telegram_api
        self._notification_history = {}  # Track sent notifications to avoid duplicates
        self._lock = threading.Lock()

    def send_error(self, account, symbol, error_type, details=""):
        """Send error notification with deduplication"""
        account = str(account)  # Ensure account is string for concatenation
        message = f"Cash strategy {error_type} error {account} {symbol}"
        if details:
            message += f": {details}"

        # Get Telegram group ID
        telegram_group = account + "_telegram"
        chat_id = configuration.ConfigurationLoader.get_configuration().get(telegram_group)

        if not chat_id:
            logger.error(f"No Telegram chat ID found for {telegram_group}")
            return False

        try:
            self.telegram_api.send_message(chat_id, message)
            logger.info(f"Sent error notification to {account}: {error_type}")
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False

    def send_success(self, account, symbol, message_type, details):
        """Send success notification"""
        account = str(account)  # Ensure account is string for concatenation
        message = f"Cash strategy {message_type} {account} {symbol} {details}"

        telegram_group = account + "_telegram"
        chat_id = configuration.ConfigurationLoader.get_configuration().get(telegram_group)

        if not chat_id:
            logger.error(f"No Telegram chat ID found for {telegram_group}")
            return False

        try:
            self.telegram_api.send_message(chat_id, message)
            logger.info(f"Sent success notification to {account}: {message_type}")
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False

    def send_manual_close_request(self, account, symbol):
        """Send manual close request for accounts without API access"""
        account = str(account)  # Ensure account is string for concatenation
        # Handle account routing
        if account in ["sharekhan", "anvitha", "adithya"]:
            telegram_group = "deepti_telegram"
        else:
            telegram_group = account + "_telegram"

        chat_id = configuration.ConfigurationLoader.get_configuration().get(telegram_group)

        if not chat_id:
            logger.error(f"No Telegram chat ID found for {telegram_group}")
            return False

        message = f"Cash strategy please close {account} {symbol}"

        try:
            self.telegram_api.send_message(chat_id, message)
            logger.info(f"Sent manual close request for {account} {symbol}")
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False


class cash_stratergy:
    # Symbol mapping for special cases (CSV symbol -> NSE symbol)
    SYMBOL_MAPPING = {
        'M_M': 'M&M',          # Mahindra & Mahindra
        'M&MFIN': 'M&MFIN',    # M&M Financial (already correct)
        'L_T': 'LT',           # Larsen & Toubro
    }

    def __init__(self):
        self.csv_path = "cash_stratergy.csv"
        self.remote_csv_url = "https://docs.google.com/spreadsheets/d/19y1fKqAZtMaCzUHEgKV15FLRSSAXLWVdq-kR1-TY6VY/export?format=csv"
        self.correct_rejected_orders_url = "https://docs.google.com/spreadsheets/d/1yTl32dlt3h9t2MYuncGnc9oLvn-gB_VkEGMvQ4s08Z8/export?format=csv"
        self.execution_tracker = {"morning": 0, "afternoon": 0}
        self.nso_open = None
        self._cached_positions = None
        self._last_fetch_time = None
        self.telegram_api = TelegramSend.telegram_send_api()  # Create once and reuse

        # Initialize caching and rate limiting
        self.price_cache = NSEPriceCache(ttl_seconds=60, rate_limit_calls=10, rate_limit_period=60)
        self.notifier = TelegramNotifier(self.telegram_api)

        # Session pool for reuse
        self._session = None
        self._session_created_at = None
        self._session_ttl = timedelta(minutes=10)  # Recreate session every 10 minutes

        # Order retry tracking
        self._order_retry_count = {}  # {(account, symbol, order_type): retry_count}
        self._max_order_retries = 3

    def _normalize_symbol(self, symbol):
        """
        Convert CSV symbols to NSE-compatible symbols
        
        Args:
            symbol: Symbol from CSV (e.g., 'M_M', 'L_T')
        
        Returns:
            NSE-compatible symbol (e.g., 'M&M', 'LT')
        """
        return self.SYMBOL_MAPPING.get(symbol, symbol)

    def _get_session(self):
        """Get or create a reusable session with automatic refresh"""
        now = datetime.now()

        # Create new session if none exists or if expired
        if self._session is None or (self._session_created_at and now - self._session_created_at > self._session_ttl):
            if self._session:
                try:
                    self._session.close()
                except Exception as e:
                    logger.warning(f"Error closing old session: {e}")

            self._session = requests.Session()
            self._session_created_at = now
            logger.debug("Created new session")

        return self._session

    def get_nse_ltp(self, symbol, max_retries=3, use_cache=True):
        """
        Fetch NSE LTP with caching, rate limiting, and retry logic

        Args:
            symbol: Stock symbol
            max_retries: Maximum number of retry attempts (default: 3)
            use_cache: Whether to use cached prices (default: True)

        Returns:
            float: Last traded price

        Raises:
            ValueError: If fetching price fails after all retries
        """
        # Check cache first
        if use_cache:
            cached_price = self.price_cache.get(symbol)
            if cached_price is not None:
                return cached_price

        for attempt in range(max_retries):
            # Check rate limit before making call
            can_call, wait_time = self.price_cache.can_make_call()
            if not can_call:
                logger.warning(f"Rate limit hit, waiting {wait_time:.1f}s before retry")
                sleep(wait_time + 1)  # Wait slightly longer than required
                continue

            try:
                session = self._get_session()

                # Step 1: First request to NSE main page (sets cookies)
                main_url = f"https://www.nseindia.com/get-quotes/equity?symbol={symbol}"
                session.get(main_url, headers=headers, timeout=10)

                # Step 2: Fetch the actual API using the same session
                api_url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
                headers_with_referer = headers.copy()
                headers_with_referer["Referer"] = main_url

                # Record API call for rate limiting
                self.price_cache.record_call()
                response = session.get(api_url, headers=headers_with_referer, timeout=10)

                if response.status_code == 200:
                    price = response.json()["priceInfo"]["lastPrice"]
                    # Cache the result
                    self.price_cache.set(symbol, price)
                    return price
                else:
                    logger.warning(f"Failed to fetch price for {symbol} (attempt {attempt + 1}/{max_retries}): HTTP {response.status_code}")
                    if attempt == max_retries - 1:
                        logger.error(f"Failed to fetch price for {symbol} after {max_retries} attempts: HTTP {response.status_code}")
                        raise ValueError(f"Failed to fetch price for {symbol}: HTTP {response.status_code}")

            except requests.exceptions.RequestException as e:
                logger.warning(f"Network error fetching price for {symbol} (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    logger.error(f"Failed to fetch price for {symbol} after {max_retries} attempts: {e}")
                    raise ValueError(f"Failed to fetch price for {symbol}: {e}") from e
            except Exception as e:
                logger.error(f"Error fetching price for {symbol}: {e}")
                raise

            # Wait before retry (exponential backoff)
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 2  # 2, 4, 6 seconds
                time.sleep(wait_time)

    def get_nse_ltp_with_fallback(self, symbol):
        """
        Fetch NSE LTP with fallback to alternative method

        Args:
            symbol: Stock symbol

        Returns:
            float: Last traded price or None if all methods fail

        Raises:
            ValueError: If all methods fail to fetch price
        """
        try:
            # Primary method: Direct NSE API with caching
            return self.get_nse_ltp(symbol, max_retries=3)
        except Exception as e:
            logger.warning(f"Primary NSE API failed for {symbol}: {e}")

            try:
                # Fallback: Use the F&O securities endpoint
                logger.info(f"Trying fallback method for {symbol}")
                price = self.nse_custom_function_secfno(symbol, "lastPrice")
                if price:
                    # Cache the fallback result
                    self.price_cache.set(symbol, price)
                    return price
                else:
                    # If fallback returns None, raise error with context from primary failure
                    logger.error(f"Fallback method returned None for {symbol}")
                    raise ValueError(f"Fallback method returned None for {symbol}") from e
            except ValueError:
                # Re-raise ValueError as-is (already has proper context)
                raise
            except Exception as e2:
                logger.error(f"Fallback method also failed for {symbol}: {e2}")
                # Re-raise with context from both failures
                raise ValueError(f"All methods failed to fetch price for {symbol}") from e


    def nsefetch(self, payload):
        try:
            output = requests.get(payload,headers=headers, timeout=10).json()
            #print(output)
        except ValueError:
            s =requests.Session()
            output = s.get("http://nseindia.com",headers=headers)
            output = s.get(payload,headers=headers).json()
        return output

    def nse_custom_function_secfno(self, symbol,attribute="lastPrice"):
        current_time = datetime.now()
        print("Fetching data from NSE" + symbol)
        try:
            if not hasattr(self, '_last_fetch_time') or not hasattr(self, '_cached_positions') or \
                self._last_fetch_time is None or (current_time - self._last_fetch_time).total_seconds() > 300:
                positions = self.nsefetch('https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O')
                self._cached_positions = positions
                self._last_fetch_time = current_time
            else:
                positions = self._cached_positions
            endp = len(positions['data'])
            for x in range(0, endp):
                if positions['data'][x]['symbol']==symbol.upper():
                    value = float(positions['data'][x][attribute])
                    return value
        except Exception as e:
            print("Error fetching data from NSE")
            print(e)

    def apply_corrections(self):
        """
        Applies corrections to orders based on remote CSV data.

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Download and validate remote CSV
            remote_data = pd.read_csv(self.correct_rejected_orders_url)
            required_columns = ['sl_no', 'leg', 'account', 'symbol']
            if not all(col in remote_data.columns for col in required_columns):
                logger.error("Remote correction CSV missing required columns")
                return False

            # Load or create local CSV
            try:
                local_data = pd.read_csv(self.csv_path)
            except FileNotFoundError:
                logger.warning("Local CSV not found. Creating new file")
                local_data = pd.DataFrame(columns=remote_data.columns)
                local_data.to_csv(self.csv_path, index=False)
                return True

            # Process each row in remote data
            for _, row in remote_data.iterrows():
                try:
                    local_row = local_data[local_data['sl_no'] == row['sl_no']]
                    if local_row.empty:
                        logger.warning(f"Row {row['sl_no']} not found in local CSV")
                        continue

                    leg = str(row['leg']).lower().strip()
                    if leg in ['open', 'buy']:
                        self._handle_open_correction(local_data, row)
                    elif leg in ['close', 'sell']:
                        self._handle_close_correction(local_data, row)
                    else:
                        logger.warning(f"Invalid leg value: {row['leg']} for sl_no {row['sl_no']}")

                except Exception as e:
                    logger.error(f"Error processing row {row['sl_no']}: {str(e)}")
                    continue

            # Save updates
            local_data.to_csv(self.csv_path, index=False)
            logger.info("Order corrections applied successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to apply order corrections: {str(e)}")
            return False

    def _handle_open_correction(self, local_data, row):
        """Handle open leg corrections"""
        logger.info(f"Correcting open order for row {row['sl_no']}")
        mask = local_data['sl_no'] == row['sl_no']
        local_data.loc[mask, 'status'] = 'open'
        local_data.loc[mask, 'open_order_status'] = 'Complete'
        local_data.loc[mask, 'buy_order_id'] = None
        local_data.loc[mask, 'buy_price'] = row['price']

        # Update quantity if available in correction and missing locally
        if 'quantity' in row and pd.notna(row['quantity']):
            current_qty = local_data.loc[mask, 'quantity'].iloc[0]
            if pd.isna(current_qty) or current_qty == 0:
                local_data.loc[mask, 'quantity'] = row['quantity']
                logger.info(f"Updated quantity from correction for row {row['sl_no']}: {row['quantity']}")

        local_data.loc[mask, 'open_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _handle_close_correction(self, local_data, row):
        """Handle close leg corrections"""
        try:
            logger.info(f"Correcting close order for row {row['sl_no']}")
            mask = local_data['sl_no'] == row['sl_no']
            local_data.loc[mask, 'status'] = 'close'
            local_data.loc[mask, 'close_order_status'] = 'Complete'
            local_data.loc[mask, 'close_order_id'] = None
            local_data.loc[mask, 'sell_price'] = row['price']
            local_data.loc[mask, 'close_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            self._calculate_and_record_pnl(local_data.loc[mask].iloc[0], row)

        except Exception as e:
            logger.error(f"Error in close correction: {str(e)}")
            raise

    def _calculate_and_record_pnl(self, local_row, remote_row):
        """Calculate and record P&L for closed trades"""
        try:
            # Ensure numeric values
            buy_price = float(local_row['buy_price'])

            # Get quantity from local row, fallback to remote if missing
            quantity = float(local_row['quantity'])
            if (pd.isna(quantity) or quantity == 0) and 'quantity' in remote_row and pd.notna(remote_row['quantity']):
                quantity = float(remote_row['quantity'])

            sell_price = float(remote_row['price'])

            profit_loss = (sell_price - buy_price) * quantity

            # Calculate brokerage
            brokerage_dict = brokrage_calculator.calculate_equity_delivery(
                buy_price, sell_price, quantity)
            brokerage = brokerage_dict['total_charges']

            # Record PnL to CSV file
            pl_dict = {
                'Date': datetime.now().strftime("%Y-%m-%d"),
                'Account': remote_row['account'],
                'Symbol': remote_row['symbol'],
                'Quantity': quantity,
                'NumberofTrade': 1,
                'TotalPNL': profit_loss,
                'Brokerage': brokerage,
                'CloseTime': datetime.now().strftime("%H:%M:%S"),
                'Stratergy': 'cash_short',
                'NetPNL': profit_loss - brokerage
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

            # Send notification to Telegram using centralized notifier
            self.notifier.send_success(remote_row['account'], remote_row['symbol'], "P/L", f"{profit_loss}")

        except Exception as e:
            print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
            logger.error(f"Error calculating PnL: {str(e)}")
            raise

    def sync_cash_strategy(self):
        """
        Syncs the remote CSV with the local CSV based on today's date and updates
        or inserts rows accordingly.
        """
        # Step 1: Download the CSV from the remote URL
        remote_data = pd.read_csv(self.remote_csv_url)
        remote_data['date'] = pd.to_datetime(remote_data['date'], errors='coerce')

        # Step 2: Load the local CSV
        try:
            local_data = pd.read_csv(self.csv_path)
            # Validate sl_no column exists
            if 'sl_no' not in local_data.columns:
                logger.warning("Local CSV missing 'sl_no' column. Recreating with remote structure.")
                local_data = pd.DataFrame(columns=remote_data.columns)
        except FileNotFoundError:
            logger.warning("Local CSV not found. Creating a new one.")
            local_data = pd.DataFrame(columns=remote_data.columns)

        # Step 3: Filter rows for today's date or missing rows
        today_date = datetime.now().date()

        # Get existing sl_nos from local data for comparison
        existing_sl_nos = set(local_data['sl_no'].dropna().unique()) if 'sl_no' in local_data.columns else set()

        # 1. Today's rows (for updates to existing rows)
        is_today = remote_data['date'].dt.date == today_date

        # 2. Missing rows (newly added to remote, regardless of date)
        is_missing = ~remote_data['sl_no'].isin(existing_sl_nos)

        # Combine: Sync if it's today's data OR if it's not in our local file
        today_rows = remote_data[is_today | is_missing]

        # Step 4: Sync rows
        for _, row in today_rows.iterrows():
            sl_no = row['sl_no']

            # Validate required columns exist in local_data
            if 'sl_no' not in local_data.columns:
                local_data['sl_no'] = pd.Series(dtype='object')

            if sl_no in local_data['sl_no'].values:
                # Update existing entry - validate columns exist
                update_cols = ['sl', 'profit_target']
                existing_cols = [col for col in update_cols if col in row.index]
                if existing_cols:
                    local_data.loc[local_data['sl_no'] == sl_no, existing_cols] = row[existing_cols].values
                logger.info(f"Updated entry for sl_no: {sl_no}")
            else:
                # Add new entry
                # If status is blank/empty, set it to 'new' for new rows
                if pd.isna(row.get('status')) or row.get('status') == '' or row.get('status') is None:
                    row['status'] = 'new'
                    logger.info(f"Added new entry for sl_no: {sl_no} with status set to 'new'")
                else:
                    logger.info(f"Added new entry for sl_no: {sl_no}")
                local_data = pd.concat([local_data, pd.DataFrame([row])], ignore_index=True)

        # Save the updated local CSV
        local_data.to_csv(self.csv_path, index=False)
        logger.info("Sync completed successfully.")

    def _can_retry_order(self, account, symbol, order_type):
        """Check if order can be retried based on retry count"""
        key = (account, symbol, order_type)
        retry_count = self._order_retry_count.get(key, 0)
        return retry_count < self._max_order_retries

    def _increment_retry_count(self, account, symbol, order_type):
        """Increment retry count for an order"""
        key = (account, symbol, order_type)
        self._order_retry_count[key] = self._order_retry_count.get(key, 0) + 1
        return self._order_retry_count[key]

    def _reset_retry_count(self, account, symbol, order_type):
        """Reset retry count for an order (called on success)"""
        key = (account, symbol, order_type)
        if key in self._order_retry_count:
            del self._order_retry_count[key]

    def _process_new_orders(self, data, place_order):
        """Process rows with status 'new' - opening new positions"""
        for idx, row in data[data['status'] == 'new'].iterrows():
            logger.info(f"Processing row {row['sl_no']} with symbol {row['symbol']} and sl {row['sl']}")
            try:
                symbol = row['symbol']
                # Normalize symbol for NSE API (M_M -> M&M, etc.)
                nse_symbol = self._normalize_symbol(symbol)
                logger.info(f"Symbol mapping: {symbol} -> {nse_symbol}")

                sleep(1)
                try:
                    last_price = self.get_nse_ltp_with_fallback(nse_symbol)
                except Exception as e:
                    logger.error(f"Error fetching price for symbol {symbol} (NSE: {nse_symbol}): {e}")
                    self.notifier.send_error(row['account'], symbol, "open", f"Price fetch failed: {e}")
                    continue

                logger.info(f"Processing row {row['sl_no']} with symbol {symbol} - last_price: {last_price}, sl: {row['sl']}")

                if last_price > row['sl']:
                    quantity = int(row['amount'] / last_price)

                    # Try to place order with retry logic
                    order_id = None
                    for attempt in range(self._max_order_retries):
                        order_id = place_order.place_cash_order(row['account'], symbol, quantity, "BUY")

                        if order_id and not (isinstance(order_id, float) and pd.isna(order_id)):
                            # Success
                            logger.info(f"Order placed successfully: {order_id}")
                            self._reset_retry_count(row['account'], symbol, "open")
                            break
                        else:
                            logger.warning(f"Order placement attempt {attempt + 1}/{self._max_order_retries} failed")
                            if attempt < self._max_order_retries - 1:
                                sleep(2 * (attempt + 1))  # Exponential backoff

                    if not order_id or (isinstance(order_id, float) and pd.isna(order_id)):
                        logger.error("Order placement failed after all retries")
                        self.notifier.send_error(row['account'], symbol, "open", "Order placement failed after retries")
                        # Don't change status to rejected, keep it as 'new' to allow retry later
                        continue

                    logger.info(f"Order ID: {order_id}")

                    # Update the row in the DataFrame
                    data.loc[idx, 'buy_order_id'] = order_id
                    data.loc[idx, 'buy_price'] = last_price
                    data.loc[idx, 'open_order_status'] = 'open_pending'
                    data.loc[idx, 'status'] = 'open' # Transition directly to open
                    data.loc[idx, 'quantity'] = quantity

                    # Save immediately after placing order to prevent duplicate processing
                    data.to_csv(self.csv_path, index=False)
                    logger.info(f"Status updated to 'open' for row {row['sl_no']}")
                else:
                    logger.info(f"Skipping row {row['sl_no']} with symbol {symbol} - price {last_price} not > sl {row['sl']}")

            except Exception as e:
                print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                logger.error(f"Error processing 'new' row {row['sl_no']}: {e}")
                self.notifier.send_error(row['account'], symbol, "open", str(e))

    def _process_open_positions(self, data, place_order):
        """Process rows with status 'open' - checking for close conditions"""
        for idx, row in data[data['status'] == 'open'].iterrows():
            try:
                # Skip if a close order is already pending (has close_order_id)
                if pd.notna(row.get('close_order_id')) and row['close_order_id'] != -1 and row.get('close_order_status') != 'rejected':
                    # If we have an ID and it's not marked as rejected (internally), skip
                    if row.get('close_order_status') != 'Complete':
                        continue

                symbol = row['symbol']
                # Normalize symbol for NSE API (M_M -> M&M, etc.)
                nse_symbol = self._normalize_symbol(symbol)
                logger.info(f"Processing row {row['sl_no']} with symbol {symbol} (NSE: {nse_symbol}) and sl {row['sl']}")
                sleep(1)

                try:
                    last_price = self.get_nse_ltp_with_fallback(nse_symbol)
                except Exception as e:
                    logger.error(f"Error fetching price for symbol {symbol} (NSE: {nse_symbol}): {e}")
                    self.notifier.send_error(row['account'], symbol, "close", f"Price fetch failed: {e}")
                    continue

                # Validate last_price is not None
                if last_price is None:
                    logger.error(f"Got None price for symbol {symbol}, skipping close check")
                    self.notifier.send_error(row['account'], symbol, "close", "Price is None")
                    continue

                # Validate profit_target exists and is not NaN
                profit_target = row.get('profit_target', float('inf'))
                if pd.isna(profit_target):
                    profit_target = float('inf')

                logger.info(f"Row {row['sl_no']} ({symbol}): last_price={last_price}, sl={row['sl']}, profit_target={profit_target}")

                if last_price <= row['sl'] or last_price >= profit_target:
                    reason = "Stop Loss" if last_price <= row['sl'] else "Profit Target"
                    logger.info(f"Exit triggered for {symbol} (Row {row['sl_no']}). Reason: {reason}. "
                                f"Price: {last_price}, SL: {row['sl']}, Profit Target: {profit_target}")

                    if row['account'] == "deepti":
                        # Try to place close order with retry logic
                        order_id = None
                        for attempt in range(self._max_order_retries):
                            order_id = place_order.place_cash_order(row['account'], symbol, row['quantity'], "SELL")

                            if order_id and not (isinstance(order_id, float) and pd.isna(order_id)):
                                # Success
                                logger.info(f"Close order placed successfully: {order_id}")
                                self._reset_retry_count(row['account'], symbol, "close")
                                break
                            else:
                                logger.warning(f"Close order attempt {attempt + 1}/{self._max_order_retries} failed")
                                if attempt < self._max_order_retries - 1:
                                    sleep(2 * (attempt + 1))  # Exponential backoff

                        if not order_id or (isinstance(order_id, float) and pd.isna(order_id)):
                            logger.error("Close order placement failed after all retries")
                            self.notifier.send_error(row['account'], symbol, "close", "Order placement failed after retries")
                            continue

                        logger.info(f"Order ID: {order_id}")

                        # Update the row in the DataFrame
                        data.loc[idx, 'close_order_id'] = order_id
                        data.loc[idx, 'close_order_status'] = 'close_pending'
                        # Keep status as 'open'
                    else:
                        # Manual close required for non-API accounts
                        data.loc[idx, 'close_order_status'] = 'close_pending'
                        # Keep status as 'open'
                        self.notifier.send_manual_close_request(row['account'], symbol)

                    # Save immediately after triggering close
                    data.to_csv(self.csv_path, index=False)

            except Exception as e:
                print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                logger.error(f"Error processing 'open' row {row['sl_no']}: {e}")
                self.notifier.send_error(row['account'], symbol, "close", str(e))

    def _process_pending_orders(self, data, place_order):
        """Process rows with pending orders - checking order status"""
        # Filter rows where either open or close is pending
        mask = (data['open_order_status'] == 'open_pending') | (data['close_order_status'] == 'close_pending')
        for idx, row in data[mask].iterrows():
            try:
                # Determine which order to check
                is_open_pending = row['open_order_status'] == 'open_pending'
                order_id = row['buy_order_id'] if is_open_pending else row['close_order_id']
                order_type = 'open' if is_open_pending else 'close'

                logger.info(f"Checking {order_type} order {order_id} for row {row['sl_no']} ({row['symbol']})")

                # Check for invalid order_id
                if order_id == -1 or pd.isna(order_id):
                    logger.error(f"Invalid order_id ({order_id}) for row {row['sl_no']}")
                    # Internally mark as rejected so we can retry if needed, but don't set status to 'rejected'
                    data.loc[idx, f'{order_type}_order_status'] = 'rejected'
                    self.notifier.send_error(row['account'], row['symbol'], order_type,
                                           f"Order failed - invalid order_id: {order_id}")
                    # Save immediately to persist internal status change
                    data.to_csv(self.csv_path, index=False)
                    continue

                status, final_price = place_order.order_status(row['account'], order_id, row['buy_price'])

                if status == "Complete":
                    if not is_open_pending:
                        # Close order completed
                        data.loc[idx, 'sell_price'] = final_price
                        data.loc[idx, 'close_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        profit_loss = (final_price - row['buy_price']) * row['quantity']

                        # Send success notification
                        self.notifier.send_success(row['account'], row['symbol'], "p/l",
                                                  f"{row.get('strategy', 'cash')} {profit_loss}")

                        brokarage_dict = brokrage_calculator.calculate_equity_delivery(
                            row['buy_price'], final_price, row['quantity'])
                        brokrage = brokarage_dict['total_charges']

                        pl_dict = {
                            'Date': datetime.now().strftime("%Y-%m-%d"),
                            'Account': row['account'],
                            'Symbol': row['symbol'],
                            'Quantity': row['quantity'],
                            'NumberofTrade': 1,
                            'TotalPNL': profit_loss,
                            'Brokerage': brokrage,
                            'CloseTime': datetime.now().strftime("%H:%M:%S"),
                            'Stratergy': 'cash_short',
                            'NetPNL': profit_loss - brokrage
                        }

                        current_month = datetime.now().strftime("%m")
                        file_name = f"pnl/consolidated_pnl_{current_month}.csv"
                        os.makedirs("pnl", exist_ok=True)

                        if os.path.exists(file_name):
                            df = pd.read_csv(file_name)
                            df = pd.concat([df, pd.DataFrame([pl_dict])], ignore_index=True)
                        else:
                            df = pd.DataFrame([pl_dict])

                        df.to_csv(file_name, index=False)

                        # Update internal order status
                        data.loc[idx, 'close_order_status'] = 'Complete'

                        # IMPORTANT: Only move to 'close' status for deepti account
                        if row['account'] == 'deepti':
                            data.loc[idx, 'status'] = 'close'
                            logger.info(f"Row {row['sl_no']} moved to 'close' status (Account: deepti)")
                        else:
                            logger.info(f"Row {row['sl_no']} stays in 'open' status after successful close (Account: {row['account']})")
                    else:
                        # Open order completed
                        data.loc[idx, 'buy_price'] = final_price
                        data.loc[idx, 'open_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        data.loc[idx, 'open_order_status'] = 'Complete'
                        data.loc[idx, 'status'] = 'open'
                        logger.info(f"Row {row['sl_no']} open order complete")

                elif status in ["Rejected", "Cancelled", "Failed"]:
                    # Handle rejected/failed orders
                    logger.error(f"Order {order_id} for row {row['sl_no']} has status: {status}")
                    data.loc[idx, f'{order_type}_order_status'] = 'rejected'
                    # status remains 'new' (if open failed) or 'open' (if close failed)
                    self.notifier.send_error(row['account'], row['symbol'], order_type,
                                           f"Order {status.lower()} - order_id: {order_id}")
                    # Save immediately to persist status change
                    data.to_csv(self.csv_path, index=False)

                else:
                    # Order still pending or in unknown state
                    logger.info(f"Order {order_id} for row {row['sl_no']} still pending with status: {status}")

            except Exception as e:
                print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                logger.error(f"Error processing 'pending' row {row['sl_no']}: {e}")
                data.loc[idx, f'{order_type}_order_status'] = 'rejected'
                self.notifier.send_error(row['account'], row['symbol'], order_type, str(e)[:50])
                # Save immediately to persist status change
                data.to_csv(self.csv_path, index=False)

    def execute_strategy(self, place_order, max_executions=2):
        """
        Executes the cash strategy based on the CSV file and strategy rules.

        Args:
            place_order (PlaceOrder): Instance of PlaceOrder to handle orders.
            max_executions (int): Maximum number of executions allowed in the morning and afternoon.
        """

        # return if time is less than 9:15
        now = datetime.now()
        if datetime.strptime("09:15:00", "%H:%M:%S").time() > now.time():
            return

        # Check NFO market is open or not
        if self.nso_open is None:
            exchange_data = ExchangeData()
            exchange_data_var = exchange_data.is_nfo_open()
            if exchange_data_var is False:
                print("NFO market is closed")
                self.nso_open = False
                return
            else:
                self.nso_open = True

        elif self.nso_open is False:
            return

        # Determine if the function can execute based on the time of day
        now = datetime.now()
        if datetime.strptime("09:27:00", "%H:%M:%S").time() <= now.time() <= datetime.strptime("09:33:00", "%H:%M:%S").time():
            logger.info(f"Execution tracker morning count: {self.execution_tracker['morning']}")
            print(f"Execution tracker morning count: {self.execution_tracker['morning']}")
            # Check morning executions limit:
            if self.execution_tracker["morning"] >= max_executions:
                logger.info("Maximum exceeded.")
                return
            self.execution_tracker["morning"] += 1
        elif datetime.strptime("15:16:00", "%H:%M:%S").time() <= now.time() <= datetime.strptime("15:26:00", "%H:%M:%S").time():
            logger.info(f"Execution tracker evening count: {self.execution_tracker['afternoon']}")
            # Check afternoon executions limit:
            if self.execution_tracker["afternoon"] >= max_executions + 1:
                logger.info("Maximum exceeded.")
                return

            if self.execution_tracker["afternoon"] == max_executions:
                self.send_csv()

            self.execution_tracker["afternoon"] += 1
        else:
            return

        self.apply_corrections()

        # Load the CSV
        logger.info("Executing cash strategy.")
        data = pd.read_csv(self.csv_path)

        # Process different order states using helper methods
        self._process_new_orders(data, place_order)
        self._process_open_positions(data, place_order)
        self._process_pending_orders(data, place_order)

        # Save the updated CSV
        data.to_csv(self.csv_path, index=False)
        print("Strategy executed and CSV updated.")

    # Function to send csv file over telegram
    def send_csv(self):
        """Sends the strategy CSV file via Telegram."""
        try:
            logger.info("Sending CSV file over Telegram.")

            # Verify file exists
            if not os.path.exists(self.csv_path):
                logger.error(f"CSV file not found at path: {self.csv_path}")
                return False

            # Get Telegram group ID
            telegram_group = "dummy" + "_telegram"
            id1 = configuration.ConfigurationLoader.get_configuration().get(telegram_group)

            if not id1:
                logger.error(f"Could not find Telegram ID for group: {telegram_group}")
                return False

            # Initialize Telegram API and send file
            self.telegram_api.send_file(id1, self.csv_path)

        except Exception as e:
            logger.error(f"Error sending CSV file to Telegram: {str(e)}")
            print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
            return False


"""
# Example usage:
if __name__ == "__main__":

    from PlaceOrder import PlaceOrder

    accounts = ["dummy", "deepti"]
    #accounts = ["dummy"]
    symbols = ["BANKNIFTY"]

    place_order = PlaceOrder()

    # Initalize all accounts
    for account in accounts:
        place_order.init_account(account)

    auto_straddle_strategy = cash_stratergy()
    auto_straddle_strategy.sync_cash_strategy()
    auto_straddle_strategy.execute_strategy(place_order)  # Pass the place_order argument
"""
