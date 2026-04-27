"""
Elliott Wave: Backtest vs Actual Live Code -- 10-Year Trade Comparison
=====================================================================

Feeds the SAME real Nifty 200 OHLCV data (from elliot_backtest/.cache/) to:
  1. The Backtester engine  (elliot_wave_strategy.py)  -> 1470 trades, 15% CAGR
  2. The ACTUAL ElliotCashStratergy live code (elliot_cash_stratergy.py)
     driven day-by-day with mocked broker/NSE/Telegram

Then compares every trade: entry date, exit date, exit reason, P&L.

No future data leakage: on simulated date D, only OHLCV up to D is available.

Matching backtest behaviour:
  - Exits are PENDING: triggered on day D, executed at day D+1's Open
    (except Time_Stop which executes at today's Close)
  - LTP is set to bar Low for SL/trailing checks, High for target checks
    (matching backtest's intraday Low/High exit detection)
  - days_held counts trading days (not calendar days)
  - ATR uses full history up to current date (matching backtest)
"""

import sys
import os
import tempfile
from datetime import datetime as _real_datetime, timedelta
from unittest.mock import MagicMock
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# -- Mock external modules BEFORE importing live code --
for _mod in ('TelegramSend', 'configuration', 'exchange_state', 'brokrage_calculator'):
    sys.modules.setdefault(_mod, MagicMock())

from elliot_wave_strategy import (
    Backtester, StrategyConfig, TradeSignal, TradeResult, Position,
    SignalType, WaveType, SwingPoint, WaveStructure,
    compute_atr, compute_rsi, generate_signals,
    detect_swing_points, identify_wave_structures,
    load_stock_data, load_nifty200_from_csv,
)
from elliot_cash_stratergy import ElliotCashStratergy
import elliot_cash_stratergy as _ecs_module


# =============================================================================
# DATA LOADING -- real Nifty 200 cached OHLCV from the backtest run
# =============================================================================

CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'elliot_backtest', '.cache'
)

NIFTY200_CSV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'elliot_backtest', 'ind_nifty200list.csv'
)


def _load_real_stock_data():
    """Load real OHLCV from elliot_backtest/.cache/ (same data the backtest used).

    Falls back to load_stock_data() with yfinance if cache not found.
    """
    start_date = "2016-01-01"
    end_date = "2026-04-12"

    if not os.path.isdir(CACHE_DIR):
        pytest.skip(
            f"Cache directory not found: {CACHE_DIR}\n"
            f"Run elliot_backtest/run_backtest.py first to download data."
        )

    # Load symbols
    if os.path.exists(NIFTY200_CSV):
        csv_df = pd.read_csv(NIFTY200_CSV)
        symbols = [f"{sym.strip()}.NS" for sym in csv_df["Symbol"].dropna()]
    else:
        symbols = load_nifty200_from_csv()

    stock_data = {}
    for sym in symbols:
        safe_sym = sym.replace("&", "_AND_")
        cache_file = os.path.join(
            CACHE_DIR, f"{safe_sym}_{start_date}_{end_date}.csv"
        )
        if not os.path.exists(cache_file):
            continue
        try:
            df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
            if len(df) > 100:
                df.attrs["symbol"] = sym
                stock_data[sym] = df
        except Exception:
            continue

    if not stock_data:
        pytest.skip("No cached stock data files found")

    return stock_data


# =============================================================================
# BACKTEST WRAPPER
# =============================================================================

def _backtest_config():
    """Same config as run_backtest.py"""
    return StrategyConfig(
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
        max_allocation_pct=10.0,
        time_stop_days=90,
    )


def _run_backtest(stock_data, config):
    """Run wave analysis + backtest. Returns (Backtester, all_signals dict)."""
    all_signals = {}
    for sym, df in stock_data.items():
        df.attrs["symbol"] = sym
        swings = detect_swing_points(df, config)
        structures = identify_wave_structures(swings, config)
        sigs = generate_signals(df, structures, config)
        if sigs:
            all_signals[sym] = sigs

    bt = Backtester(config)
    bt.run(stock_data, all_signals)
    return bt, all_signals


def _save_trades_csv(bt, filepath):
    """Save backtest trades to CSV."""
    records = []
    for t in bt.trades:
        records.append({
            "symbol": t.symbol,
            "signal_type": t.signal_type.value,
            "entry_date": t.entry_date,
            "exit_date": t.exit_date,
            "entry_price": round(t.entry_price, 2),
            "exit_price": round(t.exit_price, 2),
            "shares": t.shares,
            "pnl": round(t.pnl, 2),
            "pnl_pct": round(t.pnl_pct, 2),
            "exit_reason": t.exit_reason,
            "days_held": t.days_held,
        })
    df = pd.DataFrame(records)
    df.to_csv(filepath, index=False)
    return df


def _compute_cagr(equity_curve, initial_capital):
    if not equity_curve:
        return 0.0
    first = equity_curve[0]["date"]
    last = equity_curve[-1]["date"]
    final_eq = equity_curve[-1]["equity"]
    years = (last - first).days / 365.25
    if years <= 0 or final_eq <= 0:
        return 0.0
    return (final_eq / initial_capital) ** (1 / years) - 1


# =============================================================================
# LIVE CODE REPLAY HARNESS
# =============================================================================

class _MockDatetime(_real_datetime):
    """datetime subclass with controllable now()."""
    _fake_now = None

    @classmethod
    def now(cls, tz=None):
        if cls._fake_now is not None:
            return cls._fake_now
        return _real_datetime.now(tz)


class LiveCodeReplayHarness:
    """
    Drives the ACTUAL ElliotCashStratergy code through historical OHLCV
    day by day with mocked external dependencies.

    Matching backtest behaviour:
      - No future data: on date D only bars up to D are available
      - Uses actual _process_open_positions() for exit logic
      - Smart LTP: Low for SL/trailing checks, High for target, Close otherwise
      - Pending exit: triggered exits execute at NEXT day's Open (like backtest)
      - Trading-day counting for days_held (not calendar days)
      - Entries injected to match backtest (same date, price, shares, SL, target)
    """

    def __init__(self, stock_data, config):
        self.stock_data = stock_data
        self.config = config
        self._sim_date = None
        self._ltp = {}
        self._exit_log = {}

        self._pass_mode = 'stops'  # 'stops', 'target', 'time'
        self._tmp_dir = tempfile.mkdtemp()
        self._csv_path = os.path.join(self._tmp_dir, 'ew_live_replay.csv')

        # Create ElliotCashStratergy bypassing __init__
        self.ecs = ElliotCashStratergy.__new__(ElliotCashStratergy)
        self.ecs._config = config
        self.ecs.csv_path = self._csv_path
        self.ecs.notifier = MagicMock()
        self.ecs.price_cache = MagicMock()
        self.ecs._ohlcv_cache = {}
        self.ecs._order_retry_count = {}
        self.ecs._max_order_retries = 1
        self.ecs.SYMBOL_MAPPING = {}
        self.ecs._resume_state = {
            'start_time': None, 'phase': None, 'last_sl_no': None
        }
        self.ecs._time_budget_seconds = 999999

        # -- Data hooks: feed historical data, NO future leak --
        self.ecs.get_nse_ltp_with_fallback = self._hook_get_ltp
        self.ecs._normalize_symbol = lambda sym: sym
        self.ecs._fetch_recent_ohlcv = self._hook_fetch_ohlcv
        self.ecs._compute_current_atr = self._hook_compute_atr
        self.ecs._is_time_budget_exceeded = lambda: False
        self.ecs._trigger_sell = self._hook_trigger_sell
        self.ecs._update_trailing_stop = self._hook_update_trailing_stop

    def _hook_get_ltp(self, symbol):
        return self._ltp.get(symbol)

    def _hook_fetch_ohlcv(self, symbol):
        df = self.stock_data.get(symbol)
        if df is None:
            return None
        available = df[df.index <= self._sim_date]
        return available.tail(30) if not available.empty else None

    def _hook_compute_atr(self, symbol):
        """Use full history ATR (matching backtest), not 30-bar window."""
        df = self.stock_data.get(symbol)
        if df is None:
            return None
        available = df[df.index <= self._sim_date]
        if len(available) < self.config.atr_period + 1:
            return None
        atr = compute_atr(
            available['High'], available['Low'], available['Close'],
            self.config.atr_period,
        )
        v = atr.iloc[-1]
        return None if pd.isna(v) else float(v)

    def _hook_update_trailing_stop(self, data, idx, row, last_price):
        """Override to match backtest: Close for highest_close, trading days."""
        symbol = row['symbol']

        # Always use bar Close for highest_close (matching backtest exactly)
        # NOT last_price, which may be Low/High from smart LTP
        close_price = last_price  # fallback
        df = self.stock_data.get(symbol)
        if df is not None and self._sim_date in df.index:
            close_price = float(df.loc[self._sim_date, 'Close'])

        highest_close = float(row.get('highest_close') or 0)
        if close_price > highest_close:
            highest_close = close_price
            data.loc[idx, 'highest_close'] = highest_close

        # Count trading days (number of bars since entry), matching backtest
        open_date = row.get('open_date')
        days_held = int(row.get('days_held') or 0)
        if pd.notna(open_date):
            try:
                open_dt = pd.to_datetime(open_date)
                df = self.stock_data.get(symbol)
                if df is not None:
                    trading_bars = df[
                        (df.index >= open_dt) & (df.index <= self._sim_date)
                    ]
                    days_held = len(trading_bars)
                else:
                    days_held += 1
                data.loc[idx, 'days_held'] = days_held
            except Exception:
                days_held += 1
                data.loc[idx, 'days_held'] = days_held

        # Update trailing stop using ATR
        trailing_stop = float(row.get('trailing_stop') or row.get('sl') or 0)
        atr_val = self._hook_compute_atr(symbol)
        if atr_val is not None and highest_close > 0:
            new_trailing = highest_close - (
                self.config.trailing_atr_multiplier * atr_val
            )
            if new_trailing > trailing_stop:
                trailing_stop = new_trailing
                data.loc[idx, 'trailing_stop'] = trailing_stop

        return trailing_stop, days_held

    def _hook_trigger_sell(self, data, idx, row, place_order, reason, last_price):
        """Capture exit trigger -- filtered by _pass_mode to match backtest priority.

        Backtest priority: Stop_Loss > Trailing_Stop > Target_Hit > Time_Stop.
        We run 3 passes (Low, High, Close) and only accept matching reasons.
        """
        # Filter by current pass mode
        if self._pass_mode == 'stops' and reason not in ('Stop_Loss', 'Trailing_Stop'):
            return False
        if self._pass_mode == 'target' and reason != 'Target_Hit':
            return False
        if self._pass_mode == 'time' and reason != 'Time_Stop':
            return False

        sl_no = row['sl_no']
        self._exit_log[sl_no] = {
            'reason': reason,
            'exit_price': last_price,
            'exit_date': self._sim_date,
        }
        data.loc[idx, 'close_order_id'] = int(sl_no) + 50000
        data.loc[idx, 'close_order_status'] = 'close_pending'
        data.loc[idx, 'close_date'] = self._sim_date.strftime("%Y-%m-%d")
        data.to_csv(self._csv_path, index=False)
        return True

    def _set_ltp_all(self, date, price_type):
        """Set LTP for all symbols to a specific bar price (Low/High/Close)."""
        for sym, df in self.stock_data.items():
            if date in df.index:
                self._ltp[sym] = float(df.loc[date, price_type])

    def run(self, all_signals, backtest_trades):
        """Replay historical data through the actual live code.

        Entries injected to match backtest. Exits use pending mechanism:
        triggered on day D, executed at day D+1 Open (matching backtest).
        Returns list of trade dicts.
        """
        # Signal lookup for SL/target
        sig_map = {}
        for sigs in all_signals.values():
            for s in sigs:
                sig_map[(s.symbol, s.date)] = s

        # Build entry schedule from backtest trades
        entry_by_date = {}
        for t in backtest_trades:
            sig = sig_map.get((t.symbol, t.entry_date))
            if sig:
                entry_by_date.setdefault(t.entry_date, []).append((t, sig))

        all_dates = sorted(
            set().union(*(df.index for df in self.stock_data.values()))
        )

        # Init DataFrame with proper dtypes
        cols_dtypes = {
            'sl_no': 'int64', 'account': 'str', 'symbol': 'str',
            'sl': 'float64', 'amount': 'float64', 'percent_increase': 'float64',
            'status': 'str', 'buy_order_id': 'float64', 'buy_price': 'float64',
            'open_order_status': 'str', 'open_date': 'str',
            'quantity': 'float64', 'profit_target': 'float64',
            'trailing_stop': 'float64', 'highest_close': 'float64',
            'days_held': 'float64', 'close_order_id': 'float64',
            'close_order_status': 'str', 'close_date': 'str',
            'sell_price': 'float64',
        }
        data = pd.DataFrame({c: pd.Series(dtype=d) for c, d in cols_dtypes.items()})
        data.to_csv(self._csv_path, index=False)

        sl_counter = 0
        completed = []
        mock_po = MagicMock()
        pending_exits = {}  # sl_no -> {reason, symbol, entry_date, ...}

        orig_dt = _ecs_module.datetime
        orig_sleep = _ecs_module.sleep
        _ecs_module.sleep = lambda *a, **kw: None

        try:
            _ecs_module.datetime = _MockDatetime

            for date in all_dates:
                self._sim_date = date
                self.ecs._ohlcv_cache = {}
                _MockDatetime._fake_now = _real_datetime(
                    date.year, date.month, date.day, 15, 20
                )

                # --- Execute PENDING exits from previous day at today's Open ---
                for sl_no in list(pending_exits.keys()):
                    info = pending_exits[sl_no]
                    sym = info['symbol']
                    if sym in self.stock_data and date in self.stock_data[sym].index:
                        exit_price = float(
                            self.stock_data[sym].loc[date, 'Open']
                        )
                        pnl = (exit_price - info['entry_price']) * info['shares']
                        completed.append({
                            'symbol': sym,
                            'entry_date': info['entry_date'],
                            'exit_date': date,
                            'entry_price': info['entry_price'],
                            'exit_price': exit_price,
                            'shares': info['shares'],
                            'pnl': round(pnl, 2),
                            'exit_reason': info['reason'],
                            'days_held': info['days_held'],
                        })
                        # Mark as closed in DataFrame
                        didx = info['data_idx']
                        if didx in data.index:
                            data.loc[didx, 'status'] = 'close'
                            data.loc[didx, 'close_order_status'] = 'Complete'
                            data.loc[didx, 'sell_price'] = exit_price
                        del pending_exits[sl_no]

                data.to_csv(self._csv_path, index=False)

                # --- THREE-PASS EXIT LOGIC ---
                # IMPORTANT: Exits are checked BEFORE new entries (matching
                # backtest order: Backtester._update_positions() runs first,
                # then new signals are processed).  This prevents newly-
                # entered positions from triggering false exits on entry day.
                # Matches backtest priority: SL > Trailing > Target > Time
                # Pass 1 (LTP=Low): catches Stop_Loss & Trailing_Stop
                # Pass 2 (LTP=High): catches Target_Hit
                # Pass 3 (LTP=Close): catches Time_Stop
                # _hook_update_trailing_stop is idempotent (uses Close for
                # highest_close, trading days from dates, same ATR each pass)

                has_open = (
                    'status' in data.columns
                    and (data['status'] == 'open').any()
                )
                if has_open:
                    self._exit_log = {}

                    # Pass 1: LTP = Low -> Stop_Loss / Trailing_Stop
                    self._pass_mode = 'stops'
                    self._set_ltp_all(date, 'Low')
                    self.ecs._process_open_positions(data, mock_po)

                    # Pass 2: LTP = High -> Target_Hit
                    self._pass_mode = 'target'
                    self._set_ltp_all(date, 'High')
                    self.ecs._process_open_positions(data, mock_po)

                    # Pass 3: LTP = Close -> Time_Stop
                    self._pass_mode = 'time'
                    self._set_ltp_all(date, 'Close')
                    self.ecs._process_open_positions(data, mock_po)

                    # Process triggered exits
                    if 'close_order_status' in data.columns:
                        newly_pending = data[
                            data['close_order_status'] == 'close_pending'
                        ]
                        for idx in newly_pending.index:
                            row = data.loc[idx]
                            sl_no = row['sl_no']
                            info = self._exit_log.get(sl_no, {})
                            reason = info.get('reason', 'Unknown')

                            if reason == 'Time_Stop':
                                # Time stop: immediate at Close (like backtest)
                                sym = row['symbol']
                                close_price = float(
                                    self.stock_data[sym].loc[date, 'Close']
                                ) if (
                                    sym in self.stock_data
                                    and date in self.stock_data[sym].index
                                ) else float(
                                    self._ltp.get(sym, row['buy_price'])
                                )
                                pnl = (
                                    close_price - row['buy_price']
                                ) * row['quantity']
                                completed.append({
                                    'symbol': sym,
                                    'entry_date': pd.to_datetime(
                                        row['open_date']
                                    ),
                                    'exit_date': date,
                                    'entry_price': float(row['buy_price']),
                                    'exit_price': close_price,
                                    'shares': int(row['quantity']),
                                    'pnl': round(pnl, 2),
                                    'exit_reason': reason,
                                    'days_held': int(
                                        row.get('days_held') or 0
                                    ),
                                })
                                data.loc[idx, 'status'] = 'close'
                                data.loc[idx, 'close_order_status'] = 'Complete'
                                data.loc[idx, 'sell_price'] = close_price
                            else:
                                # SL/Trailing/Target: PENDING next-day Open
                                pending_exits[sl_no] = {
                                    'reason': reason,
                                    'symbol': row['symbol'],
                                    'entry_date': pd.to_datetime(
                                        row['open_date']
                                    ),
                                    'entry_price': float(row['buy_price']),
                                    'shares': int(row['quantity']),
                                    'days_held': int(
                                        row.get('days_held') or 0
                                    ),
                                    'data_idx': idx,
                                }
                                # Keep status='open' but close_order_id set
                                # so live code skips it on subsequent days

                # --- INJECT NEW ENTRIES (after exit checks, matching backtest order) ---
                if date in entry_by_date:
                    for trade, sig in entry_by_date[date]:
                        sl_counter += 1
                        pct = (
                            (sig.target_price / trade.entry_price) - 1
                        ) * 100
                        new_row = pd.DataFrame([{
                            'sl_no': sl_counter,
                            'account': 'deepti',
                            'symbol': trade.symbol,
                            'sl': sig.stop_loss,
                            'amount': trade.shares * trade.entry_price,
                            'percent_increase': pct,
                            'status': 'open',
                            'buy_order_id': sl_counter,
                            'buy_price': trade.entry_price,
                            'open_order_status': 'Complete',
                            'open_date': date.strftime("%Y-%m-%d"),
                            'quantity': trade.shares,
                            'profit_target': sig.target_price,
                            'trailing_stop': sig.stop_loss,
                            'highest_close': trade.entry_price,
                            'days_held': 0,
                            'close_order_id': np.nan,
                            'close_order_status': np.nan,
                            'close_date': np.nan,
                            'sell_price': np.nan,
                        }])
                        data = pd.concat(
                            [data, new_row], ignore_index=True
                        )

                data.to_csv(self._csv_path, index=False)

            # Close remaining open positions (including any still-pending)
            if all_dates:
                last_date = all_dates[-1]

                # Finalize any remaining pending exits at last day Close
                for sl_no, info in pending_exits.items():
                    sym = info['symbol']
                    price = self._ltp.get(sym, info['entry_price'])
                    pnl = (price - info['entry_price']) * info['shares']
                    completed.append({
                        'symbol': sym,
                        'entry_date': info['entry_date'],
                        'exit_date': last_date,
                        'entry_price': info['entry_price'],
                        'exit_price': float(price),
                        'shares': info['shares'],
                        'pnl': round(pnl, 2),
                        'exit_reason': info['reason'],
                        'days_held': info['days_held'],
                    })

                # Close any still-open positions (no exit triggered)
                if 'status' in data.columns:
                    open_mask = (data['status'] == 'open')
                    if 'close_order_id' in data.columns:
                        open_mask = open_mask & (
                            data['close_order_id'].isna()
                            | (data['close_order_id'] == -1)
                        )
                    for idx in data[open_mask].index:
                        row = data.loc[idx]
                        sym = row['symbol']
                        price = self._ltp.get(sym, row['buy_price'])
                        pnl = (price - row['buy_price']) * row['quantity']
                        completed.append({
                            'symbol': sym,
                            'entry_date': pd.to_datetime(row['open_date']),
                            'exit_date': last_date,
                            'entry_price': float(row['buy_price']),
                            'exit_price': float(price),
                            'shares': int(row['quantity']),
                            'pnl': round(pnl, 2),
                            'exit_reason': 'End_of_Backtest',
                            'days_held': int(row.get('days_held') or 0),
                        })

        finally:
            _ecs_module.datetime = orig_dt
            _ecs_module.sleep = orig_sleep

        return completed


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture(scope="module")
def config():
    return _backtest_config()


@pytest.fixture(scope="module")
def stock_data():
    return _load_real_stock_data()


@pytest.fixture(scope="module")
def backtest_and_signals(stock_data, config):
    bt, all_signals = _run_backtest(stock_data, config)
    csv_path = os.path.join(os.path.dirname(__file__), 'backtest_trades.csv')
    _save_trades_csv(bt, csv_path)
    return bt, all_signals


@pytest.fixture(scope="module")
def backtest_result(backtest_and_signals):
    return backtest_and_signals[0]


@pytest.fixture(scope="module")
def all_signals(backtest_and_signals):
    return backtest_and_signals[1]


@pytest.fixture(scope="module")
def live_result(stock_data, all_signals, config, backtest_result):
    harness = LiveCodeReplayHarness(stock_data, config)
    return harness.run(all_signals, backtest_result.trades)


@pytest.fixture(scope="module")
def comparison(backtest_result, live_result):
    bt_map = {}
    for t in backtest_result.trades:
        bt_map[(t.symbol, t.entry_date)] = {
            "symbol": t.symbol,
            "entry_date": t.entry_date,
            "exit_date": t.exit_date,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl": t.pnl,
            "exit_reason": t.exit_reason,
            "days_held": t.days_held,
        }

    live_map = {}
    for t in live_result:
        live_map[(t["symbol"], t["entry_date"])] = t

    common = sorted(set(bt_map) & set(live_map))
    paired = [{"backtest": bt_map[k], "live": live_map[k]} for k in common]
    bt_only = sorted(set(bt_map) - set(live_map))
    live_only = sorted(set(live_map) - set(bt_map))

    return {
        "paired": paired,
        "bt_only": [bt_map[k] for k in bt_only],
        "live_only": [live_map[k] for k in live_only],
        "bt_total": len(bt_map),
        "live_total": len(live_map),
    }


# =============================================================================
# TESTS -- Backtest Sanity (CAGR ~15-17%)
# =============================================================================

class TestBacktestSanity:

    def test_produces_trades(self, backtest_result):
        n = len(backtest_result.trades)
        assert n > 100, f"Expected 1000+ trades, got {n}"
        print(f"\n  Backtest total trades: {n}")

    def test_cagr_around_15_percent(self, backtest_result, config):
        cagr = _compute_cagr(backtest_result.equity_curve, config.initial_capital)
        final_eq = (
            backtest_result.equity_curve[-1]["equity"]
            if backtest_result.equity_curve else 0
        )
        print(f"\n  Backtest CAGR: {cagr * 100:.1f}%")
        print(f"  Final equity: {final_eq:,.0f} (initial: {config.initial_capital:,.0f})")
        assert cagr > 0.10, f"Expected CAGR > 10%, got {cagr * 100:.1f}%"

    def test_trades_csv_saved(self, backtest_result):
        csv_path = os.path.join(os.path.dirname(__file__), 'backtest_trades.csv')
        assert os.path.exists(csv_path)
        df = pd.read_csv(csv_path)
        assert len(df) == len(backtest_result.trades)
        print(f"\n  Trades saved to: {csv_path}")


# =============================================================================
# TESTS -- Live Code vs Backtest Comparison
# =============================================================================

class TestLiveVsBacktestComparison:

    def test_live_produces_trades(self, live_result):
        assert len(live_result) > 0, "Live replay produced 0 trades"
        print(f"\n  Live trades: {len(live_result)}")

    def test_all_entries_matched(self, comparison):
        bt_total = comparison["bt_total"]
        paired = len(comparison["paired"])
        bt_only = len(comparison["bt_only"])
        print(f"\n  Backtest trades: {bt_total}")
        print(f"  Live trades:     {comparison['live_total']}")
        print(f"  Paired:          {paired}")
        if bt_only:
            print(f"  Backtest-only:   {bt_only}")
        assert paired == bt_total, (
            f"Only {paired}/{bt_total} backtest trades matched. "
            f"{bt_only} entries missing from live."
        )

    def test_entry_prices_identical(self, comparison):
        for p in comparison["paired"]:
            bt, lv = p["backtest"], p["live"]
            assert bt["entry_price"] == pytest.approx(
                lv["entry_price"], abs=0.01
            ), (
                f"{bt['symbol']} {bt['entry_date']}: "
                f"BT={bt['entry_price']} vs LIVE={lv['entry_price']}"
            )

    def test_exit_reason_agreement(self, comparison):
        total = len(comparison["paired"])
        if total == 0:
            pytest.skip("No paired trades")

        same = sum(
            1 for p in comparison["paired"]
            if p["backtest"]["exit_reason"] == p["live"]["exit_reason"]
        )
        pct = same / total * 100

        print(f"\n  Exit reason match: {same}/{total} ({pct:.0f}%)")

        # Show first 15 mismatches
        mismatches = [
            p for p in comparison["paired"]
            if p["backtest"]["exit_reason"] != p["live"]["exit_reason"]
        ]
        for m in mismatches[:15]:
            bt, lv = m["backtest"], m["live"]
            print(
                f"    {bt['symbol']:>18s} entered {str(bt['entry_date'])[:10]}: "
                f"BT={bt['exit_reason']:<16s} LIVE={lv['exit_reason']}"
            )
        if len(mismatches) > 15:
            print(f"    ... and {len(mismatches) - 15} more")

        # Smart LTP (Low/High) + pending exit should give high agreement
        assert pct >= 70, f"Exit reason agreement too low: {pct:.0f}%"

    def test_exit_date_report(self, comparison):
        total = len(comparison["paired"])
        if total == 0:
            pytest.skip("No paired trades")

        same_date = sum(
            1 for p in comparison["paired"]
            if p["backtest"]["exit_date"] == p["live"]["exit_date"]
        )
        pct = same_date / total * 100

        offsets = []
        for p in comparison["paired"]:
            bt_e = p["backtest"]["exit_date"]
            lv_e = p["live"]["exit_date"]
            if bt_e != lv_e:
                offsets.append((lv_e - bt_e).days)

        print(f"\n  Exit date match: {same_date}/{total} ({pct:.0f}%)")
        if offsets:
            print(
                f"  Offsets (live - backtest): "
                f"min={min(offsets)}, max={max(offsets)}, "
                f"mean={np.mean(offsets):.1f} days"
            )

    def test_pnl_direction_agreement(self, comparison):
        total = 0
        agree = 0
        for p in comparison["paired"]:
            bt_pnl, lv_pnl = p["backtest"]["pnl"], p["live"]["pnl"]
            if bt_pnl == 0 or lv_pnl == 0:
                continue
            total += 1
            if (bt_pnl > 0) == (lv_pnl > 0):
                agree += 1

        pct = agree / total * 100 if total > 0 else 100
        print(f"\n  P&L direction agreement: {agree}/{total} ({pct:.0f}%)")
        assert pct >= 70, f"P&L direction agreement too low: {pct:.0f}%"

    def test_detailed_comparison_report(self, comparison, config, backtest_result):
        cagr = _compute_cagr(backtest_result.equity_curve, config.initial_capital)

        print("\n" + "=" * 120)
        print("  BACKTEST vs ACTUAL LIVE CODE -- TRADE-BY-TRADE COMPARISON")
        print("=" * 120)
        print(f"  Backtest CAGR: {cagr * 100:.1f}%")
        print(f"  Backtest trades: {comparison['bt_total']}")
        print(f"  Live trades:     {comparison['live_total']}")
        print(f"  Paired:          {len(comparison['paired'])}")

        if comparison["paired"]:
            bt_pnl = sum(p["backtest"]["pnl"] for p in comparison["paired"])
            live_pnl = sum(p["live"]["pnl"] for p in comparison["paired"])

            same_reason = sum(
                1 for p in comparison["paired"]
                if p["backtest"]["exit_reason"] == p["live"]["exit_reason"]
            )
            same_date = sum(
                1 for p in comparison["paired"]
                if p["backtest"]["exit_date"] == p["live"]["exit_date"]
            )

            print(
                f"\n  Total P&L -- Backtest: {bt_pnl:,.0f}  |  "
                f"Live: {live_pnl:,.0f}"
            )
            print(f"  Same exit reason: {same_reason}/{len(comparison['paired'])}")
            print(f"  Same exit date:   {same_date}/{len(comparison['paired'])}")

            # Print first 50 trades with diffs highlighted
            hdr = (
                f"  {'Symbol':>18s} {'Entry':<12} "
                f"{'BT Exit':<12} {'LIVE Exit':<12} "
                f"{'BT Reason':<16} {'LIVE Reason':<16} "
                f"{'BT PnL':>12} {'LIVE PnL':>12}"
            )
            print(f"\n{hdr}")
            print("  " + "-" * 114)

            shown = 0
            for p in comparison["paired"]:
                bt, lv = p["backtest"], p["live"]
                differs = bt["exit_reason"] != lv["exit_reason"]
                if shown >= 50 and not differs:
                    continue
                marker = "*" if differs else " "
                print(
                    f"{marker} {bt['symbol']:>18s} "
                    f"{str(bt['entry_date'])[:10]:<12} "
                    f"{str(bt['exit_date'])[:10]:<12} "
                    f"{str(lv['exit_date'])[:10]:<12} "
                    f"{bt['exit_reason']:<16} {lv['exit_reason']:<16} "
                    f"{bt['pnl']:>12,.0f} {lv['pnl']:>12,.0f}"
                )
                shown += 1

            print(f"\n  * = exit reason differs")
            print(
                "\n  Matching mechanisms:"
                "\n    - Exits are PENDING: triggered day D, executed day D+1 Open"
                "\n    - Smart LTP: Low for SL/trailing, High for target"
                "\n    - Trading-day counting for days_held"
                "\n    - Full-history ATR (matching backtest)"
            )

        print("=" * 120)

    def test_save_live_trades_csv(self, live_result):
        csv_path = os.path.join(os.path.dirname(__file__), 'live_replay_trades.csv')
        df = pd.DataFrame(live_result)
        df.to_csv(csv_path, index=False)
        print(f"\n  Live trades saved to: {csv_path}")
        assert len(df) > 0


# =============================================================================
# TESTS -- ATR Consistency & Signal Determinism
# =============================================================================

class TestATRConsistency:
    def test_atr_full_vs_30bar_window(self, stock_data, config):
        # Test on first 3 stocks
        for sym, df in list(stock_data.items())[:3]:
            check_idx = min(200, len(df) - 1)
            atr_full = compute_atr(
                df["High"], df["Low"], df["Close"], config.atr_period
            )
            full_val = atr_full.iloc[check_idx]

            window = df.iloc[max(0, check_idx - 29): check_idx + 1]
            atr_win = compute_atr(
                window["High"], window["Low"], window["Close"], config.atr_period
            )
            win_val = atr_win.iloc[-1]

            assert not pd.isna(full_val), f"Full ATR NaN for {sym}"
            assert not pd.isna(win_val), f"Window ATR NaN for {sym}"
            assert full_val == pytest.approx(win_val, rel=0.05), (
                f"{sym}: full={full_val:.4f} vs window={win_val:.4f}"
            )


class TestSignalDeterminism:
    def test_deterministic(self, stock_data, config):
        for sym, df in list(stock_data.items())[:3]:
            df.attrs["symbol"] = sym
            sigs1 = generate_signals(
                df,
                identify_wave_structures(
                    detect_swing_points(df, config), config
                ),
                config,
            )
            sigs2 = generate_signals(
                df,
                identify_wave_structures(
                    detect_swing_points(df, config), config
                ),
                config,
            )
            assert len(sigs1) == len(sigs2), f"{sym}: {len(sigs1)} vs {len(sigs2)}"
            for a, b in zip(sigs1, sigs2):
                assert a.date == b.date
                assert a.entry_price == b.entry_price
