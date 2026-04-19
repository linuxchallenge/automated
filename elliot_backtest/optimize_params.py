"""
===============================================================================
ELLIOTT WAVE STRATEGY — PARAMETER OPTIMIZATION
===============================================================================

Tests all parameter combinations and saves results to CSV.

Setup:
    1. Place in same folder as elliott_wave_strategy.py and ind_nifty200list.csv
    2. Run: python optimize_params.py

Output:
    optimization_results.csv — one row per parameter combination with
    all performance metrics.

Note:
    - Downloads data ONCE, then reuses for all parameter combos
    - Total combinations: 2 × 3 × 3 × 3 × 2 = 108 combos
    - Estimated time: 30-60 minutes depending on your machine
===============================================================================
"""

import sys
import itertools
import time
from datetime import datetime
from elliott_wave_strategy import (
    StrategyConfig, Backtester, load_nifty200_from_csv, load_stock_data,
    detect_swing_points, identify_wave_structures, generate_signals,
    compute_performance_metrics
)
import pandas as pd

pd.set_option("display.max_columns", 30)
pd.set_option("display.width", 200)


def run_single_backtest(stock_data, config):
    """
    Run a single backtest with given config on pre-loaded data.
    Returns a dict of results.
    """
    # Detect waves and generate signals for each stock
    all_signals = {}
    total_structures = 0
    total_signals = 0

    for sym, df in stock_data.items():
        df.attrs["symbol"] = sym
        swings = detect_swing_points(df, config)
        structures = identify_wave_structures(swings, config)
        signals = generate_signals(df, structures, config)
        total_structures += len(structures)
        total_signals += len(signals)
        if signals:
            all_signals[sym] = signals

    # Backtest
    bt = Backtester(config)
    trades_df = bt.run(stock_data, all_signals)

    # Compute metrics
    metrics = compute_performance_metrics(
        trades_df, bt.equity_curve, config.initial_capital,
        max_concurrent=bt.max_concurrent_positions,
        max_capital_deployed=bt.max_capital_deployed
    )

    if "error" in metrics:
        return None

    # Extract equity curve stats
    eq_df = pd.DataFrame(bt.equity_curve)
    eq_df.set_index("date", inplace=True)
    final_equity = eq_df["equity"].iloc[-1] if len(eq_df) > 0 else config.initial_capital
    days = (eq_df.index[-1] - eq_df.index[0]).days if len(eq_df) > 1 else 1
    years = days / 365.25

    # Build result row
    result = {
        # Parameters
        "max_positions": config.max_positions,
        "max_allocation_pct": round(config.max_allocation_pct, 2),
        "swing_lookback": config.swing_lookback,
        "min_swing_pct": config.min_swing_pct,
        "rsi_wave3_min": config.rsi_wave3_entry_min,
        "rsi_wave3_max": config.rsi_wave3_entry_max,
        "time_stop_days": config.time_stop_days,

        # Signal stats
        "wave_structures_found": total_structures,
        "total_signals_generated": total_signals,
        "signals_taken": bt.signals_taken,
        "signals_skipped_max_pos": bt.signals_skipped_max_positions,
        "signals_skipped_already_in": bt.signals_skipped_already_in_stock,
        "signals_skipped_no_cash": bt.signals_skipped_no_cash,
        "signal_take_rate_pct": round(
            bt.signals_taken / bt.total_signals_received * 100, 1
        ) if bt.total_signals_received > 0 else 0,

        # Trade stats
        "total_trades": len(trades_df) if not trades_df.empty else 0,
        "win_rate_pct": metrics.get("Win Rate (%)", 0),
        "avg_win_pct": metrics.get("Avg Win (%)", 0),
        "avg_loss_pct": metrics.get("Avg Loss (%)", 0),
        "expectancy_pct": metrics.get("Expectancy per Trade (%)", 0),
        "profit_factor": metrics.get("Profit Factor", 0),
        "max_consec_wins": metrics.get("Max Consecutive Wins", 0),
        "max_consec_losses": metrics.get("Max Consecutive Losses", 0),
        "avg_days_held": metrics.get("Avg Days Held", 0),

        # Returns
        "total_return_pct": metrics.get("Total Return (%)", 0),
        "cagr_full_capital_pct": metrics.get("CAGR on Full Capital (%)", 0),
        "sharpe_ratio": metrics.get("Sharpe Ratio (approx)", 0),
        "max_drawdown_pct": metrics.get("Max Drawdown (%)", 0),
        "calmar_ratio": metrics.get("Calmar Ratio", 0),
        "final_equity": final_equity,

        # Capital deployment
        "max_concurrent_positions": bt.max_concurrent_positions,
        "avg_open_positions": round(eq_df["positions"].mean(), 1) if len(eq_df) > 0 else 0,
        "max_capital_deployed": bt.max_capital_deployed,
        "avg_capital_deployed": round(eq_df["capital_deployed"].mean(), 2) if len(eq_df) > 0 else 0,
        "avg_utilization_pct": round(
            eq_df["capital_deployed"].mean() / eq_df["equity"].mean() * 100, 1
        ) if len(eq_df) > 0 and eq_df["equity"].mean() > 0 else 0,
        "cagr_on_max_deployed_pct": metrics.get("CAGR if fund = Max Deployed (%)", 0),
        "cagr_on_avg_deployed_pct": metrics.get("CAGR if fund = Avg Deployed (%)", 0),

        # Wave breakdown
        "wave3_trades": metrics.get("Wave 3 Trades", 0),
        "wave3_win_rate_pct": metrics.get("Wave 3 Win Rate (%)", 0),
        "wave5_trades": metrics.get("Wave 5 Trades", 0),
        "wave5_win_rate_pct": metrics.get("Wave 5 Win Rate (%)", 0),
    }

    return result


def main():
    print("=" * 100)
    print("ELLIOTT WAVE STRATEGY — PARAMETER OPTIMIZATION")
    print("=" * 100)

    # ── Define Parameter Grid ────────────────────────────────────────────
    param_grid = {
        "max_positions":    [10, 15],
        "swing_lookback":   [5, 10, 20],
        "min_swing_pct":    [2.0, 3.0, 5.0],
        "rsi_wave3_range":  [(40, 65), (35, 60), (40, 70)],
        "time_stop_days":   [65, 90],
    }

    # Calculate total combinations
    total_combos = 1
    for values in param_grid.values():
        total_combos *= len(values)

    print(f"\n  Parameter Grid:")
    for param, values in param_grid.items():
        print(f"    {param}: {values}")
    print(f"\n  Total Combinations: {total_combos}")

    # ── Fixed parameters (not being optimized) ───────────────────────────
    print(f"\n  Fixed Parameters:")
    print(f"    wave2_retrace: 0.382–0.786")
    print(f"    wave4_retrace: 0.236–0.500")
    print(f"    wave3_ext_min: 1.618")
    print(f"    volume_expansion_factor: 1.1")
    print(f"    breakout_bars: 3")
    print(f"    trailing_atr_multiplier: 2.5")
    print(f"    wave3_target_extension: 1.618")
    print(f"    wave5_target_extension: 0.786")
    print(f"    initial_capital: 1,000,000")

    # ── Load Data ONCE ───────────────────────────────────────────────────
    print(f"\n{'─' * 100}")
    print("STEP 1: Loading stock data (one-time download)...")
    print(f"{'─' * 100}")

    symbols = load_nifty200_from_csv("ind_nifty200list.csv")
    stock_data = load_stock_data(symbols, start="2016-01-01", end="2026-04-12")

    if not stock_data:
        print("ERROR: No data loaded. Exiting.")
        return

    print(f"\n  Loaded {len(stock_data)} stocks successfully.")

    # ── Run All Combinations ─────────────────────────────────────────────
    print(f"\n{'─' * 100}")
    print(f"STEP 2: Running {total_combos} parameter combinations...")
    print(f"{'─' * 100}\n")

    results = []
    combo_num = 0
    start_time = time.time()

    for (max_pos, swing_lb, min_swing, rsi_range, time_stop) in itertools.product(
        param_grid["max_positions"],
        param_grid["swing_lookback"],
        param_grid["min_swing_pct"],
        param_grid["rsi_wave3_range"],
        param_grid["time_stop_days"],
    ):
        combo_num += 1
        alloc_pct = round(100.0 / max_pos, 2)
        rsi_min, rsi_max = rsi_range

        config = StrategyConfig(
            swing_lookback=swing_lb,
            min_swing_pct=min_swing,
            wave2_retrace_min=0.382,
            wave2_retrace_max=0.786,
            wave3_ext_min=1.618,
            wave4_retrace_min=0.236,
            wave4_retrace_max=0.500,
            rsi_period=14,
            rsi_wave3_entry_min=rsi_min,
            rsi_wave3_entry_max=rsi_max,
            rsi_wave5_entry_min=45,
            rsi_wave5_entry_max=70,
            volume_expansion_factor=1.1,
            breakout_bars=3,
            trailing_atr_multiplier=2.5,
            time_stop_days=time_stop,
            wave3_target_extension=1.618,
            wave5_target_extension=0.786,
            initial_capital=1_000_000,
            max_positions=max_pos,
            max_allocation_pct=alloc_pct,
            risk_per_trade_pct=2.0,
        )

        # Progress
        elapsed = time.time() - start_time
        avg_per_combo = elapsed / combo_num if combo_num > 1 else 0
        remaining = avg_per_combo * (total_combos - combo_num)

        print(f"  [{combo_num:>3}/{total_combos}] "
              f"pos={max_pos:>2} swing={swing_lb:>2} min_pct={min_swing:.1f} "
              f"rsi={rsi_min:.0f}-{rsi_max:.0f} time={time_stop:>2}d "
              f"... ", end="", flush=True)

        try:
            result = run_single_backtest(stock_data, config)
            if result:
                results.append(result)
                print(f"CAGR={result['cagr_full_capital_pct']:>6.2f}% "
                      f"WR={result['win_rate_pct']:>5.1f}% "
                      f"DD={result['max_drawdown_pct']:>6.2f}% "
                      f"Trades={result['total_trades']:>3} "
                      f"Taken={result['signal_take_rate_pct']:>5.1f}% "
                      f"[ETA: {remaining/60:.0f}m]")
            else:
                print(f"NO TRADES — skipped")
        except Exception as e:
            print(f"ERROR: {e}")

    # ── Save Results ─────────────────────────────────────────────────────
    print(f"\n{'─' * 100}")
    print("STEP 3: Saving results...")
    print(f"{'─' * 100}")

    if not results:
        print("  No results to save!")
        return

    results_df = pd.DataFrame(results)

    # Sort by CAGR descending
    results_df = results_df.sort_values("cagr_full_capital_pct", ascending=False)
    results_df.to_csv("optimization_results.csv", index=False)

    print(f"\n  Saved {len(results_df)} results to: optimization_results.csv")

    # ── Summary ──────────────────────────────────────────────────────────
    total_time = time.time() - start_time
    print(f"\n{'=' * 100}")
    print("OPTIMIZATION COMPLETE")
    print(f"{'=' * 100}")
    print(f"  Total Time:          {total_time/60:.1f} minutes")
    print(f"  Combos Tested:       {len(results)}")
    print(f"  Combos Failed:       {total_combos - len(results)}")

    # Top 10
    print(f"\n{'─' * 100}")
    print("TOP 10 PARAMETER COMBINATIONS (by CAGR)")
    print(f"{'─' * 100}")

    top10 = results_df.head(10)
    display_cols = [
        "max_positions", "swing_lookback", "min_swing_pct",
        "rsi_wave3_min", "rsi_wave3_max", "time_stop_days",
        "total_trades", "win_rate_pct", "cagr_full_capital_pct",
        "max_drawdown_pct", "sharpe_ratio", "profit_factor",
        "signal_take_rate_pct", "avg_open_positions",
    ]
    print(top10[display_cols].to_string(index=False))

    # Bottom 5
    print(f"\n{'─' * 100}")
    print("BOTTOM 5 (worst performing)")
    print(f"{'─' * 100}")
    bottom5 = results_df.tail(5)
    print(bottom5[display_cols].to_string(index=False))

    # Best by different metrics
    print(f"\n{'─' * 100}")
    print("BEST BY DIFFERENT METRICS")
    print(f"{'─' * 100}")

    best_cagr = results_df.loc[results_df["cagr_full_capital_pct"].idxmax()]
    best_sharpe = results_df.loc[results_df["sharpe_ratio"].idxmax()]
    best_winrate = results_df.loc[results_df["win_rate_pct"].idxmax()]
    best_calmar = results_df.loc[results_df["calmar_ratio"].idxmax()]
    least_dd = results_df.loc[results_df["max_drawdown_pct"].idxmax()]  # least negative

    for label, row in [
        ("Best CAGR", best_cagr),
        ("Best Sharpe", best_sharpe),
        ("Best Win Rate", best_winrate),
        ("Best Calmar", best_calmar),
        ("Least Drawdown", least_dd),
    ]:
        print(f"\n  {label}:")
        print(f"    Params: pos={int(row['max_positions'])} "
              f"swing={int(row['swing_lookback'])} "
              f"min_pct={row['min_swing_pct']} "
              f"rsi={int(row['rsi_wave3_min'])}-{int(row['rsi_wave3_max'])} "
              f"time={int(row['time_stop_days'])}d")
        print(f"    CAGR={row['cagr_full_capital_pct']:.2f}% "
              f"WR={row['win_rate_pct']:.1f}% "
              f"DD={row['max_drawdown_pct']:.2f}% "
              f"Sharpe={row['sharpe_ratio']:.2f} "
              f"PF={row['profit_factor']:.2f} "
              f"Trades={int(row['total_trades'])}")

    print(f"\n{'=' * 100}")
    print(f"Full results saved to: optimization_results.csv")
    print(f"{'=' * 100}")


if __name__ == "__main__":
    main()
