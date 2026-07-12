---
name: search-logs
description: How to find and search the trading engine logs and runtime CSVs when debugging. Use whenever a task needs runtime logs (errors, order flow, strategy behavior) or runtime data files.
---

# Searching trading logs and runtime data

The trading engine runs on the Raspberry Pi, not on this machine. `logging_config.py` writes to `/tmp/auto_trade_YYYY-MM-DD.log` **on the Pi only** — the local `/tmp` here has nothing.

Log files are copied from the Pi into the repo root. Search there:

```bash
ls /home/adithya/Code/automated/auto_trade_*.log
grep -i "<pattern>" /home/adithya/Code/automated/auto_trade_2026-07-10.log
```

Notes:
- One file per trading day, named `auto_trade_YYYY-MM-DD.log`. Days may be missing (weekends, holidays, or not yet copied from the Pi).
- If the date you need is absent, ask the user to copy it from the Pi — do not assume the system didn't run that day.

## Runtime CSVs (`~/temp/data_collection/`)

The runtime data directory `~/temp/data_collection/` (option chain CSVs, sold-options tracking, ledger data, fund snapshots, EW signal files) also exists **only on the Pi**. It is not present on this machine — do not search for it here.

If a task needs one of those CSVs, ask the user to copy the specific file from the Pi into a local folder, then work from that copy.
