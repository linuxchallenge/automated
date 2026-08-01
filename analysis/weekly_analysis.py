"""Weekly market analysis -> Telegram. Single entry point for all sections.

Merges what used to be three separate Saturday cron jobs:
  1. relative_strength.py     — every NSE index ranked against Nifty 50
  2. weekly_index_report.py   — trend/position of the majors and commodities
  3. weekly_breadth_report.py — constituent participation and advance/decline

Each section still sends exactly what it sent before (RS summary + CSV, index
table, breadth PNG + table). One new message is appended: a Gemini reading of
all three datasets together, which is the part no single section could produce.

Sections are independent. A failure in one is reported and the rest still run
and send — previously an NSE outage took out relative_strength.py alone, now it
cannot take the other two with it.

Run:  python weekly_analysis.py                (print only)
      python weekly_analysis.py --send         (also send to Telegram)
      python weekly_analysis.py --no-llm       (skip both Gemini calls)
      python weekly_analysis.py --only breadth (one section: rs|index|breadth)
"""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0103

import os
import sys
import traceback
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'auto_straddle'))
from TelegramSend import telegram_send_api

import breadth_report
import index_report
import llm_analysis
import relative_strength

CHAT_ID = "-891000076"  # "Daily Nifty 200 update" group


def _section(name, fn, *args, **kwargs):
    """Run one section, converting any failure into a printed note.

    Returns (result, ok). The traceback goes to the cron log; the section is
    simply absent from the combined analysis.
    """
    print("\n" + "=" * 70)
    print(f"SECTION: {name}")
    print("=" * 70)
    try:
        return fn(*args, **kwargs), True
    except Exception as e:
        print(f"\n!! {name} failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        return None, False


def _wanted(name, argv):
    """--only rs|index|breadth restricts the run to one section."""
    if '--only' not in argv:
        return True
    i = argv.index('--only')
    return i + 1 < len(argv) and argv[i + 1] == name


def main(argv=None):
    argv = sys.argv if argv is None else argv
    send = '--send' in argv
    use_llm = '--no-llm' not in argv

    rs_df = index_rows = breadth_data = None
    failed = []

    if _wanted('rs', argv):
        res, ok = _section("Relative strength", relative_strength.run,
                           send=send, chat_id=CHAT_ID)
        rs_df = res
        if not ok or rs_df is None:
            failed.append("relative strength")

    if _wanted('index', argv):
        res, ok = _section("Index report", index_report.run,
                           send=send, chat_id=CHAT_ID)
        if ok and res:
            index_rows = res[0]
        else:
            failed.append("index report")

    if _wanted('breadth', argv):
        res, ok = _section("Breadth report", breadth_report.run,
                           send=send, include_llm=use_llm, chat_id=CHAT_ID)
        if ok and res:
            breadth_data = res[0]
        if not ok or breadth_data is None:
            failed.append("breadth report")

    # --- Combined reading across whatever succeeded -------------------------
    if use_llm:
        combined, _ = _section("Combined analysis", llm_analysis.combined_analysis,
                               rs_df=rs_df, index_rows=index_rows,
                               breadth_data=breadth_data,
                               week=datetime.now().strftime('%d-%b-%Y'))
        if combined:
            header = f"*Combined Weekly Analysis — {datetime.now().strftime('%d-%b-%Y')}*\n\n"
            note = ""
            if failed:
                note = ("\n\n_Note: " + ", ".join(failed) +
                        " unavailable this week; analysis covers the rest._")
            message = header + combined + note
            print("\n" + message)
            if send:
                telegram_send_api().send_message(CHAT_ID, message)
                print(f"\nSent combined analysis to Telegram ({CHAT_ID})")

    print("\n" + "=" * 70)
    if failed:
        print(f"Done — {len(failed)} section(s) failed: {', '.join(failed)}")
    else:
        print("Done — all sections completed.")
    if not send:
        print("--- Add --send flag to send to Telegram ---")


if __name__ == '__main__':
    main()
