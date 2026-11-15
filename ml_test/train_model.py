"""Train the Nifty direction model on the most recent data and export it for the Pi.

Usage: python train_model.py SRC_DIR [--train_days 120] [--horizon 6] [--k 0.6]
SRC_DIR holds the options_chain_*.csv files (searched recursively, as in build_data.py).
Writes ../auto_straddle/nifty_ml_model.json: the LightGBM trees as plain arrays, which
nifty_ml_features.predict_proba evaluates without lightgbm. The export is checked
against lightgbm's own predictions before it is written.
"""
import argparse
import json
import os
import numpy as np
import lightgbm as lgb
from build_data import load_ohlc, load_options, nf, HERE
from pipeline import labels

BARS_PER_DAY = 25
OUT = os.path.join(HERE, "..", "auto_straddle", "nifty_ml_model.json")


def export_tree(node, out):
    """Flatten one dumped tree; returns the node id (leaves are -(leaf index + 1))."""
    if "leaf_value" in node and "split_index" not in node:
        out["v"].append(node["leaf_value"])
        return -len(out["v"])
    assert node["decision_type"] == "<=", node["decision_type"]
    i = len(out["f"])
    for key in ("f", "t", "l", "r"):
        out[key].append(None)
    out["f"][i], out["t"][i] = node["split_feature"], node["threshold"]
    out["l"][i] = export_tree(node["left_child"], out)
    out["r"][i] = export_tree(node["right_child"], out)
    return i


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--train_days", type=int, default=120)
    ap.add_argument("--horizon", type=int, default=6)
    ap.add_argument("--k", type=float, default=0.6)
    a = ap.parse_args()

    ohlc = load_ohlc("15min")
    opt, _ = load_options(a.src, "15min")
    X = nf.make_features(ohlc, opt, BARS_PER_DAY).dropna()
    y, _ = labels(ohlc, a.horizon, a.k)
    y = y.reindex(X.index)
    Xt, yt = X[y.notna()], y[y.notna()].astype(int)
    Xt, yt = Xt.iloc[-a.train_days * BARS_PER_DAY:], yt.iloc[-a.train_days * BARS_PER_DAY:]

    clf = lgb.LGBMClassifier(
        n_estimators=200, learning_rate=0.03, num_leaves=15,
        min_child_samples=100, subsample=0.8, subsample_freq=1,
        colsample_bytree=0.8, reg_lambda=5, verbose=-1, random_state=0)
    clf.fit(Xt, yt)

    trees = []
    for info in clf.booster_.dump_model()["tree_info"]:
        tree = {"f": [], "t": [], "l": [], "r": [], "v": []}
        export_tree(info["tree_structure"], tree)
        trees.append(tree)
    model = {"features": list(X.columns), "classes": 3, "class_names": ["bearish", "sideways", "bullish"],
             "horizon": a.horizon, "k": a.k, "bars_per_day": BARS_PER_DAY,
             "train_from": str(Xt.index[0]), "train_to": str(Xt.index[-1]), "train_rows": len(Xt), "trees": trees}

    check = X.iloc[-2000:]
    want = clf.predict_proba(check)
    got = np.array([nf.predict_proba(model, row) for _, row in check.iterrows()])
    err = np.abs(want - got).max()
    print(f"trained on {len(Xt)} bars, {Xt.index.normalize().nunique()} days: {Xt.index[0]} -> {Xt.index[-1]}")
    print("label mix:", yt.value_counts(normalize=True).round(3).sort_index().to_dict())
    print(f"export check on {len(check)} rows: max probability difference {err:.2e}")
    assert err < 1e-9, "exported model does not match lightgbm"
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(model, fh, separators=(",", ":"))
    print(f"wrote {os.path.normpath(OUT)} ({os.path.getsize(OUT) / 1024:.0f} KB, {len(trees)} trees, {len(model['features'])} features)")
