# Nifty ML option-selling strategy (paper on dummy, live on deepti)

Status as of 2026-11-18: paper trading on `dummy` since 2026-11-16. Live on `deepti` (Angel One) since 2026-11-18 (first day: one strangle, +Rs 1,810 gross). P&L reporting (day-end row, daily ledger, weekly and monthly report) added on 2026-11-18: tested on the laptop (165 tests pass), not yet committed.

## In one paragraph

Every 15 minutes a LightGBM model predicts whether Nifty will be bullish, bearish or sideways over the next 90 minutes. The strategy sells out-of-the-money options 100 points from ATM: a PE when bullish, a CE when bearish, and both when sideways. Bullish and bearish calls must also be confirmed by PCR; sideways relies on the model alone. A leg is closed when the new signal no longer wants it, and everything is closed at 15:15. It runs on the `dummy` account (filled on paper without reaching a broker) and on `deepti` (real Angel One orders), each with its own position, files and Telegram chat.

## Trading rules

| Item | Rule |
|---|---|
| Instrument | NIFTY weekly options, 1 lot (65) per `quantity` in the sheet |
| Decision times | Each 15-minute candle close from 9:30 |
| Entry threshold | A signal is entered only if its class is the model's top class with probability **above 0.45** |
| Exit threshold | A signal that is already held is kept until its class probability falls **below 0.40** (see "Entry and exit thresholds") |
| Bullish signal | Top class is bullish above 0.45, **and** PCR is above 1.4 or at least 0.3 above today's lowest PCR |
| Bearish signal | Top class is bearish above 0.45, **and** PCR is below 0.7 or at least 0.3 below today's highest PCR |
| Sideways signal | Top class is sideways above 0.45. No PCR check |
| No signal | No class qualifies for entry (top class at or below 0.45, or a bullish/bearish call not confirmed by PCR) **and** nothing held is still at or above 0.40 |
| Bullish trade | Sell the PE 100 points below ATM (ATM 23000 → sell 22900 PE) |
| Bearish trade | Sell the CE 100 points above ATM (ATM 23000 → sell 23100 CE) |
| Sideways trade | Sell both: the PE 100 below ATM and the CE 100 above |
| Exit | At each check, any held leg the new signal does not want is closed, and any wanted leg not yet held is sold |
| Cooling period | After any exit, no new leg is sold at that check. The earliest new entry is the next 15-minute check, if the signal still wants it |
| Daily limit | At most 2 trades a day. A trade is the leg or legs sold at one 15-minute check, so a strangle is one trade. After the second trade no new leg is sold that day; exits and the square-off still run |
| Square-off | All open legs are closed at 15:15 |
| Last entry | The 14:00 bar. Exits continue until 15:15 |
| Stop-loss | None |
| Positions | At most one PE and one CE at a time |

How the legs change between signals:

| From → to | What happens |
|---|---|
| Sideways → bullish | CE is closed, the existing PE is kept |
| Sideways → bearish | PE is closed, the existing CE is kept |
| Bullish → sideways | PE is kept, a CE is sold at the current ATM + 100 (no exit, so no cooling) |
| Bearish → sideways | CE is kept, a PE is sold at the current ATM − 100 (no exit, so no cooling) |
| Bullish ↔ bearish | The held leg is closed. The other side is sold at the next check, if the signal still holds |
| Anything → no signal | All legs are closed |

### Entry and exit thresholds

Two thresholds stop a position being closed and re-opened because the probability wobbles around one number.

- **Entry:** the top class must be above 0.45, as before.
- **Hold:** if nothing qualifies for entry, the signal that matches the open legs is kept while that class's probability is at least 0.40. Open legs are read from the trades file: PE and CE = sideways, PE only = bullish, CE only = bearish. Held bullish needs bullish at 0.40 or more; held sideways needs sideways at 0.40 or more.
- **Switch:** a different class above 0.45 (confirmed by PCR if directional) replaces the held one, and the normal leg changes apply.
- **PCR applies to entering only.** A held bullish or bearish leg is not closed because PCR stopped confirming; it is closed when its probability falls below 0.40.
- **Why:** on 2026-11-16 at 13:30 the sideways probability was 0.4473, 0.003 under 0.45. The strategy closed both legs and sold the same two strikes again at 13:45. Replaying that day's logged probabilities through the new rule gives one strangle from 09:30 to 15:15 and no 13:30 exit.
- **Effect over 337 backtest days** (cooling period included):

  | Exit level | Legs sold per day | Orders per day (sell + buy) | Average hold per leg |
  |---|---|---|---|
  | 0.45 (no gap) | 3.83 | 7.66 | 8.1 bars (2 hours) |
  | 0.40 (chosen) | 3.01 | 6.02 | 11.3 bars (2.8 hours) |
  | 0.35 | 2.46 | 4.92 | 14.7 bars |

  These count orders only. Whether holding longer earns more points has not been tested.
- **A lower exit level holds weakening positions longer.** There is no stop-loss, so the only protection against a moving market is the signal falling below 0.40 and the 15:15 square-off.

Notes on the rules:

- The daily limit counts entries, not legs. A leg added at a later check (for example a CE added when bullish becomes sideways) is its own trade and counts towards the 2. A rejected entry does not count. The count is read from the trades file, so it survives a restart.

- A kept leg stays at its original strike even if ATM has moved since it was sold.
- "ATM" is the engine's ATM strike from the option chain: the strike where the CE and PE prices are closest, which is usually but not always the strike nearest to spot.
- PCR is the engine's value: total put open interest divided by total call open interest. Today's lowest and highest PCR come from today's option chain file.
- The cooling period works on the 15-minute checks, not a stopwatch. Any exit blocks any entry at that check, not only re-entry on the same side.
- If the inputs for a bar are not available within 5 minutes (candle or option chain data missing), that bar is skipped and open legs are left as they are.
- The 14:00 last entry exists because the model looks 90 minutes ahead and was only trained on bars up to 14:00.

## Orders and status checks

Entries and exits go through `PlaceOrder`, the same order adapter the other option strategies use. `PlaceOrder` fills `dummy` orders itself at the expected price and sends `deepti` orders to Angel One as MARKET orders with INTRADAY product, on the nearest expiry. Every other account is refused.

| Step | What happens |
|---|---|
| Entry | A sell order is placed and the leg is recorded as `pending` with the option chain price |
| Exit | A buy order is placed and the exit is recorded as `pending` |
| Every loop (about once a minute) | Each pending order is checked with `order_status` |
| Order complete | The leg is marked `complete`, the broker's fill price replaces the expected price and P&L is recalculated. The Telegram message is sent once every leg of that entry or exit is confirmed |
| Entry rejected or cancelled | The leg is retired as "entry rejected" with zero P&L and an error is sent. The next attempt is at the next 15-minute check, if the signal still wants it |
| Exit rejected or cancelled | The leg is reopened and the close is placed again on the same loop, with an error message |
| Order could not be placed | Entry: an error is sent and it is tried again at the next 15-minute check. Exit: retried every loop, with one error message |
| Still unconfirmed after 2 minutes | One warning message; it keeps checking, and never places a duplicate |
| Entry not yet confirmed | The leg is not closed until its entry is confirmed |

- **One Telegram message per entry and per exit.** Legs sold in the same 15-minute bar are announced together (for example `sold 25150 PE at 74.75 and 25350 CE at 57.9`). Legs closed in the same bar are announced together with each leg's points and a total. The message waits until no leg of the group is still pending. A leg added at a later check (for example a CE added when bullish becomes sideways) is its own entry message. Errors and warnings stay one message each.
- The day total is sent only after every exit is confirmed. It gives gross, charges (from `brokrage_calculator`) and net rupees, and the same numbers go into the P&L reports (see "P&L reporting").
- Any status containing "reject" or "cancel" counts as a rejection. Anything else that is not `Complete` counts as still pending.
- Angel One statuses arrive through `PlaceOrder.order_status`, which maps them to `Complete`, `Open`, `Rejected`, `Cancelled`, `NotFound` or an API error. Only `Complete`, `Rejected` and `Cancelled` change a leg; `NotFound` and API errors keep it pending and it is asked again on the next loop.
- To add another account: add it to `LIVE_ACCOUNTS` in `NiftyMlStratergy.py`. Check that broker's status strings in `PlaceOrder.order_status` first.

## Live account (deepti)

| Item | Rule |
|---|---|
| Accounts | `PAPER_ACCOUNTS = ('dummy',)`, `LIVE_ACCOUNTS = ('deepti',)`. Any other account in the sheet is refused and logged |
| Separate per account | Trades file, signals file, Telegram chat (`<account>_telegram`), held legs, cooling period, daily trade count and order alerts. Both accounts can trade in the same bar |
| Shared | The model runs once per 15-minute bar; every account uses those probabilities with its own held legs |
| Lot cap | A live account sells only if its sheet quantity is at most 1 lot (`MAX_LIVE_LOTS`). Above that, one alert a day and no new entries; open legs are still closed |
| Failed sell order | If a sell order cannot be placed, the order may still have reached Angel One. New entries stop for the rest of the day on that account (`csv/ml_blocked_<date>_deepti_NIFTY.txt`, survives a restart) and an alert asks you to check the broker. Open legs are still managed |
| Close without a quote | The buy-back is placed even when the option chain has no price for the held strike. P&L comes from Angel One's fill price |
| Unconfirmed sell at square-off | A leg whose sell order is still unconfirmed cannot be closed. One alert at 15:15 asks you to check the broker and close it manually |
| Order ids | Stored as text in the trades file so long Angel One ids keep every digit |
| Square-off | 15:15, the same as paper. In practice the close goes out at the first engine loop after 15:15 (15:16-15:19 so far). Your other deepti strategies close at 15:10 and 15:13 |
| Stop-loss | None, as on paper |

## The model

- **What it predicts:** the Nifty move over the next 6 bars (90 minutes), within the same day. Bullish means a rise of more than 0.6 times the recent typical 6-bar move, bearish a fall of more than that, otherwise sideways.
- **Inputs (44 features):**
  - Price: recent returns, volatility, distance from 20- and 100-bar averages, gap up/down at the open, distance to the previous day's high and low, time of day, day of week.
  - Williams Alligator: trend state on 15-minute bars and on daily bars, and the mouth width (same definition as `alligator_api.py`).
  - Expiry: trading days to the weekly expiry (Thursday until August 2025, Tuesday after, moved earlier on holidays).
  - Option chain: PCR, its changes and 5-day z-score, ATM straddle premium, a futures-basis proxy from put-call parity, call/put skew, distance to the highest-OI call and put strikes, put/call OI ratios.
  - Bank Nifty: PCR, straddle premium and strength relative to Nifty.
- **Algorithm:** LightGBM multiclass, 200 rounds, 15 leaves, learning rate 0.03, minimum 100 samples per leaf, 80% row and column sampling, L2 regularisation 5, fixed random seed.
- **Current model:** trained on 3,000 bars over 158 trading days, 2025-12-02 to 2026-11-13.
- **Data gap:** option chain files for 2026-07-18 to 2026-10-25 do not exist, so that period is not in the training data.
- **How it runs on the Pi:** the trees are exported to `auto_straddle/nifty_ml_model.json` and evaluated in plain Python. The Pi needs no machine-learning packages.
- **Same code for training and live:** both build features with `auto_straddle/nifty_ml_features.py`, including the rule that drops holidays and incomplete days.

## Training the model

Training happens on the laptop only. The Pi has no lightgbm and does not need it.

### What you need on the laptop

| Item | Where |
|---|---|
| Python packages | `lightgbm`, `scikit-learn`, `pandas`, `requests` in the repo `venv` (already installed) |
| Nifty price files | `ml_test/NIFTY 50_15minute.csv` and `NIFTY 50_5minute.csv` |
| Option chain files | `~/temp/data_collection/data_collection/` (all sub-folders are searched; the largest file per date is used) |
| The Pi | Reachable at `pitest@192.168.1.4` over SSH, for the weekly copy and the deploy |

### What training does

`train_model.py` runs these steps:

1. Loads the 15-minute Nifty prices and keeps regular-session weekdays.
2. Loads every usable day of Nifty and Bank Nifty option chain files and turns the minute snapshots into 15-minute bars. Holidays, weekends and badly incomplete days are dropped, and so are files older than 2024-08-19, which lack the open-interest columns.
3. Builds the 44 features. Rows whose 5-day option lookback crosses a gap in the files are dropped.
4. Labels each bar bullish, bearish or sideways from the next 6 bars of the same day.
5. Trains LightGBM on the most recent 3,000 labelled bars.
6. Exports the trees to `auto_straddle/nifty_ml_model.json`.
7. Checks the export against lightgbm's own predictions on 2,000 rows. If they differ, it stops and writes nothing.

### Weekly routine

Run after Friday's close, with the Pi reachable.

**Step 1 — retrain:**

```
sh ml_test/weekly_retrain.sh
```

This does three things in order:

- Copies the Pi's option chain files to `~/temp/data_collection/data_collection/csv_pi/`. The Pi keeps only about 15 days, so skipping two weeks leaves a permanent hole in the training data.
- Updates both Nifty price files from Upstox's public endpoint (no login). This is `update_prices.py`, which can also be run on its own.
- Runs `train_model.py` and overwrites the model file.

**Step 2 — check the output.** It should look like this:

```
trained on 3000 bars, 158 days: 2025-12-02 09:45:00 -> 2026-11-13 14:00:00
label mix: {0: 0.266, 1: 0.492, 2: 0.242}
export check on 2000 rows: max probability difference 2.22e-16
wrote .../auto_straddle/nifty_ml_model.json (452 KB, 600 trees, 44 features)
```

- The training window should end on the latest trading day. If it ends earlier, the price files or the option chain copy did not update.
- The label mix (0 bearish, 1 sideways, 2 bullish) should be roughly a quarter, a half, a quarter.
- The export check difference should be tiny (around 1e-16).

**Step 3 — deploy:**

```
sh ml_test/deploy_model.sh
```

- Commits only the model file and pushes the current branch.
- Shows the commits the Pi will receive and asks before pulling there.
- No restart is needed: the engine reloads the model file at its next 15-minute check.
- The pull also brings any other commits already pushed to the branch. Those take effect only when the engine is next restarted.

`deploy_model.sh` has been syntax-checked but not run, because it commits and pushes.

### Training by hand

```
cd ml_test
../venv/bin/python update_prices.py
../venv/bin/python train_model.py ~/temp/data_collection/data_collection
```

Options for `train_model.py`:

| Option | Default | Meaning |
|---|---|---|
| `--train_days` | 120 | Training size in units of 25 bars, so 120 gives 3,000 labelled bars. About 19 bars a day carry a label, so this spans about 158 trading days |
| `--horizon` | 6 | Bars ahead that the label looks (6 = 90 minutes) |
| `--k` | 0.6 | How big a move counts as bullish or bearish, in multiples of the recent typical move |

Changing `--horizon` or `--k` changes what the model predicts, so re-check the backtest before deploying such a model.

### Checking a model before trusting it

These are research tools and are not part of the weekly routine:

```
cd ml_test
../venv/bin/python build_data.py ~/temp/data_collection/data_collection --freq 15min
../venv/bin/python pipeline.py data/nifty_15m.csv --bars_per_day 25
```

`pipeline.py` retrains week by week over the whole history and reports out-of-sample accuracy, log-loss against a no-skill forecast, feature importance, and a simple directional backtest with baselines.

## Where the live data comes from

| Input | Source |
|---|---|
| 15-minute Nifty candles | `MarketData.get_ohlc` (broker candles, the same call the index future strategy uses) |
| PCR, ATM strike, option prices | The option chain snapshot the engine already fetches every minute |
| Option history for PCR changes and z-score | The daily `csv/options_chain_NIFTY_<date>.csv` and `options_chain_BANKNIFTY_<date>.csv` files on the Pi (newest 12 files) |
| Expected entry price | `atm_next_pe_price` / `atm_next_ce_price` from the snapshot (the options 100 points from ATM) |
| Expected exit price | The same fields if ATM has not moved; otherwise one extra option chain request for the prices around the entry ATM |
| Final prices | The fill price returned by `order_status` (for `dummy`, the expected price) |

## Files

| File | Purpose |
|---|---|
| `auto_straddle/NiftyMlStratergy.py` | The live strategy |
| `auto_straddle/nifty_ml_features.py` | Feature and model code shared by training and live |
| `auto_straddle/nifty_ml_pnl.py` | Day P&L from a trades file, shared by the strategy and the daily ledger; also the backfill command |
| `auto_straddle/nifty_ml_model.json` | The trained model |
| `auto_straddle/AutoStraddle.py` | Calls the strategy for sheet rows with Stratergy `ml` |
| `auto_straddle/tests/test_nifty_ml_stratergy.py` | Tests |
| `ml_test/train_model.py` | Trains and exports the model |
| `ml_test/update_prices.py` | Brings the Nifty 15-minute and 5-minute price files up to date |
| `ml_test/weekly_retrain.sh` | Copies the Pi's option chain files, updates prices and retrains |
| `ml_test/deploy_model.sh` | Commits the model file, pushes, and pulls on the Pi |
| `ml_test/build_data.py`, `pipeline.py` | Research and backtest scripts. `train_model.py` imports both, so training needs them |
| `ml_test/straddle.py` | Short-straddle P&L estimate (research only) |

Only the `auto_straddle/` files are needed on the Pi.

## Turning it on

1. Commit the `auto_straddle/` files above and pull on the Pi.
2. In the options account sheet, Stratergy `ml`, Symbol `NIFTY`:
   - paper: Account `dummy`, quantity `1`;
   - live: Account `deepti`, quantity `1` (the code refuses more than 1 lot).
3. Restart the engine before the open.

Removing a row (or setting its quantity to 0) turns that account off. On the first live day, watch the first entry and exit in the Angel One order book and in Telegram.

## Output

- Telegram message to each account's own chat (`dummy_telegram`, `deepti_telegram`) when each entry and each exit is confirmed (one message for all legs of that trade), on any order error, and a day total after 15:15.
- `csv/ml_signals_<date>_<account>_NIFTY.csv`: one row per 15-minute decision per account with the three probabilities, the model's raw call (`model_call`, entry threshold only), PCR, the day's PCR low and high, the final `signal` and what was `held` going into the bar. A day where `model_call` is `none` but `signal` is `sideways` is a held position kept by the exit threshold. The file name gained the account on 2026-11-17 (it was `ml_signals_<date>_NIFTY.csv`).
- `csv/ml_trades_<date>_<account>_NIFTY.csv`: one row per option leg with entry, exit, reason, order ids, order states and P&L in points and rupees. P&L is before brokerage, taxes and slippage.

## P&L reporting

The strategy feeds the same three reports as your other strategies, under the strategy name `NiftyMl`.

| Report | When | How NiftyMl gets in |
|---|---|---|
| Day total (Telegram) | After the 15:15 square-off, once every exit is confirmed | The strategy sends it to the account's chat |
| `pnl/consolidated_pnl_<MM>.csv` | Same moment | One row per account per day: `Date, Account, Symbol, Quantity (lots), NumberofTrade, TotalPNL, Brokarge, CloseTime, Stratergy=NiftyMl, NetPNL`. Written once, even if the engine restarts after the square-off |
| Daily ledger (next morning) | Engine start, for the previous trading day | `ledger_calculation.calculate_nifty_ml_ledger` reads `ml_trades_<previous day>_<account>_NIFTY.csv` and adds a `NiftyMl` line to the account's strategy breakdown and total |
| Weekly (Saturday 9:00) and monthly report | cron, `monthly_pnl_report.py` | Reads the consolidated file; `NiftyMl` has a margin multiplier of 1.5 (mostly strangles) |

- Only legs whose sell and buy-back were both confirmed count. A rejected sell and a buy-back still pending are left out.
- Charges are `brokrage_calculator.calculate_equity_options(exit, entry, lots × 65)` per leg, like FarSell.
- `dummy` rows are written too, like the other strategies; the reports leave `dummy` out.
- The shared calculation is in `auto_straddle/nifty_ml_pnl.py`, which has no broker or market-data imports, so the ledger does not depend on the strategy loading.
- **Backfill** a day traded before this existed (run on the Pi from `~/temp/data_collection`; safe to repeat):
  `python3 ~/automated/auto_straddle/nifty_ml_pnl.py 2026-11-18 deepti`
- Limit: if a buy-back is still unconfirmed at 15:29, when the engine stops calling the strategy, no day row is written. Run the backfill command for that day once it is confirmed.

## What the backtests say

Out-of-sample walk-forward over 337 days (2025-02-25 to 2026-11-13), decision bars from 9:30 to 14:00:

| Signal | Share of decision bars |
|---|---|
| Sideways | 68.9% |
| No signal | 29.4% |
| Bearish (model + PCR) | 1.5% |
| Bullish (model + PCR) | 0.3% |

- **It is mostly a short-strangle strategy.** It holds a position on 99% of days and sells about 3.9 legs a day. Directional trades are rare. With the cooling period and the 0.40 exit threshold the simulation gives 3.0 legs sold a day (3.8 without the exit threshold).
- **Sideways calls:** the next 90 minutes were actually sideways 54.2% of the time, against 51.4% for all bars. That is a small improvement over selling blindly.
- **Directional calls when they fired:**

  | | Calls | Right | Sideways | Wrong way |
  |---|---|---|---|---|
  | Bullish | 18 | 22% | 44% | 33% |
  | Bearish | 95 | 22% | 41% | 37% |

- **Option P&L has not been backtested** for these rules. The stored data only has prices around the current ATM, so a held leg cannot be priced once ATM moves. The paper run is what will produce P&L numbers.
- **Model on its own:** in the earlier walk-forward tests its direction calls were close to a coin flip and it did worse than a no-skill forecast on log-loss.

Treat the paper run as a test of the plumbing and a way to collect live results, not as evidence the strategy makes money.

## How it was checked

- **Tests:** 165 pass, including deepti orders driven through the real `PlaceOrder` adapter with a fake Angel One object (quantity 65, INTRADAY, 19-digit ids unchanged, the 4-attempt failure path). They cover each signal, every leg transition, the cooling period, square-off, restart recovery, the model reload, and the order flow with a fake broker (fills at a different price, rejections, failed placements, unconfirmed orders).
- **Model export:** the plain-Python evaluation matches lightgbm to about 2e-16.
- **Live versus training:** five past bars were replayed through the live code path using the Pi's own files. Four matched the training features exactly; the fifth differed by 0.3% in probability because of the expiry limit below.

## Known limits

- Runs on the Pi's Python 3.9 and pandas 1.5 (checked read-only). If it fails there, the error is logged and the other strategies are unaffected: the import, the creation and every call of the strategy are each wrapped in their own try/except in `AutoStraddle.py`.
- A hang (not an error) is different: like every strategy, it is covered by the engine's 5-minute watchdog, which restarts the whole engine.
- Days-to-expiry can be off by one when a holiday on expiry day lies ahead, because the Pi has no holiday calendar.
- Paper fills use the option chain's last traded price, so real fills would be worse by the bid-ask spread.
- The live path has not yet run against Angel One. Tests use a fake broker; the first real entry and exit should be watched.
- The strategy never checks the broker's positions. It trusts its own trades file and the order statuses.
- Orders can be sent more than once when a placement looks failed. `PlaceOrder` retries a deepti order up to 3 times whenever the Angel One wrapper returns no valid id (so up to 4 attempts for a sell and 4 for a buy-back), and the wrapper itself retries `placeOrder` once after an exception other than a timeout, without first looking for an existing order. If an attempt that looked failed actually reached the broker, the result is two sells (a double position) or two buy-backs (leaving a long option). This is existing behaviour shared with your other strategies; the strategy's own protection is that it never retries a failed sell after `PlaceOrder` gives up.
- A rejected sell (for example for lack of margin) does not stop entries: it is tried again at the next 15-minute check, so a margin shortfall gives one rejected order and one error message every 15 minutes until 14:00.
- If Angel One's automatic intraday square-off runs before the strategy's 15:15 close, the strategy's buy-back would open a long option. You chose to keep 15:15; the other deepti strategies close at 15:10 and 15:13.
- No stop-loss. With real money, a held position is protected only by the signal falling below 0.40 and the square-off.
- Your own rule in `CLAUDE.md` was to paper trade for 1-2 months first; live trading started after 2 paper days, at 1 lot.
- With a real broker, if one leg's close is rejected after its sibling's close completed, the sibling is announced once alone and again together with the retried leg, so it appears twice in Telegram. Dummy orders are never rejected, so this cannot happen today.
- From 2026-08-03 the index is frozen from 15:15 to 15:25 and then prints the close, which looks like a closing auction. The 15:15 square-off sits right at that boundary.
- The signal for the current bar is kept in memory. Open legs, pending orders and the cooling period are read from the trades file, so they survive a restart.
