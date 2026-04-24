"""Elliott Wave Signal Generator — generates daily trade signals from Nifty 200 universe."""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=R0902
# pylint: disable=R0903
# pylint: disable=R0912
# pylint: disable=R0914
# pylint: disable=R0915
# pylint: disable=R1702

import json
import logging
import os
import random
import time
import traceback
from datetime import datetime
from io import StringIO

import pandas as pd
import requests
from tvDatafeed import Interval, TvDatafeed

import TelegramSend
import configuration
from elliot_wave_strategy import (
    StrategyConfig,
    detect_swing_points,
    identify_wave_structures,
    generate_signals,
)

# Google Sheet URL for EW account config (account | initial_amount | delta_change | date_sync)
ACCOUNTS_URL = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vTjXLK6OU5QmCMC_GOk88MWL5a5IIKTjq0tlcbrbXAoQiOryhCj78dyv4MR07qBTag8RGwH4twtIrTw"
    "/pub?output=csv"
)

logger = logging.getLogger(__name__)


def _read_csv_from_url(url, max_retries=3):
    """Download a CSV from a URL and return as DataFrame."""
    http_headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=http_headers, timeout=30)
            resp.raise_for_status()
            return pd.read_csv(StringIO(resp.text))
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed to read URL {url}: {e}")
            if attempt == max_retries - 1:
                raise
    return None


class ElliotWaveSignalGenerator:
    """Generates Elliott Wave trade signals from Nifty 200 daily OHLCV data."""

    EW_CSV_COLUMNS = [
        'sl_no', 'symbol', 'account', 'amount', 'sl', 'percent_increase',
        'date', 'status', 'buy_price', 'quantity', 'open_order_status',
        'open_date', 'close_order_status', 'close_order_id', 'sell_price',
        'close_date', 'signal_type', 'confidence', 'profit_target',
        'buy_order_id',
    ]

    def __init__(self, accounts_url: str, nifty200_csv: str):
        """
        Args:
            accounts_url: Google Sheet URL with columns account | amount
            nifty200_csv: Path to ind_nifty200list.csv
        """
        self.accounts_url = accounts_url
        self.nifty200_csv = nifty200_csv
        self.telegram_api = TelegramSend.telegram_send_api()
        self.config = StrategyConfig()

        self.tv_obj = None
        self.tv_error = 0
        self.tv_timeout_retries = 0
        self.max_tv_timeout_retries = 3

        # Load credentials from same file used by commodity_data
        credentials_file = os.path.join(os.path.dirname(__file__), 'tv_credentials.json')
        try:
            with open(credentials_file, 'r', encoding='utf-8') as f:
                self.credentials = json.load(f)
            logger.info(f"Loaded {len(self.credentials)} TV credentials")
        except FileNotFoundError:
            logger.error(f"TV credentials file not found: {credentials_file}")
            self.credentials = []
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing TV credentials file: {e}")
            self.credentials = []

        self._init_tv_connection()

    # ------------------------------------------------------------------
    # TradingView connection (mirrors commodity_data.py pattern)
    # ------------------------------------------------------------------

    def _init_tv_connection(self):
        """Initialize TradingView connection with credential rotation."""
        for attempt in range(5):
            if not self.credentials:
                logger.error("No TV credentials available")
                self.tv_obj = None
                return

            cred = random.choice(self.credentials)
            username = cred['username']
            logger.info(f"TV connection attempt {attempt + 1}/5 with user: {username}")

            try:
                self.tv_obj = TvDatafeed(username, cred['password'], random_user_agent=True)  # pylint: disable=unexpected-keyword-arg
                if self.tv_obj.token != 'unauthorized_user_token':
                    logger.info(f"TV connection successful (token: {self.tv_obj.token})")
                    self.tv_timeout_retries = 0
                    return
            except Exception as e:
                logger.error(f"TV connection error: {e}")

            time.sleep(5)

        logger.error("TV connection failed after 5 attempts")
        self.tv_obj = None

    def _reconnect_tv(self):
        """Reconnect TradingView on persistent failures."""
        logger.warning("Reconnecting TradingView...")
        try:
            if self.tv_obj and hasattr(self.tv_obj, 'ws') and self.tv_obj.ws:
                try:
                    self.tv_obj.ws.close()
                except Exception:
                    pass
        except Exception:
            pass
        self._init_tv_connection()

    # ------------------------------------------------------------------
    # OHLCV fetch
    # ------------------------------------------------------------------

    def _fetch_ohlcv(self, symbol: str) -> pd.DataFrame:
        """
        Fetch fresh daily OHLCV from TradingView (NSE).

        Returns DataFrame with capitalized columns (Open, High, Low, Close, Volume)
        and a DatetimeIndex — format required by detect_swing_points().
        """
        return self._fetch_ohlcv_tv(symbol)

    def _fetch_ohlcv_tv(self, symbol: str, max_retries: int = 3) -> pd.DataFrame:
        """Fetch daily OHLCV from TradingView for an NSE equity symbol."""
        if self.tv_obj is None:
            logger.error(f"TV not connected, cannot fetch {symbol}")
            return pd.DataFrame()

        for retry in range(max_retries):
            try:
                tv_data = self.tv_obj.get_hist(
                    symbol=symbol,
                    exchange='NSE',
                    interval=Interval.in_daily,
                    n_bars=500,
                )

                if tv_data is None:
                    logger.warning(f"TV returned None for {symbol}, retry {retry + 1}/{max_retries}")
                    self.tv_timeout_retries += 1
                    if self.tv_timeout_retries >= self.max_tv_timeout_retries:
                        logger.warning("Multiple consecutive TV failures, reconnecting...")
                        self._reconnect_tv()
                        self.tv_timeout_retries = 0
                    time.sleep(2 * (retry + 1))
                    continue

                # Drop the symbol column added by tvDatafeed
                if 'symbol' in tv_data.columns:
                    tv_data = tv_data.drop(columns=['symbol'])

                # Index is datetime — strip timezone info
                tv_data.index = tv_data.index.tz_localize(None)
                tv_data.index.name = 'Date'

                # Capitalize column names to match elliott_wave_strategy expectations
                tv_data.columns = [c.capitalize() for c in tv_data.columns]
                # tvDatafeed returns: open, high, low, close, volume → Open, High, Low, Close, Volume

                self.tv_error = 0
                self.tv_timeout_retries = 0
                logger.debug(f"Fetched {len(tv_data)} daily bars for {symbol}")
                return tv_data

            except Exception as e:
                error_str = str(e).lower()
                is_timeout = 'timeout' in error_str or 'timed out' in error_str
                is_conn_lost = 'connection' in error_str and 'lost' in error_str

                logger.error(f"TV error for {symbol} (retry {retry + 1}/{max_retries}): {e}")

                if is_timeout or is_conn_lost:
                    self.tv_timeout_retries += 1
                    if self.tv_timeout_retries >= self.max_tv_timeout_retries:
                        logger.warning("Multiple TV timeouts, reconnecting...")
                        self._reconnect_tv()
                        self.tv_timeout_retries = 0
                    time.sleep(2 * (retry + 1))
                    continue
                logger.error(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                self.tv_error += 1
                if self.tv_error > 5:
                    logger.warning("Too many TV errors, connection may be broken")
                return pd.DataFrame()

        logger.warning(f"TV retries exhausted for {symbol}")
        return pd.DataFrame()

    # ------------------------------------------------------------------
    # Main signal generation
    # ------------------------------------------------------------------

    NSE_NIFTY200_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv"

    def load_nifty200(self):
        """Download Nifty 200 symbols from NSE archives daily. Falls back to local cache."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.nseindia.com/",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            resp = requests.get(self.NSE_NIFTY200_URL, headers=headers, timeout=15)
            resp.raise_for_status()
            content = resp.text
            # Save as local cache for fallback
            os.makedirs(os.path.dirname(self.nifty200_csv), exist_ok=True)
            with open(self.nifty200_csv, "w", encoding="utf-8") as f:
                f.write(content)
            nifty_df = pd.read_csv(StringIO(content))
            nifty_df.columns = [c.strip() for c in nifty_df.columns]
            symbols = nifty_df['Symbol'].dropna().unique().tolist()
            logger.info(f"Downloaded {len(symbols)} Nifty 200 symbols from NSE")
            return symbols
        except Exception as e:
            logger.warning(f"NSE download failed: {e}. Falling back to local cache.")

        # Fallback: local cache
        nifty_df = pd.read_csv(self.nifty200_csv)
        nifty_df.columns = [c.strip() for c in nifty_df.columns]
        symbols = nifty_df['Symbol'].dropna().unique().tolist()
        logger.info(f"Loaded {len(symbols)} Nifty 200 symbols from local cache")
        return symbols

    # ------------------------------------------------------------------
    # Account amount tracking with compound delta_change
    # ------------------------------------------------------------------

    ACCOUNTS_CSV_COLUMNS = ['account', 'sync_date', 'delta_change_pct', 'current_amount']

    def sync_account_amounts(self, accounts_csv_path: str):
        """Sync local account amounts from Google Sheet using compound delta_change.

        Google Sheet columns: account | initial_amount | delta_change | date_sync
          - delta_change: percentage (e.g. 10 means +10%)
          - date_sync: date when delta_change was last applied

        Local CSV columns: account | sync_date | delta_change_pct | current_amount
          - One row appended per sync event (history preserved).
          - Latest row per account (by sync_date) is the active state.

        Logic per account:
          - If account absent in local CSV → append seed row:
            current_amount = initial_amount, sync_date = date_sync, delta_change_pct = 0
          - If date_sync > latest local sync_date → append new row:
            current_amount = prev_amount * (1 + delta_change / 100)
          - If date_sync <= latest local sync_date → no-op (prevents double-apply)

        Returns list of dicts with keys: account, current_amount, per_trade_amount.
        """
        max_positions = self.config.max_positions

        # Download remote sheet
        try:
            remote_df = _read_csv_from_url(self.accounts_url)
            remote_df.columns = [c.strip().lower() for c in remote_df.columns]
            remote_df['delta_change'] = pd.to_numeric(
                remote_df.get('delta_change', 0), errors='coerce').fillna(0)
            remote_df['initial_amount'] = pd.to_numeric(
                remote_df['initial_amount'], errors='coerce').fillna(0)
            remote_df['date_sync'] = pd.to_datetime(
                remote_df['date_sync'], dayfirst=True, errors='coerce')
        except Exception as e:
            logger.error(f"Failed to download accounts sheet: {e}")
            return []

        # Load local history CSV; build map: account → latest row
        try:
            local_df = pd.read_csv(accounts_csv_path)
            local_df['sync_date'] = pd.to_datetime(local_df['sync_date'], errors='coerce')
            local_df['current_amount'] = pd.to_numeric(local_df['current_amount'], errors='coerce')
        except FileNotFoundError:
            local_df = pd.DataFrame(columns=self.ACCOUNTS_CSV_COLUMNS)

        # Latest entry per account
        latest_map = {}
        if not local_df.empty:
            for acct, grp in local_df.groupby('account'):
                latest_row = grp.sort_values('sync_date').iloc[-1]
                latest_map[str(acct).strip()] = {
                    'current_amount': float(latest_row['current_amount']),
                    'sync_date': latest_row['sync_date'],
                }

        new_rows = []
        result = []

        for _, remote_row in remote_df.iterrows():
            account = str(remote_row['account']).strip()
            initial_amount = float(remote_row['initial_amount'])
            delta_change = float(remote_row['delta_change'])
            remote_date = remote_row['date_sync']

            if account not in latest_map:
                # First time — seed with initial_amount, delta 0
                new_amount = initial_amount
                new_rows.append({
                    'account': account,
                    'sync_date': remote_date.date() if pd.notna(remote_date) else '',
                    'delta_change_pct': 0,
                    'current_amount': round(new_amount, 2),
                })
                latest_map[account] = {'current_amount': new_amount, 'sync_date': remote_date}
                logger.info(f"New account '{account}' seeded with {initial_amount}")
            else:
                local_date = latest_map[account]['sync_date']
                remote_date_valid = pd.notna(remote_date)
                local_date_valid = pd.notna(local_date)

                if remote_date_valid and (not local_date_valid or remote_date > local_date):
                    old_amount = latest_map[account]['current_amount']
                    new_amount = old_amount * (1 + delta_change / 100)
                    new_rows.append({
                        'account': account,
                        'sync_date': remote_date.date(),
                        'delta_change_pct': delta_change,
                        'current_amount': round(new_amount, 2),
                    })
                    latest_map[account] = {'current_amount': new_amount, 'sync_date': remote_date}
                    logger.info(
                        f"Account '{account}': {old_amount:.2f} → {new_amount:.2f} "
                        f"(delta={delta_change}%, date={remote_date.date()})"
                    )
                else:
                    new_amount = latest_map[account]['current_amount']

            result.append({
                'account': account,
                'current_amount': round(latest_map[account]['current_amount'], 2),
                'per_trade_amount': round(latest_map[account]['current_amount'] / max_positions, 2),
            })

        if new_rows:
            appended = pd.concat(
                [local_df, pd.DataFrame(new_rows, columns=self.ACCOUNTS_CSV_COLUMNS)],
                ignore_index=True,
            )
            appended.to_csv(accounts_csv_path, index=False)
            logger.info(f"Appended {len(new_rows)} row(s) to {accounts_csv_path}")

        return result

    def load_accounts(self):
        """Download and parse account config from Google Sheet.

        Sheet columns: account | initial_amount | delta_change | date_sync
        Returns list of dicts with keys: account, amount, current_capital.
        amount = (initial_amount + delta_change) * 10%

        NOTE: For production use, prefer sync_account_amounts() which applies
        compound delta_change correctly. This method is kept for backward compatibility.
        """
        MAX_ALLOCATION_PCT = 10.0
        try:
            accounts_df = _read_csv_from_url(self.accounts_url)
            accounts_df.columns = [c.strip().lower() for c in accounts_df.columns]
            accounts_df['delta_change'] = pd.to_numeric(
                accounts_df.get('delta_change', 0), errors='coerce').fillna(0)
            accounts_df['initial_amount'] = pd.to_numeric(
                accounts_df['initial_amount'], errors='coerce').fillna(0)
            result = []
            for _, row in accounts_df.iterrows():
                current_capital = row['initial_amount'] + row['delta_change']
                result.append({
                    'account': str(row['account']).strip(),
                    'amount': round(current_capital * MAX_ALLOCATION_PCT / 100, 2),
                    'current_capital': round(current_capital, 2),
                })
            return result
        except Exception as e:
            logger.error(f"Failed to load accounts from {self.accounts_url}: {e}")
            return []

    def generate_daily_signals(self, csv_path: str, accounts_csv_path: str = None):
        """
        Generate Elliott Wave signals for today and append to the EW strategy CSV.

        Args:
            csv_path: Path to elliot_cash_stratergy.csv
            accounts_csv_path: Optional path to elliot_accounts.csv for compound
                delta_change tracking. When provided, sync_account_amounts() is
                called and per_trade_amount = current_amount / max_positions.
                When None, falls back to simple (initial_amount + delta_change) * 10%.
        """
        today = datetime.now().date()
        logger.info(f"Generating EW signals for {today}")

        # 1. Load accounts config
        if accounts_csv_path is not None:
            accounts_list = self.sync_account_amounts(accounts_csv_path)
            if not accounts_list:
                logger.error("No accounts returned from sync_account_amounts()")
                return 0
            # Normalise to DataFrame with 'account' and 'amount' columns
            accounts_df = pd.DataFrame([
                {'account': a['account'], 'amount': a['per_trade_amount']}
                for a in accounts_list
            ])
        else:
            # Fallback: simple (initial_amount + delta_change) * 10%
            MAX_ALLOCATION_PCT = 10.0
            try:
                accounts_df = _read_csv_from_url(self.accounts_url)
                accounts_df.columns = [c.strip().lower() for c in accounts_df.columns]
                required_cols = {'account', 'initial_amount'}
                if not required_cols.issubset(accounts_df.columns):
                    logger.error(f"Accounts sheet missing required columns. Found: {list(accounts_df.columns)}")
                    return 0
                accounts_df['delta_change'] = pd.to_numeric(accounts_df.get('delta_change', 0), errors='coerce').fillna(0)
                accounts_df['initial_amount'] = pd.to_numeric(accounts_df['initial_amount'], errors='coerce').fillna(0)
                accounts_df['amount'] = (accounts_df['initial_amount'] + accounts_df['delta_change']) * MAX_ALLOCATION_PCT / 100
            except Exception as e:
                logger.error(f"Failed to load accounts from {self.accounts_url}: {e}")
                return 0

        # 2. Load Nifty 200 tickers
        try:
            symbols = self.load_nifty200()
        except Exception as e:
            logger.error(f"Failed to load Nifty 200 CSV {self.nifty200_csv}: {e}")
            return 0

        logger.info(f"Processing {len(symbols)} Nifty 200 symbols")

        # 3. Load existing EW CSV to avoid duplicates
        try:
            existing_df = pd.read_csv(csv_path)
        except FileNotFoundError:
            existing_df = pd.DataFrame(columns=self.EW_CSV_COLUMNS)

        # Symbols already tracked (status new or open) — skip re-adding them
        skip_symbols = set()
        if 'symbol' in existing_df.columns and 'status' in existing_df.columns:
            skip_mask = existing_df['status'].isin(['new', 'open'])
            skip_symbols = set(existing_df.loc[skip_mask, 'symbol'].dropna().unique())

        # 4. Generate signals for each symbol
        new_rows = []
        signals_found = 0
        date_str = today.strftime("%Y%m%d")

        for idx, symbol in enumerate(symbols):
            if symbol in skip_symbols:
                logger.debug(f"Skipping {symbol} — already active")
                continue

            # Throttle TV requests to avoid connection drops (free account: max 2 connections)
            if idx > 0:
                time.sleep(0.5)

            try:
                df = self._fetch_ohlcv(symbol)
                if df.empty or len(df) < 60:
                    logger.warning(f"EW signals: {symbol} skipped — insufficient data ({len(df)} bars)")
                    continue

                df.attrs["symbol"] = symbol
                swings = detect_swing_points(df, self.config)
                structures = identify_wave_structures(swings, self.config)
                signals = generate_signals(df, structures, self.config)

                # Keep only today's signals
                today_signals = [s for s in signals if s.date.date() == today]

                for signal in today_signals:
                    percent_increase = (signal.target_price / signal.entry_price - 1) * 100

                    for _, acc_row in accounts_df.iterrows():
                        account = str(acc_row['account']).strip()
                        amount = float(acc_row['amount'])
                        sl_no = f"EW_{date_str}_{symbol}_{account}"

                        row = {
                            'sl_no': sl_no,
                            'symbol': symbol,
                            'account': account,
                            'amount': amount,
                            'sl': signal.stop_loss,
                            'percent_increase': round(percent_increase, 2),
                            'date': today.isoformat(),
                            'status': 'new',
                            'signal_type': signal.signal_type.value,
                            'confidence': round(signal.confidence, 2),
                            'buy_price': None,
                            'quantity': None,
                            'open_order_status': None,
                            'open_date': None,
                            'close_order_status': None,
                            'close_order_id': None,
                            'sell_price': None,
                            'close_date': None,
                            'profit_target': None,
                            'buy_order_id': None,
                        }
                        new_rows.append(row)
                        signals_found += 1
                        logger.info(
                            f"EW signal: {symbol} account={account} "
                            f"type={signal.signal_type.value} entry={signal.entry_price:.2f} "
                            f"sl={signal.stop_loss:.2f} target={signal.target_price:.2f} "
                            f"confidence={signal.confidence:.2f} pct={percent_increase:.2f}%"
                        )

            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")
                continue

        if not new_rows:
            logger.info("No new EW signals generated today")
            self._send_telegram_summary(0, 0)
            return 0

        # 5. Append to CSV
        new_df = pd.DataFrame(new_rows, columns=self.EW_CSV_COLUMNS)
        combined = pd.concat([existing_df, new_df], ignore_index=True)
        combined.to_csv(csv_path, index=False)

        unique_symbols = len({r['symbol'] for r in new_rows})
        logger.info(f"Generated {signals_found} EW signals for {unique_symbols} symbols")

        # 6. Telegram summary
        self._send_telegram_summary(signals_found, unique_symbols)
        return len(new_rows)

    def _send_telegram_summary(self, num_signals: int, num_symbols: int):
        try:
            config = configuration.ConfigurationLoader.get_configuration()
            chat_id = config.get("deepti_telegram")
            if not chat_id:
                return
            if num_signals == 0:
                msg = "EW Strategy: No new signals generated today"
            else:
                msg = f"EW Strategy: {num_signals} signals generated for {num_symbols} symbols"
            self.telegram_api.send_message(chat_id, msg)
        except Exception as e:
            logger.error(f"Failed to send Telegram summary: {e}")
