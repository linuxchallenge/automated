"""Module providing broker-sourced OHLC data with provider fallback """

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
import logging
from datetime import datetime, time, timedelta
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

# Angel caps the date range per request depending on the interval
ANGEL_MAX_DAYS = {
    '1m': 30,
    '5m': 100,
    '15m': 200,
    '30m': 200,
    '60m': 400,
    '1d': 2000,
}

# Trading session per segment. Bars are stamped with their open time, so a bar
# at or after the session close is vendor padding (5paisa emits a 15:30 bar for
# NSE every day) and is dropped - left in, it flips the alligator trend.
SEGMENT_SESSIONS = {
    'NSE_INDEX': (time(9, 15), time(15, 30)),
}

DEFAULT_PROVIDER_ORDER = ['angel', 'fivepaisa']


class MarketData:
    """Fetch OHLC candles from broker APIs with fallback between providers.

    Providers read their session off the PlaceOrder object that the strategies
    already receive, so no extra login handling is needed.
    """

    def __init__(self, place_order_obj, provider_order=None):
        self.place_order_obj = place_order_obj
        self.provider_order = provider_order or list(DEFAULT_PROVIDER_ORDER)

    def get_ohlc(self, symbol, segment='NSE_INDEX', interval='15m', days=90, provider=None):
        """Return OHLC candles as a DataFrame with OHLC_COLUMNS, or None.

        Tries each provider in order and returns the first usable frame.
        """
        instrument = INSTRUMENTS.get((segment, symbol))
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
                data = fetcher(instrument[name], interval, days)
                data = self._apply_session(data, segment, interval)
                if data is not None and not data.empty:
                    logger.info(f"Fetched {len(data)} {interval} candles for {symbol} from {name}")
                    return data
                logger.warning(f"No {interval} data for {symbol} from {name}")
            except Exception as e:
                logger.error(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                logger.error(f"Error fetching {symbol} from {name}: {e}")

        logger.error(f"All market data providers failed for {symbol} {interval}")
        return None

    def _fetch_angel(self, mapping, interval, days):
        if self.place_order_obj.obj_1 is None:
            logger.error("Angel session not initialised, cannot fetch candles")
            return None

        angel_interval = ANGEL_INTERVAL.get(interval)
        if angel_interval is None:
            logger.error(f"Angel does not support interval {interval}")
            return None

        days = self._clamp_days(days, ANGEL_MAX_DAYS.get(interval), 'angel', interval)
        to_date = datetime.now()
        from_date = to_date - timedelta(days=days)

        params = {
            "exchange": mapping['exchange'],
            "symboltoken": mapping['token'],
            "interval": angel_interval,
            "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
            "todate": to_date.strftime("%Y-%m-%d %H:%M"),
        }

        response = self.place_order_obj.obj_1.obj.getCandleData(params)

        if not response or not response.get('data'):
            logger.warning(f"Empty candle response from Angel: {response}")
            return None

        data = pd.DataFrame(response['data'], columns=['Date', 'open', 'high', 'low', 'close', 'volume'])
        return self._normalise(data)

    def _fetch_fivepaisa(self, mapping, interval, days):
        if self.place_order_obj.obj_2 is None:
            logger.error("5paisa session not initialised, cannot fetch candles")
            return None

        fp_interval = FIVEPAISA_INTERVAL.get(interval)
        if fp_interval is None:
            logger.error(f"5paisa does not support interval {interval}")
            return None

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

    def _clamp_days(self, days, max_days, provider, interval):
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
