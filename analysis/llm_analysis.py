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
import time

import requests

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A floating alias: Google repoints it, and thinking behaviour differs across
# generations, so the same prompt can start truncating with no code change.
# Pin an explicit model id here if a week's output regresses for no reason.
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


def _thinking_configs(model, budget):
    """Thinking-cap candidates for this prompt, most likely dialect first.

    Thinking tokens come out of maxOutputTokens, so an uncapped budget on a
    dense prompt is what truncates the reply. Which dialect caps it depends on
    the model generation: Gemini 3 takes a coarse level, 2.5 takes a token count
    (0-24576). GEMINI_MODEL is a floating alias, so its name does not say which
    generation actually answers — gemini-flash-latest resolves to a Gemini 3
    model whose name contains no '3' at all, and sending it the 2.5 dialect is
    accepted with HTTP 200 and then ignored, so the cap silently does nothing.
    Hence: try a dialect, fall back on the 400. Last entry is no cap at all —
    losing the cap beats losing the section.
    """
    if budget is None:
        return [None]
    level = {"thinkingLevel": "low" if budget <= 4096 else "high"}
    count = {"thinkingBudget": max(0, min(int(budget), 24576))}
    older = any(g in model for g in ('2.0', '2.5', '1.5'))
    return [count, level, None] if older else [level, count, None]


def _post(payload, key, attempts=3):
    """POST once, retrying only transient 5xx. Returns the last response.

    A single 503 from Google used to lose the whole combined analysis for the
    week — the same failure class as the old 60s timeout. This is a weekly cron
    job with a 300s budget per call, so a couple of backed-off retries are free.
    """
    r = None
    for i in range(attempts):
        r = requests.post(GEMINI_URL.format(GEMINI_MODEL),
                          headers={'x-goog-api-key': key,
                                   'Content-Type': 'application/json'},
                          json=payload, timeout=GEMINI_TIMEOUT)
        if r.status_code < 500:
            return r
        print(f"  Gemini HTTP {r.status_code} (attempt {i + 1}/{attempts})")
        if i < attempts - 1:
            time.sleep(5 * (i + 1))
    return r


def gemini_generate(prompt, max_chars=LLM_MAX_CHARS, max_tokens=2000,
                    temperature=0.4, thinking_budget=None, bullets_only=False):
    """Run one Gemini prompt, returning Telegram-safe text or None."""
    key = gemini_key()
    if not key:
        return None
    cfg = {"temperature": temperature, "maxOutputTokens": max_tokens}
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": cfg}
    try:
        for think in _thinking_configs(GEMINI_MODEL, thinking_budget):
            cfg.pop("thinkingConfig", None)
            if think:
                cfg["thinkingConfig"] = think
            r = _post(payload, key)
            if not (r.status_code == 400 and think):
                break
            print(f"  Gemini rejected {next(iter(think))} — retrying")
        if r.status_code == 429:
            print(f"  Gemini rate limited (free tier): {r.text[:200]}")
            return None
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
    # A prompt that asks the model to plan silently is honoured by convention
    # only; nothing stops the plan, a preamble or a heading reaching Telegram.
    # Keep the bullets alone — but only when there are bullets to keep, so a
    # reply in another shape degrades to unfiltered rather than to nothing.
    if bullets_only:
        kept = [ln for ln in text.splitlines() if ln.startswith('- ')]
        if kept:
            text = "\n".join(kept)
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


def _level(v):
    """Absolute price level. Two decimals below 1000 — USDINR near 87.5 rounds
    to a useless '88' at zero decimals, while Nifty at 24,836 needs none."""
    return f"{v:,.2f}" if abs(v) < 1000 else f"{v:,.0f}"


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
        # Absolute levels matter for the WATCH bullet: asked for a falsifiable
        # trigger with only percentages available, the model invents a price.
        # Emitted only when index_report supplies them so this stays optional.
        lvl = ", ".join(
            f"{tag} {_level(m[k])}" for k, tag in
            [('close', 'close'), ('ema50', '50 EMA'), ('ema200', '200 EMA'),
             ('swing_low', 'swing low'), ('swing_high', 'swing high')]
            if m.get(k) is not None and m[k] == m[k]
        )
        line = (
            f"{label}: last week {m['weekly_pct']:+.1f}%, "
            f"{m['vs50']:+.1f}% vs its 50 EMA, {m['vs200']:+.1f}% vs its 200 EMA, "
            f"{m['from_low']:+.1f}% from the last swing low, "
            f"{m['from_up']:+.1f}% from the last swing high{note}"
            + (f"\n  levels: {lvl}" if lvl else "")
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
    "PLAN BEFORE YOU WRITE. Do this silently and output none of it:\n"
    "a. List every observation you can support with numbers copied from above.\n"
    "b. Discard any where you cannot point to the exact figures in the facts.\n"
    "c. Assign the six strongest to the six bullets, one subject each.\n"
    "d. Re-read each number against the facts, then write the bullets alone.\n\n"
    "The reader has ALREADY SEEN all of these tables. A bullet that restates one "
    "row is worthless to them. Every bullet must add something not visible from "
    "reading a single row: a relationship between two datasets, a divergence, a "
    "change of pace, or a consequence.\n\n"
    "Write exactly 6 bullets, each starting with '- ', in this fixed order:\n"
    "1. STANCE: the single most important thing about this week, combining price "
    "trend with participation. Commit to a view — constructive, deteriorating or "
    "mixed — and justify it with numbers. 'Mixed' is a verdict only if you name "
    "the two things pulling against each other.\n"
    "2. CONFIRMATION: the strongest point where two datasets agree, naming both.\n"
    "3. DIVERGENCE: the strongest point where two DIFFERENT datasets disagree — "
    "an index rising while its breadth falls, a relative-strength leader inside "
    "a weakening segment, a benchmark near its highs on narrowing participation. "
    "Two timeframes within a single dataset are NOT a cross-dataset divergence "
    "and do not belong here. Name both datasets and one number from each. If "
    "there is genuinely no material divergence, say which pair you checked and "
    "give the number that rules it out.\n"
    "4. ROTATION: which sectors or themes money moved into and out of, from "
    "relative strength, and whether the one-week and six-month pictures agree.\n"
    "5. GLOBAL AND MACRO: US equities, Hang Seng, crude, gold and USDINR, and "
    "what they imply for Indian equities. Never omit this bullet and never treat "
    "those names as Indian indices.\n"
    "6. WATCH: one falsifiable trigger for next week. It must contain a named "
    "metric, a direction, a threshold taken from the numbers above, and what "
    "crossing it would signal — so that next Monday it is plainly true or false. "
    "'Watch whether X breaks' and 'monitor the trend' are failures.\n"
    "Do not use the same index, sector or theme as the subject of more than one "
    "bullet — six bullets should cover six different things.\n"
    "If a bullet needs a dataset that is absent, replace it with the next most "
    "useful observation and state which data was missing.\n\n"
    "Subject choice: prefer the broad benchmarks (Nifty 50, Bank Nifty, Midcap, "
    "Smallcap) and the large sector indices. Make a narrow thematic index the "
    "subject of a bullet only when its number is among the largest on the page — "
    "a small theme topping a one-week table is noise, not rotation.\n\n"
    "Evidence rules:\n"
    "- Every bullet carries at least two figures copied from above exactly as "
    "written, sign and unit included. A sentence you cannot pin to a figure in "
    "the facts is deleted, not softened.\n"
    "- Never compute, sum, average, annualise or estimate a number. If the figure "
    "you want is not above, make a different point.\n"
    "- Always state the period a number covers (one week, one month, six months, "
    "the six-week trend). Never describe a one-week move as if it spanned six.\n"
    "- An A/D ratio below 1.0 is weak, not negative.\n"
    "- Relative strength is measured against Nifty 50: a positive figure is "
    "outperformance, not an absolute gain. A leader can still have fallen.\n"
    "- A percentage against an EMA or a swing point is a distance, not a return.\n"
    "- A rise in USDINR is rupee weakness; a fall is rupee strength.\n\n"
    "Banned, because they carry no information: suggests, appears to, could "
    "indicate, remains to be seen, bears watching, investors should, cautious "
    "optimism, healthy consolidation, in the coming weeks.\n\n"
    "Three worked contrasts. Do not imitate the weak versions:\n"
    "WEAK (rereads one row): 'Nifty 50 rose 2.6% last week and sits 0.6% below "
    "its swing high.'\n"
    "STRONG (same row, earns its place): 'Nifty 50's 2.6% week came with 50 DMA "
    "breadth up 16 points to 61%, so this leg is broad participation rather than "
    "a few heavyweights carrying it.'\n"
    "WEAK divergence (one dataset, two timeframes): 'Pharma leads over one week "
    "at +3.1% but lags over six months at -4.2%.'\n"
    "STRONG divergence (two datasets contradicting): 'Smallcap 250 sits 0.4% off "
    "its swing high, yet only 38% of its constituents hold their 50 DMA against "
    "61% for the Nifty 50 — the index high is being carried by a narrowing few.'\n"
    "WEAK watch (unfalsifiable): 'Watch whether breadth confirms the move.'\n"
    "STRONG watch (settles next Monday): 'If Nifty Bank closes below its 50 EMA "
    "at 51,480 while above-50-DMA breadth stays under 45%, the four-week advance "
    "is over rather than pausing.'\n\n"
    "No preamble, no headings, no markdown. One or two sentences per bullet, "
    "25-50 words."
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
    # otherwise truncated the reply after three of the six bullets. The silent
    # planning step adds to that, so the budget is raised alongside it.
    # Temperature is below the module default: this is arithmetic-adjacent
    # writing where sampling variety buys nothing and costs number fidelity.
    # Thinking is capped so the planning step cannot eat the whole budget and
    # leave three bullets. 4096 selects the low tier on a Gemini 3 model, which
    # measured ~800 thinking tokens — ample for a four-step plan, and bounded,
    # which the token-count dialect is not on this model.
    # max_chars is Telegram headroom rather than a target: six bullets of up to
    # 50 words land near 2400, and the cap must not be what removes bullet 6.
    return gemini_generate(prompt, max_chars=3000, max_tokens=16000,
                           temperature=0.25, thinking_budget=4096,
                           bullets_only=True)
