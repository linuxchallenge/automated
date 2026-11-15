"""Short ATM straddle P&L from the *_atm.csv bar file written by build_data.py.

The files only quote the straddle at the current ATM strike, so the buy-back price
of a straddle sold earlier at another strike is estimated: forward from put-call
parity, volatility backed out of the current ATM straddle (Bachelier), then the
straddle repriced at the sold strike. Running this file checks that estimate
against the held_* columns (real quotes at the engine's own sold strike).

Usage: python straddle.py data/nifty_15m_atm.csv [--horizon 6] [--cost_pts 3]
"""
import argparse
import numpy as np
import pandas as pd
from scipy.special import ndtr


def _value(m, v):
    """Bachelier straddle price; m = forward - strike, v = sigma*sqrt(T), both in points."""
    d = m / v
    return 2 * v * np.exp(-d * d / 2) / np.sqrt(2 * np.pi) + m * (2 * ndtr(d) - 1)


def straddle_at(strike, atm_strike, ce, pe):
    """Estimated CE+PE price at `strike`, given the quoted CE/PE at `atm_strike`."""
    strike, atm_strike, ce, pe = (np.asarray(x, dtype=float) for x in (strike, atm_strike, ce, pe))
    fwd, quoted = atm_strike + ce - pe, ce + pe
    lo, hi = np.full(len(quoted), 1e-6), np.maximum(quoted, 1.0) * 2
    for _ in range(50):  # bisection for v
        mid = (lo + hi) / 2
        high = _value(ce - pe, mid) > quoted
        hi, lo = np.where(high, mid, hi), np.where(high, lo, mid)
    return np.where(np.isnan(quoted), np.nan, _value(fwd - strike, (lo + hi) / 2))


def short_pnl(a, horizon):
    """Points earned by selling the ATM straddle at each bar and buying it back
    `horizon` bars later the same day (NaN when the exit falls outside the day)."""
    a = a.replace(0, np.nan)
    fut = a.groupby(a.index.normalize())[["atm_strike", "ce", "pe"]].shift(-horizon)
    exit_price = straddle_at(a["atm_strike"], fut["atm_strike"], fut["ce"], fut["pe"])
    return a["ce"] + a["pe"] - exit_price


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--horizon", type=int, default=6)
    ap.add_argument("--cost_pts", type=float, default=3.0, help="round-trip cost per straddle in index points (placeholder)")
    a = ap.parse_args()
    df = pd.read_csv(a.csv, parse_dates=["timestamp"]).set_index("timestamp").sort_index()

    v = df[(df["held_strike"] > 0) & (df["held_ce"] > 0) & (df["held_pe"] > 0) & (df["ce"] > 0) & (df["pe"] > 0)]
    v = v[v["held_strike"] != v["atm_strike"]]
    err = straddle_at(v["held_strike"], v["atm_strike"], v["ce"], v["pe"]) - (v["held_ce"] + v["held_pe"])
    gap = (v["held_strike"] - v["atm_strike"]).abs()
    print(f"repricing check on {len(v)} bars where the held strike differs from ATM:")
    for lo, hi in ((0, 50), (50, 100), (100, 200), (200, 1e9)):
        e = err[(gap > lo) & (gap <= hi)]
        if len(e):
            print(f"  strike gap {lo:>3}-{hi if hi < 1e9 else 'up':<4} n={len(e):<5} mean err={e.mean():+.2f} pts  "
                  f"median abs err={e.abs().median():.2f} pts  95% abs err={e.abs().quantile(0.95):.2f} pts")

    pnl = short_pnl(df, a.horizon).dropna()
    net = pnl - a.cost_pts
    prem = (df["ce"] + df["pe"]).reindex(pnl.index)
    print(f"\nalways-sell baseline, hold {a.horizon} bars, {len(pnl)} entries, {pnl.index.normalize().nunique()} days:")
    print(f"  premium sold: median {prem.median():.1f} pts")
    print(f"  gross: mean {pnl.mean():+.2f} pts  median {pnl.median():+.2f}  win rate {(pnl > 0).mean():.3f}  "
          f"worst {pnl.min():.1f}  5th pct {pnl.quantile(0.05):.1f}")
    print(f"  net of {a.cost_pts} pts: mean {net.mean():+.2f} pts  win rate {(net > 0).mean():.3f}")
    tod = pnl.groupby(pnl.index.strftime("%H:%M")).mean()
    print("  mean gross pts by entry time:", "  ".join(f"{t} {x:+.1f}" for t, x in tod.items()))
