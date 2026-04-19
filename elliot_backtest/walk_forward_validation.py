"""
===============================================================================
WALK-FORWARD VALIDATION
===============================================================================

Splits the 10-year data into two halves:
    - In-Sample (IS):      2016-01-01 to 2020-12-31  (5 years, used for finding parameters)
    - Out-of-Sample (OOS): 2021-01-01 to 2026-04-12  (5+ years, used to validate)

Compares performance across both periods to check if the strategy has
genuine edge or is overfit.

Key validation test:
    - If IS and OOS results are similar → strategy is robust
    - If OOS is much worse than IS → strategy is overfit

Usage:
    python walk_forward_validation.py
===============================================================================
"""

from elliott_wave_strategy import *
import pandas as pd

pd.set_option("display.max_columns", 30)
pd.set_option("display.width", 200)
pd.set_option("display.float_format", lambda x: f"{x:.2f}")


def run_period_backtest(stock_data_all: dict, config: StrategyConfig,
                        start: str, end: str, period_name: str) -> dict:
    """
    Run backtest on a slice of the data for a specific period.

    Args:
        stock_data_all: Full dataset
        config: StrategyConfig
        start: Period start date (e.g., "2016-01-01")
        end: Period end date (e.g., "2020-12-31")
        period_name: Label for output

    Returns: dict of results
    """
    print(f"\n{'=' * 100}")
    print(f"{period_name}: {start} to {end}")
    print('=' * 100)

    # Slice each stock's data to the period
    period_data = {}
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)

    for sym, df in stock_data_all.items():
        df_slice = df.loc[(df.index >= start_ts) & (df.index <= end_ts)].copy()
        if len(df_slice) > 100:
            df_slice.attrs["symbol"] = sym
            period_data[sym] = df_slice

    print(f"  Stocks with sufficient data in this period: {len(period_data)}")

    # Detect waves and generate signals for each stock
    all_signals = {}
    total_structures = 0
    total_signals = 0

    for sym, df in period_data.items():
        df.attrs["symbol"] = sym
        swings = detect_swing_points(df, config)
        structures = identify_wave_structures(swings, config)
        signals = generate_signals(df, structures, config)
        total_structures += len(structures)
        total_signals += len(signals)
        if signals:
            all_signals[sym] = signals

    print(f"  Wave structures found: {total_structures}")
    print(f"  Signals generated:     {total_signals}")

    # Backtest
    bt = Backtester(config)
    trades_df = bt.run(period_data, all_signals)

    if trades_df.empty:
        print(f"  NO TRADES in {period_name}!")
        return None

    metrics = compute_performance_metrics(
        trades_df, bt.equity_curve, config.initial_capital,
        max_concurrent=bt.max_concurrent_positions,
        max_capital_deployed=bt.max_capital_deployed
    )

    eq_df = pd.DataFrame(bt.equity_curve)
    eq_df.set_index("date", inplace=True)
    final_equity = eq_df["equity"].iloc[-1]
    days = (eq_df.index[-1] - eq_df.index[0]).days
    years = days / 365.25

    # Wave 3 vs Wave 5 split
    w3 = trades_df[trades_df["Signal"] == "Wave3_Entry"]
    w5 = trades_df[trades_df["Signal"] == "Wave5_Entry"]

    result = {
        "period": period_name,
        "start": start,
        "end": end,
        "years": round(years, 2),
        "stocks_with_data": len(period_data),
        "wave_structures": total_structures,
        "signals_generated": total_signals,
        "signals_taken": bt.signals_taken,
        "signals_ignored": bt.signals_skipped_max_positions + bt.signals_skipped_already_in_stock,
        "total_trades": len(trades_df),
        "wave3_trades": len(w3),
        "wave5_trades": len(w5),
        "win_rate_pct": metrics["Win Rate (%)"],
        "wave3_win_rate_pct": metrics.get("Wave 3 Win Rate (%)", 0),
        "wave5_win_rate_pct": metrics.get("Wave 5 Win Rate (%)", 0),
        "avg_win_pct": metrics["Avg Win (%)"],
        "avg_loss_pct": metrics["Avg Loss (%)"],
        "expectancy_pct": metrics["Expectancy per Trade (%)"],
        "profit_factor": metrics["Profit Factor"],
        "total_return_pct": metrics["Total Return (%)"],
        "cagr_pct": metrics["CAGR on Full Capital (%)"],
        "sharpe": metrics["Sharpe Ratio (approx)"],
        "max_drawdown_pct": metrics["Max Drawdown (%)"],
        "calmar": metrics["Calmar Ratio"],
        "avg_days_held": metrics["Avg Days Held"],
        "max_concurrent_pos": bt.max_concurrent_positions,
        "avg_open_pos": round(eq_df["positions"].mean(), 1),
        "max_capital_deployed": bt.max_capital_deployed,
        "avg_utilization_pct": round(
            eq_df["capital_deployed"].mean() / eq_df["equity"].mean() * 100, 1
        ),
        "final_equity": round(final_equity, 2),
    }

    # Print summary
    print(f"\n  ── RESULTS ──")
    print(f"  Total trades:     {result['total_trades']}")
    print(f"  Win rate:         {result['win_rate_pct']:.2f}%")
    print(f"  Avg win / loss:   +{result['avg_win_pct']:.2f}% / {result['avg_loss_pct']:.2f}%")
    print(f"  Expectancy/trade: {result['expectancy_pct']:.2f}%")
    print(f"  Profit factor:    {result['profit_factor']:.2f}")
    print(f"  CAGR:             {result['cagr_pct']:.2f}%")
    print(f"  Sharpe ratio:     {result['sharpe']:.2f}")
    print(f"  Max drawdown:     {result['max_drawdown_pct']:.2f}%")
    print(f"  Calmar ratio:     {result['calmar']:.2f}")
    print(f"  Final equity:     ₹{result['final_equity']:,.2f}")

    return result


def main():
    print("=" * 100)
    print("WALK-FORWARD VALIDATION")
    print("=" * 100)

    # ── Configuration (the "best CAGR" profile from optimization) ────────
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
        max_allocation_pct=10.0,
        time_stop_days=90,
        transaction_cost_per_trade=60.0,
    )

    print(f"\n  Config: swing={config.swing_lookback} min_pct={config.min_swing_pct} "
          f"RSI={config.rsi_wave3_entry_min}-{config.rsi_wave3_entry_max} "
          f"time={config.time_stop_days}d pos={config.max_positions}")
    print(f"  Transaction cost: ₹{config.transaction_cost_per_trade:.0f} per trade "
          f"(entry + exit = ₹{config.transaction_cost_per_trade * 2:.0f} round-trip)")

    # ── Load ALL data once ───────────────────────────────────────────────
    print(f"\n  Loading full data set (2016-01-01 to 2026-04-12)...")
    symbols = load_nifty200_from_csv("ind_nifty200list.csv")
    stock_data = load_stock_data(symbols, start="2016-01-01", end="2026-04-12")

    if not stock_data:
        print("ERROR: No data loaded. Exiting.")
        return

    # ── Run In-Sample Period ─────────────────────────────────────────────
    is_result = run_period_backtest(
        stock_data, config,
        start="2016-01-01", end="2020-12-31",
        period_name="IN-SAMPLE (2016-2020)"
    )

    # ── Run Out-of-Sample Period ─────────────────────────────────────────
    oos_result = run_period_backtest(
        stock_data, config,
        start="2021-01-01", end="2026-04-12",
        period_name="OUT-OF-SAMPLE (2021-2026)"
    )

    # ── Run Full Period (reference) ──────────────────────────────────────
    full_result = run_period_backtest(
        stock_data, config,
        start="2016-01-01", end="2026-04-12",
        period_name="FULL PERIOD (reference)"
    )

    # ── Side-by-side Comparison ──────────────────────────────────────────
    if is_result and oos_result:
        print(f"\n{'=' * 100}")
        print("SIDE-BY-SIDE COMPARISON")
        print('=' * 100)

        metrics_to_compare = [
            ("CAGR (%)",              "cagr_pct",            "{:.2f}"),
            ("Total Return (%)",      "total_return_pct",    "{:.2f}"),
            ("Win Rate (%)",          "win_rate_pct",        "{:.2f}"),
            ("Profit Factor",         "profit_factor",       "{:.2f}"),
            ("Expectancy (%)",        "expectancy_pct",      "{:.2f}"),
            ("Sharpe Ratio",          "sharpe",              "{:.2f}"),
            ("Max Drawdown (%)",      "max_drawdown_pct",    "{:.2f}"),
            ("Calmar Ratio",          "calmar",              "{:.2f}"),
            ("Total Trades",          "total_trades",        "{}"),
            ("Avg Days Held",         "avg_days_held",       "{:.1f}"),
            ("Wave 3 WR (%)",         "wave3_win_rate_pct",  "{:.1f}"),
            ("Wave 5 WR (%)",         "wave5_win_rate_pct",  "{:.1f}"),
            ("Max Concurrent",        "max_concurrent_pos",  "{}"),
            ("Avg Utilization (%)",   "avg_utilization_pct", "{:.1f}"),
        ]

        print(f"\n  {'Metric':<25} {'IN-SAMPLE':>15} {'OUT-OF-SAMPLE':>17} {'DIFF':>12}")
        print(f"  {'-' * 25} {'-' * 15} {'-' * 17} {'-' * 12}")
        for label, key, fmt in metrics_to_compare:
            is_val = is_result[key]
            oos_val = oos_result[key]
            is_str = fmt.format(is_val)
            oos_str = fmt.format(oos_val)

            if isinstance(is_val, (int, float)) and isinstance(oos_val, (int, float)):
                if isinstance(is_val, int) and isinstance(oos_val, int):
                    diff = oos_val - is_val
                    diff_str = f"{diff:+d}"
                else:
                    diff = oos_val - is_val
                    diff_str = f"{diff:+.2f}"
            else:
                diff_str = "-"

            print(f"  {label:<25} {is_str:>15} {oos_str:>17} {diff_str:>12}")

        # ── Verdict ──────────────────────────────────────────────────────
        print(f"\n{'=' * 100}")
        print("VERDICT")
        print('=' * 100)

        cagr_drop = is_result["cagr_pct"] - oos_result["cagr_pct"]
        wr_drop = is_result["win_rate_pct"] - oos_result["win_rate_pct"]
        pf_drop = is_result["profit_factor"] - oos_result["profit_factor"]

        print(f"\n  CAGR dropped from IS to OOS by:       {cagr_drop:+.2f}% points")
        print(f"  Win Rate dropped from IS to OOS by:   {wr_drop:+.2f}% points")
        print(f"  Profit Factor dropped from IS to OOS: {pf_drop:+.2f}")

        # Interpret
        print(f"\n  Interpretation:")
        if oos_result["cagr_pct"] > 7 and oos_result["profit_factor"] > 1.2:
            print(f"    ✓ Strategy REMAINS PROFITABLE out of sample — robust edge")
        elif oos_result["cagr_pct"] > 0 and oos_result["profit_factor"] > 1.0:
            print(f"    ⚠ Strategy is MARGINALLY PROFITABLE out of sample — weak edge")
        else:
            print(f"    ✗ Strategy FAILED out of sample — likely overfit")

        if abs(cagr_drop) < 5:
            print(f"    ✓ CAGR is consistent across periods (diff < 5%)")
        elif cagr_drop > 10:
            print(f"    ✗ Large CAGR degradation OOS — warning sign of overfit")

        if oos_result["max_drawdown_pct"] < is_result["max_drawdown_pct"] * 1.5:
            print(f"    ✓ Drawdown behavior similar across periods")
        else:
            print(f"    ⚠ Drawdown worsened significantly OOS")

    # ── Save results ─────────────────────────────────────────────────────
    all_results = [r for r in [is_result, oos_result, full_result] if r]
    if all_results:
        results_df = pd.DataFrame(all_results)
        results_df.to_csv("walk_forward_results.csv", index=False)
        print(f"\n  Full results saved to: walk_forward_results.csv")

    print(f"\n{'=' * 100}")
    print("DONE")
    print('=' * 100)


if __name__ == "__main__":
    main()
