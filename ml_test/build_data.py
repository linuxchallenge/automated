"""Build pipeline.py input from the Nifty OHLC file plus the engine's
options_chain_<SYMBOL>_*.csv snapshots.

Usage: python build_data.py [SRC_DIR] [--freq 15min] [--ohlc "NIFTY 50_15minute.csv"] [--out ...]
Prices (spot/open/high/low) come from the OHLC file. With SRC_DIR, option-chain
features are joined on and the output covers only the dates both have
(data/nifty_15m.csv); without it the output is price-only over the whole OHLC
history (data/nifty_15m_price.csv).
SRC_DIR is searched recursively; if a date exists in several folders the largest
file wins. Bars are labelled at their close (right edge), so a bar at time t only
holds data from at or before t. Chain files older than 2024-08-19 lack the
open-interest columns and are skipped.
"""
import argparse
import glob
import os
import sys
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "auto_straddle"))
import nifty_ml_features as nf  # noqa: E402  (shared with the live strategy)

ATM = {"spot_price": "spot", "atm_strike": "atm_strike", "atm_current_ce_price": "ce", "atm_current_pe_price": "pe",
       "atm_next_ce_price": "next_ce", "atm_next_pe_price": "next_pe",
       "prev_atm_strike": "held_strike", "prev_atm_ce_price": "held_ce", "prev_atm_pe_price": "held_pe"}


def load_ohlc(freq, path=None):
    path = path or os.path.join(HERE, f"NIFTY 50_{freq.replace('min', 'minute')}.csv")
    d = pd.read_csv(path, parse_dates=["date"]).set_index("date")
    return nf.session_bars(d, freq)


def day_bars(src, symbol, freq):
    """{date string: chain bars} for one symbol, one (largest) file per date."""
    best = {}  # filename -> (size, path)
    for f in glob.glob(os.path.join(src, "**", f"options_chain_{symbol}_*.csv"), recursive=True):
        name, size = os.path.basename(f), os.path.getsize(f)
        if " " not in name and size > best.get(name, (0, ""))[0]:
            best[name] = (size, f)
    bars = {}
    for name in sorted(best):
        d = nf.read_chain_csv(best[name][1])
        if d is None:
            continue
        b = nf.chain_bars(d, freq)
        if nf.chain_day_ok(b, freq):  # drops holidays, weekends and badly incomplete days
            bars[name[-14:-4]] = b
    return bars


def load_options(src, freq):
    """(option columns for the model, raw ATM quotes for straddle.py) over every usable day."""
    nifty, bank = day_bars(src, "NIFTY", freq), day_bars(src, "BANKNIFTY", freq)
    days = [d for d in sorted(nifty) if d in bank]
    opt = pd.concat(nf.option_frame(nifty[d], bank[d]) for d in days)
    atm = pd.concat(nifty[d][list(ATM)].rename(columns=ATM) for d in days)
    opt.index.name = atm.index.name = "timestamp"
    return opt, atm


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?")
    ap.add_argument("--ohlc", default=None)
    ap.add_argument("--freq", default="15min")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    tag = a.freq.replace("min", "m")
    out = a.out or (f"data/nifty_{tag}.csv" if a.src else f"data/nifty_{tag}_price.csv")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    df = load_ohlc(a.freq, a.ohlc)
    if a.src:
        ohlc, (opt, atm) = df, load_options(a.src, a.freq)
        df = ohlc.join(opt, how="inner")
        # the first bar kept for each day absorbs any earlier OHLC bars, so day open/high/low stay exact
        for t in df.groupby(df.index.normalize()).head(1).index:
            pre = ohlc.loc[t.normalize():t]
            df.loc[t, ["open", "high", "low"]] = pre["open"].iloc[0], pre["high"].max(), pre["low"].min()
        # raw ATM quotes for straddle.py; kept out of the feature file so pipeline.py never sees them
        atm.to_csv(out.replace(".csv", "_atm.csv"))
    df.to_csv(out)
    print(f"{out}: {len(df)} bars, {df.index.normalize().nunique()} days, {df.index[0]} -> {df.index[-1]}")
    print(df.describe().loc[["min", "50%", "max"]].round(4).T)
