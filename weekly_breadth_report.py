"""
Weekly Market Breadth Report -> Telegram

For each tracked index it reports, per week for the last 6 weeks:
  - % of constituents above their 50 DMA
  - % of constituents above their 200 DMA
  - Advance/decline count and ratio (week close over prior week close)

Constituent lists come from niftyindices.com (cached to ind_*list.csv in this
directory, which is also the fallback if the download fails). Daily closes come
from yfinance. Output is a PNG dashboard plus a compact text table.

Commentary is rule-based (always) plus a free-tier Gemini reading of the same
table (skipped silently if the key is missing or the call fails).

Run:  python weekly_breadth_report.py            (print + write PNG only)
      python weekly_breadth_report.py --send      (also send to Telegram)
      python weekly_breadth_report.py --no-llm    (skip the Gemini call)
"""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0103

import json
import os
import re
import sys
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import requests
import yfinance as yf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'auto_straddle'))
from TelegramSend import telegram_send_api

# --- Telegram destination (hardcoded) --------------------------------------
CHAT_ID = "-891000076"  # "Daily Nifty 200 update" group (same as weekly_index_report)

# --- Parameters ------------------------------------------------------------
WEEKS = 6             # weeks of history shown
BATCH = 100           # yfinance tickers per download call
MIN_BARS = 210        # need >200 closes for a valid 200 DMA
HISTORY = '2y'        # yfinance download period
PNG_PATH = '/tmp/weekly_breadth.png'

CONSTITUENT_URL = "https://niftyindices.com/IndexConstituent/{}.csv"
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36'}

# label, niftyindices constituent-file slug, plot colour
INDEXES = [
    ("NIFTY 50",     "ind_nifty50list",          "#1f77b4"),
    ("NIFTY 200",    "ind_nifty200list",         "#ff7f0e"),
    ("NIFTY 500",    "ind_nifty500list",         "#2ca02c"),
    ("MIDCAP 150",   "ind_niftymidcap150list",   "#d62728"),
    ("SMALLCAP 250", "ind_niftysmallcap250list", "#9467bd"),
]


def load_constituents(slug):
    """Download the constituent list, caching to ind_*list.csv; fall back to cache."""
    path = os.path.join(os.path.dirname(__file__), f'{slug}.csv')
    try:
        r = requests.get(CONSTITUENT_URL.format(slug), headers=UA, timeout=30)
        if r.status_code == 200 and 'Symbol' in r.text[:200]:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(r.text)
        else:
            print(f"  {slug}: download HTTP {r.status_code}, using cache")
    except Exception as e:
        print(f"  {slug}: download error ({e}), using cache")
    try:
        df = pd.read_csv(path)
        return sorted({s.strip() + '.NS' for s in df['Symbol']})
    except Exception as e:
        print(f"  {slug}: no usable list ({e})")
        return []


def fetch_closes(tickers):
    """Daily adjusted closes for all tickers, downloaded in batches."""
    frames = []
    for i in range(0, len(tickers), BATCH):
        batch = tickers[i:i + BATCH]
        try:
            d = yf.download(batch, period=HISTORY, interval='1d', auto_adjust=True,
                            progress=False, threads=True)
            frames.append(d['Close'])
            print(f"  batch {i // BATCH + 1}: {d['Close'].shape}")
        except Exception as e:
            print(f"  batch {i // BATCH + 1} failed: {e}")
    if not frames:
        return None
    close = pd.concat(frames, axis=1).sort_index()
    close = close.loc[:, ~close.columns.duplicated()]
    close = close.dropna(axis=1, thresh=MIN_BARS)
    # One missing bar makes rolling(200) NaN for the next 200 days, which would
    # silently drop that stock from the >200 DMA count while it still counts
    # toward >50 DMA — different denominators in the same row. Bridge the gaps.
    return close.ffill()


def compute_breadth(close, members):
    """Return {label: [{week_end, pct50, pct200, adv, dec, ad_ratio}, ...]}."""
    dma50 = close.rolling(50).mean()
    dma200 = close.rolling(200).mean()
    ok50 = close.notna() & dma50.notna()
    ok200 = close.notna() & dma200.notna()
    above50 = (close > dma50) & ok50
    above200 = (close > dma200) & ok200

    out = {}
    for label, syms in members.items():
        cols = [s for s in syms if s in close.columns]
        if not cols:
            continue
        pct50 = above50[cols].sum(1) / ok50[cols].sum(1) * 100
        pct200 = above200[cols].sum(1) / ok200[cols].sum(1) * 100

        chg = close[cols].resample('W-FRI').last().diff()
        adv = (chg > 0).sum(1)
        dec = (chg < 0).sum(1)
        w50 = pct50.resample('W-FRI').last()
        w200 = pct200.resample('W-FRI').last()
        # map each W-FRI bucket to the actual last trading day in it
        wdate = pd.Series(close.index, index=close.index).resample('W-FRI').last()

        rows = []
        for d in w50.index[-WEEKS:]:
            a, dd = int(adv.get(d, 0)), int(dec.get(d, 0))
            rows.append({
                'week_end': pd.Timestamp(wdate[d]).date(),
                'pct50': float(w50[d]),
                'pct200': float(w200[d]),
                'adv': a,
                'dec': dd,
                'ad_ratio': (a / dd) if dd else float('nan'),
            })
        out[label] = {'count': len(cols), 'rows': rows}
    return out


def render_dashboard(data, path=PNG_PATH):
    """3-panel PNG: %>50 DMA trend, %>200 DMA trend, latest-week A/D ratio."""
    fig, axes = plt.subplots(3, 1, figsize=(9, 11),
                             gridspec_kw={'height_ratios': [3, 3, 2]})
    fig.patch.set_facecolor('white')
    labels = [lbl for lbl, _, _ in INDEXES if lbl in data]
    colors = {lbl: c for lbl, _, c in INDEXES}
    xs = [r['week_end'].strftime('%d-%b') for r in data[labels[0]]['rows']]

    for ax, key, title in [(axes[0], 'pct50', '% of constituents above 50 DMA'),
                           (axes[1], 'pct200', '% of constituents above 200 DMA')]:
        for lbl in labels:
            ax.plot(xs, [r[key] for r in data[lbl]['rows']], marker='o',
                    color=colors[lbl], label=lbl, linewidth=2, markersize=5)
        ax.axhline(50, color='#888888', linestyle='--', linewidth=1)
        ax.set_title(title, fontsize=12, weight='bold')
        ax.set_ylim(0, 100)
        ax.grid(alpha=0.25)
        ax.set_ylabel('%')
    axes[0].legend(fontsize=8, ncol=5, loc='upper center', bbox_to_anchor=(0.5, -0.08))

    ax = axes[2]
    latest = [data[lbl]['rows'][-1] for lbl in labels]
    ratios = [r['ad_ratio'] for r in latest]
    bars = ax.barh(labels, ratios,
                   color=['#2ca02c' if r >= 1 else '#d62728' for r in ratios])
    ax.axvline(1, color='#888888', linestyle='--', linewidth=1)
    ax.set_title(f"Advance/decline ratio — week ending {latest[0]['week_end']:%d-%b-%Y}",
                 fontsize=12, weight='bold')
    ax.set_xlabel('advances / declines')
    ax.grid(alpha=0.25, axis='x')
    ax.invert_yaxis()
    for b, r, row in zip(bars, ratios, latest):
        ax.text(b.get_width(), b.get_y() + b.get_height() / 2,
                f"  {r:.2f}  ({row['adv']}/{row['dec']})", va='center', fontsize=9)
    ax.set_xlim(0, max(max(ratios), 1.2) * 1.35)

    fig.suptitle(f"Market Breadth — {datetime.now().strftime('%d-%b-%Y')}",
                 fontsize=15, weight='bold')
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(path, dpi=110, facecolor='white')
    plt.close(fig)
    return path


def _trend(rows, key):
    """Week-on-week change in a metric, as a signed string."""
    d = rows[-1][key] - rows[-2][key]
    return f"{d:+.0f}"


BROAD = "NIFTY 500"   # proxy for "the market" in the regime/streak notes
SWING = 5             # pts of w-o-w >50 DMA change that counts as a broad move
DIVERGE = 10          # pts of smallcap-vs-largecap >50 DMA gap worth flagging


def _regime(pct200):
    if pct200 >= 65:
        return "strong"
    if pct200 >= 55:
        return "constructive"
    if pct200 >= 45:
        return "neutral"
    if pct200 >= 35:
        return "weak"
    return "washed out"


def build_commentary(data):
    """Plain-language reading of the breadth table, from thresholds and deltas."""
    notes = []
    labels = [lbl for lbl, _, _ in INDEXES if lbl in data]
    latest = {lbl: data[lbl]['rows'][-1] for lbl in labels}
    delta = {lbl: latest[lbl]['pct50'] - data[lbl]['rows'][-2]['pct50'] for lbl in labels}

    # 1. Did the whole market move together this week?
    lo, hi = min(delta.values()), max(delta.values())
    if hi <= -SWING:
        notes.append(f"⚠ Breadth broke down: all {len(labels)} indices lost "
                     f"{abs(hi):.0f}-{abs(lo):.0f} pts of >50 DMA this week.")
    elif lo >= SWING:
        notes.append(f"✅ Breadth thrust: all {len(labels)} indices gained "
                     f"{lo:.0f}-{hi:.0f} pts of >50 DMA this week.")

    # 2. Short-term breadth below long-term breadth = downtrend forming.
    crossed = [l for l in labels if latest[l]['pct50'] < latest[l]['pct200']]
    if crossed:
        notes.append(f"⚠ {', '.join(crossed)}: >50 DMA now below >200 DMA "
                     f"(short-term weaker than long-term).")

    # 3. Weakest / strongest participation this week.
    rated = [l for l in labels if latest[l]['ad_ratio'] == latest[l]['ad_ratio']]
    if rated:
        w = min(rated, key=lambda l: latest[l]['ad_ratio'])
        s = max(rated, key=lambda l: latest[l]['ad_ratio'])
        if latest[w]['ad_ratio'] < 1:
            notes.append(f"⚠ {w} weakest, A/D {latest[w]['ad_ratio']:.2f} "
                         f"({latest[w]['adv']}/{latest[w]['dec']}).")
        if latest[s]['ad_ratio'] >= 2:
            notes.append(f"✅ {s} strongest, A/D {latest[s]['ad_ratio']:.2f} "
                         f"({latest[s]['adv']}/{latest[s]['dec']}).")

    # 4. Are small caps leading or lagging the large caps?
    if "SMALLCAP 250" in latest and "NIFTY 50" in latest:
        gap = latest["SMALLCAP 250"]['pct50'] - latest["NIFTY 50"]['pct50']
        if abs(gap) >= DIVERGE:
            side = "leading" if gap > 0 else "lagging"
            notes.append(f"• Small caps {side} large caps by {abs(gap):.0f} pts "
                         f"of >50 DMA.")

    # 5. How persistent is the weakness / strength?
    if BROAD in data:
        rows = data[BROAD]['rows']   # every week diffs against the prior week
        under = sum(1 for r in rows if r['ad_ratio'] < 1)
        if under >= 3:
            notes.append(f"• A/D below 1.0 in {under} of last {len(rows)} weeks.")
        elif len(rows) - under >= 3:
            notes.append(f"• A/D above 1.0 in {len(rows) - under} of last "
                         f"{len(rows)} weeks.")
        p200 = data[BROAD]['rows'][-1]['pct200']
        notes.append(f"• {BROAD}: {p200:.0f}% above 200 DMA — {_regime(p200)}.")

    return notes


# --- Optional LLM commentary ------------------------------------------------
# Free-tier Gemini. Purely additive: any failure just leaves the rule-based
# notes above in place, so the cron job never breaks on an LLM problem.
GEMINI_MODEL = "gemini-flash-latest"
LLM_MAX_CHARS = 1500   # keeps the whole report well under Telegram's 4096 cap
GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/"
              "models/{}:generateContent")
GEMINI_PROMPT = (
    "Market breadth for Indian equity indices, week ending {week}.\n\n{facts}\n\n"
    "Definitions: 'above 50 DMA' is the percentage of that index's constituent "
    "stocks trading above their own 50-day moving average; same for 200 DMA. "
    "A/D ratio is advances divided by declines over the LAST WEEK only. The "
    "one-week change is the move from the previous week to this week. The "
    "6-week history is the longer trend — do not describe a one-week move as "
    "having happened over six weeks, or vice versa.\n\n"
    "Write exactly 3 bullet points on what this means for market direction. "
    "Start each with '- '. Cite specific numbers and state the correct time "
    "period for each. Do not call a ratio negative when it is merely below 1.0. "
    "No preamble, no headings, no markdown formatting, under 30 words each."
)


def _llm_facts(data):
    """Explicitly labelled breadth figures — safer for an LLM than the ASCII table."""
    blocks = []
    for lbl, _, _ in INDEXES:
        if lbl not in data:
            continue
        rows = data[lbl]['rows']
        r, prev = rows[-1], rows[-2]
        hist = ", ".join(f"{x['week_end']:%d-%b} {x['pct50']:.0f}%" for x in rows)
        ad = f"{r['ad_ratio']:.2f}" if r['ad_ratio'] == r['ad_ratio'] else "n/a"
        blocks.append(
            f"{lbl} ({data[lbl]['count']} stocks)\n"
            f"  above 50 DMA now: {r['pct50']:.0f}% "
            f"(previous week {prev['pct50']:.0f}%, one-week change "
            f"{r['pct50'] - prev['pct50']:+.0f} points)\n"
            f"  above 200 DMA now: {r['pct200']:.0f}%\n"
            f"  last week: {r['adv']} advances, {r['dec']} declines, A/D ratio {ad}\n"
            f"  above-50-DMA over the last 6 weeks (oldest first): {hist}"
        )
    return "\n\n".join(blocks)


def _gemini_key():
    """API key from env, else the gitignored credentials file."""
    key = os.environ.get('GEMINI_API_KEY')
    if key:
        return key.strip()
    path = os.path.join(os.path.dirname(__file__), 'auto_straddle',
                        'gemini_credentials.json')
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)['api_key'].strip()
    except Exception as e:
        print(f"  no Gemini key ({e})")
        return None


def gemini_commentary(data):
    """LLM reading of the breadth figures, or None if anything goes wrong."""
    key = _gemini_key()
    if not key:
        return None
    week = data[list(data)[0]]['rows'][-1]['week_end'].strftime('%d-%b-%Y')
    prompt = GEMINI_PROMPT.format(week=week, facts=_llm_facts(data))
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2000},
    }
    try:
        r = requests.post(GEMINI_URL.format(GEMINI_MODEL),
                          headers={'x-goog-api-key': key,
                                   'Content-Type': 'application/json'},
                          json=payload, timeout=60)
        if r.status_code != 200:
            print(f"  Gemini HTTP {r.status_code}: {r.text[:200]}")
            return None
        parts = r.json()['candidates'][0]['content']['parts']
        text = "".join(p.get('text', '') for p in parts).strip()
    except Exception as e:
        print(f"  Gemini error: {e}")
        return None
    if not text:
        return None
    # Telegram parse_mode='markdown' rejects unbalanced * _ ` [ ] — strip them
    # so a stray character in the model's reply can't fail the whole send.
    text = re.sub(r'[*_`\[\]]', '', text)
    text = "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())
    # Telegram sendMessage caps at 4096 chars and send_message swallows the
    # resulting 400, so cap the one variable-length part of the report.
    return text[:LLM_MAX_CHARS].rstrip()


def build_report(data):
    """Telegram markdown: latest-week snapshot plus 6-week %>50 DMA trend."""
    title = f"*Market Breadth — {datetime.now().strftime('%d-%b-%Y')}*\n"
    lines = [f"{'Index':<13}{'>50':>5}{'>200':>6}{'A/D':>6}{'d50':>5}\n",
             "-" * 35 + "\n"]
    for lbl, _, _ in INDEXES:
        if lbl not in data:
            lines.append(f"{lbl:<13}  fetch failed\n")
            continue
        rows = data[lbl]['rows']
        r = rows[-1]
        ad = f"{r['ad_ratio']:.2f}" if r['ad_ratio'] == r['ad_ratio'] else "-"
        lines.append(f"{lbl:<13}{r['pct50']:>5.0f}{r['pct200']:>6.0f}"
                     f"{ad:>6}{_trend(rows, 'pct50'):>5}\n")

    weeks = [r['week_end'].strftime('%d-%b') for r in data[list(data)[0]]['rows']]
    trend = [f"\n{'>50 DMA %':<13}" + "".join(f"{w[:2]:>5}" for w in weeks) + "\n",
             "-" * (13 + 5 * len(weeks)) + "\n"]
    for lbl, _, _ in INDEXES:
        if lbl in data:
            trend.append(f"{lbl:<13}" +
                         "".join(f"{r['pct50']:>5.0f}" for r in data[lbl]['rows']) + "\n")

    table = "".join(lines + trend)
    notes = build_commentary(data)
    body = "\n" + "\n".join(notes) + "\n" if notes else ""

    if '--no-llm' not in sys.argv:
        llm = gemini_commentary(data)
        if llm:
            body += "\n" + llm + "\n"

    legend = ("\n_>50/>200 = % of constituents above 50/200 DMA · "
              "A/D = advances/declines this week · d50 = w-o-w change in >50_")
    return title + "```\n" + table + "```" + body + legend


def main():
    members = {}
    for label, slug, _ in INDEXES:
        print(f"Loading {label} constituents ...")
        syms = load_constituents(slug)
        if syms:
            members[label] = syms

    if not members:
        print("No constituent lists available — aborting.")
        return

    universe = sorted(set().union(*members.values()))
    print(f"Fetching closes for {len(universe)} tickers ...")
    close = fetch_closes(universe)
    if close is None or close.empty:
        print("Price download failed — aborting.")
        return
    print(f"usable: {close.shape[1]} tickers, {close.shape[0]} days, "
          f"last {close.index[-1].date()}")

    data = compute_breadth(close, members)
    if not data:
        print("No breadth data computed — aborting.")
        return

    report = build_report(data)
    print("\n" + report)
    png = render_dashboard(data)
    print(f"\nDashboard written to {png}")

    if '--send' in sys.argv:
        tg = telegram_send_api()
        tg.send_photo(CHAT_ID, png)
        tg.send_message(CHAT_ID, report)
        print(f"\nSent to Telegram ({CHAT_ID})")
    else:
        print("\n--- Add --send flag to send to Telegram ---")


if __name__ == '__main__':
    main()
