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
# Generous because generation time scales with the token budget and this model
# thinks before answering: the combined call budgets 12000 tokens and has taken
# over a minute, which a 60s timeout turned into a silently missing section.
# Nothing here is latency-sensitive — it is a weekly cron job.
GEMINI_TIMEOUT = 300


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
                          json=payload, timeout=GEMINI_TIMEOUT)
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
    """Trend/position metrics, split into Indian equity and the global backdrop.

    Presented as two labelled blocks rather than one list: under a single
    "major indices and commodities" heading the model read the whole table as
    Indian equity and left the US, Hong Kong, crude, gold and the rupee out of
    its reading entirely. Imported lazily so this module keeps working if
    index_report's TradingView dependency is unavailable.
    """
    if not index_rows:
        return None
    try:
        from index_report import GLOBAL_MACRO
    except Exception:
        GLOBAL_MACRO = set()

    india, world = [], []
    for label, m, approx in index_rows:
        if m is None:
            continue
        note = "  (approximate, COMEX fallback)" if approx else ""
        line = (
            f"{label}: last week {m['weekly_pct']:+.1f}%, "
            f"{m['vs50']:+.1f}% vs its 50 EMA, {m['vs200']:+.1f}% vs its 200 EMA, "
            f"{m['from_low']:+.1f}% from the last swing low, "
            f"{m['from_up']:+.1f}% from the last swing high{note}"
        )
        (world if label in GLOBAL_MACRO else india).append(line)
    if not india and not world:
        return None

    common = ("daily bars; 'vs EMA' is how far the current price sits "
              "above/below that EMA; swing high/low are the most recent "
              "Williams fractals")
    blocks = []
    if india:
        blocks.append(f"INDIAN INDICES ({common})\n" + "\n".join(india))
    if world:
        blocks.append(
            "GLOBAL AND MACRO BACKDROP — same metrics, but these are NOT Indian "
            "equity indices and must be read as the external conditions Indian "
            "equities trade against: US equities (Nasdaq, S&P 500), China/Hong "
            "Kong (Hang Seng), commodities quoted in rupees on MCX (gold, "
            "silver, crude oil), and the rupee itself (USDINR — a RISE means a "
            f"WEAKER rupee, which is a headwind for Indian equities) ({common})\n"
            + "\n".join(world))
    return "\n\n".join(blocks)


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
    "You are a buy-side strategist writing the Monday morning note for an Indian "
    "equity desk, week ending {week}. Three datasets follow. They measure "
    "different things: relative strength is sector and theme rotation within "
    "India, the index table is price trend and position for Indian indices plus "
    "the global and macro block, breadth is how broad participation is inside the "
    "Indian indices.\n\n"
    "AVAILABLE THIS WEEK: {available}. Write only about data shown below. If a "
    "dataset is absent, never speculate about what it would have said.\n\n"
    "{facts}\n\n"
    "The reader has ALREADY SEEN all of these tables. A bullet that restates one "
    "row is worthless to them. Every bullet must add something not visible from "
    "reading a single row: a relationship between two datasets, a divergence, a "
    "change of pace, or a consequence.\n\n"
    "Write exactly 6 bullets, each starting with '- ', in this fixed order:\n"
    "1. STANCE: the single most important thing about this week, combining price "
    "trend with participation. Commit to a view — constructive, deteriorating or "
    "mixed — and justify it with numbers.\n"
    "2. CONFIRMATION: the strongest point where two datasets agree, naming both.\n"
    "3. DIVERGENCE: the strongest point where two DIFFERENT datasets disagree — "
    "an index rising while its breadth falls, a relative-strength leader inside "
    "a weakening segment, a benchmark near its highs on narrowing participation. "
    "Two timeframes within a single dataset are NOT a cross-dataset divergence "
    "and do not belong here. If there is genuinely no material divergence, say "
    "so and give the number that rules it out.\n"
    "4. ROTATION: which sectors or themes money moved into and out of, from "
    "relative strength, and whether the one-week and six-month pictures agree.\n"
    "5. GLOBAL AND MACRO: US equities, Hang Seng, crude, gold and USDINR, and "
    "what they imply for Indian equities. Never omit this bullet and never treat "
    "those names as Indian indices.\n"
    "6. WATCH: one falsifiable trigger for next week — a specific level or "
    "threshold, and what crossing it would signal. Not 'watch whether X breaks'.\n"
    "Do not use the same index, sector or theme as the subject of more than one "
    "bullet — six bullets should cover six different things.\n"
    "If a bullet needs a dataset that is absent, replace it with the next most "
    "useful observation and state which data was missing.\n\n"
    "Accuracy rules:\n"
    "- Always state the period a number covers (one week, one month, six months, "
    "the six-week trend). Never describe a one-week move as if it spanned six.\n"
    "- An A/D ratio below 1.0 is weak, not negative.\n"
    "- Relative strength is measured against Nifty 50: a positive figure is "
    "outperformance, not an absolute gain.\n"
    "- A rise in USDINR is rupee weakness; a fall is rupee strength.\n"
    "- Use only numbers that appear above. Never compute or estimate new ones.\n\n"
    "WEAK bullet, do not imitate — it just rereads one row:\n"
    "'Nifty 50 rose 2.6% last week and sits 0.6% below its swing high.'\n"
    "STRONG bullet — same row, but earns its place:\n"
    "'Nifty 50's 2.6% week came with 50 DMA breadth up 16 points to 61%, so this "
    "leg is broad participation rather than a few heavyweights carrying it.'\n\n"
    "No preamble, no headings, no markdown. One sentence per bullet, 25-45 words."
)


def combined_analysis(rs_df=None, index_rows=None, breadth_data=None, week=None):
    """LLM reading across all three sections, or None if it can't be produced.

    Needs at least two of the three datasets — with only one there is nothing
    to cross-reference and the per-section commentary already covers it.
    """
    sections = [("relative strength", _rs_facts(rs_df)),
                ("the index table", _index_facts(index_rows)),
                ("market breadth", _breadth_facts(breadth_data))]
    present = [(name, f) for name, f in sections if f]
    if len(present) < 2:
        print(f"  combined analysis skipped: only {len(present)} of 3 sections available")
        return None
    # Naming the available sections stops the model inventing sector rotation
    # out of thin air on a week when relative strength failed to fetch.
    prompt = COMBINED_PROMPT.format(week=week or "this week",
                                    available=", ".join(n for n, _ in present),
                                    facts="\n\n".join(f for _, f in present))
    # Budget is generous because it also has to cover this model's thinking
    # tokens, which on a prompt this dense ran to several thousand and
    # otherwise truncated the reply after three of the five bullets.
    return gemini_generate(prompt, max_chars=2500, max_tokens=12000)
