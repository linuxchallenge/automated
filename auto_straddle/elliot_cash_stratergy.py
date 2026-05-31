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
from pathlib import Path
import traceback
import logging
from time import sleep
import threading
import time
import signal
import requests
import pandas as pd
import TelegramSend
import configuration
from exchange_state import ExchangeData
import brokrage_calculator
from elliot_wave_strategy import StrategyConfig, compute_atr
from cash_stratergy import shared_price_cache, get_angel_ltp

ELLIOT_DATA_DIR = Path.home() / 'temp' / 'data_collection' / 'elliot'


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

    def send_buy_failed(self, account, symbol, quantity, sl, percent_increase):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        chat_id = self._get_chat_id(account)
        if not chat_id:
            return False
        message = (
            f"EW BUY FAILED {account} {symbol}\n"
            f"Qty: {quantity} | SL: {sl} | Target: +{percent_increase}%\n"
            f"Please buy manually and update Google Sheet, or will retry tomorrow."
        )
        try:
            self.telegram_api.send_message(chat_id, message)
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False

    def send_sell_failed(self, account, symbol, quantity, price):
        chat_id = self._get_chat_id(account)
        if not chat_id:
            return False
        message = (
            f"EW SELL FAILED {account} {symbol}\n"
            f"Qty: {quantity} | Price ~{price:.2f}\n"
            f"Please sell manually and update Google Sheet, or will retry next trigger."
        )
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
        ELLIOT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.csv_path = str(ELLIOT_DATA_DIR / 'elliot_cash_stratergy.csv')
        # Remote Google Sheet URL for date-based full-row corrections (same column structure as local CSV)
        self.remote_csv_url = (
            "https://docs.google.com/spreadsheets/d/e/"
            "2PACX-1vTruc_tyeub2h90CDyKxbZ2eggT97R__8a3JLcavhEBhCdfjr9YxvK_U-trRNDQsiaQv8Ec1oHk4y3I"
            "/pub?output=csv"
        )
        self.execution_tracker = {"morning": 0, "afternoon": 0}
        self.nso_open = None
        self._cached_positions = None
        self._last_fetch_time = None
        self.telegram_api = TelegramSend.telegram_send_api()

        self.price_cache = shared_price_cache
        self.notifier = TelegramNotifier(self.telegram_api)

        self._session = None
        self._session_created_at = None
        self._place_order = None
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

        self._config = StrategyConfig()
        self._ohlcv_cache = {}  # symbol -> DataFrame, refreshed once per execute_strategy call
        self._tv_obj = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_tv_obj(self):
        """Lazy-init TvDatafeed (no credentials needed for NSE)."""
        if self._tv_obj is None:
            try:
                from tvDatafeed import TvDatafeed  # pylint: disable=C0415
                self._tv_obj = TvDatafeed()
            except Exception as e:
                logger.error(f"Failed to init TvDatafeed: {e}")
        return self._tv_obj

    def _fetch_recent_ohlcv(self, symbol):
        """Fetch recent 30-bar daily OHLCV for a symbol. Returns DataFrame or None."""
        if symbol in self._ohlcv_cache:
            return self._ohlcv_cache[symbol]

        tv = self._get_tv_obj()
        if tv is None:
            return None

        try:
            from tvDatafeed import Interval  # pylint: disable=C0415
            tv_symbol = symbol.replace('&', '_')
            df = tv.get_hist(symbol=tv_symbol, exchange='NSE', interval=Interval.in_daily, n_bars=30)
            if df is not None and not df.empty:
                df.columns = [c.capitalize() for c in df.columns]
                if 'Symbol' in df.columns:
                    df = df.drop(columns=['Symbol'])
                self._ohlcv_cache[symbol] = df
                return df
        except Exception as e:
            logger.warning(f"Failed to fetch OHLCV for {symbol}: {e}")
        return None

    def _compute_current_atr(self, symbol):
        """Get the latest ATR(14) value for a symbol. Returns float or None."""
        df = self._fetch_recent_ohlcv(symbol)
        if df is None or len(df) < self._config.atr_period + 1:
            return None
        atr_series = compute_atr(df['High'], df['Low'], df['Close'], self._config.atr_period)
        last_atr = atr_series.iloc[-1]
        if pd.isna(last_atr):
            return None
        return float(last_atr)

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
                angel_api = getattr(getattr(self, '_place_order', None), 'obj_1', None)
                if angel_api:
                    logger.info(f"Trying Angel One LTP fallback for {symbol}")
                    price = get_angel_ltp(symbol, angel_api, self.price_cache)
                    if price:
                        return price
                    raise ValueError(f"Angel One LTP fallback returned None for {symbol}") from e
                logger.error(f"No Angel One API available for fallback for {symbol}")
                raise ValueError(f"No Angel One API available for fallback for {symbol}") from e
            except ValueError:
                raise
            except Exception as e2:
                logger.error(f"Angel One LTP fallback also failed for {symbol}: {e2}")
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
        Sync remote Google Sheet (full signals sheet) into local CSV.

        Logic:
          - sl_no NOT in local → insert with status='new'
          - sl_no in local, remote status='close' → close all local copies immediately
          - sl_no in local, other changes → apply only if remote date > local date

        NOTE: This function expects the remote sheet to have the same column structure
        as the local CSV (with a 'status' column). If the remote URL points to the
        manual corrections sheet instead (detected by 'entry_exit' column), it bails
        out to avoid corrupting local data.
        """
        if not self.remote_csv_url or 'PLACEHOLDER' in self.remote_csv_url:
            logger.info("EW sync_elliot_strategy: remote URL not configured, skipping.")
            return
        try:
            remote_data = pd.read_csv(self.remote_csv_url)
        except Exception as e:
            logger.error(f"Failed to download remote EW sheet: {e}")
            return

        # Guard: corrections sheet has 'entry_exit' column; full signals sheet has 'status'.
        # If both URLs are the same (misconfiguration), skip to avoid inserting junk rows.
        if 'entry_exit' in remote_data.columns or 'status' not in remote_data.columns:
            logger.warning("EW sync_elliot_strategy: remote sheet looks like corrections sheet "
                           "(missing 'status' column). Skipping to avoid duplicate rows.")
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

        existing_sl_nos = set(str(x) for x in local_data['sl_no'].dropna()) if 'sl_no' in local_data.columns else set()

        for _, row in remote_data.iterrows():
            sl_no = str(row['sl_no'])

            if sl_no not in existing_sl_nos:
                new_row = row.copy()
                if pd.isna(new_row.get('status')) or new_row.get('status') == '':
                    new_row['status'] = 'new'
                local_data = pd.concat([local_data, pd.DataFrame([new_row])], ignore_index=True)
                existing_sl_nos.add(sl_no)  # prevent re-insert if remote has duplicate sl_nos
                logger.info(f"Inserted new EW row: {sl_no}")
            else:
                remote_status = str(row.get('status', '')).strip().lower()
                if remote_status == 'close':
                    sl_no_mask = local_data['sl_no'].astype(str) == sl_no
                    active = local_data.loc[sl_no_mask, 'status'].isin(['open', 'new', 'pending'])
                    if active.any():
                        local_data.loc[sl_no_mask & active, 'status'] = 'close'
                        logger.info(f"Closed EW row(s) from remote sheet: {sl_no}")

        local_data.to_csv(self.csv_path, index=False)
        logger.info("EW sync completed.")

    # ------------------------------------------------------------------
    # Order processing
    # ------------------------------------------------------------------

    def _pet_watchdog(self):
        """Reset the SIGALRM watchdog to 300s. Called between rows so a slow
        row doesn't time out the entire strategy run."""
        try:
            signal.alarm(300)  # pylint: disable=no-member
        except Exception:
            pass  # Non-Linux environments don't support SIGALRM

    def _process_new_orders(self, data, place_order):
        """Process rows with status 'new' — place BUY orders."""
        for idx, row in data[data['status'] == 'new'].iterrows():
            self._pet_watchdog()
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
                        logger.warning(f"EW: BUY attempt {attempt + 1}/{self._max_order_retries} failed for {symbol} (order_id={order_id})")
                        if attempt < self._max_order_retries - 1:
                            sleep(2 * (attempt + 1))

                    if not order_id or (isinstance(order_id, float) and pd.isna(order_id)):
                        logger.error(f"EW: BUY order failed after all retries for {symbol}")
                        data.loc[idx, 'open_order_status'] = 'buy_failed'
                        data.to_csv(self.csv_path, index=False)
                        self.notifier.send_buy_failed(
                            row['account'], symbol, quantity,
                            row['sl'], row['percent_increase']
                        )
                        continue

                    # Compute profit_target from percent_increase
                    profit_target = last_price * (1 + float(row['percent_increase']) / 100)

                    data.loc[idx, 'buy_order_id'] = order_id
                    data.loc[idx, 'buy_price'] = last_price
                    data.loc[idx, 'open_order_status'] = 'open_pending'
                    data.loc[idx, 'status'] = 'open'
                    data.loc[idx, 'quantity'] = quantity
                    data.loc[idx, 'profit_target'] = profit_target
                    data.loc[idx, 'trailing_stop'] = float(row.get('sl') or 0)
                    data.loc[idx, 'highest_close'] = last_price
                    data.loc[idx, 'days_held'] = 0

                    data.to_csv(self.csv_path, index=False)
                    logger.info(f"EW: BUY order placed for {symbol} order_id={order_id} profit_target={profit_target:.2f}")
                else:
                    logger.info(f"EW: Skipping {row['sl_no']} — price {last_price} not above sl {row['sl']}")

            except Exception as e:
                print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                logger.error(f"EW: Error processing new row {row['sl_no']}: {e}")
                self.notifier.send_error(row['account'], symbol, "open", str(e))

    def _update_trailing_stop(self, data, idx, row, last_price):
        """Update trailing stop and tracking fields for an open position.

        Matches backtest logic:
          highest_close = max(highest_close, current_price)
          trailing_stop = max(trailing_stop, highest_close - 2.5 * ATR(14))
          days_held += 1  (incremented once per execution cycle)
        """
        symbol = row['symbol']

        # Update highest close
        highest_close = float(row.get('highest_close') or 0)
        if last_price > highest_close:
            highest_close = last_price
            data.loc[idx, 'highest_close'] = highest_close

        # Update days held
        days_held_val = row.get('days_held')
        days_held = int(float(days_held_val)) if pd.notna(days_held_val) else 0
        open_date = row.get('open_date')
        if pd.notna(open_date):
            try:
                open_dt = pd.to_datetime(open_date)
                days_held = (datetime.now() - open_dt).days
                data.loc[idx, 'days_held'] = days_held
            except Exception:
                days_held = days_held + 1
                data.loc[idx, 'days_held'] = days_held

        # Update trailing stop using ATR
        trailing_stop = float(row.get('trailing_stop') or row.get('sl') or 0)
        atr_val = self._compute_current_atr(symbol)
        if atr_val is not None and highest_close > 0:
            new_trailing = highest_close - (self._config.trailing_atr_multiplier * atr_val)
            if new_trailing > trailing_stop:
                trailing_stop = new_trailing
                data.loc[idx, 'trailing_stop'] = trailing_stop
                logger.info(f"EW: Trailing stop updated for {symbol}: {trailing_stop:.2f} (ATR={atr_val:.2f})")

        return trailing_stop, days_held

    def _trigger_sell(self, data, idx, row, place_order, *, last_price):  # pylint: disable=too-many-arguments
        """Place SELL order (API or manual) and update CSV. Returns True if handled."""
        symbol = row['symbol']

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
                data.loc[idx, 'close_order_id'] = None
                data.loc[idx, 'close_order_status'] = 'sell_failed'
                data.to_csv(self.csv_path, index=False)
                self.notifier.send_sell_failed(
                    row['account'], symbol,
                    row['quantity'], last_price
                )
                return False

            data.loc[idx, 'close_order_id'] = order_id
            data.loc[idx, 'close_order_status'] = 'close_pending'
            data.loc[idx, 'close_date'] = datetime.now().strftime("%Y-%m-%d")
        else:
            data.loc[idx, 'close_order_status'] = 'close_pending'
            data.loc[idx, 'close_date'] = datetime.now().strftime("%Y-%m-%d")
            self.notifier.send_manual_close_request(row['account'], symbol)

        data.to_csv(self.csv_path, index=False)
        return True

    def _process_open_positions(self, data, place_order, resume_from_sl_no=None):
        """Process rows with status 'open' — check SL/trailing stop/target/time stop.

        Exit priority (matches backtest):
          1. Hard stop loss (last_price <= sl)
          2. Trailing stop   (last_price <= trailing_stop)
          3. Profit target   (last_price >= profit_target)
          4. Time stop       (days_held >= time_stop_days, default 90)
        """
        open_rows = data[data['status'] == 'open']
        should_skip = resume_from_sl_no is not None
        last_sl_no = None

        for idx, row in open_rows.iterrows():
            if should_skip:
                if row['sl_no'] == resume_from_sl_no:
                    should_skip = False
                continue

            self._pet_watchdog()
            if self._is_time_budget_exceeded():
                logger.info(f"EW: Time budget exceeded at row {row['sl_no']}")
                return last_sl_no, False

            try:
                close_status = row.get('close_order_status')
                # Skip rows already in a terminal or pending close state,
                # but allow retry when previous SELL attempt failed.
                if pd.notna(row.get('close_order_id')) and row['close_order_id'] != -1 \
                        and close_status not in ('rejected', 'sell_failed'):
                    if close_status != 'Complete':
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

                # Update trailing stop, highest_close, days_held
                trailing_stop, days_held = self._update_trailing_stop(data, idx, row, last_price)

                profit_target = row.get('profit_target', float('inf'))
                if pd.isna(profit_target):
                    profit_target = float('inf')

                hard_sl = float(row.get('sl') or 0)

                logger.info(
                    f"EW: {row['sl_no']} ({symbol}): price={last_price} "
                    f"hard_sl={hard_sl} trailing={trailing_stop:.2f} "
                    f"target={profit_target} days={days_held}"
                )

                # --- EXIT CHECKS (priority order, matching backtest) ---
                reason = None

                # 1. Hard stop loss
                if last_price <= hard_sl:
                    reason = "Stop_Loss"

                # 2. Trailing stop
                elif trailing_stop > hard_sl and last_price <= trailing_stop:
                    reason = "Trailing_Stop"

                # 3. Profit target
                elif last_price >= profit_target:
                    reason = "Target_Hit"

                # 4. Time stop (90 days default)
                elif days_held >= self._config.time_stop_days:
                    reason = "Time_Stop"

                if reason:
                    logger.info(f"EW: Exit triggered for {symbol} ({row['sl_no']}). Reason: {reason}")
                    self._trigger_sell(data, idx, row, place_order, last_price=last_price)

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
                        # close_date was set when the SELL order was placed; keep it.
                        # Fall back to today only if missing (e.g. legacy rows).
                        close_date_val = row.get('close_date')
                        if pd.isna(close_date_val) or not close_date_val:
                            close_date_val = datetime.now().strftime("%Y-%m-%d")
                            data.loc[idx, 'close_date'] = close_date_val

                        profit_loss = (final_price - row['buy_price']) * row['quantity']

                        self.notifier.send_success(row['account'], row['symbol'], "p/l",
                                                   f"elliot_wave {profit_loss}")

                        brokerage_dict = brokrage_calculator.calculate_equity_delivery(
                            row['buy_price'], final_price, row['quantity'])
                        brokerage = brokerage_dict['total_charges']

                        pl_dict = {
                            'Date': str(close_date_val)[:10],
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

                        current_month = str(close_date_val)[5:7]  # MM from YYYY-MM-DD
                        pnl_dir = Path.home() / 'temp' / 'data_collection' / 'pnl'
                        pnl_dir.mkdir(parents=True, exist_ok=True)
                        file_name = str(pnl_dir / f"consolidated_pnl_{current_month}.csv")

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

                        # Initialize trailing stop tracking with actual fill price
                        data.loc[idx, 'highest_close'] = final_price
                        data.loc[idx, 'trailing_stop'] = float(row.get('sl') or 0)
                        data.loc[idx, 'days_held'] = 0

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
        # Store place_order ref for Angel One LTP fallback
        self._place_order = place_order

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
        self._ohlcv_cache = {}  # Fresh OHLCV data each execution cycle

        logger.info("EW: Executing Elliott Wave cash strategy.")
        try:
            data = pd.read_csv(self.csv_path)
        except FileNotFoundError:
            logger.warning("EW: CSV not found, nothing to process.")
            return

        new_count = len(data[data['status'] == 'new']) if 'status' in data.columns else 0
        open_count = len(data[data['status'] == 'open']) if 'status' in data.columns else 0
        pending_count = len(data[data['status'] == 'pending']) if 'status' in data.columns else 0
        logger.info(f"EW: Rows — new={new_count} open={open_count} pending={pending_count}")

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
