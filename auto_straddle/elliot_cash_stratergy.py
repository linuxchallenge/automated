"""Elliott Wave Cash Strategy — executes BUY/SELL orders based on EW signals."""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=W0105
# pylint: disable=C0302
# pylint: disable=R0902
# pylint: disable=R0911
# pylint: disable=R0912
# pylint: disable=R0914
# pylint: disable=R0915
# pylint: disable=R1702

from datetime import datetime, timedelta
import os
import traceback
import logging
from time import sleep
import threading
import time
import requests
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

logger = logging.getLogger(__name__)


class NSEPriceCache:
    """Thread-safe cache for NSE price data with TTL and rate limiting."""

    def __init__(self, ttl_seconds=120, rate_limit_calls=20, rate_limit_period=60):
        self._cache = {}
        self._lock = threading.Lock()
        self.ttl = timedelta(seconds=ttl_seconds)
        self._call_timestamps = []
        self.rate_limit_calls = rate_limit_calls
        self.rate_limit_period = timedelta(seconds=rate_limit_period)

    def get(self, symbol):
        with self._lock:
            if symbol in self._cache:
                entry = self._cache[symbol]
                if datetime.now() - entry['timestamp'] < self.ttl:
                    return entry['price']
                del self._cache[symbol]
            return None

    def set(self, symbol, price):
        with self._lock:
            self._cache[symbol] = {'price': price, 'timestamp': datetime.now()}

    def can_make_call(self):
        with self._lock:
            now = datetime.now()
            self._call_timestamps = [
                ts for ts in self._call_timestamps
                if now - ts < self.rate_limit_period
            ]
            if len(self._call_timestamps) >= self.rate_limit_calls:
                oldest_call = self._call_timestamps[0]
                wait_time = (oldest_call + self.rate_limit_period - now).total_seconds()
                return False, wait_time
            return True, 0

    def record_call(self):
        with self._lock:
            self._call_timestamps.append(datetime.now())

    def clear(self):
        with self._lock:
            self._cache.clear()


class TelegramNotifier:
    """Centralized handler for Telegram notifications."""

    def __init__(self, telegram_api):
        self.telegram_api = telegram_api
        self._lock = threading.Lock()

    def _get_chat_id(self, account):
        account = str(account)
        if account in ["sharekhan", "anvitha", "adithya"]:
            telegram_group = "deepti_telegram"
        else:
            telegram_group = account + "_telegram"
        return configuration.ConfigurationLoader.get_configuration().get(telegram_group)

    def send_error(self, account, symbol, error_type, details=""):
        message = f"EW strategy {error_type} error {account} {symbol}"
        if details:
            message += f": {details}"
        chat_id = self._get_chat_id(account)
        if not chat_id:
            return False
        try:
            self.telegram_api.send_message(chat_id, message)
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False

    def send_success(self, account, symbol, message_type, details):
        message = f"EW strategy {message_type} {account} {symbol} {details}"
        chat_id = self._get_chat_id(account)
        if not chat_id:
            return False
        try:
            self.telegram_api.send_message(chat_id, message)
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False

    def send_manual_close_request(self, account, symbol):
        chat_id = self._get_chat_id(account)
        if not chat_id:
            return False
        message = f"EW strategy please close {account} {symbol}"
        try:
            self.telegram_api.send_message(chat_id, message)
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False


class ElliotCashStratergy:
    """Executes BUY/SELL equity orders driven by Elliott Wave signals CSV."""

    SYMBOL_MAPPING = {
        'M_M': 'M&M',
        'M&MFIN': 'M&MFIN',
        'L_T': 'LT',
    }

    def __init__(self):
        self.csv_path = "elliot_cash_stratergy.csv"
        # Remote Google Sheet URL for manual corrections (same column structure as local CSV)
        # User must set this to the correct URL
        self.remote_csv_url = "https://docs.google.com/spreadsheets/d/PLACEHOLDER_EW_SHEET_ID/export?format=csv"
        self.execution_tracker = {"morning": 0, "afternoon": 0}
        self.nso_open = None
        self._cached_positions = None
        self._last_fetch_time = None
        self.telegram_api = TelegramSend.telegram_send_api()

        self.price_cache = NSEPriceCache(ttl_seconds=60, rate_limit_calls=10, rate_limit_period=60)
        self.notifier = TelegramNotifier(self.telegram_api)

        self._session = None
        self._session_created_at = None
        self._session_ttl = timedelta(minutes=10)

        self._order_retry_count = {}
        self._max_order_retries = 3

        self._csv_sent_date = None

        self._resume_state = {
            'phase': None,
            'last_sl_no': None,
            'start_time': None
        }
        self._time_budget_seconds = 60

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_time_budget_exceeded(self):
        if self._resume_state['start_time'] is None:
            return False
        elapsed = (datetime.now() - self._resume_state['start_time']).total_seconds()
        return elapsed >= self._time_budget_seconds

    def _normalize_symbol(self, symbol):
        return self.SYMBOL_MAPPING.get(symbol, symbol)

    def _get_session(self):
        now = datetime.now()
        if self._session is None or (self._session_created_at and now - self._session_created_at > self._session_ttl):
            if self._session:
                try:
                    self._session.close()
                except Exception as e:
                    logger.warning(f"Error closing old session: {e}")
            self._session = requests.Session()
            self._session_created_at = now
        return self._session

    def get_nse_ltp(self, symbol, max_retries=3, use_cache=True):
        if use_cache:
            cached_price = self.price_cache.get(symbol)
            if cached_price is not None:
                return cached_price

        for attempt in range(max_retries):
            can_call, wait_time = self.price_cache.can_make_call()
            if not can_call:
                logger.warning(f"Rate limit hit, waiting {wait_time:.1f}s before retry")
                sleep(wait_time + 1)
                continue

            try:
                session = self._get_session()
                main_url = f"https://www.nseindia.com/get-quotes/equity?symbol={symbol}"
                session.get(main_url, headers=headers, timeout=10)

                api_url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
                headers_with_referer = headers.copy()
                headers_with_referer["Referer"] = main_url

                self.price_cache.record_call()
                response = session.get(api_url, headers=headers_with_referer, timeout=10)

                if response.status_code == 200:
                    price = response.json()["priceInfo"]["lastPrice"]
                    self.price_cache.set(symbol, price)
                    return price
                logger.warning(f"Failed to fetch price for {symbol} (attempt {attempt + 1}/{max_retries}): HTTP {response.status_code}")
                if attempt == max_retries - 1:
                    raise ValueError(f"Failed to fetch price for {symbol}: HTTP {response.status_code}")

            except requests.exceptions.RequestException as e:
                logger.warning(f"Network error fetching price for {symbol} (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    raise ValueError(f"Failed to fetch price for {symbol}: {e}") from e
            except Exception as e:
                logger.error(f"Error fetching price for {symbol}: {e}")
                raise

            if attempt < max_retries - 1:
                time.sleep((attempt + 1) * 2)
        return None

    def nsefetch(self, payload):
        try:
            output = requests.get(payload, headers=headers, timeout=10).json()
        except ValueError:
            s = requests.Session()
            output = s.get("http://nseindia.com", headers=headers)
            output = s.get(payload, headers=headers).json()
        return output

    def nse_custom_function_secfno(self, symbol, attribute="lastPrice"):
        current_time = datetime.now()
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
                if positions['data'][x]['symbol'] == symbol.upper():
                    value = float(positions['data'][x][attribute])
                    return value
        except Exception as e:
            logger.error(f"Error fetching data from NSE: {e}")
        return None

    def get_nse_ltp_with_fallback(self, symbol):
        try:
            return self.get_nse_ltp(symbol, max_retries=3)
        except Exception as e:
            logger.warning(f"Primary NSE API failed for {symbol}: {e}")
            try:
                price = self.nse_custom_function_secfno(symbol, "lastPrice")
                if price:
                    self.price_cache.set(symbol, price)
                    return price
                raise ValueError(f"Fallback method returned None for {symbol}") from e
            except ValueError:
                raise
            except Exception as e2:
                logger.error(f"Fallback method also failed for {symbol}: {e2}")
                raise ValueError(f"All methods failed to fetch price for {symbol}") from e

    def _reset_retry_count(self, account, symbol, order_type):
        key = (account, symbol, order_type)
        if key in self._order_retry_count:
            del self._order_retry_count[key]

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    def sync_elliot_strategy(self):
        """
        Date-based sync: download remote Google Sheet and merge into local CSV.

        Logic:
          - If sl_no NOT in local → insert row with status='new'
          - If sl_no IN local AND gsheet_row['date'] > local_row['date'] → update local row
            (handles manual corrections: buy_price, status, etc.)
        """
        try:
            remote_data = pd.read_csv(self.remote_csv_url)
        except Exception as e:
            logger.error(f"Failed to download remote EW sheet: {e}")
            return

        remote_data['date'] = pd.to_datetime(remote_data['date'], errors='coerce')

        try:
            local_data = pd.read_csv(self.csv_path)
            if 'sl_no' not in local_data.columns:
                logger.warning("Local EW CSV missing 'sl_no' column. Recreating.")
                local_data = pd.DataFrame(columns=remote_data.columns)
        except FileNotFoundError:
            logger.warning("Local EW CSV not found. Creating new one.")
            local_data = pd.DataFrame(columns=remote_data.columns)

        local_data['date'] = pd.to_datetime(local_data['date'], errors='coerce')

        existing_sl_nos = set(local_data['sl_no'].dropna().unique()) if 'sl_no' in local_data.columns else set()

        for _, row in remote_data.iterrows():
            sl_no = row['sl_no']

            if sl_no not in existing_sl_nos:
                # Insert as new
                new_row = row.copy()
                if pd.isna(new_row.get('status')) or new_row.get('status') == '':
                    new_row['status'] = 'new'
                local_data = pd.concat([local_data, pd.DataFrame([new_row])], ignore_index=True)
                logger.info(f"Inserted new EW row: {sl_no}")
            else:
                # Update if remote date is newer than local date
                local_row = local_data[local_data['sl_no'] == sl_no].iloc[0]
                remote_date = row.get('date')
                local_date = local_row.get('date')

                if pd.notna(remote_date) and pd.notna(local_date) and remote_date > local_date:
                    # Update all fields from remote
                    mask = local_data['sl_no'] == sl_no
                    for col in remote_data.columns:
                        if col in local_data.columns:
                            local_data.loc[mask, col] = row[col]
                    logger.info(f"Updated EW row from remote: {sl_no} (remote_date={remote_date}, local_date={local_date})")

        local_data.to_csv(self.csv_path, index=False)
        logger.info("EW sync completed.")

    # ------------------------------------------------------------------
    # Order processing
    # ------------------------------------------------------------------

    def _process_new_orders(self, data, place_order):
        """Process rows with status 'new' — place BUY orders."""
        for idx, row in data[data['status'] == 'new'].iterrows():
            logger.info(f"EW: Processing new row {row['sl_no']} symbol={row['symbol']} sl={row['sl']}")
            try:
                symbol = row['symbol']
                normalized_symbol = self._normalize_symbol(symbol)

                sleep(1)
                try:
                    last_price = self.get_nse_ltp_with_fallback(normalized_symbol)
                except Exception as e:
                    logger.error(f"EW: Price fetch failed for {symbol}: {e}")
                    self.notifier.send_error(row['account'], symbol, "open", f"Price fetch failed: {e}")
                    continue

                logger.info(f"EW: {row['sl_no']} symbol={symbol} last_price={last_price} sl={row['sl']}")

                if last_price > row['sl']:
                    quantity = int(float(row['amount']) / last_price)

                    order_id = None
                    for attempt in range(self._max_order_retries):
                        order_id = place_order.place_cash_order(row['account'], symbol, quantity, "BUY")
                        if order_id and not (isinstance(order_id, float) and pd.isna(order_id)):
                            self._reset_retry_count(row['account'], symbol, "open")
                            break
                        logger.warning(f"EW: BUY attempt {attempt + 1}/{self._max_order_retries} failed for {symbol}")
                        if attempt < self._max_order_retries - 1:
                            sleep(2 * (attempt + 1))

                    if not order_id or (isinstance(order_id, float) and pd.isna(order_id)):
                        logger.error(f"EW: BUY order failed after all retries for {symbol}")
                        self.notifier.send_error(row['account'], symbol, "open", "Order placement failed after retries")
                        continue

                    # Compute profit_target from percent_increase
                    profit_target = last_price * (1 + float(row['percent_increase']) / 100)

                    data.loc[idx, 'buy_order_id'] = order_id
                    data.loc[idx, 'buy_price'] = last_price
                    data.loc[idx, 'open_order_status'] = 'open_pending'
                    data.loc[idx, 'status'] = 'open'
                    data.loc[idx, 'quantity'] = quantity
                    data.loc[idx, 'profit_target'] = profit_target

                    data.to_csv(self.csv_path, index=False)
                    logger.info(f"EW: BUY order placed for {symbol} order_id={order_id} profit_target={profit_target:.2f}")
                else:
                    logger.info(f"EW: Skipping {row['sl_no']} — price {last_price} not above sl {row['sl']}")

            except Exception as e:
                print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                logger.error(f"EW: Error processing new row {row['sl_no']}: {e}")
                self.notifier.send_error(row['account'], symbol, "open", str(e))

    def _process_open_positions(self, data, place_order, resume_from_sl_no=None):
        """Process rows with status 'open' — check SL/profit target and place SELL orders."""
        open_rows = data[data['status'] == 'open']
        should_skip = resume_from_sl_no is not None
        last_sl_no = None

        for idx, row in open_rows.iterrows():
            if should_skip:
                if row['sl_no'] == resume_from_sl_no:
                    should_skip = False
                continue

            if self._is_time_budget_exceeded():
                logger.info(f"EW: Time budget exceeded at row {row['sl_no']}")
                return last_sl_no, False

            try:
                if pd.notna(row.get('close_order_id')) and row['close_order_id'] != -1 and row.get('close_order_status') != 'rejected':
                    if row.get('close_order_status') != 'Complete':
                        last_sl_no = row['sl_no']
                        continue

                symbol = row['symbol']
                normalized_symbol = self._normalize_symbol(symbol)
                sleep(1)

                try:
                    last_price = self.get_nse_ltp_with_fallback(normalized_symbol)
                except Exception as e:
                    logger.error(f"EW: Price fetch failed for {symbol}: {e}")
                    self.notifier.send_error(row['account'], symbol, "close", f"Price fetch failed: {e}")
                    last_sl_no = row['sl_no']
                    continue

                if last_price is None:
                    logger.error(f"EW: None price for {symbol}, skipping")
                    last_sl_no = row['sl_no']
                    continue

                profit_target = row.get('profit_target', float('inf'))
                if pd.isna(profit_target):
                    profit_target = float('inf')

                logger.info(f"EW: {row['sl_no']} ({symbol}): price={last_price} sl={row['sl']} target={profit_target}")

                if last_price <= row['sl'] or last_price >= profit_target:
                    reason = "Stop Loss" if last_price <= row['sl'] else "Profit Target"
                    logger.info(f"EW: Exit triggered for {symbol} ({row['sl_no']}). Reason: {reason}")

                    if row['account'] == "deepti":
                        order_id = None
                        for attempt in range(self._max_order_retries):
                            order_id = place_order.place_cash_order(row['account'], symbol, row['quantity'], "SELL")
                            if order_id and not (isinstance(order_id, float) and pd.isna(order_id)):
                                self._reset_retry_count(row['account'], symbol, "close")
                                break
                            if attempt < self._max_order_retries - 1:
                                sleep(2 * (attempt + 1))

                        if not order_id or (isinstance(order_id, float) and pd.isna(order_id)):
                            logger.error(f"EW: SELL order failed after retries for {symbol}")
                            self.notifier.send_error(row['account'], symbol, "close", "Order placement failed after retries")
                            last_sl_no = row['sl_no']
                            continue

                        data.loc[idx, 'close_order_id'] = order_id
                        data.loc[idx, 'close_order_status'] = 'close_pending'
                    else:
                        data.loc[idx, 'close_order_status'] = 'close_pending'
                        self.notifier.send_manual_close_request(row['account'], symbol)

                    data.to_csv(self.csv_path, index=False)

                last_sl_no = row['sl_no']

            except Exception as e:
                print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                logger.error(f"EW: Error processing open row {row['sl_no']}: {e}")
                self.notifier.send_error(row['account'], symbol, "close", str(e))
                last_sl_no = row['sl_no']

        return last_sl_no, True

    def _process_pending_orders(self, data, place_order):
        """Process rows with pending orders — check order status and record PnL."""
        mask = (data['open_order_status'] == 'open_pending') | (data['close_order_status'] == 'close_pending')
        for idx, row in data[mask].iterrows():
            try:
                is_open_pending = row['open_order_status'] == 'open_pending'
                order_id = row['buy_order_id'] if is_open_pending else row['close_order_id']
                order_type = 'open' if is_open_pending else 'close'

                if order_id == -1 or pd.isna(order_id):
                    if row['account'] not in ['deepti']:
                        logger.debug(f"EW: Skipping {row['sl_no']} — non-API account with manual close pending")
                        continue
                    logger.error(f"EW: Invalid order_id ({order_id}) for row {row['sl_no']}")
                    data.loc[idx, f'{order_type}_order_status'] = 'rejected'
                    self.notifier.send_error(row['account'], row['symbol'], order_type,
                                             f"Order failed - invalid order_id: {order_id}")
                    data.to_csv(self.csv_path, index=False)
                    continue

                status, final_price = place_order.order_status(row['account'], order_id, row['buy_price'])

                if status == "Complete":
                    if not is_open_pending:
                        data.loc[idx, 'sell_price'] = final_price
                        data.loc[idx, 'close_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        profit_loss = (final_price - row['buy_price']) * row['quantity']

                        self.notifier.send_success(row['account'], row['symbol'], "p/l",
                                                   f"elliot_wave {profit_loss}")

                        brokerage_dict = brokrage_calculator.calculate_equity_delivery(
                            row['buy_price'], final_price, row['quantity'])
                        brokerage = brokerage_dict['total_charges']

                        pl_dict = {
                            'Date': datetime.now().strftime("%Y-%m-%d"),
                            'Account': row['account'],
                            'Symbol': row['symbol'],
                            'Quantity': row['quantity'],
                            'NumberofTrade': 1,
                            'TotalPNL': profit_loss,
                            'Brokerage': brokerage,
                            'CloseTime': datetime.now().strftime("%H:%M:%S"),
                            'Stratergy': 'elliot_wave',
                            'NetPNL': profit_loss - brokerage
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
                        data.loc[idx, 'close_order_status'] = 'Complete'

                        if row['account'] == 'deepti':
                            data.loc[idx, 'status'] = 'close'
                    else:
                        data.loc[idx, 'buy_price'] = final_price
                        data.loc[idx, 'open_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        data.loc[idx, 'open_order_status'] = 'Complete'
                        data.loc[idx, 'status'] = 'open'

                        # Recompute profit_target with actual fill price
                        try:
                            pct = float(row['percent_increase'])
                            data.loc[idx, 'profit_target'] = final_price * (1 + pct / 100)
                        except Exception:
                            pass

                elif status in ["Rejected", "Cancelled", "Failed"]:
                    logger.error(f"EW: Order {order_id} for row {row['sl_no']} status: {status}")
                    data.loc[idx, f'{order_type}_order_status'] = 'rejected'
                    self.notifier.send_error(row['account'], row['symbol'], order_type,
                                             f"Order {status.lower()} - order_id: {order_id}")
                    data.to_csv(self.csv_path, index=False)
                else:
                    logger.info(f"EW: Order {order_id} for row {row['sl_no']} still pending: {status}")

            except Exception as e:
                print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                logger.error(f"EW: Error processing pending row {row['sl_no']}: {e}")
                data.loc[idx, f'{order_type}_order_status'] = 'rejected'
                self.notifier.send_error(row['account'], row['symbol'], order_type, str(e)[:50])
                data.to_csv(self.csv_path, index=False)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def execute_strategy(self, place_order, max_executions=2):
        """
        Execute the EW cash strategy.

        Timing windows match cash_stratergy:
          - Morning:   09:27 – 09:33
          - Afternoon: 15:16 – 15:26
        """
        now = datetime.now()
        if datetime.strptime("09:15:00", "%H:%M:%S").time() > now.time():
            return

        if self.nso_open is None:
            exchange_data = ExchangeData()
            exchange_data_var = exchange_data.is_nfo_open()
            if exchange_data_var is False:
                logger.info("EW: NFO market closed")
                self.nso_open = False
                return
            self.nso_open = True
        elif self.nso_open is False:
            return

        is_resuming = self._resume_state.get('phase') is not None

        now = datetime.now()
        if datetime.strptime("09:27:00", "%H:%M:%S").time() <= now.time() <= datetime.strptime("09:33:00", "%H:%M:%S").time():
            if self.execution_tracker["morning"] >= max_executions and not is_resuming:
                return
            if not is_resuming:
                self.execution_tracker["morning"] += 1
        elif datetime.strptime("15:16:00", "%H:%M:%S").time() <= now.time() <= datetime.strptime("15:26:00", "%H:%M:%S").time():
            if self.execution_tracker["afternoon"] >= max_executions + 1 and not is_resuming:
                return

            today = now.date()
            if self._csv_sent_date != today:
                self.send_csv()
                self._csv_sent_date = today

            if not is_resuming:
                self.execution_tracker["afternoon"] += 1
        elif not is_resuming:
            return

        self._resume_state['start_time'] = datetime.now()

        logger.info("EW: Executing Elliott Wave cash strategy.")
        try:
            data = pd.read_csv(self.csv_path)
        except FileNotFoundError:
            logger.warning("EW: CSV not found, nothing to process.")
            return

        current_phase = self._resume_state.get('phase') or 'new'

        if current_phase == 'new':
            self._process_new_orders(data, place_order)
            if self._is_time_budget_exceeded():
                self._resume_state['phase'] = 'open'
                self._resume_state['last_sl_no'] = None
                data.to_csv(self.csv_path, index=False)
                return
            current_phase = 'open'

        if current_phase == 'open':
            resume_sl_no = self._resume_state.get('last_sl_no')
            last_sl_no, completed = self._process_open_positions(data, place_order, resume_sl_no)
            if not completed:
                self._resume_state['phase'] = 'open'
                self._resume_state['last_sl_no'] = last_sl_no
                data.to_csv(self.csv_path, index=False)
                return
            current_phase = 'pending'

        if current_phase == 'pending':
            self._process_pending_orders(data, place_order)

        logger.info("EW: Strategy execution complete")
        self._resume_state = {'phase': None, 'last_sl_no': None, 'start_time': None}
        data.to_csv(self.csv_path, index=False)

    def send_csv(self):
        try:
            if not os.path.exists(self.csv_path):
                logger.error(f"EW: CSV not found at {self.csv_path}")
                return False
            telegram_group = "deepti_telegram"
            chat_id = configuration.ConfigurationLoader.get_configuration().get(telegram_group)
            if not chat_id:
                return False
            self.telegram_api.send_file(chat_id, self.csv_path)
            logger.info("EW: CSV sent via Telegram")
            return True
        except Exception as e:
            logger.error(f"EW: Error sending CSV: {e}")
            return False
