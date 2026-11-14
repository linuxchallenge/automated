"""Module providing broker-sourced OHLC data with provider fallback """

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
import logging
from datetime import datetime, time, timedelta
from time import monotonic, sleep
import traceback
import pandas as pd

logger = logging.getLogger(__name__)

# Canonical column contract returned by get_ohlc()
OHLC_COLUMNS = ['Date', 'open', 'high', 'low', 'close', 'volume']

# Instrument registry: (segment, symbol) -> per provider identifiers.
# Add a symbol by adding a row here - no provider code changes needed.
INSTRUMENTS = {
    ('NSE_INDEX', 'NIFTY'): {
        'angel': {'exchange': 'NSE', 'token': '99926000'},
        'fivepaisa': {'exch': 'N', 'exch_type': 'C', 'scrip_code': 999920000},
    },
    ('NSE_INDEX', 'BANKNIFTY'): {
        'angel': {'exchange': 'NSE', 'token': '99926009'},
        'fivepaisa': {'exch': 'N', 'exch_type': 'C', 'scrip_code': 999920005},
    },
}

# Interval names per provider, keyed by our canonical interval
ANGEL_INTERVAL = {
    '1m': 'ONE_MINUTE',
    '5m': 'FIVE_MINUTE',
    '15m': 'FIFTEEN_MINUTE',
    '30m': 'THIRTY_MINUTE',
    '60m': 'ONE_HOUR',
    '1d': 'ONE_DAY',
}

FIVEPAISA_INTERVAL = {
    '1m': '1m',
    '5m': '5m',
    '15m': '15m',
    '30m': '30m',
    '60m': '60m',
    '1d': '1d',
}

# Maximum history per request, per provider and interval. The alligator's SMMA
# is recursive so more history is preferred, but the caps differ: Angel serves
# its documented limits, 5paisa refuses anything past ~180 days (measured).
PROVIDER_MAX_DAYS = {
    'angel': {
        '1m': 30, '5m': 100, '15m': 200, '30m': 200, '60m': 400, '1d': 2000,
    },
    'fivepaisa': {
        '1m': 30, '5m': 100, '15m': 180, '30m': 180, '60m': 180, '1d': 2000,
    },
}

DEFAULT_MAX_DAYS = 90

# When a provider returns nothing for the requested window, retry with shorter
# ones before giving up - the documented caps are not always what a provider
# actually serves. Fractions of the resolved window, floored at MIN_DAYS so a
# retry never returns too little for the alligator's 21-bar lookback.
DAY_BACKOFF = [1.0, 0.75, 0.5, 0.25]
MIN_DAYS = 20

# MCX has no spot, so market data comes from the nearest futures contract.
# GOLD/SILVER read from the mini/micro contracts - data only, orders are
# unchanged. Symbols absent here use their own name.
MCX_DATA_CONTRACT = {
    'GOLD': 'GOLDM',
    'SILVER': 'SILVERMIC',
}

# Roll to the next contract when the nearest is this close to expiry, matching
# angelone_api.getTokenInfo so data and orders agree on which contract is front.
MCX_ROLLOVER_DAYS = 10

# Trading session per segment. Bars are stamped with their open time, so a bar
# at or after the session close is vendor padding (5paisa emits a 15:30 bar for
# NSE every day) and is dropped - left in, it flips the alligator trend.
SEGMENT_SESSIONS = {
    'NSE_INDEX': (time(9, 15), time(15, 30)),
    'MCX_FUT': (time(9, 0), time(23, 59)),
}

# Pandas frequency per canonical interval, used to snap bars onto a common grid.
# 5paisa stamps a bar with its first trade time rather than the bucket boundary,
# so on illiquid contracts its bars land off-grid and bucket differently to Angel.
INTERVAL_FREQ = {
    '1m': '1min',
    '5m': '5min',
    '15m': '15min',
    '30m': '30min',
    '60m': '60min',
    '1d': 'D',
}

# Angel's candle API allows roughly 3 requests/second, but the strategies fetch several
# symbols back-to-back (often on the same bar boundary), and a 0.4s gap still drew
# hundreds of 'exceeding access rate' refusals a week. Candle calls are therefore paced
# to one per second (ANGEL_MIN_CALL_GAP) and retried with backoff when refused.
ANGEL_MIN_CALL_GAP = 1.0
ANGEL_RATE_LIMIT_RETRIES = 3
ANGEL_RATE_LIMIT_BACKOFF = 1.5
ANGEL_RATE_LIMIT_MARKER = 'exceeding access rate'

DEFAULT_PROVIDER_ORDER = ['angel', 'fivepaisa']


class MarketData:
    """Fetch OHLC candles from broker APIs with fallback between providers.

    Providers read their session off the PlaceOrder object that the strategies
    already receive, so no extra login handling is needed.
    """

    # Shared across instances: strategies build their own MarketData objects,
    # but the rate limit is per Angel API key.
    _last_angel_call = 0.0

    def __init__(self, place_order_obj, provider_order=None):
        self.place_order_obj = place_order_obj
        self.provider_order = provider_order or list(DEFAULT_PROVIDER_ORDER)

    def get_ohlc(self, symbol, segment='NSE_INDEX', interval='15m', days=None, provider=None):
        """Return OHLC candles as a DataFrame with OHLC_COLUMNS, or None.

        Tries each provider in order and returns the first usable frame.
        days=None (the default) requests the maximum history for the interval.
        """
        instrument = self._instrument_for(segment, symbol)
        if instrument is None:
            logger.error(f"No instrument mapping for {segment}/{symbol}")
            return None

        providers = [provider] if provider else self.provider_order

        for name in providers:
            fetcher = getattr(self, f"_fetch_{name}", None)
            if fetcher is None:
                logger.error(f"Unknown market data provider: {name}")
                continue
            if name not in instrument:
                logger.warning(f"{symbol} has no {name} mapping, skipping provider")
                continue
            try:
                for attempt_days in self._day_ladder(days, interval, name):
                    data = fetcher(instrument[name], interval, attempt_days)
                    data = self._apply_session(data, segment, interval)
                    data = self._align_to_grid(data, interval)
                    if data is not None and not data.empty:
                        last = data.iloc[-1]
                        lag_min = (datetime.now() - last['Date'].to_pydatetime()).total_seconds() / 60.0
                        logger.info(
                            f"MDATA {symbol} {interval} src={name} rows={len(data)} "
                            f"days={attempt_days} first={data.iloc[0]['Date']} "
                            f"last={last['Date']} close={last['close']} lag={lag_min:.1f}min")
                        return data
                    logger.warning(f"No {interval} data for {symbol} from {name} "
                                   f"over {attempt_days} days")
            except Exception as e:
                logger.error(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                logger.error(f"Error fetching {symbol} from {name}: {e}")

        logger.error(f"All market data providers failed for {symbol} {interval}")
        return None

    def _instrument_for(self, segment, symbol):
        """Per-provider identifiers for a symbol, static or resolved live."""
        if segment == 'MCX_FUT':
            return self._resolve_mcx(symbol)
        return INSTRUMENTS.get((segment, symbol))

    def _resolve_mcx(self, symbol):
        """Resolve the front-month MCX contract for a symbol.

        Angel and 5paisa use the same numeric code for MCX, so one lookup in
        Angel's scrip master serves both providers.
        """
        contract = MCX_DATA_CONTRACT.get(symbol, symbol)
        token_df = self._angel_token_df()
        if token_df is None:
            logger.error(f"No Angel scrip master available to resolve MCX {contract}")
            return None

        rows = token_df[(token_df['exch_seg'] == 'MCX') &
                        (token_df['instrumenttype'] == 'FUTCOM') &
                        (token_df['name'] == contract)].copy()
        if rows.empty:
            logger.error(f"No MCX FUTCOM contracts found for {contract}")
            return None

        # Live token_df has expiry as datetime64; the CSV cache stores ISO strings
        # (2026-11-30) and Angel's raw scrip master uses DDMMMYYYY. Try ISO first,
        # then DDMMMYYYY for whatever is left unparsed.
        if not pd.api.types.is_datetime64_any_dtype(rows['expiry']):
            parsed = pd.to_datetime(rows['expiry'], format='%Y-%m-%d', errors='coerce')
            retry = parsed.isna()
            if retry.any():
                parsed[retry] = pd.to_datetime(rows.loc[retry, 'expiry'], format='%d%b%Y', errors='coerce')
            rows['expiry'] = parsed
        if rows['expiry'].isna().all():
            logger.error(f"Unparseable expiry values for {contract} in the scrip master")
            return None
        today = pd.Timestamp(datetime.now().date())
        rows = rows[rows['expiry'] >= today].sort_values(by='expiry')
        if rows.empty:
            logger.error(f"All {contract} contracts have expired in the scrip master")
            return None

        row = rows.iloc[0]
        if (row['expiry'] - today).days <= MCX_ROLLOVER_DAYS and len(rows) > 1:
            row = rows.iloc[1]
            logger.info(f"{contract} front contract within {MCX_ROLLOVER_DAYS} days, rolling to next")

        token = str(row['token'])
        logger.info(f"Resolved MCX {symbol} -> {row['symbol']} (token {token}, expiry {row['expiry'].date()})")
        return {
            'angel': {'exchange': 'MCX', 'token': token},
            'fivepaisa': {'exch': 'M', 'exch_type': 'D', 'scrip_code': int(token)},
        }

    def _angel_token_df(self):
        """Angel's scrip master, from the live session or its on-disk cache."""
        obj_1 = getattr(self.place_order_obj, 'obj_1', None)
        token_df = getattr(obj_1, 'token_df', None)
        if token_df is not None and not token_df.empty:
            return token_df
        try:
            return pd.read_csv('token_map_angelone.csv')
        except Exception as e:
            logger.error(f"Could not read cached Angel scrip master: {e}")
            return None

    def _fetch_angel(self, mapping, interval, days):
        if self.place_order_obj.obj_1 is None:
            logger.error("Angel session not initialised, cannot fetch candles")
            return None

        angel_interval = ANGEL_INTERVAL.get(interval)
        if angel_interval is None:
            logger.error(f"Angel does not support interval {interval}")
            return None

        days = self._resolve_days(days, interval, 'angel')
        to_date = datetime.now()
        from_date = to_date - timedelta(days=days)

        params = {
            "exchange": mapping['exchange'],
            "symboltoken": mapping['token'],
            "interval": angel_interval,
            "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
            "todate": to_date.strftime("%Y-%m-%d %H:%M"),
        }

        response = self._angel_candle_request(params)

        if not response or not response.get('data'):
            logger.warning(f"Empty candle response from Angel: {response}")
            return None

        data = pd.DataFrame(response['data'], columns=['Date', 'open', 'high', 'low', 'close', 'volume'])
        return self._normalise(data)

    def _angel_candle_request(self, params):
        """Call Angel's candle API, pacing requests and retrying when throttled."""
        last_error = None
        for attempt in range(ANGEL_RATE_LIMIT_RETRIES):
            elapsed = monotonic() - MarketData._last_angel_call
            if elapsed < ANGEL_MIN_CALL_GAP:
                sleep(ANGEL_MIN_CALL_GAP - elapsed)
            MarketData._last_angel_call = monotonic()
            try:
                return self.place_order_obj.obj_1.obj.getCandleData(params)
            except Exception as e:
                if ANGEL_RATE_LIMIT_MARKER not in str(e):
                    raise
                last_error = e
                backoff = ANGEL_RATE_LIMIT_BACKOFF * (attempt + 1)
                logger.warning(f"Angel rate limited, retry {attempt + 1}/{ANGEL_RATE_LIMIT_RETRIES} "
                               f"in {backoff:.1f}s")
                sleep(backoff)
        raise last_error

    def _fetch_fivepaisa(self, mapping, interval, days):
        if self.place_order_obj.obj_2 is None:
            logger.error("5paisa session not initialised, cannot fetch candles")
            return None

        fp_interval = FIVEPAISA_INTERVAL.get(interval)
        if fp_interval is None:
            logger.error(f"5paisa does not support interval {interval}")
            return None

        days = self._resolve_days(days, interval, 'fivepaisa')
        to_date = datetime.now()
        from_date = to_date - timedelta(days=days)

        data = self.place_order_obj.obj_2.obj.historical_data(
            mapping['exch'],
            mapping['exch_type'],
            mapping['scrip_code'],
            fp_interval,
            from_date.strftime("%Y-%m-%d"),
            to_date.strftime("%Y-%m-%d"),
        )

        # historical_data() swallows errors and returns None, or a str on bad interval
        if data is None or isinstance(data, str) or data.empty:
            logger.warning(f"Unusable candle response from 5paisa: {data}")
            return None

        data = data.rename(columns={
            'Datetime': 'Date', 'Open': 'open', 'High': 'high',
            'Low': 'low', 'Close': 'close', 'Volume': 'volume',
        })
        return self._normalise(data)

    def _apply_session(self, data, segment, interval):
        """Drop intraday bars outside the segment's trading session."""
        if data is None or data.empty or interval == '1d':
            return data

        session = SEGMENT_SESSIONS.get(segment)
        if session is None:
            return data

        start, end = session
        bar_time = data['Date'].dt.time
        keep = (bar_time >= start) & (bar_time < end)
        dropped = int((~keep).sum())
        if dropped:
            logger.info(f"Dropped {dropped} out-of-session bars for {segment}")
        return data[keep].reset_index(drop=True)

    def _align_to_grid(self, data, interval):
        """Snap bars onto the interval grid so every provider agrees on buckets."""
        freq = INTERVAL_FREQ.get(interval)
        if data is None or data.empty or freq is None:
            return data

        floored = data['Date'].dt.floor(freq)
        if floored.equals(data['Date']):
            return data

        off_grid = int((floored != data['Date']).sum())
        data = data.assign(Date=floored).groupby('Date', as_index=False).agg(
            {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
        logger.info(f"Snapped {off_grid} off-grid bars onto the {interval} grid")
        return data.sort_values(by='Date').reset_index(drop=True)

    def _day_ladder(self, days, interval, provider):
        """Windows to try for a provider, longest first."""
        resolved = self._resolve_days(days, interval, provider)
        ladder = []
        for fraction in DAY_BACKOFF:
            candidate = max(int(resolved * fraction), MIN_DAYS)
            if candidate not in ladder:
                ladder.append(candidate)
            if candidate == MIN_DAYS:
                break
        return ladder

    def _resolve_days(self, days, interval, provider):
        """days=None means the most history this provider allows for the interval."""
        max_days = PROVIDER_MAX_DAYS.get(provider, {}).get(interval)
        if days is None:
            return max_days if max_days is not None else DEFAULT_MAX_DAYS
        if max_days is not None and days > max_days:
            logger.warning(f"{provider} caps {interval} history at {max_days} days, requested {days}")
            return max_days
        return days

    def _normalise(self, data):
        """Coerce a provider frame to the canonical contract."""
        missing = [c for c in OHLC_COLUMNS if c not in data.columns]
        if missing:
            logger.error(f"Provider frame missing columns {missing}")
            return None

        data = data[OHLC_COLUMNS].copy()
        data['Date'] = pd.to_datetime(data['Date'], errors='coerce')

        # Strip timezone so all providers agree on naive IST timestamps
        if data['Date'].dt.tz is not None:
            data['Date'] = data['Date'].dt.tz_convert('Asia/Kolkata').dt.tz_localize(None)

        for column in ['open', 'high', 'low', 'close', 'volume']:
            data[column] = pd.to_numeric(data[column], errors='coerce')

        data = data.dropna(subset=['Date', 'open', 'high', 'low', 'close'])
        data['volume'] = data['volume'].fillna(0)
        data = data.sort_values(by='Date', ascending=True).reset_index(drop=True)

        return data
