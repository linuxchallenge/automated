# Elliott Wave Cash Strategy Integration Plan

## Goal
Integrate the `elliot_backtest/` Elliott Wave strategy as an automated signal source
for the existing `cash_stratergy` execution engine. Replaces manual Google Sheets input.

## Architecture Overview

```
Every evening 3:30–4:00 PM:
  ElliotWaveSignalGenerator.generate_daily_signals()
    ├─ Download OHLCV (yfinance) for Nifty 200
    ├─ Run Elliott Wave detection
    ├─ Filter to today's signals only
    ├─ Read accounts/allocations from Google Sheets
    └─ Append new rows (status='new') to cash_stratergy.csv

Next morning 9:27–9:33 AM (existing code unchanged):
  cash_stratergy.execute_strategy()
    ├─ Process 'new' rows → place BUY orders
    ├─ Process 'open' rows → check SL/target
    └─ Process 'pending' rows → confirm order status
```

---

## Files to Create

### 1. `auto_straddle/elliot_wave_strategy.py`
Copy from `elliot_backtest/elliott_wave_strategy.py` (keep identical).
This makes the `auto_straddle/` module self-contained.

### 2. `auto_straddle/elliot_wave_signals.py`
New class `ElliotWaveSignalGenerator` with:
- `__init__(config_url)` — reads account/allocation config from Google Sheets URL
- `generate_daily_signals(csv_path, nifty200_csv)` — main entry point:
  1. Load Nifty 200 list
  2. For each symbol: fetch OHLCV via yfinance (with local cache), skip if < 100 bars
  3. Run `detect_swing_points()` + `identify_wave_structures()` + `generate_signals()`
  4. Filter signals where `signal.date.date() == today`
  5. Load `cash_stratergy.csv`; skip symbols already 'new' or 'open'
  6. For each account in Google Sheets config, append a row per signal
  7. Save updated CSV
  8. Send Telegram summary of new signals

**CSV row fields written:**
```
sl_no         = "EW_{YYYYMMDD}_{SYMBOL}_{ACCOUNT}"
date          = today
symbol        = NSE symbol (e.g. RELIANCE)
exchange      = NSE
account       = from Google Sheets config
sl            = signal.stop_loss
profit_target = signal.target_price
amount        = from Google Sheets config (allocation per trade)
status        = 'new'
signal_type   = 'WAVE3_ENTRY' or 'WAVE5_ENTRY'
strategy      = 'elliot_wave'
confidence    = signal.confidence (0-100)
```

---

## Files to Modify

### 3. `auto_straddle/AutoStraddle.py`
Add after the `cash_sl_updated_today` block:
```python
from elliot_wave_signals import ElliotWaveSignalGenerator
ew_generator = ElliotWaveSignalGenerator(ew_config_url)
ew_signals_generated_today = False

# In main loop, new time window 15:30–16:00:
if time_dt(15, 30) <= current_time_dt <= time_dt(16, 0):
    if not ew_signals_generated_today:
        signal.alarm(600)  # 10 min budget
        try:
            ew_generator.generate_daily_signals(
                csv_path='cash_stratergy.csv',
                nifty200_csv='<path>/ind_nifty200list.csv'
            )
            ew_signals_generated_today = True
        except Exception as e:
            logging.error(...)

# Reset at midnight:
if current_time_dt < time_dt(0, 5):
    ew_signals_generated_today = False
```

### 4. `auto_straddle/cash_stratergy.py`
- In `_process_pending_orders()` PnL recording: use `row.get('strategy', 'cash_short')`
  instead of hardcoded `'cash_short'` so EW trades log as `'elliot_wave'`
- Same fix in `_calculate_and_record_pnl()`
- No other changes needed — existing buy/sell/SL/target logic works as-is

---

## New Google Sheets Config (User to Create)

Sheet URL for EW accounts/allocations:

| Account | Amount  | MaxPositions |
|---------|---------|-------------|
| deepti  | 50000   | 10          |
| leelu   | 30000   | 5           |

- `Amount` = rupees allocated per trade (used to compute quantity = Amount / LTP)
- `MaxPositions` = max concurrent EW positions per account
- Store URL in `configuration.py` Google Sheet as `elliot_wave_config`

---

## Key Design Decisions

### Signal timing
- EW signals fire at bar close, entry is "next day's open"
- In live trading: signal written to CSV at 3:30-4 PM with `status='new'`
- Order executed at 9:27-9:33 AM next morning at market price
- Entry condition check: `last_price > row['sl']` (existing logic) prevents entering if stock gapped down below SL overnight

### Deduplication
- Skip symbol if any row with that symbol exists in CSV with `status in ('new', 'open')`
- `sl_no` format `EW_{date}_{symbol}_{account}` prevents duplicate inserts on re-runs

### Data cache
- Store cached OHLCV at `~/temp/data_collection/ew_cache/` (consistent with `cur_dir` in AutoStraddle.py)
- Cache per symbol as CSV, refresh if older than 1 day

### Symbol mapping
- Nifty 200 CSV uses `TICKER.NS` Yahoo format → strip `.NS` for NSE orders
- Existing `SYMBOL_MAPPING` in `cash_stratergy` handles M&M, LT etc.

### Max positions guard
- `ElliotWaveSignalGenerator` counts existing 'open' + 'new' EW positions per account
- Skips new signals if account is already at `MaxPositions`

---

## Implementation Steps (in order)

1. Copy `elliot_backtest/elliott_wave_strategy.py` → `auto_straddle/elliot_wave_strategy.py`
2. Create `auto_straddle/elliot_wave_signals.py` (ElliotWaveSignalGenerator class)
3. Modify `auto_straddle/cash_stratergy.py` (use `row.get('strategy', 'cash_short')` in 2 places)
4. Modify `auto_straddle/AutoStraddle.py` (add EW signal generation time window)
5. User: create Google Sheets config with accounts/amounts and share URL

---

## Dependencies to verify
- `yfinance` installed in the `auto_straddle` Python environment
- `numpy`, `pandas` (already used — OK)
- `ind_nifty200list.csv` path accessible from `cur_dir`
