"""Elliott Wave Signal Generator — generates daily trade signals from Nifty 200 universe."""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=R0902
# pylint: disable=R0903
# pylint: disable=R0914
# pylint: disable=R0915
# pylint: disable=R1702

import json
import logging
import os
import random
import time
import traceback

# Google Sheet URL for EW account config (account | initial_amount | delta_change | date_sync)
ACCOUNTS_URL = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vTjXLK6OU5QmCMC_GOk88MWL5a5IIKTjq0tlcbrbXAoQiOryhCj78dyv4MR07qBTag8RGwH4twtIrTw"
    "/pub?output=csv"
)
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

    def _fetch_ohlcv_tv(self, symbol: str, max_retries: int = 2) -> pd.DataFrame:
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
                    time.sleep(1)
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
                    time.sleep(1)
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

    def load_nifty200(self):
        """Load NSE symbols from Nifty 200 CSV. Returns list of plain symbols."""
        nifty_df = pd.read_csv(self.nifty200_csv)
        nifty_df.columns = [c.strip() for c in nifty_df.columns]
        return nifty_df['Symbol'].dropna().unique().tolist()

    def load_accounts(self):
        """Download and parse account config from Google Sheet.

        Sheet columns: account | initial_amount | delta_change | date_sync
        Returns list of dicts with keys: account, amount, current_capital.
        amount = (initial_amount + delta_change) * 10%
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

    def generate_daily_signals(self, csv_path: str):
        """
        Generate Elliott Wave signals for today and append to the EW strategy CSV.

        Args:
            csv_path: Path to elliot_cash_stratergy.csv
        """
        today = datetime.now().date()
        logger.info(f"Generating EW signals for {today}")

        # 1. Load accounts config
        # Sheet columns: account | initial_amount | delta_change | date_sync
        # Per-trade amount = (initial_amount + delta_change) * 10% of capital
        MAX_ALLOCATION_PCT = 10.0
        try:
            accounts_df = _read_csv_from_url(self.accounts_url)
            accounts_df.columns = [c.strip().lower() for c in accounts_df.columns]
            required_cols = {'account', 'initial_amount'}
            if not required_cols.issubset(accounts_df.columns):
                logger.error(f"Accounts sheet missing required columns. Found: {list(accounts_df.columns)}")
                return
            # Compute per-trade amount from capital
            accounts_df['delta_change'] = pd.to_numeric(accounts_df.get('delta_change', 0), errors='coerce').fillna(0)
            accounts_df['initial_amount'] = pd.to_numeric(accounts_df['initial_amount'], errors='coerce').fillna(0)
            accounts_df['amount'] = (accounts_df['initial_amount'] + accounts_df['delta_change']) * MAX_ALLOCATION_PCT / 100
        except Exception as e:
            logger.error(f"Failed to load accounts from {self.accounts_url}: {e}")
            return

        # 2. Load Nifty 200 tickers
        try:
            symbols = self.load_nifty200()
        except Exception as e:
            logger.error(f"Failed to load Nifty 200 CSV {self.nifty200_csv}: {e}")
            return

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

        for symbol in symbols:
            if symbol in skip_symbols:
                logger.debug(f"Skipping {symbol} — already active")
                continue

            try:
                df = self._fetch_ohlcv(symbol)
                if df.empty or len(df) < 60:
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
