"""Day P&L of the Nifty ML strategy from its trades file, shared by the strategy (day-end row in
pnl/consolidated_pnl_<MM>.csv) and ledger_calculation (next-morning ledger).

Kept free of broker and market-data imports so the ledger can use it without loading the strategy.
"""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0103
import os
import sys
import pandas as pd
import brokrage_calculator

STRATEGY_NAME = 'NiftyMl'   # the Stratergy value in pnl/consolidated_pnl_<MM>.csv
LOT_SIZE = 65


def completed_legs(trades):
    """Legs that were really sold and bought back: entry and exit both confirmed by the broker."""
    if trades is None or trades.empty:
        return pd.DataFrame(columns=[] if trades is None else trades.columns)
    return trades[(trades['entry_state'] == 'complete') & (trades['exit_state'] == 'complete')]


def day_summary(trades, lot_size=LOT_SIZE):
    """Gross, brokerage and net rupees of one day's trades file, counting completed legs only.
    A trade is the leg(s) sold in one 15-minute bar, as for the daily trade limit."""
    legs = completed_legs(trades)
    if legs.empty:
        return None
    brokerage = 0.0
    for _, leg in legs.iterrows():
        units = float(leg['quantity']) * lot_size
        brokerage += brokrage_calculator.calculate_equity_options(
            float(leg['exit_price']), float(leg['entry_price']), units)['total_charges']
    gross = float(legs['pnl_rupees'].astype(float).sum())
    return {
        'legs': len(legs),
        'trades': pd.to_datetime(legs['entry_time']).dt.floor("15min").nunique(),
        'quantity': float(legs['quantity'].astype(float).max()),
        'gross': gross,
        'brokerage': brokerage,
        'net': gross - brokerage,
        'close_time': str(legs['exit_time'].max()),
    }


def append_consolidated_pnl(trades, account, symbol, date, pnl_dir="pnl"):
    """Append the day's row to <pnl_dir>/consolidated_pnl_<MM>.csv in the format the other strategies use.
    Returns the row, or None when there is nothing to record or the row is already there."""
    summary = day_summary(trades)
    if summary is None:
        return None
    file_name = os.path.join(pnl_dir, f"consolidated_pnl_{date[5:7]}.csv")
    existing = pd.read_csv(file_name) if os.path.exists(file_name) else pd.DataFrame()
    if not existing.empty and ((existing['Date'].astype(str) == date) & (existing['Account'] == account)
                               & (existing['Symbol'] == symbol) & (existing['Stratergy'] == STRATEGY_NAME)).any():
        return None  # already recorded (a restart after the square-off)
    row = {
        'Date': date,
        'Account': account,
        'Symbol': symbol,
        'Quantity': summary['quantity'],
        'NumberofTrade': summary['trades'],
        'TotalPNL': summary['gross'],
        'Brokarge': summary['brokerage'],
        'CloseTime': summary['close_time'],
        'Stratergy': STRATEGY_NAME,
        'NetPNL': summary['net'],
    }
    os.makedirs(pnl_dir, exist_ok=True)
    pd.concat([existing, pd.DataFrame([row])], ignore_index=True).to_csv(file_name, index=False)
    return row


if __name__ == "__main__":
    # Backfill a day that ran before the day-end row existed. Run from ~/temp/data_collection:
    #   python3 ~/automated/auto_straddle/nifty_ml_pnl.py 2026-11-18 deepti
    # Safe to repeat: an existing row is not added twice.
    day, acct = sys.argv[1], sys.argv[2]
    path = f"csv/ml_trades_{day}_{acct}_NIFTY.csv"
    frame = pd.read_csv(path, dtype={'entry_order_id': str, 'exit_order_id': str})
    print(append_consolidated_pnl(frame, acct, 'NIFTY', day) or f"nothing added for {day} {acct} (no completed legs, or already recorded)")
