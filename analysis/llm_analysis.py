"""Gemini plumbing shared by the weekly analysis sections.

Holds the transport (key lookup, POST, reply sanitising) plus the combined
cross-section analysis that reads relative strength, the index table and the
breadth table together.

Every entry point returns None on any failure. The LLM is purely additive: the
weekly cron job must never break because a free-tier API call did.
"""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0103

import json
import os
import re

import requests

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GEMINI_MODEL = "gemini-flash-latest"
GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/"
              "models/{}:generateContent")
LLM_MAX_CHARS = 1500   # keeps each report well under Telegram's 4096 cap


def gemini_key():
    """API key from env, else the gitignored credentials file."""
    key = os.environ.get('GEMINI_API_KEY')
    if key:
        return key.strip()
    path = os.path.join(REPO_ROOT, 'auto_straddle', 'gemini_credentials.json')
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)['api_key'].strip()
    except Exception as e:
        print(f"  no Gemini key ({e})")
        return None


def _drop_partial_bullet(text):
    """Drop a trailing half-written bullet from a truncated reply.

    Whole bullets are still worth sending; a sentence that stops mid-word is
    not. If nothing complete survives, the caller gets '' and falls back to the
    rule-based notes.
    """
    lines = text.splitlines()
    while lines and not lines[-1].rstrip().endswith(('.', '!', '?')):
        lines.pop()
    return "\n".join(lines).strip()


def gemini_generate(prompt, max_chars=LLM_MAX_CHARS, max_tokens=2000, temperature=0.4):
    """Run one Gemini prompt, returning Telegram-safe text or None."""
    key = gemini_key()
    if not key:
        return None
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature,
                             "maxOutputTokens": max_tokens},
    }
    try:
        r = requests.post(GEMINI_URL.format(GEMINI_MODEL),
                          headers={'x-goog-api-key': key,
                                   'Content-Type': 'application/json'},
                          json=payload, timeout=60)
        if r.status_code != 200:
            print(f"  Gemini HTTP {r.status_code}: {r.text[:200]}")
            return None
        cand = r.json()['candidates'][0]
        parts = cand['content']['parts']
        text = "".join(p.get('text', '') for p in parts).strip()
        # This model thinks before answering and the thinking counts against
        # maxOutputTokens, so an under-budgeted call returns a reply cut off
        # mid-sentence. Say so in the cron log rather than shipping the stub.
        if cand.get('finishReason') == 'MAX_TOKENS':
            print(f"  Gemini hit maxOutputTokens ({max_tokens}) — reply truncated")
            text = _drop_partial_bullet(text)
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
    return text[:max_chars].rstrip()


# --- Combined cross-section analysis ---------------------------------------
# Facts are written out with explicit labels and units rather than reusing the
# ASCII tables: the tables are laid out for a phone screen and an LLM reads
# their abbreviated headers wrong.

TOP_N = 8   # RS leaders/laggards quoted per period


def _rs_facts(rs_df):
    """Relative-strength leaders and laggards vs Nifty 50."""
    if rs_df is None or len(rs_df) == 0:
        return None
    blocks = []
    for col, name in [('RS_1W', 'one week'), ('RS_1M', 'one month'),
                      ('RS_6M', 'six months')]:
        if col not in rs_df.columns:
            continue
        valid = rs_df[rs_df[col].notna()]
        if valid.empty:
            continue
        top = valid.nlargest(TOP_N, col)
        # Exclude the leaders from the laggards: with a short index list the two
        # halves would otherwise overlap and quote the same index twice, which
        # reads to the LLM as contradictory.
        bot = valid.drop(top.index).nsmallest(TOP_N, col)
        blocks.append(
            f"Over {name}, leaders: " +
            ", ".join(f"{r['Index']} {r[col]:+.1f}%" for _, r in top.iterrows()) +
            "\n  laggards: " +
            ", ".join(f"{r['Index']} {r[col]:+.1f}%" for _, r in bot.iterrows())
        )
    if not blocks:
        return None
    return ("RELATIVE STRENGTH (index return minus Nifty 50 return over the same "
            f"period, in percentage points; {len(rs_df)} indices ranked)\n" +
            "\n".join(blocks))


def _index_facts(index_rows):
    """Trend/position metrics for the tracked majors and commodities."""
    if not index_rows:
        return None
    lines = []
    for label, m, approx in index_rows:
        if m is None:
            continue
        note = "  (approximate, COMEX fallback)" if approx else ""
        lines.append(
            f"{label}: last week {m['weekly_pct']:+.1f}%, "
            f"{m['vs50']:+.1f}% vs its 50 EMA, {m['vs200']:+.1f}% vs its 200 EMA, "
            f"{m['from_low']:+.1f}% from the last swing low, "
            f"{m['from_up']:+.1f}% from the last swing high{note}"
        )
    if not lines:
        return None
    return ("MAJOR INDICES AND COMMODITIES (daily bars; 'vs EMA' is how far the "
            "current price sits above/below that EMA; swing high/low are the most "
            "recent Williams fractals)\n" + "\n".join(lines))


def _breadth_facts(breadth_data):
    """Participation: how many constituents are above their own DMAs."""
    if not breadth_data:
        return None
    lines = []
    for lbl, d in breadth_data.items():
        rows = d['rows']
        r, prev = rows[-1], rows[-2] if len(rows) > 1 else rows[-1]
        ad = f"{r['ad_ratio']:.2f}" if r['ad_ratio'] == r['ad_ratio'] else "n/a"
        hist = ", ".join(f"{x['week_end']:%d-%b} {x['pct50']:.0f}%" for x in rows)
        lines.append(
            f"{lbl} ({d['count']} stocks): {r['pct50']:.0f}% above 50 DMA "
            f"(previous week {prev['pct50']:.0f}%, one-week change "
            f"{r['pct50'] - prev['pct50']:+.0f} points), "
            f"{r['pct200']:.0f}% above 200 DMA, last week {r['adv']} advances / "
            f"{r['dec']} declines, A/D ratio {ad}\n"
            f"  above-50-DMA over the last {len(rows)} weeks (oldest first): {hist}"
        )
    if not lines:
        return None
    return ("MARKET BREADTH (percentage of each index's constituent stocks trading "
            "above their own 50/200 day moving average; A/D ratio is advances "
            "divided by declines over the LAST WEEK only)\n" + "\n".join(lines))


COMBINED_PROMPT = (
    "You are reading three weekly datasets for the Indian equity market, week "
    "ending {week}. They measure different things: relative strength is sector "
    "and theme rotation, the index table is price trend and position, breadth is "
    "how broad the participation is.\n\n{facts}\n\n"
    "Write a combined reading in exactly 5 bullet points, each starting with "
    "'- '. Requirements:\n"
    "1. At least two bullets must connect findings ACROSS datasets (for example: "
    "an index near its highs while its breadth is falling, or a sector leading on "
    "relative strength while the broad market weakens).\n"
    "2. Cite specific numbers and always state which period they cover "
    "(one week, one month, six months, six-week trend).\n"
    "3. Do not describe a one-week move as if it happened over six weeks, or "
    "vice versa. Do not call an A/D ratio negative when it is merely below 1.0. "
    "Relative strength is measured against Nifty 50, so a positive figure means "
    "outperformance, not an absolute gain.\n"
    "4. End with one bullet on what to watch next week.\n"
    "No preamble, no headings, no markdown formatting, under 35 words each."
)


def combined_analysis(rs_df=None, index_rows=None, breadth_data=None, week=None):
    """LLM reading across all three sections, or None if it can't be produced.

    Needs at least two of the three datasets — with only one there is nothing
    to cross-reference and the per-section commentary already covers it.
    """
    facts = [f for f in (_rs_facts(rs_df), _index_facts(index_rows),
                         _breadth_facts(breadth_data)) if f]
    if len(facts) < 2:
        print(f"  combined analysis skipped: only {len(facts)} of 3 sections available")
        return None
    prompt = COMBINED_PROMPT.format(week=week or "this week",
                                    facts="\n\n".join(facts))
    # Budget is generous because it also has to cover this model's thinking
    # tokens, which on a prompt this dense ran to several thousand and
    # otherwise truncated the reply after three of the five bullets.
    return gemini_generate(prompt, max_chars=2500, max_tokens=12000)
