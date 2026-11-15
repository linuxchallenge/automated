"""Feature and model code shared by the ml_test research scripts and NiftyMlStratergy.

Pure pandas/numpy on purpose: the Pi runs the trained model from a JSON export
(see ml_test/train_model.py), so it needs no machine-learning packages.
All bars are labelled at their close: a bar at time t only holds data from at or before t.
"""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
import glob
import json
import math
import os
import numpy as np
import pandas as pd

PRICE_COLS = ("spot", "open", "high", "low")
OI_RANKS = ("highest", "second_highest", "third_highest")
# columns of options_chain_<SYMBOL>_<date>.csv that the features need
CHAIN_COLS = ["time", "spot_price", "pe_to_ce_ratio", "atm_strike", "atm_current_ce_price", "atm_current_pe_price",
              "atm_next_ce_price", "atm_next_pe_price", "prev_atm_strike", "prev_atm_ce_price", "prev_atm_pe_price",
              "ce_strangle_price", "pe_strangle_price", "ce_highest_strike", "pe_highest_strike"] \
             + [f"{side}_{rank}_open_interest" for side in ("ce", "pe") for rank in OI_RANKS]


def alligator(high, low, close):
    """Williams Alligator as in alligator_api.py: SMMA 13/8/5 of (H+L)/2, shifted 8/5/3 bars.
    Returns trend (+1 close above all three lines, -1 below all, 0 sideways) and lips-jaw spread."""
    hl2 = (high + low) / 2
    jaw, teeth, lips = (hl2.ewm(alpha=1 / n, adjust=False, min_periods=n).mean().shift(k)
                        for n, k in ((13, 8), (8, 5), (5, 3)))
    lines = pd.concat([jaw, teeth, lips], axis=1)
    trend = (close > lines.max(axis=1)).astype(int) - (close < lines.min(axis=1)).astype(int)
    return trend.where(lines.notna().all(axis=1)), (lips - jaw) / close


def expiry_dte(index):
    """Trading days to the weekly Nifty expiry (0 = expiry day). Thursday until Aug 2025,
    Tuesday after; if that weekday is a holiday the expiry is the previous trading day."""
    days = pd.DatetimeIndex(index.normalize().unique())
    days = days.union(pd.bdate_range(days[-1], periods=8)[1:])  # so the last days still see a next expiry
    ok = days.dayofweek <= np.where(days < pd.Timestamp("2025-09-01"), 3, 1)
    expiries = pd.Series(days[ok]).groupby(days[ok].to_period("W").values).max()
    pos = np.arange(len(days))
    nxt = pd.Series(np.where(days.isin(expiries.values), pos, np.nan)).bfill().values
    return pd.Series(nxt - pos, index=days).reindex(index.normalize()).values


def price_features(df, bars_per_day=25):
    """Features from price bars alone (spot, and open/high/low when present)."""
    f = pd.DataFrame(index=df.index)
    s = df["spot"]
    r = np.log(s).diff()
    for n in (1, 3, 6, 12, bars_per_day):
        f[f"ret_{n}"] = np.log(s).diff(n)
    f["vol_12"] = r.rolling(12).std()
    f["vol_50"] = r.rolling(50).std()
    f["ma_gap_20"] = s / s.rolling(20).mean() - 1
    f["ma_gap_100"] = s / s.rolling(100).mean() - 1
    day = df.index.normalize()
    o, h, l = (df[c] if c in df else s for c in ("open", "high", "low"))
    d = pd.DataFrame({"open": o.groupby(day).first(), "high": h.groupby(day).max(),
                      "low": l.groupby(day).min(), "close": s.groupby(day).last()})
    prev = d.shift(1).reindex(day)  # previous trading day's OHLC on every bar
    f["gap"] = d["open"].reindex(day).values / prev["close"].values - 1  # gap up / down at the open
    f["pdh"] = s / prev["high"].values - 1  # distance to previous day's high / low
    f["pdl"] = s / prev["low"].values - 1
    f["allig"], f["allig_mouth"] = alligator(h, l, s)
    f["allig_day"] = alligator(d["high"], d["low"], d["close"])[0].shift(1).reindex(day).values
    f["dte"] = expiry_dte(df.index)
    f["tod"] = df.index.hour * 60 + df.index.minute
    f["dow"] = df.index.dayofweek
    return f


def chain_features(df, bars_per_day=25):
    """Features from option-chain bars: PCR level/changes/z-score, and level + 3-bar change of every other column."""
    f = pd.DataFrame(index=df.index)
    if "pcr" in df:
        p = df["pcr"]
        f["pcr"] = p
        for n in (1, 3, 6, 12):
            f[f"pcr_chg_{n}"] = p.diff(n)
        f["pcr_z"] = (p - p.rolling(bars_per_day * 5).mean()) / p.rolling(bars_per_day * 5).std()
    for c in df.columns:
        if c != "pcr":
            f[c] = df[c]
            f[f"{c}_chg_3"] = df[c].diff(3)
    return f


def features(df, bars_per_day=25):
    """All features from one joined frame (price columns plus any other numeric columns)."""
    chain = df[[c for c in df.columns if c not in PRICE_COLS]]
    return price_features(df, bars_per_day).join(chain_features(chain, bars_per_day))


def make_features(ohlc, opt, bars_per_day=25):
    """Price features from the full price history joined with option features where both exist.
    Used for the deployed model, both when training it and live. Rows whose option lookback
    crosses a gap in the chain files are dropped."""
    chain = chain_features(opt, bars_per_day)[chain_window_ok(opt.index, bars_per_day)]
    return price_features(ohlc, bars_per_day).join(chain, how="inner")


def chain_window_ok(index, bars_per_day=25, max_days=12):
    """True where the 5-day lookback of the option features (PCR z-score) is contiguous, i.e. the bar
    5 trading days back is at most max_days calendar days old. False after gaps in the chain files."""
    ts = pd.Series(index, index=index)
    return ((ts - ts.shift(bars_per_day * 5)) <= pd.Timedelta(days=max_days)).values


def session_bars(d, freq="15min", keep_last_day=False):
    """Bars labelled by their open with open/high/low/close columns -> close-labelled regular-session
    bars as spot/open/high/low. Weekends and short sessions are dropped; keep_last_day keeps the
    (still incomplete) last day for live use."""
    d = d[["open", "high", "low", "close"]].astype(float).rename(columns={"close": "spot"}).sort_index()
    d.index = d.index + pd.to_timedelta(freq)
    d = d.between_time("09:16", "15:30")
    d = d[d.index.dayofweek < 5]
    day = d.index.normalize()
    full_day = len(pd.date_range("09:30", "15:30", freq=freq))
    keep = d.groupby(day)["spot"].transform("size") >= 0.8 * full_day
    if keep_last_day and len(d):
        keep = keep | (day == day[-1])
    d = d[keep]
    d.index.name = "timestamp"
    return d[list(PRICE_COLS)]


def read_chain_csv(path):
    """One options_chain_<SYMBOL>_<date>.csv as a time-indexed numeric frame, or None if unusable
    (unreadable, or an old layout without the open-interest columns)."""
    try:
        d = pd.read_csv(path)
    except Exception:
        return None
    if not set(CHAIN_COLS).issubset(d.columns):
        return None
    d["time"] = pd.to_datetime(d["time"], errors="coerce")
    d = d.dropna(subset=["time"]).set_index("time").sort_index()
    return d[CHAIN_COLS[1:]].apply(pd.to_numeric, errors="coerce")


def chain_bars(d, freq="15min"):
    """Minute snapshots -> bars holding the last snapshot at or before each bar close."""
    b = d.resample(freq, closed="right", label="right").last().between_time("09:30", "15:30")
    return b.dropna(subset=["spot_price"])


def chain_day_ok(b, freq="15min", partial=False):
    """False for holidays (stale, flat spot), weekends and badly incomplete days.
    partial=True skips the completeness check, for the day still in progress."""
    if b.empty or b.index[0].dayofweek > 4 or b["spot_price"].nunique() < 0.5 * len(b):
        return False
    return partial or len(b) >= 0.8 * len(pd.date_range("09:30", "15:30", freq=freq))


def chain_raw(d, prefix=""):
    """Bar-level option columns that feed chain_features (prefix 'bn_' = Bank Nifty: PCR and straddle only)."""
    d = d.replace(0, np.nan)  # 0 = missing quote
    s = d["spot_price"]
    f = pd.DataFrame(index=d.index)
    f[prefix + "pcr"] = d["pe_to_ce_ratio"]
    # ATM straddle premium relative to spot (implied-volatility proxy)
    f[prefix + "straddle"] = (d["atm_current_ce_price"] + d["atm_current_pe_price"]) / s
    if prefix:
        return f
    # put-call parity forward (K + C - P) vs spot: futures-basis proxy
    f["basis"] = (d["atm_strike"] + d["atm_current_ce_price"] - d["atm_current_pe_price"]) / s - 1
    # strangle strikes sit ~symmetric around ATM, so the PE/CE price ratio is a skew proxy
    f["skew"] = np.log(d["pe_strangle_price"] / d["ce_strangle_price"])
    # distance from spot to the highest-OI call / put strike
    f["ce_wall"] = d["ce_highest_strike"] / s - 1
    f["pe_wall"] = d["pe_highest_strike"] / s - 1
    f["wall_oi"] = np.log(d["pe_highest_open_interest"] / d["ce_highest_open_interest"])
    f["top3_oi"] = np.log(sum(d[f"pe_{r}_open_interest"] for r in OI_RANKS)
                          / sum(d[f"ce_{r}_open_interest"] for r in OI_RANKS))
    return f


def option_frame(nifty_bars, bank_bars):
    """Nifty and Bank Nifty chain bars of one or more days -> the option columns used as model input."""
    f = chain_raw(nifty_bars).join(chain_raw(bank_bars, "bn_"))
    f["bn_rel"] = bank_bars["spot_price"] / nifty_bars["spot_price"]  # Bank Nifty relative strength
    return f.dropna()


def recent_option_frame(chain_dir, freq="15min", days=12):
    """option_frame over the newest `days` daily chain files found in chain_dir (live use)."""
    frames = []
    paths = sorted(glob.glob(os.path.join(chain_dir, "options_chain_NIFTY_*.csv")))[-days:]
    for path in paths:
        n = read_chain_csv(path)
        b = read_chain_csv(path.replace("options_chain_NIFTY_", "options_chain_BANKNIFTY_"))
        if n is None or b is None:
            continue
        n, b, partial = chain_bars(n, freq), chain_bars(b, freq), path == paths[-1]
        if chain_day_ok(n, freq, partial) and chain_day_ok(b, freq, partial):
            frames.append(option_frame(n, b))
    return pd.concat(frames) if frames else None


def load_model(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def predict_proba(model, row):
    """Class probabilities [bearish, sideways, bullish] for one feature row (dict or Series).
    Evaluates the exported LightGBM trees directly; the row must not contain NaN."""
    x = [float(row[name]) for name in model["features"]]
    n_class = model["classes"]
    scores = [0.0] * n_class
    for i, tree in enumerate(model["trees"]):
        feat, thr, left, right = tree["f"], tree["t"], tree["l"], tree["r"]
        node = 0 if feat else -1  # leaves are stored as -(leaf index + 1)
        while node >= 0:
            node = left[node] if x[feat[node]] <= thr[node] else right[node]
        scores[i % n_class] += tree["v"][-node - 1]
    top = max(scores)
    exps = [math.exp(v - top) for v in scores]
    return [e / sum(exps) for e in exps]
