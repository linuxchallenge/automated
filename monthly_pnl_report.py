"""
Monthly PNL Analysis & Telegram Report
Run at month-end: python monthly_pnl_report.py [month_number]
Example: python monthly_pnl_report.py 4   (for April)
"""

import os
import sys
from datetime import datetime

import pandas as pd

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'auto_straddle'))
from TelegramSend import telegram_send_api
import configuration

# ---------------------------------------------------------------------------
# Margin per lot (₹) — UPDATE monthly from Zerodha margin calculator
# https://zerodha.com/margin-calculator/Futures/
# https://zerodha.com/margin-calculator/Commodity/
# For option selling (AutoStraddle/FarSell): use ~same as futures margin per lot
# For straddle (2 short legs): margin ≈ 1.5x single leg due to SPAN benefit
# ---------------------------------------------------------------------------
MARGIN_PER_LOT = {
    # Index options/futures
    'NIFTY':       178_850,
    'BANKNIFTY':   191_412,
    'FINNIFTY':    181_131,
    'MIDCPNIFTY':  220_067,
    'SENSEX':      150_000,
    # Commodities
    'CRUDEOIL':    339_833,
    'NATURALGAS':   57_860,
    'COPPER':      295_017,
    'GOLD':      1_456_388,
    'LEAD':         72_462,
    'ZINC':        156_465,
    'ALUMINIUM':   172_871,
    'SILVER':    1_493_211,
}

LOT_SIZES = {
    'AutoStraddle': {'NIFTY': 65, 'BANKNIFTY': 30, 'FINNIFTY': 65, 'MIDCPNIFTY': 50, 'SENSEX': 10},
    'FarSell':      {'NIFTY': 65, 'BANKNIFTY': 30, 'FINNIFTY': 65, 'MIDCPNIFTY': 50, 'SENSEX': 10},
    'IndexFuture':  {'NIFTY': 65, 'BANKNIFTY': 30, 'FINNIFTY': 65},
    'Commodity':    {'CRUDEOIL': 10, 'NATURALGAS': 250, 'COPPER': 2500, 'GOLD': 10,
                     'LEAD': 1000, 'ZINC': 1000, 'ALUMINIUM': 1000, 'SILVER': 1},
    'cash_short':   {},
    'NiftyPositional': {'NIFTY': 65, 'SENSEX': 20},
}

# Straddle uses 2 short legs — margin is ~1.5x single leg (SPAN benefit)
STRADDLE_MARGIN_MULTIPLIER = {
    'AutoStraddle':     1.5,
    'FarSell':          1.0,   # single leg sell
    'IndexFuture':      1.0,
    'Commodity':        1.0,
    'cash_short':       0.0,
    'NiftyPositional':  1.5,   # strangle (2 short legs)
}

EXCLUDE_ACCOUNTS = ['dummy']

PNL_DIR = os.path.expanduser("~/temp/data_collection/pnl")


def indian_format(value):
    """Format number in Indian comma style (e.g., 5,54,585)."""
    neg = value < 0
    value = int(round(abs(value)))
    s = str(value)
    if len(s) <= 3:
        return ("-" if neg else "") + s
    last3 = s[-3:]
    rest = s[:-3]
    parts = []
    while rest:
        parts.append(rest[-2:])
        rest = rest[:-2]
    parts.reverse()
    result = ",".join(parts) + "," + last3
    return ("-" if neg else "") + result


def load_pnl_data(month=None):
    """Load consolidated PNL CSV for given month number."""
    if month is None:
        month = datetime.now().month
    filename = f"consolidated_pnl_{month:02d}.csv"

    # Try live PNL_DIR first (~/temp/data_collection/pnl), then local directory
    local_path = os.path.join(os.path.dirname(__file__), filename)
    remote_path = os.path.join(PNL_DIR, filename)

    for path in [remote_path, local_path]:
        if os.path.exists(path):
            df = pd.read_csv(path)
            df['Date'] = pd.to_datetime(df['Date'])
            # Exclude dummy account
            df = df[~df['Account'].isin(EXCLUDE_ACCOUNTS)]
            # Keep only the latest year's data
            latest_year = df['Date'].dt.year.max()
            df = df[df['Date'].dt.year == latest_year]
            return df, path

    print(f"File not found: {filename}")
    sys.exit(1)


def calc_capital_used(row):
    """Estimate margin/capital deployed for a trade."""
    strategy = row['Stratergy']
    symbol = row['Symbol']
    qty = row['Quantity']

    margin = MARGIN_PER_LOT.get(symbol, 100_000)
    multiplier = STRADDLE_MARGIN_MULTIPLIER.get(strategy, 1.0)
    return qty * margin * multiplier


def generate_account_report(adf, account, month_name):
    """Generate monthly report for a single account."""
    lines = []
    lines.append(f"📊 *{account.upper()} — {month_name}*")
    lines.append("")

    adf = adf.copy()
    adf['CapitalUsed'] = adf.apply(calc_capital_used, axis=1)

    # --- Summary ---
    net = adf['NetPNL'].sum()
    gross = adf['TotalPNL'].sum()
    brok = adf['Brokarge'].sum()
    trading_days = adf['Date'].dt.date.nunique()
    total_trades = len(adf)
    cap = adf.groupby('Date')['CapitalUsed'].sum().mean()
    daily_pnl = adf.groupby('Date')['NetPNL'].sum()
    wins = (daily_pnl > 0).sum()
    losses = (daily_pnl <= 0).sum()
    roi = (net / cap * 100) if cap > 0 else 0

    lines.append("*── SUMMARY ──*")
    lines.append(f"Gross PNL: ₹{indian_format(gross)}")
    lines.append(f"Brokerage: ₹{indian_format(brok)}")
    lines.append(f"*Net PNL: ₹{indian_format(net)}*")
    lines.append(f"Days: {trading_days} | Trades: {total_trades}")
    lines.append(f"Win/Loss Days: {wins}/{losses}")
    lines.append(f"Avg Capital: ₹{indian_format(cap)} | ROI: {roi:.1f}%")
    lines.append("")

    # --- Strategy breakdown ---
    lines.append("*── STRATEGY WISE ──*")
    for strategy, sdf in adf.groupby('Stratergy'):
        s_net = sdf['NetPNL'].sum()
        s_brok = sdf['Brokarge'].sum()
        s_cap = sdf.groupby('Date')['CapitalUsed'].sum().mean()
        trade_count = len(sdf)
        win_trades = (sdf['NetPNL'] > 0).sum()
        s_roi = (s_net / s_cap * 100) if s_cap > 0 else 0

        lines.append(f"*{strategy}*")
        lines.append(f"  Net: ₹{indian_format(s_net)} | Brok: ₹{indian_format(s_brok)}")
        lines.append(f"  Trades: {trade_count} | Win%: {win_trades/trade_count*100:.0f}%")
        lines.append(f"  Capital: ₹{indian_format(s_cap)} | ROI: {s_roi:.1f}%")
        lines.append("")

    # --- Symbol breakdown ---
    lines.append("*── SYMBOL WISE ──*")
    sym_summary = adf.groupby('Symbol').agg(
        NetPNL=('NetPNL', 'sum'),
        Trades=('NetPNL', 'count'),
    ).sort_values('NetPNL', ascending=False)

    for symbol, row in sym_summary.iterrows():
        s_net = row['NetPNL']
        sign = "🟢" if s_net > 0 else "🔴"
        lines.append(f"  {sign} {symbol}: ₹{indian_format(s_net)} ({int(row['Trades'])} trades)")
    lines.append("")

    # --- Best/Worst days ---
    sorted_daily = daily_pnl.sort_values()
    lines.append("*── TOP 3 BEST & WORST DAYS ──*")
    lines.append("Worst:")
    for dt, pnl in sorted_daily.head(3).items():
        lines.append(f"  {dt.strftime('%d-%b')}: ₹{indian_format(pnl)}")
    lines.append("Best:")
    for dt, pnl in sorted_daily.tail(3).items():
        lines.append(f"  {dt.strftime('%d-%b')}: ₹{indian_format(pnl)}")

    report = "\n".join(lines)
    # Escape underscores for Telegram markdown (avoid italic parsing)
    report = report.replace("_", "\\_")
    return report


def generate_all_reports(df, month_name):
    """Generate per-account reports. Returns dict {account: report_text}."""
    df = df.copy()
    reports = {}
    for account, adf in df.groupby('Account'):
        reports[account] = generate_account_report(adf, account, month_name)
    return reports


def send_to_telegram(reports):
    """Send each account's report to its own telegram group."""
    telegram = telegram_send_api()
    config = configuration.ConfigurationLoader.get_configuration()

    for account, report in reports.items():
        telegram_key = account + "_telegram"
        group_id = config.get(telegram_key)
        if group_id:
            for chunk in split_message(report, 4000):
                telegram.send_message(group_id, chunk)
            print(f"Sent to {account} ({group_id})")
        else:
            print(f"No telegram group configured for {account}")


def split_message(text, max_len):
    """Split long message at line boundaries."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        idx = text.rfind('\n', 0, max_len)
        if idx == -1:
            idx = max_len
        chunks.append(text[:idx])
        text = text[idx + 1:]
    return chunks


def main():
    """Generate and optionally send monthly PNL reports."""
    month = int(sys.argv[1]) if len(sys.argv) > 1 else datetime.now().month
    month_name = datetime(datetime.now().year, month, 1).strftime('%B %Y')

    df, path = load_pnl_data(month)
    print(f"Loaded {len(df)} trades from {path}")

    reports = generate_all_reports(df, month_name)

    # Print to console
    for account, report in reports.items():
        print("\n" + "=" * 50)
        print(report)

    # Ask before sending
    if '--send' in sys.argv:
        send_to_telegram(reports)
    else:
        print("\n--- Add --send flag to send to Telegram ---")


if __name__ == '__main__':
    main()
