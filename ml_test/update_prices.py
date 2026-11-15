"""Bring NIFTY 50_15minute.csv and NIFTY 50_5minute.csv up to the last completed trading day.

Usage: python update_prices.py
Candles come from Upstox's public historical-candle endpoint (no login). The last day
already in each file is fetched again in full, because it may have been saved part-way
through the session. Today's candles are only taken after 15:45.
"""
import datetime as dt
import os
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "https://api.upstox.com/v3/historical-candle/NSE_INDEX%7CNifty%2050/minutes/{minutes}/{to}/{frm}"


def fetch(minutes, start, end):
    """Candles from start to end (dates, inclusive); the endpoint serves at most a month per request."""
    rows = []
    while start <= end:
        stop = min(start + dt.timedelta(days=27), end)
        r = requests.get(URL.format(minutes=minutes, to=stop, frm=start), headers={"Accept": "application/json"}, timeout=30)
        r.raise_for_status()
        rows += r.json()["data"]["candles"]
        start = stop + dt.timedelta(days=1)
    d = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "oi"])
    d["date"] = pd.to_datetime(d["date"]).dt.tz_localize(None)
    d["volume"] = 0  # the file carries 0 volume throughout
    return d.drop(columns="oi").drop_duplicates("date").sort_values("date")


def update(minutes):
    path = os.path.join(HERE, f"NIFTY 50_{minutes}minute.csv")
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines(keepends=True)
    last_day = dt.date.fromisoformat(lines[-1][:10])
    now = dt.datetime.now()
    end = now.date() if now.time() >= dt.time(15, 45) else now.date() - dt.timedelta(days=1)
    if last_day > end:
        print(f"{os.path.basename(path)}: already up to {lines[-1][:19]}")
        return
    new = fetch(minutes, last_day, end)
    if new.empty or new["date"].iloc[0].date() != last_day:
        raise SystemExit(f"{os.path.basename(path)}: Upstox returned no candles for {last_day}; file left unchanged")
    keep = [lines[0]] + [l for l in lines[1:] if l[:10] < str(last_day)]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.writelines(keep)
    new.to_csv(tmp, mode="a", header=False, index=False, date_format="%Y-%m-%d %H:%M:%S")
    os.replace(tmp, path)
    added = new[new["date"].dt.date > last_day]
    print(f"{os.path.basename(path)}: {added['date'].dt.date.nunique()} new days, {len(added)} new bars, "
          f"now ends {new['date'].iloc[-1]}")


if __name__ == "__main__":
    for m in (15, 5):
        update(m)
