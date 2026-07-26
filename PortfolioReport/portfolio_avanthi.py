"""Fetch and print portfolio detail (holdings + positions) for avanthi (Zerodha)."""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0103
# pylint: disable=C0209

import os
import sys

# The zerodha package (and its credentials) live in auto_straddle/.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'auto_straddle'))

import zerodha.zerodha_api as zerodha_module  # noqa: E402


def get_portfolio():
    obj = zerodha_module.zerodha_api()

    # --- Holdings (delivery / CNC stocks) ---
    holdings = obj.kite.holdings()
    print("\n===== HOLDINGS =====")
    total_current = 0.0
    for h in holdings:
        # Pledged shares sit under collateral_quantity; freshly bought under t1_quantity.
        qty = (int(h.get('quantity', 0) or 0)
               + int(h.get('t1_quantity', 0) or 0)
               + int(h.get('collateral_quantity', 0) or 0))
        ltp = float(h.get('last_price', 0) or 0)
        current = ltp * qty
        total_current += current
        # Kite returns average_price=0 for pledged holdings, so cost basis / real
        # P&L aren't available for them. Only trust avg/pnl when avg is present.
        avg = float(h.get('average_price', 0) or 0)
        pledged = ' [pledged]' if h.get('collateral_type') else ''
        if avg > 0:
            pnl = float(h.get('pnl', 0) or 0)
            print(f"{h.get('tradingsymbol', ''):15} qty={qty:6} avg={avg:10.2f} "
                  f"ltp={ltp:10.2f} current={current:12.2f} pnl={pnl:10.2f}{pledged}")
        else:
            print(f"{h.get('tradingsymbol', ''):15} qty={qty:6} avg={'N/A':>10} "
                  f"ltp={ltp:10.2f} current={current:12.2f} pnl={'N/A':>10}{pledged}")
    print(f"\nTotal current market value = {total_current:.2f}")
    print("(avg/pnl shown as N/A where Kite reports no cost basis, e.g. pledged holdings)")

    # --- Positions (intraday / F&O / commodity) ---
    positions = obj.kite.positions()
    print("\n===== POSITIONS (net) =====")
    for p in positions.get('net', []):
        qty = int(p.get('quantity', 0) or 0)
        if qty == 0:
            continue
        print(f"{p.get('tradingsymbol', ''):20} exch={p.get('exchange', ''):5} "
              f"qty={qty:6} avg={float(p.get('average_price', 0) or 0):10.2f} "
              f"ltp={float(p.get('last_price', 0) or 0):10.2f} pnl={float(p.get('pnl', 0) or 0):10.2f}")

    return holdings, positions


if __name__ == "__main__":
    get_portfolio()
