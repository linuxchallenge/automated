"""
Weekly Major-Index Report -> Telegram

For each tracked index/commodity (daily bars from TradingView) it reports:
  - Weekly up/down %          (last completed week close-over-close)
  - % above/below 50 EMA      (200/50 day EMA on daily closes)
  - % above/below 200 EMA
  - % from last low fractal   (Williams bullish/swing-low fractal)
  - % from last up  fractal   (Williams bearish/swing-high fractal)

Run:  python weekly_index_report.py            (print only)
      python weekly_index_report.py --send      (also send to Telegram)

NOTE: TradingView symbols for the Nifty sub-indices vary; if a row shows
"fetch failed", adjust its (symbol, exchange) in INSTRUMENTS below.
"""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0103

import json
import os
import random
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'auto_straddle'))
from tvDatafeed import Interval, TvDatafeed
from TelegramSend import telegram_send_api
from alligator_api import alligator_api

# --- Telegram destination (hardcoded) --------------------------------------
CHAT_ID = "-891000076"  # "Daily Nifty 200 update" group (same as relative_strength report)

# --- Parameters ------------------------------------------------------------
N_BARS = 400          # enough daily bars for a 200-EMA
FRACTAL_PERIOD = 10   # ~21-bar Williams fractal: only significant swing highs/lows

# --- Instruments -----------------------------------------------------------
# Primary feed is TradingView. If TV fails, a per-instrument backup feed is used:
#   nse       -> NSE indices API (relative_strength.get_historical_data), by index name
#   commodity -> commodity_data (TV-login + Upstox INR fallback), by MCX symbol
#   yf        -> yfinance, by Yahoo ticker
# label, TV symbol, TV exchange, TV fut_contract, backup_kind, backup_arg
INSTRUMENTS = [
    ("NIFTY",           "NIFTY",         "NSE",    None, "nse", "NIFTY 50"),
    ("BANKNIFTY",       "BANKNIFTY",     "NSE",    None, "nse", "NIFTY BANK"),
    ("NIFTY 200",       "CNX200",        "NSE",    None, "nse", "NIFTY 200"),
    ("NIFTY SMALLCAP",  "CNXSMALLCAP",   "NSE",    None, "nse", "NIFTY SMALLCAP 100"),
    ("NIFTY MIDCAP",    "CNXMIDCAP",     "NSE",    None, "nse", "NIFTY MIDCAP 100"),
    ("NIFTY ALPHA 50",  "NIFTYALPHA50",  "NSE",    None, "nse", "NIFTY ALPHA 50"),
    ("NIFTY200 ALPHA",  "NIFTY200ALPHA30", "NSE",  None, "nse", "NIFTY200 ALPHA 30"),
    ("GOLD",            "GOLD",          "MCX",    1,    "commodity", "GOLD"),
    ("SILVER",          "SILVER",        "MCX",    1,    "commodity", "SILVER"),
    ("NASDAQ",          "IXIC",          "NASDAQ", None, "yf", "^IXIC"),
    ("HANG SENG",       "HSI",           "HSI",    None, "yf", "^HSI"),
]

# Lazily-created backup feed handles (only built if TV actually fails).
_nse_session = None
_commodity = None


def make_tv():
    """Create an authenticated TvDatafeed using the commodity TV login."""
    creds_file = os.path.join(os.path.dirname(__file__), 'auto_straddle', 'tv_credentials.json')
    try:
        creds = json.load(open(creds_file, encoding='utf-8'))
    except Exception as e:
        print(f"Could not load TV credentials ({e}); using anonymous access")
        creds = []
    for _ in range(5):
        try:
            if creds:
                c = random.choice(creds)
                tv = TvDatafeed(c['username'], c['password'], random_user_agent=True)
            else:
                tv = TvDatafeed()
            if tv.token != 'unauthorized_user_token':
                return tv
        except Exception as e:
            print(f"TV connect error: {e}")
        time.sleep(3)
    # last resort: anonymous
    return TvDatafeed()


def fetch_daily(tv, symbol, exchange, fut):
    for retry in range(3):
        try:
            if fut:
                df = tv.get_hist(symbol=symbol, exchange=exchange,
                                 interval=Interval.in_daily, n_bars=N_BARS, fut_contract=fut)
            else:
                df = tv.get_hist(symbol=symbol, exchange=exchange,
                                 interval=Interval.in_daily, n_bars=N_BARS)
            if df is not None and not df.empty:
                df.index = df.index.tz_localize(None)
                return df
        except Exception as e:
            print(f"  fetch error {symbol} (retry {retry + 1}/3): {e}")
        time.sleep(1.5 * (retry + 1))
    return None


def _normalize(df):
    """Return df with a tz-naive datetime index and lowercase o/h/l/c columns."""
    df = df.copy()
    # Flatten yfinance MultiIndex columns (Price, Ticker) -> Price
    if hasattr(df.columns, 'nlevels') and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).lower() for c in df.columns]
    if 'date' in df.columns:
        df = df.set_index('date')
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df[['open', 'high', 'low', 'close']].dropna()


def _fetch_comex_inr(metal):
    """COMEX metal (USD) x USDINR -> INR-denominated daily OHLC via yfinance.

    Report metrics are all relative %, so the ~constant MCX premium over COMEX
    cancels out; this tracks MCX's % moves with ~2y of history (valid 200 EMA).
    """
    import yfinance as yf
    m = yf.download(metal, period='2y', interval='1d', progress=False)
    fx = yf.download('USDINR=X', period='2y', interval='1d', progress=False)
    if m is None or len(m) == 0 or fx is None or len(fx) == 0:
        return None
    m = _normalize(m)
    fxc = _normalize(fx)['close'].reindex(m.index).ffill()
    for col in ['open', 'high', 'low', 'close']:
        m[col] = m[col] * fxc
    return m.dropna()


def _fetch_commodity(symbol):
    """GOLD/SILVER backup: COMEXxINR (full history) then commodity_data (MCX)."""
    global _commodity
    metal = {"GOLD": "GC=F", "SILVER": "SI=F"}.get(symbol)
    if metal:
        try:
            df = _fetch_comex_inr(metal)
            if df is not None and len(df):
                print(f"  using COMEXxINR backup for {symbol} ({len(df)} bars)")
                return df
        except Exception as e:
            print(f"  COMEXxINR error for {symbol}: {e}")
    try:
        from commodity_data import commodity_data
        if _commodity is None:
            _commodity = commodity_data()
        d = _commodity.historic_data(symbol, daily=True)
        return _normalize(d) if d is not None and len(d) else None
    except Exception as e:
        print(f"  commodity_data error for {symbol}: {e}")
        return None


def fetch_backup(kind, arg):
    """Fetch daily OHLC from the per-instrument backup feed. Returns df or None."""
    global _nse_session
    if kind == "commodity":
        return _fetch_commodity(arg)
    try:
        if kind == "nse":
            import relative_strength as rs
            if _nse_session is None:
                _nse_session = rs.get_nse_session()
            to = datetime.now().strftime('%d-%m-%Y')
            fr = (datetime.now() - timedelta(days=420)).strftime('%d-%m-%Y')
            df = rs.get_historical_data(arg, fr, to, _nse_session)
        elif kind == "yf":
            import yfinance as yf
            df = yf.download(arg, period='2y', interval='1d', progress=False)
        else:
            return None
        if df is None or len(df) == 0:
            return None
        return _normalize(df)
    except Exception as e:
        print(f"  backup ({kind}) error for {arg}: {e}")
        return None


def compute_metrics(df):
    """Return dict of the reported metrics from a daily OHLCV dataframe."""
    close = df['close'].iloc[-1]

    ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
    ema200 = df['close'].ewm(span=200, adjust=False).mean().iloc[-1]

    # Weekly return: last completed week close over prior week close
    wk = df['close'].resample('W-FRI').last().dropna()
    weekly_pct = (wk.iloc[-1] / wk.iloc[-2] - 1) * 100 if len(wk) >= 2 else float('nan')

    # Williams fractals (bullish = swing low, bearish = swing high).
    # Report distance from the most recent significant swing low (lower fractal)
    # and swing high (upper fractal).
    frac = alligator_api.WILLIAMS_FRACTAL(df, period=FRACTAL_PERIOD)
    low_fr = df['low'][frac['BullishFractal'] == 1]
    up_fr = df['high'][frac['BearishFractal'] == 1]
    from_low = (close / low_fr.iloc[-1] - 1) * 100 if len(low_fr) else float('nan')
    from_up = (close / up_fr.iloc[-1] - 1) * 100 if len(up_fr) else float('nan')

    return {
        'weekly_pct': weekly_pct,
        'vs50': (close / ema50 - 1) * 100,
        'vs200': (close / ema200 - 1) * 100,
        'from_low': from_low,
        'from_up': from_up,
    }


# Short labels so each row fits one line on a phone (total width ~34 chars).
NAME_W = 9
SHORT = {
    "NIFTY":          "NIFTY",
    "BANKNIFTY":      "BANKNIFTY",
    "NIFTY 200":      "NIFTY200",
    "NIFTY SMALLCAP": "SMALLCAP",
    "NIFTY MIDCAP":   "MIDCAP",
    "NIFTY ALPHA 50": "ALPHA50",
    "NIFTY200 ALPHA": "200ALP30",
    "GOLD":           "GOLD",
    "SILVER":         "SILVER",
    "NASDAQ":         "NASDAQ",
    "HANG SENG":      "HANGSENG",
}


def _fmt(v):
    if v != v:  # NaN
        return f"{'-':>5}"
    if abs(v) >= 10:  # 3-digit value: drop the decimal so a column separator survives
        return f"{v:+5.0f}"
    return f"{v:+5.1f}"


def build_report(rows):
    """rows: list of (label, metrics|None). Returns telegram markdown string."""
    title = f"*Weekly Index Report — {datetime.now().strftime('%d-%b-%Y')}*\n"
    cols = f"{'Name':<{NAME_W}}{'Wk':>5}{'50':>5}{'200':>5}{'LoF':>5}{'UpF':>5}\n"
    lines = [cols, "-" * (NAME_W + 25) + "\n"]
    approx_names = []
    for label, m, approx in rows:
        name = SHORT.get(label, label)[:NAME_W]
        if m is None:
            lines.append(f"{name:<{NAME_W}}  fetch failed\n")
            continue
        if approx:
            approx_names.append(name.strip())
        lines.append(
            f"{name:<{NAME_W}}{_fmt(m['weekly_pct'])}{_fmt(m['vs50'])}"
            f"{_fmt(m['vs200'])}{_fmt(m['from_low'])}{_fmt(m['from_up'])}\n"
        )
    legend = ("\n_Wk=week % · 50/200=% vs EMA · "
              "LoF/UpF=% from last lower/upper fractal (swing low/high)_")
    note = ""
    if approx_names:
        note = ("\n_⚠ " + ", ".join(approx_names) +
                " via COMEX fallback (TV down) — approx, EMA unreliable_")
    return title + "```\n" + "".join(lines) + "```" + legend + note


def main():
    tv = make_tv()
    rows = []
    for label, symbol, exchange, fut, backup_kind, backup_arg in INSTRUMENTS:
        print(f"Fetching {label} ({exchange}:{symbol}) ...")
        df = fetch_daily(tv, symbol, exchange, fut)
        approx = False
        if df is None:
            print(f"  TV failed, trying backup ({backup_kind}:{backup_arg}) ...")
            df = fetch_backup(backup_kind, backup_arg)
            # Commodity fallback (COMEXxINR) approximates MCX; long EMA can be off
            # due to import-duty steps. Index backups (nse/yf) are exact.
            approx = df is not None and backup_kind == "commodity"
        if df is None:
            rows.append((label, None, False))
            continue
        try:
            rows.append((label, compute_metrics(df), approx))
        except Exception as e:
            print(f"  metric error {label}: {e}")
            rows.append((label, None, False))

    report = build_report(rows)
    print("\n" + report)

    if '--send' in sys.argv:
        if CHAT_ID == "REPLACE_WITH_CHAT_ID":
            print("\nCHAT_ID not set — refusing to send. Edit CHAT_ID at top of script.")
            return
        telegram_send_api().send_message(CHAT_ID, report)
        print(f"\nSent to Telegram ({CHAT_ID})")
    else:
        print("\n--- Add --send flag to send to Telegram ---")


if __name__ == '__main__':
    main()
