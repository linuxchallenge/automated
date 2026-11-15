"""Walk-forward LightGBM baseline for Nifty bullish/bearish/sideways.

Input CSV columns (min): timestamp, spot
Optional: open, high, low (used for gap / previous-day / Alligator features), pcr,
and any other numeric columns (vix, fut, oi ...), which become features automatically.

Usage: python pipeline.py data.csv [--horizon 6] [--k 0.6] [--cost 0.0004] [--thr 0.45]
Use --synthetic to smoke-test without data.
"""
import argparse
import os
import sys
import numpy as np
import pandas as pd
import lightgbm as lgb

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "auto_straddle"))
from nifty_ml_features import features  # noqa: E402  (shared with the live strategy)

CLASSES = {0: "bearish", 1: "sideways", 2: "bullish"}


def load(path, synthetic=False):
    if synthetic:
        n = 20000
        idx = pd.date_range("2023-01-02 09:15", periods=n, freq="15min")
        rng = np.random.default_rng(0)
        spot = 18000 * np.exp(np.cumsum(rng.normal(0, 0.0012, n)))
        pcr = 1 + 0.2 * np.sin(np.arange(n) / 500) + rng.normal(0, 0.05, n)
        return pd.DataFrame({"spot": spot, "pcr": pcr}, index=idx)
    df = pd.read_csv(path, parse_dates=["timestamp"]).set_index("timestamp").sort_index()
    return df


def labels(df, horizon, k):
    # intraday only: neither the forward window nor the vol scale crosses the overnight gap
    ls, day = np.log(df["spot"]), df.index.normalize()
    fwd = ls.groupby(day).shift(-horizon) - ls
    vol = ls.groupby(day).diff().rolling(50, min_periods=25).std() * np.sqrt(horizon)  # ATR-like scale
    y = pd.Series(1, index=df.index)
    y[fwd > k * vol] = 2
    y[fwd < -k * vol] = 0
    y[fwd.isna() | vol.isna()] = np.nan
    return y, fwd


def walk_forward(X, y, fwd, horizon, train_bars, test_bars, thr, cost):
    pos = np.zeros(len(X))
    start = train_bars
    preds = np.full((len(X), 3), np.nan)
    base = np.full((len(X), 3), np.nan)  # training-window class frequencies (no-skill forecast)
    gains = []
    while start < len(X):
        tr_end = start - horizon  # purge: drop bars whose label overlaps test
        tr = slice(max(0, tr_end - train_bars), tr_end)
        te = slice(start, min(start + test_bars, len(X)))
        Xt, yt = X.iloc[tr], y.iloc[tr]
        m = yt.notna()
        if m.sum() > 500:
            clf = lgb.LGBMClassifier(
                n_estimators=200, learning_rate=0.03, num_leaves=15,
                min_child_samples=100, subsample=0.8, subsample_freq=1,
                colsample_bytree=0.8, reg_lambda=5, verbose=-1, importance_type="gain")
            clf.fit(Xt[m], yt[m].astype(int))
            preds[te] = clf.predict_proba(X.iloc[te])
            base[te] = yt[m].astype(int).value_counts(normalize=True).reindex([0, 1, 2]).fillna(1e-6).values
            gains.append(clf.feature_importances_ / clf.feature_importances_.sum())
        start += test_bars
    p = pd.DataFrame(preds, index=X.index, columns=[0, 1, 2]).dropna()
    top = p.idxmax(axis=1)
    conf = p.max(axis=1)
    sig = pd.Series(0, index=p.index)
    sig[(top == 2) & (conf > thr)] = 1
    sig[(top == 0) & (conf > thr)] = -1
    # trade the next bar's return (signal known at close of bar t, earns t->t+1)
    base = pd.DataFrame(base, index=X.index, columns=[0, 1, 2]).loc[p.index]
    imp = pd.Series(np.mean(gains, axis=0), index=X.columns).sort_values(ascending=False)
    return sig, p, base, imp


def quality(p, base, y, sig, imp):
    """Out-of-sample prediction quality vs the no-skill (training base rate) forecast."""
    y = y.reindex(p.index).dropna().astype(int)
    p, base, sig = p.loc[y.index], base.loc[y.index], sig.loc[y.index]
    ll = lambda q: -np.log(q.values[np.arange(len(y)), y.values].clip(1e-9)).mean()
    print(f"log-loss: model={ll(p):.4f}  base-rate={ll(base):.4f}  (lower is better)")
    print(f"accuracy: model={(p.idxmax(axis=1) == y).mean():.3f}  base-rate={(base.idxmax(axis=1) == y).mean():.3f}")
    for name, s, good, bad in (("long", 1, 2, 0), ("short", -1, 0, 2)):
        m = sig == s
        print(f"{name:<5} calls: n={m.sum()}  right={(y[m] == good).mean():.3f}  "
              f"sideways={(y[m] == 1).mean():.3f}  wrong way={(y[m] == bad).mean():.3f}  "
              f"(base rate of right={(y == good).mean():.3f})")
    print("feature importance (share of gain, mean over folds):")
    print(imp.round(3).to_string())


def evaluate(sig, spot, cost):
    r = np.log(spot).diff().shift(-1)           # next-bar return
    held = sig.shift(0)
    turn = held.diff().abs().fillna(0)
    pnl = held * r - turn * cost                # cost per unit of position change
    pnl = pnl[sig.index[sig.notna()]].dropna()
    return pnl


def report(name, pnl, bars_per_year=25 * 250):
    if len(pnl) == 0:
        print(name, "no data"); return
    sharpe = pnl.mean() / (pnl.std() + 1e-12) * np.sqrt(bars_per_year)
    eq = pnl.cumsum()
    dd = (eq - eq.cummax()).min()
    print(f"{name:<22} net_ret={pnl.sum():+.3f}  sharpe={sharpe:+.2f}  maxDD={dd:+.3f}  trades_bars={(pnl!=0).sum()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--horizon", type=int, default=6)
    ap.add_argument("--k", type=float, default=0.6)
    ap.add_argument("--cost", type=float, default=0.0004, help="per unit position change, fraction (4 bps)")
    ap.add_argument("--thr", type=float, default=0.45)
    ap.add_argument("--bars_per_day", type=int, default=25)
    a = ap.parse_args()

    df = load(a.csv, a.synthetic)
    X = features(df, a.bars_per_day)
    y, fwd = labels(df, a.horizon, a.k)
    ok = X.notna().all(axis=1)
    X, y, fwd, df = X[ok], y[ok], fwd[ok], df[ok]

    train_bars = a.bars_per_day * 120   # ~6 months
    test_bars = a.bars_per_day * 5      # ~1 week, then retrain
    sig, p, base, imp = walk_forward(X, y, fwd, a.horizon, train_bars, test_bars, a.thr, a.cost)
    print("label mix:", y.value_counts(normalize=True).round(3).to_dict())
    print("signal mix:", sig.value_counts(normalize=True).round(3).to_dict())

    quality(p, base, y, sig, imp)

    spot = df["spot"]
    report("LightGBM (net)", evaluate(sig, spot, a.cost))
    report("LightGBM (no cost)", evaluate(sig, spot, 0))
    mom = np.sign(np.log(spot).diff(a.horizon)).reindex(sig.index).fillna(0)
    report("Momentum baseline", evaluate(mom, spot, a.cost))
    ma = (spot > spot.rolling(50).mean()).astype(int).mul(2).sub(1).reindex(sig.index).fillna(0)
    report("MA(50) baseline", evaluate(ma, spot, a.cost))
    report("Buy & hold", evaluate(pd.Series(1, index=sig.index), spot, 0))
