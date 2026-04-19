# Elliott Wave Long-Only Strategy — Handoff Document

**Purpose:** This document hands off a validated Elliott Wave backtest framework for the Indian Nifty 200 universe. Use this as context when porting to another trading framework (backtrader, zipline, vectorbt, custom, etc.).

---

## 1. Strategy Overview

**Objective:** Long-only positional trading on Nifty 200 stocks, holding 1-3 months per trade.

**Approach:** Algorithmic approximation of Elliott Wave theory:
- Detect swing highs/lows in price data
- Identify 5-wave impulse structures using Fibonacci validation
- Enter LONG at Wave 3 breakouts (most common) or Wave 5 breakouts
- Exit via trailing stop, Fibonacci target, hard stop, or time stop

**Validation status:**
- Walk-forward tested: 12.0% CAGR in-sample (2016-2020), 17.6% CAGR out-of-sample (2021-2026)
- All 108 parameter combinations in grid search were profitable
- Final production config achieves 17.14% CAGR on full 10-year backtest

---

## 2. Validated Production Config

These are the parameters I recommend locking in:

```python
# Swing detection
swing_lookback = 5              # Fastest confirmation — 5 bar delay
min_swing_pct = 3.0             # Min % move to count as swing

# Fibonacci validation
wave2_retrace_min = 0.382       # Wave 2 retracement range of Wave 1
wave2_retrace_max = 0.786
wave4_retrace_min = 0.236       # Wave 4 retracement range of Wave 3
wave4_retrace_max = 0.500
wave3_ext_min = 1.618           # Min Wave 3 extension of Wave 1

# Entry filters
rsi_period = 14
rsi_wave3_entry_min = 40        # RSI range for Wave 3 entry
rsi_wave3_entry_max = 70
rsi_wave5_entry_min = 45
rsi_wave5_entry_max = 70
volume_expansion_factor = 1.1   # Volume must be > 1.1x 20-day avg
breakout_bars = 3               # Close must break 3-bar high
volume_ma_period = 20

# Exit rules
trailing_atr_multiplier = 2.5   # Trailing stop = highest_close - 2.5*ATR(14)
atr_period = 14
time_stop_days = 90             # Max holding period
wave3_target_extension = 1.618  # Target for Wave 3 = Wave2_low + 1.618 * Wave1_range
wave5_target_extension = 0.786  # Target for Wave 5 = Wave4_low + 0.786 * Wave1_range

# Portfolio
initial_capital = 1_000_000     # ₹10 lakh
max_positions = 10              # Max concurrent trades
max_allocation_pct = 10.0       # 10% of total equity per trade
transaction_cost_per_trade = 60.0  # ₹60 per side (₹120 round-trip)
```

---

## 3. CRITICAL — Look-Ahead Bias Rules

These are non-negotiable. **Before this was fixed, CAGR was showing 40.8%. After fixing, it's 17.1%.** If you get wildly different numbers after porting, check these first.

### 3.1 Strictly Backward-Looking Swing Detection

A swing high at bar `i` requires:
- Bar `i`'s High ≥ all Highs in `[i - lookback, i]`
- Bar `i`'s High ≥ all Highs in `[i + 1, i + lookback]`
- **The swing's "confirmation date" is `i + lookback`, NOT `i`**

Same for swing lows with Lows.

**The key rule:** Any signal derived from this swing can only fire on bar `i + lookback` or later. The swing PRICE is at bar `i`, but the swing is only KNOWN at bar `i + lookback`.

### 3.2 No Structure Look-Ahead

Wave 3 entry signals must use ONLY Wave 1 start, Wave 1 end, Wave 2 end. Do NOT check if Wave 3 actually formed — that's the trade you're making.

Wave 5 entry signals must use ONLY Wave 1 → Wave 4 (all confirmed). Do NOT check if Wave 5 actually formed.

**Implementation:** Create separate wave structures for each signal type. A Wave 3 structure has only W1 + W2. A Wave 5 structure has W1 + W2 + W3 + W4. Never let one leak into the other.

### 3.3 Execution Timing

- **Entry:** Signal confirmed at today's close → execute at **tomorrow's open**
- **Exit (SL/Trailing/Target):** Breach detected intraday → execute at **tomorrow's open**
- **Exit (Time Stop):** Executed at **today's close** (deliberate EOD decision)

---

## 4. Wave Detection Algorithm

### 4.1 Pipeline

```
OHLCV data → detect_swing_points() → identify_wave_structures() → generate_signals()
```

### 4.2 Swing Points

Produce a list of alternating highs and lows. Filter out swings smaller than `min_swing_pct`. Keep the most extreme swing if consecutive same-type swings appear.

### 4.3 Wave Structures — Long Only

Pattern for long: **Low → High → Low → High → Low → High** (W1_start → W1_end → W2_end → W3_end → W4_end → W5_end)

**Wave 2 validation:**
- `wave2_retrace = (W1_end - W2_end) / (W1_end - W1_start)`
- Must satisfy: `wave2_retrace_min <= wave2_retrace <= wave2_retrace_max`
- `W2_end > W1_start` (Wave 2 cannot retrace 100% of Wave 1)

**Wave 3 validation (only for Wave 5 entry setups):**
- `wave3_range > wave1_range` (Wave 3 can't be shortest)
- `wave3_extension = wave3_range / wave1_range >= wave3_ext_min`

**Wave 4 validation (only for Wave 5 entry setups):**
- `wave4_retrace = (W3_end - W4_end) / wave3_range`
- Must satisfy retrace range
- `W4_end > W1_end` (Wave 4 cannot overlap Wave 1)

### 4.4 Signal Generation

**Wave 3 Entry (the main signal):**
After W2 confirmed, scan the next up-to-15 bars looking for breakout confirmation:
1. Close breaks above highest High of last `breakout_bars` bars
2. RSI(14) within `[rsi_wave3_entry_min, rsi_wave3_entry_max]`
3. Volume > `volume_expansion_factor` × 20-day average volume
4. Risk (entry to SL) < 10% (skip if too wide)

Entry at NEXT DAY's open. SL at `W2_end * 0.99`. Target at `W2_end + wave1_range * wave3_target_extension`.

**Wave 5 Entry (less frequent):**
After W4 confirmed, same breakout logic but with `rsi_wave5_entry_*` bounds.
SL at `W4_end * 0.99`. Target at `W4_end + wave1_range * wave5_target_extension`.

---

## 5. Portfolio & Capital Management

### 5.1 Position Sizing

Fixed percentage of **total equity** (cash + open positions value):

```python
total_equity = cash + sum(pos.shares * current_price for pos in positions)
allocation = total_equity * (max_allocation_pct / 100)  # e.g., 10% of equity
shares = int(allocation / entry_price)

# Clamp to available cash
cost = shares * entry_price
if cost > cash:
    shares = int(cash / entry_price)
```

### 5.2 Rules

- Never hold more than one position in the same stock
- Never exceed `max_positions` concurrent positions (skip signal if at max)
- Deduct `transaction_cost_per_trade` on entry and again on exit

### 5.3 Exit Priority (checked in order)

1. **Stop loss hit** (today's Low ≤ SL) → mark for exit at next day's open
2. **Trailing stop hit** (today's Low ≤ trailing_stop) → mark for exit at next day's open
3. **Target hit** (today's High ≥ target) → mark for exit at next day's open
4. **Time stop** (`days_held >= time_stop_days`) → exit at today's close immediately

**Trailing stop update:** After each bar, `trailing_stop = max(trailing_stop, highest_close_since_entry - trailing_atr_multiplier * ATR(14))`. Ratcheted up only, never down.

---

## 6. Technical Indicators

**RSI (Wilder's smoothing):**
```python
delta = close.diff()
gain = delta.where(delta > 0, 0.0)
loss = -delta.where(delta < 0, 0.0)
avg_gain = gain.ewm(alpha=1/14, min_periods=14).mean()
avg_loss = loss.ewm(alpha=1/14, min_periods=14).mean()
rsi = 100 - (100 / (1 + avg_gain / avg_loss))
```

**ATR:**
```python
tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
atr = tr.rolling(14).mean()
```

**Volume MA:** Simple rolling mean over 20 bars.

---

## 7. Universe & Data

- **Universe:** Nifty 200 (all constituents) via `ind_nifty200list.csv` from NSE website
- **Data source:** Yahoo Finance (yfinance), daily OHLCV, `.NS` suffix for NSE
- **Period tested:** 2016-01-01 to 2026-04-12
- **Data caching:** Keep a `.cache/` directory with per-symbol CSVs to avoid re-downloading

**Known gotchas:**
- Some stocks (like Zomato/ETERNAL, Jio Financial) IPO'd within the backtest period — script should skip if <100 bars of data
- Ticker renames happen (ZOMATO → ETERNAL) — just accept missing data
- `M&M.NS` has the ampersand — handle in cache filenames

---

## 8. Validated Results (Reference Targets)

Your port should produce roughly these numbers when using the production config on the same Nifty 200 universe for 2016-01-01 to 2026-04-12:

| Metric | Expected |
|---|---|
| CAGR (full capital) | ~17% |
| Win rate | ~44-45% |
| Profit factor | ~1.45 |
| Max drawdown | ~-18% |
| Sharpe ratio | ~1.2-1.3 |
| Total trades | ~1,400-1,500 |
| Avg days held | ~12 days |
| Max concurrent positions | 10 |
| Avg capital utilization | ~72% |
| Final equity (₹10L start) | ~₹46-50 L |

**Walk-forward breakdown:**
- In-sample 2016-2020: 12.0% CAGR, -18.5% max DD, 44.2% win rate
- Out-of-sample 2021-2026: 17.6% CAGR, -17.4% max DD, 44.5% win rate
- OOS > IS is the key evidence this isn't overfit

**If your port produces results wildly different from these (e.g., 40% CAGR or 2% CAGR), a look-ahead bug or indicator computation error is likely.**

---

## 9. Known Limitations (Not Fixed — Document These)

- **Survivorship bias:** Universe uses CURRENT Nifty 200, not historical constituents. Stocks that were delisted or fell out of the index during the 10-year window aren't included. This inflates results somewhat.
- **No slippage beyond next-day-open:** We assume fills at the exact open price. Real execution may differ by 0.1-0.5%.
- **No impact cost:** For mid-caps, large orders can move price. Not modeled.
- **Single market regime:** Only 2016-2026 tested. Performance in prolonged bear markets (like 2008) is unknown.

---

## 10. Files In The Current Framework

- `elliott_wave_strategy.py` — Core library (data structures, indicators, swing detection, wave identification, signal generation, backtester, metrics)
- `run_backtest.py` — Run a single backtest with the production config
- `optimize_params.py` — Grid search over parameter combinations (saves `optimization_results.csv`)
- `walk_forward_validation.py` — Split IS vs OOS and compare
- `monte_carlo_robustness.py` — Perturb parameters randomly to test fragility
- `ind_nifty200list.csv` — NSE Nifty 200 constituent list

---

## 11. Suggested Port Testing Order

1. **Port the indicators first** (RSI, ATR, Volume MA) and verify against my implementation using 3-4 sample stocks
2. **Port swing detection** — verify swings match mine for a known stock (RELIANCE.NS works well). The detection date must be `i + lookback`, not `i`
3. **Port wave structure identification** — print detected structures for a sample stock and compare
4. **Port signal generation** — compare signal list for 1-2 stocks
5. **Port the backtester** (position sizing, exits, transaction costs)
6. **Run full backtest** — CAGR should match within ~2-3% of the reference (small differences from framework-specific behavior are OK)
7. **Run walk-forward** — OOS should still beat IS, or at least be close

---

## 12. Handoff Checklist for Claude Code

Paste this as the opening message:

> I'm porting an Elliott Wave long-only strategy for the Indian Nifty 200 universe to [YOUR TARGET FRAMEWORK NAME]. I'm attaching:
> - My working Python implementation (elliott_wave_strategy.py + related runners)
> - This handoff document with the validated config and reference results
> - An example CSV showing expected walk-forward performance
>
> Key requirements:
> 1. STRICT no-look-ahead: swing confirmation at bar `i + lookback`, not bar `i`
> 2. Separate Wave 3 and Wave 5 structures (no pivot info leaking across signal types)
> 3. Next-day-open execution for all entries and SL/TP/trailing exits
> 4. Time stop exits at today's close (deliberate EOD decision)
> 5. Position sizing: fixed 10% of total equity (cash + open positions)
> 6. ₹60 per-side transaction cost, applied on both entry and exit
>
> Please mirror my parameter config structure (StrategyConfig dataclass) so I can reuse my existing tuning results. After porting, run a backtest on 2016-2026 Nifty 200 daily data and compare CAGR, win rate, and max drawdown to my reference numbers (17% / 44-45% / -18%). If they differ by more than ~3%, help me diagnose whether it's a look-ahead bug.

That's it. Claude Code can take this document + your source files and produce a clean port.
