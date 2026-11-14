---
name: search-logs
description: How to find and search the trading engine logs and runtime CSVs when debugging. Use whenever a task needs runtime logs (errors, order flow, strategy behavior) or runtime data files.
---

# Searching trading logs and runtime data

The trading engine runs on the Raspberry Pi, not on this machine. `logging_config.py` writes to `/tmp/auto_trade_YYYY-MM-DD.log` **on the Pi only** — the local `/tmp` here has nothing.

**Preferred: SSH to the Pi** (key-based auth is set up; never store the password in this repo):

```bash
ssh pitest@192.168.1.4 'grep -i "<pattern>" /tmp/auto_trade_2026-07-10.log'
ssh pitest@192.168.1.4 'ls ~/temp/data_collection/'
```

Fallback: older log copies may exist in the repo root:

```bash
ls /home/adithya/Code/automated/auto_trade_*.log
grep -i "<pattern>" /home/adithya/Code/automated/auto_trade_2026-07-10.log
```

Notes:
- One file per trading day, named `auto_trade_YYYY-MM-DD.log`. Days may be missing (weekends, holidays, or not yet copied from the Pi).
- If the date you need is absent locally, read it from the Pi over SSH — do not assume the system didn't run that day.

## Runtime CSVs (`~/temp/data_collection/`)

The runtime data directory `~/temp/data_collection/` (option chain CSVs, sold-options tracking, ledger data, fund snapshots, EW signal files) also exists **only on the Pi**. It is not present on this machine — do not search for it here.

If a task needs one of those CSVs, read it over SSH: `ssh pitest@192.168.1.4 'cat ~/temp/data_collection/<file>'`.
