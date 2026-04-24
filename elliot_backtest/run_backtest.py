"""
===============================================================================
RUN ELLIOTT WAVE BACKTEST — NIFTY 200 (Last 10 Years)
===============================================================================

Prerequisites:
    pip install yfinance pandas numpy

Setup:
    1. Place this file in the same folder as elliott_wave_strategy.py
    2. Place ind_nifty200list.csv in the same folder
       (Download from NSE: nseindia.com → Nifty 200 → Download CSV)
    3. Run: python run_backtest.py

Note:
    Downloads ~200 stocks × 10 years of daily data. First run takes
    5-10 minutes. Falls back to Nifty 50 if CSV not found.
===============================================================================
"""

from elliott_wave_strategy import *
import pandas as pd

pd.set_option("display.max_rows", 300)
pd.set_option("display.max_columns", 20)
pd.set_option("display.width", 200)
pd.set_option("display.float_format", lambda x: f"{x:.2f}")


def main():
    # ── Configuration ────────────────────────────────────────────────────
    config = StrategyConfig(
        swing_lookback=5,
        min_swing_pct=3.0,
        wave2_retrace_min=0.382,
        wave2_retrace_max=0.786,
        wave3_ext_min=1.618,
        wave4_retrace_min=0.236,
        wave4_retrace_max=0.500,
        rsi_period=14,
        rsi_wave3_entry_min=40,
        rsi_wave3_entry_max=70,
        trailing_atr_multiplier=2.5,
        initial_capital=1_000_000,
        max_positions=10,
        risk_per_trade_pct=2.0,
        max_allocation_pct=10.0,   # 100/10 = 10% per trade
        time_stop_days=90,
    )

    # ── Load Nifty 200 symbols from CSV ────────────────────────────────
    # Place ind_nifty200list.csv in the same folder as this script.
    # Falls back to Nifty 50 if CSV not found.
    symbols = load_nifty200_from_csv("ind_nifty200list.csv")
    start_date = "2016-01-01"
    end_date = "2026-04-12"

    # ── Load Data ────────────────────────────────────────────────────────
    stock_data = load_stock_data(symbols, start=start_date, end=end_date)

    if not stock_data:
        print("Failed to load data! Check your internet connection.")
        return

    print("=" * 120)
    print(f"ELLIOTT WAVE LONG-ONLY STRATEGY — NIFTY 200 ({len(symbols)} stocks, 10 YEARS)")
    print("=" * 120)

    for sym, df in stock_data.items():
        bh_return = (df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100
        print(f"  {sym}: {len(df)} bars | "
              f"₹{df['Close'].iloc[0]:.2f} → ₹{df['Close'].iloc[-1]:.2f} "
              f"(Buy & Hold: {bh_return:.1f}%)")

    # ── Wave Analysis & Signal Generation ────────────────────────────────
    all_signals = {}

    for sym, df in stock_data.items():
        df.attrs["symbol"] = sym
        swings = detect_swing_points(df, config)
        structures = identify_wave_structures(swings, config)
        signals = generate_signals(df, structures, config)

        if signals:
            all_signals[sym] = signals

        print(f"\n{'─' * 120}")
        print(f"{sym} — WAVE ANALYSIS")
        print(f"{'─' * 120}")
        print(f"  Swing points detected:   {len(swings)}")
        print(f"  Wave structures found:   {len(structures)}")
        print(f"  Trade signals generated: {len(signals)}")
        print()

        for s in signals:
            risk = (s.entry_price - s.stop_loss) / s.entry_price * 100
            reward = (s.target_price - s.entry_price) / s.entry_price * 100
            rr = reward / risk if risk > 0 else 0
            print(f"    {s.signal_type.value:12s} | "
                  f"{s.date.strftime('%Y-%m-%d')} | "
                  f"Entry: ₹{s.entry_price:>10,.2f} | "
                  f"SL: ₹{s.stop_loss:>10,.2f} | "
                  f"Target: ₹{s.target_price:>10,.2f} | "
                  f"Risk: {risk:.1f}% | R:R {rr:.1f}x | "
                  f"RSI: {s.rsi:.1f} | "
                  f"VolRatio: {s.volume_ratio:.2f} | "
                  f"Confidence: {s.confidence:.0f}")

    # ── Backtest ─────────────────────────────────────────────────────────
    print(f"\n{'=' * 120}")
    print("RUNNING BACKTEST...")
    print("=" * 120)

    bt = Backtester(config)
    trades_df = bt.run(stock_data, all_signals)
    metrics = compute_performance_metrics(
        trades_df, bt.equity_curve, config.initial_capital,
        max_concurrent=bt.max_concurrent_positions,
        max_capital_deployed=bt.max_capital_deployed
    )

    # ── Performance Summary ──────────────────────────────────────────────
    print(f"\n{'=' * 120}")
    print("BACKTEST PERFORMANCE SUMMARY")
    print("=" * 120)

    if "error" in metrics:
        print(f"\n  {metrics['error']}")
        print("  Try adjusting parameters (wider Fibonacci ranges, smaller "
              "swing lookback, lower min_swing_pct).")
        return

    for key, val in metrics.items():
        if key != "Exit Breakdown":
            print(f"  {key:.<45} {val}")

    print(f"\n  Exit Breakdown:")
    for reason, count in metrics.get("Exit Breakdown", {}).items():
        print(f"    {reason:.<40} {count}")

    # ── Complete Trade Log ───────────────────────────────────────────────
    print(f"\n{'=' * 120}")
    print("COMPLETE TRADE LOG — ALL ENTRIES & EXITS")
    print("=" * 120)

    if not trades_df.empty:
        display_df = trades_df.copy()
        display_df["Entry_Date"] = display_df["Entry_Date"].dt.strftime("%Y-%m-%d")
        display_df["Exit_Date"] = display_df["Exit_Date"].dt.strftime("%Y-%m-%d")
        print(display_df.to_string(index=False))

        # ── Per-Stock Breakdown ──────────────────────────────────────────
        for sym in symbols:
            sym_trades = trades_df[trades_df["Symbol"] == sym]
            if sym_trades.empty:
                print(f"\n  {sym}: No trades generated")
                continue

            print(f"\n{'=' * 120}")
            print(f"{sym} — STOCK SUMMARY")
            print(f"=" * 120)

            n = len(sym_trades)
            wins = len(sym_trades[sym_trades["PnL"] > 0])
            losses = n - wins
            total_pnl = sym_trades["PnL"].sum()
            avg_pnl_pct = sym_trades["PnL_%"].mean()
            avg_days = sym_trades["Days_Held"].mean()

            best_idx = sym_trades["PnL_%"].idxmax()
            worst_idx = sym_trades["PnL_%"].idxmin()
            best = sym_trades.loc[best_idx]
            worst = sym_trades.loc[worst_idx]

            w3 = sym_trades[sym_trades["Signal"] == "Wave3_Entry"]
            w5 = sym_trades[sym_trades["Signal"] == "Wave5_Entry"]

            print(f"  Total Trades:    {n}")
            print(f"  Wins / Losses:   {wins} / {losses} "
                  f"(Win Rate: {wins / n * 100:.1f}%)")
            print(f"  Total PnL:       ₹{total_pnl:>12,.2f}")
            print(f"  Avg PnL%:        {avg_pnl_pct:>8.2f}%")
            print(f"  Avg Holding:     {avg_days:.1f} days")
            print(f"  Best Trade:      {best['PnL_%']:.2f}% "
                  f"(entered {best['Entry_Date'].strftime('%Y-%m-%d')})")
            print(f"  Worst Trade:     {worst['PnL_%']:.2f}% "
                  f"(entered {worst['Entry_Date'].strftime('%Y-%m-%d')})")
            print(f"  Wave 3 Entries:  {len(w3)} trades, "
                  f"WR {len(w3[w3['PnL'] > 0]) / len(w3) * 100:.1f}%" if len(w3) > 0 else "  Wave 3 Entries:  0")
            print(f"  Wave 5 Entries:  {len(w5)} trades, "
                  f"WR {len(w5[w5['PnL'] > 0]) / len(w5) * 100:.1f}%" if len(w5) > 0 else "  Wave 5 Entries:  0")

            # Exit reason breakdown per stock
            print(f"  Exit Reasons:    {sym_trades['Exit_Reason'].value_counts().to_dict()}")

    # ── Monthly Equity Curve ─────────────────────────────────────────────
    if bt.equity_curve:
        eq_df = pd.DataFrame(bt.equity_curve)
        eq_df.set_index("date", inplace=True)

        # ── Concurrent Positions Over Time ───────────────────────────────
        print(f"\n{'=' * 120}")
        print("CONCURRENT POSITIONS ANALYSIS")
        print("=" * 120)
        print(f"  Max Concurrent Positions:     {bt.max_concurrent_positions}")
        print(f"  Max Capital Deployed:         ₹{bt.max_capital_deployed:>14,.2f}")
        print(f"  Avg Open Positions:           {eq_df['positions'].mean():.1f}")
        print(f"  Avg Capital Deployed:         ₹{eq_df['capital_deployed'].mean():>14,.2f}")

        avg_util = eq_df['capital_deployed'].mean() / eq_df['equity'].mean() * 100
        print(f"  Avg Capital Utilization:      {avg_util:.1f}%")

        total_pnl = eq_df["equity"].iloc[-1] - config.initial_capital
        days = (eq_df.index[-1] - eq_df.index[0]).days
        years = days / 365.25 if days > 0 else 1

        print(f"\n  ── FUND ALLOCATION INSIGHT ──")
        print(f"  Starting Capital:             ₹{config.initial_capital:>14,.0f}")
        print(f"  Peak Capital Needed:          ₹{bt.max_capital_deployed:>14,.0f}")
        if bt.max_capital_deployed > 0:
            roc_max = total_pnl / bt.max_capital_deployed / years * 100
            roc_avg = total_pnl / eq_df['capital_deployed'].mean() / years * 100 if eq_df['capital_deployed'].mean() > 0 else 0
            cagr_full = ((eq_df['equity'].iloc[-1] / config.initial_capital) ** (1 / years) - 1) * 100
            print(f"  CAGR (on full capital):       {cagr_full:>13.2f}%")
            print(f"  CAGR (if fund = peak deploy): {roc_max:>13.2f}%  ← size your fund to this")
            print(f"  CAGR (on avg deployed):       {roc_avg:>13.2f}%  ← true capital efficiency")

        # Monthly positions heatmap
        monthly_max_pos = eq_df["positions"].resample("M").max()
        monthly_avg_deployed = eq_df["capital_deployed"].resample("M").mean()

        print(f"\n{'=' * 120}")
        print("MONTHLY EQUITY CURVE")
        print("=" * 120)
        print(f"  {'Month':>7} | {'Equity':>14} |  {'Chg%':>6} | "
              f"{'Pos':>3} | {'Capital Deployed':>16} | ")
        print(f"  {'─' * 7} | {'─' * 14} | {'─' * 7} | "
              f"{'─' * 3} | {'─' * 16} | {'─' * 30}")

        monthly = eq_df.resample("M").last()
        monthly["return_%"] = monthly["equity"].pct_change() * 100

        for idx, row in monthly.iterrows():
            ratio = row["equity"] / config.initial_capital
            bar_len = max(1, int(ratio * 30))
            bar = "█" * bar_len
            chg = f"{row['return_%']:>+6.1f}%" if not pd.isna(row["return_%"]) else "       "
            deployed = row.get("capital_deployed", 0)
            print(f"  {idx.strftime('%Y-%m')} | "
                  f"₹{row['equity']:>13,.2f} | "
                  f"{chg} | "
                  f"{int(row['positions']):>3} | "
                  f"₹{deployed:>15,.2f} | {bar}")

    # ── Signal Tracking (at the end so it's easy to find) ─────────────
    print(f"\n{'=' * 120}")
    print("SIGNAL TRACKING — TAKEN vs IGNORED")
    print("=" * 120)
    total_sigs = bt.total_signals_received
    taken = bt.signals_taken
    skipped_max = bt.signals_skipped_max_positions
    skipped_dup = bt.signals_skipped_already_in_stock
    skipped_cash = bt.signals_skipped_no_cash
    total_skipped = skipped_max + skipped_dup + skipped_cash

    print(f"  Total Signals Generated:       {total_sigs}")
    if total_sigs > 0:
        print(f"  Signals Taken (traded):        {taken} "
              f"({taken / total_sigs * 100:.1f}%)")
        print(f"  Signals Ignored (total):       {total_skipped} "
              f"({total_skipped / total_sigs * 100:.1f}%)")
    else:
        print(f"  Signals Taken (traded):        {taken}")
        print(f"  Signals Ignored (total):       {total_skipped}")
    print(f"    ├─ Max Positions Reached:     {skipped_max}")
    print(f"    ├─ Already In Stock:          {skipped_dup}")
    print(f"    └─ Insufficient Cash:         {skipped_cash}")

    if bt.skipped_signals_log:
        skipped_df = pd.DataFrame(bt.skipped_signals_log)
        print(f"\n  Ignored Signals by Year:")
        skipped_df["year"] = pd.to_datetime(skipped_df["date"]).dt.year
        yearly = skipped_df.groupby(["year", "reason"]).size().unstack(fill_value=0)
        print(yearly.to_string())

        print(f"\n  Most Frequently Ignored Stocks (Top 10):")
        top_ignored = skipped_df["symbol"].value_counts().head(10)
        for sym, count in top_ignored.items():
            print(f"    {sym:.<30} {count} signals ignored")

    print(f"\n{'=' * 120}")
    print("DONE")
    print("=" * 120)


if __name__ == "__main__":
    main()
