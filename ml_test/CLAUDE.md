# Nifty direction model (bullish / bearish / sideways)

Full description of the live strategy, the rules, training steps and backtest numbers: `NIFTY_ML_STRATEGY.md` in this folder. Read it before changing anything here.

## Goal
Predict Nifty direction on 15 minute bars and sell options on the call.
Train on the i7 laptop; the finished model runs inference only on a Raspberry Pi.
Stage reached: paper-trading loop on the `dummy` account (implemented 2026-11-15, not yet run on the Pi). Live execution is much later.

## Data
- Prices: `NIFTY 50_15minute.csv` and `NIFTY 50_5minute.csv` in this folder (2015-01-09 onward, bars labelled by their open, volume always 0). Updated with `update_prices.py` from Upstox's public endpoint.
- Option chain: the engine's `options_chain_<SYMBOL>_<date>.csv` minute snapshots (NIFTY and BANKNIFTY), kept on the laptop under `~/temp/data_collection/data_collection/` in several backup folders. Usable from 2024-08-19 (older files lack the open-interest columns).
- PCR and spot are in the same snapshot row, about every 30-60 seconds, so any bar size works.
- The Pi keeps only about 15 days of these files. `weekly_retrain.sh` copies them to `csv_pi/`; skipping two weeks loses data for good. Known hole: 2026-07-18 to 2026-10-25.
- Built datasets live in `data/`. Never commit data (`*.csv` is git-ignored).
- From 2026-08-03 the index is frozen 15:15-15:25 and then prints the close (looks like a closing auction).

## Decisions already made
- PCR alone is a weak signal; the model uses 44 features (price, Alligator, gap, previous-day high/low, expiry, option chain, Bank Nifty).
- Labels: return over the next 6 bars of the same day, scaled by recent volatility; > +0.6*vol bullish, < -0.6*vol bearish, else sideways. Labels never cross the overnight gap.
- Model: LightGBM multiclass (conservative: num_leaves=15, min_child_samples=100, lr=0.03, 200 trees, subsample/colsample 0.8, reg_lambda=5). Act only when top-class probability > 0.45.
- The Pi has no lightgbm (armv7l, Python 3.9, pandas 1.5). The model is exported to `auto_straddle/nifty_ml_model.json` and evaluated in plain Python by `auto_straddle/nifty_ml_features.py`.
- Training and live must build features with the same code: `auto_straddle/nifty_ml_features.py`. Do not compute features anywhere else.
- "Self-learning" = scheduled rolling-window retraining (weekly, by hand, on the laptop), NOT online learning or RL. Later: champion/challenger promotion and drift monitors that pause trading.
- Trade options, not futures (changed 2026-11-15): sell the PE 100 below ATM when bullish, the CE 100 above when bearish, both when sideways.
- Bullish needs PCR > 1.4 or PCR at least 0.3 above the day's low; bearish needs PCR < 0.7 or at least 0.3 below the day's high; sideways uses the model alone.
- Exit when the signal no longer wants a leg, 15-minute cooling period after any exit, last entry at the 14:00 bar, square-off at 15:15, no stop-loss.
- Orders go through `PlaceOrder` with pending/complete status checks so the code can be reused for a real broker; the strategy refuses every account except `dummy`.
- Baselines to beat after costs: momentum, MA(50), buy & hold.
- Foundation time-series models (TimesFM, Chronos, Moirai) are NOT the plan; research shows weak gains on returns. LLMs only for news/filings sentiment features later (e.g. FinSense-ModernBERT on CPU).

## Hard rules
- Walk-forward validation only. Never random train/test splits.
- Purge/embargo around the train/test boundary because label windows overlap.
- Always include transaction costs (brokerage, STT, exchange charges, slippage). Default 4 bps per position change is a placeholder; set the real figure.
- No lookahead: features at bar t use only data available at the close of bar t; the trade earns the t -> t+1 return.
- Never place real orders without explicit approval from me. Paper trade 1-2 months first, then tiny size.
- Keep API keys in `.env`; never print or commit them.
- Check broker rules and current SEBI/NSE retail algo requirements before automating orders.
- Treat any backtest as a hypothesis, not proof. Be suspicious of results that look too good (look for leakage first).

## Status (2026-11-15)
- Research: `build_data.py` + `pipeline.py` run on real data (471 days, 2024-08-19 to 2026-11-13). No edge found for direction: calls are close to a coin flip and log-loss is worse than a no-skill forecast. An earlier "beats no-skill" result was leakage from the overnight gap in the labels.
- Sideways calls are slightly better than chance (54.2% actually sideways vs 51.4% for all bars).
- Short-straddle P&L: `straddle.py` estimates it; always selling the ATM straddle for 6 bars averaged +2.24 points gross, negative after a 3-point placeholder cost.
- Live strategy: `auto_straddle/NiftyMlStratergy.py`, wired into `AutoStraddle.py` for sheet rows with Stratergy `ml`. 115 tests pass. Uncommitted.
- Model: trained on 3,000 bars, 2025-12-02 to 2026-11-13.
- Weekly routine: `weekly_retrain.sh` (run and verified), `deploy_model.sh` (syntax-checked only, never run).
- Option P&L for the live rules has not been backtested; the stored data cannot price a held leg once ATM moves.

## Next steps
1. Commit the `auto_straddle/` files, add the sheet row (`dummy`, `NIFTY`, `ml`, quantity 1), pull on the Pi, restart.
2. First live day: confirm it runs on the Pi's Python 3.9 / pandas 1.5, and that `csv/ml_signals_*.csv` and `csv/ml_trades_*.csv` fill in.
3. Collect paper results for 1-2 months; compare against always selling the same strangle.
4. Run the weekly retrain every weekend so the option chain history has no new holes.
5. Set the real round-trip cost per option leg and report net P&L.
6. Later: champion/challenger promotion, drift monitoring, a holiday calendar for the expiry feature, more data (India VIX, futures OI and volume).

## Open questions
- Real round-trip cost per option leg (brokerage, STT, slippage)?
- Should there be a stop-loss before any real money is used?
- Exact order status strings each broker returns (needed before a real account is allowed).
