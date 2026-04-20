"""Unit tests for ElliotWaveSignalGenerator.

Tests:
  1. load_accounts() — real fetch from Google Sheet, verify dummy account & amount calculation
  2. generate_daily_signals() — mocked TV data, verify CSV rows written correctly
  3. Deduplication — symbols already active are skipped
  4. No signals case — CSV unchanged if EW finds nothing today

Run:
  cd auto_straddle
  python -m pytest tests/test_elliot_wave_signals.py -v
  # or run all tests:
  python -m pytest tests/ -v
"""

import os
import sys
import unittest
import tempfile
from datetime import date, datetime
from unittest.mock import MagicMock, patch, PropertyMock

import pandas as pd

# sys.path is managed by conftest.py

from elliot_wave_signals import ElliotWaveSignalGenerator, ACCOUNTS_URL
from elliot_wave_strategy import SignalType, TradeSignal, WaveStructure, WaveType, SwingPoint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_signal(symbol="RELIANCE", target_pct=15.0, entry=500.0, sl=470.0):
    """Build a minimal TradeSignal for today's date."""
    today = pd.Timestamp(date.today())
    w1_start = SwingPoint(index=0, date=today, price=400.0, is_high=False)
    w1_end   = SwingPoint(index=10, date=today, price=500.0, is_high=True)
    w2_end   = SwingPoint(index=20, date=today, price=470.0, is_high=False)
    ws = WaveStructure(wave1_start=w1_start, wave1_end=w1_end, wave2_end=w2_end,
                       current_wave=WaveType.WAVE_3, is_valid=True)
    target = entry * (1 + target_pct / 100)
    return TradeSignal(
        date=today,
        symbol=symbol,
        signal_type=SignalType.WAVE3_ENTRY,
        entry_price=entry,
        stop_loss=sl,
        target_price=target,
        wave_structure=ws,
        rsi=55.0,
        volume_ratio=1.3,
        confidence=72.0,
    )


def _make_dummy_ohlcv(n=300):
    """Return a minimal OHLCV DataFrame with n rows (enough for EW detection)."""
    idx = pd.date_range(end=date.today(), periods=n, freq="B")
    df = pd.DataFrame({
        "Open":   [100.0 + i * 0.1 for i in range(n)],
        "High":   [102.0 + i * 0.1 for i in range(n)],
        "Low":    [99.0  + i * 0.1 for i in range(n)],
        "Close":  [101.0 + i * 0.1 for i in range(n)],
        "Volume": [1_000_000] * n,
    }, index=idx)
    return df


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestElliotWaveSignalGenerator(unittest.TestCase):

    def setUp(self):
        """Create a temp directory for CSV outputs."""
        self.tmp_dir = tempfile.mkdtemp()
        self.csv_path = os.path.join(self.tmp_dir, "elliot_cash_stratergy.csv")
        nifty200_csv = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "elliot_backtest", "ind_nifty200list.csv"
        )
        self.generator = ElliotWaveSignalGenerator(
            accounts_url=ACCOUNTS_URL,
            nifty200_csv=nifty200_csv,
        )

    # -----------------------------------------------------------------------
    # 1. Account loading from real Google Sheet
    # -----------------------------------------------------------------------

    def test_load_accounts_real_sheet(self):
        """Fetch real accounts from Google Sheet and validate dummy entry structure."""
        accounts = self.generator.load_accounts()

        self.assertIsInstance(accounts, list, "load_accounts should return a list")
        self.assertGreater(len(accounts), 0, "Expected at least one account in the sheet")

        # Find dummy account
        dummy = next((a for a in accounts if a["account"] == "dummy"), None)
        self.assertIsNotNone(dummy, "Expected 'dummy' account in the sheet")

        # Validate structure and relationship: amount should be 10% of current_capital
        self.assertIn("current_capital", dummy)
        self.assertIn("amount", dummy)
        self.assertGreater(dummy["current_capital"], 0)
        self.assertAlmostEqual(
            dummy["amount"], dummy["current_capital"] * 0.10, places=0,
            msg="Per-trade amount should be 10% of current capital"
        )

    # -----------------------------------------------------------------------
    # 2. Signal generation writes correct rows to CSV
    # -----------------------------------------------------------------------

    @patch.object(ElliotWaveSignalGenerator, "load_nifty200", return_value=["RELIANCE"])
    @patch.object(ElliotWaveSignalGenerator, "_fetch_ohlcv")
    @patch("elliot_wave_signals.detect_swing_points", return_value=[])
    @patch("elliot_wave_signals.identify_wave_structures", return_value=[])
    @patch("elliot_wave_signals.generate_signals")
    @patch.object(ElliotWaveSignalGenerator, "_send_telegram_summary")
    def test_generate_daily_signals_writes_csv(
        self, mock_telegram, mock_gen_signals, mock_identify, mock_detect,
        mock_fetch, mock_nifty
    ):
        """generate_daily_signals() should write one row per account when signal found."""
        mock_fetch.return_value = _make_dummy_ohlcv()
        mock_gen_signals.return_value = [_make_fake_signal("RELIANCE")]

        rows_written = self.generator.generate_daily_signals(self.csv_path)

        # One account (dummy) × one symbol → 1 row
        self.assertGreater(rows_written, 0, "Should write at least 1 row")

        df = pd.read_csv(self.csv_path)
        self.assertEqual(len(df), rows_written)

        row = df[df["symbol"] == "RELIANCE"].iloc[0]
        self.assertEqual(row["account"], "dummy")
        self.assertEqual(row["status"], "new")
        self.assertEqual(row["signal_type"], "Wave3_Entry")
        self.assertAlmostEqual(row["percent_increase"], 15.0, places=1)
        self.assertTrue(row["sl_no"].startswith("EW_"))
        self.assertFalse(pd.notna(row.get("profit_target")), "profit_target should be None until order fills")

    # -----------------------------------------------------------------------
    # 3. Deduplication — active symbols are skipped
    # -----------------------------------------------------------------------

    @patch.object(ElliotWaveSignalGenerator, "load_nifty200", return_value=["RELIANCE"])
    @patch.object(ElliotWaveSignalGenerator, "_fetch_ohlcv")
    @patch("elliot_wave_signals.generate_signals")
    @patch("elliot_wave_signals.detect_swing_points", return_value=[])
    @patch("elliot_wave_signals.identify_wave_structures", return_value=[])
    @patch.object(ElliotWaveSignalGenerator, "_send_telegram_summary")
    def test_deduplication_skips_active_symbols(
        self, mock_telegram, mock_identify, mock_detect,
        mock_gen_signals, mock_fetch, mock_nifty
    ):
        """Symbols with status 'new' or 'open' should not generate duplicate rows."""
        mock_fetch.return_value = _make_dummy_ohlcv()
        mock_gen_signals.return_value = [_make_fake_signal("RELIANCE")]

        # Pre-populate CSV with RELIANCE already 'open'
        existing = pd.DataFrame([{
            "sl_no": "EW_PREV_RELIANCE_dummy",
            "symbol": "RELIANCE",
            "account": "dummy",
            "status": "open",
        }])
        existing.to_csv(self.csv_path, index=False)

        rows_written = self.generator.generate_daily_signals(self.csv_path)

        self.assertEqual(rows_written, 0, "No new rows should be written for already-active symbol")

        df = pd.read_csv(self.csv_path)
        self.assertEqual(len(df), 1, "CSV should still have only the original row")

    # -----------------------------------------------------------------------
    # 4. No signals today — CSV unchanged
    # -----------------------------------------------------------------------

    @patch.object(ElliotWaveSignalGenerator, "load_nifty200", return_value=["RELIANCE"])
    @patch.object(ElliotWaveSignalGenerator, "_fetch_ohlcv")
    @patch("elliot_wave_signals.generate_signals", return_value=[])
    @patch("elliot_wave_signals.detect_swing_points", return_value=[])
    @patch("elliot_wave_signals.identify_wave_structures", return_value=[])
    @patch.object(ElliotWaveSignalGenerator, "_send_telegram_summary")
    def test_no_signals_leaves_csv_unchanged(
        self, mock_telegram, mock_identify, mock_detect,
        mock_gen_signals, mock_fetch, mock_nifty
    ):
        """If EW finds no signals today, existing CSV should be untouched."""
        mock_fetch.return_value = _make_dummy_ohlcv()

        existing = pd.DataFrame([{
            "sl_no": "EW_PREV_TCS_dummy", "symbol": "TCS",
            "account": "dummy", "status": "close",
        }])
        existing.to_csv(self.csv_path, index=False)
        original_len = len(existing)

        rows_written = self.generator.generate_daily_signals(self.csv_path)

        self.assertEqual(rows_written, 0)
        df = pd.read_csv(self.csv_path)
        self.assertEqual(len(df), original_len, "CSV should be unchanged when no new signals")

    # -----------------------------------------------------------------------
    # 5. sl_no uniqueness for multiple accounts
    # -----------------------------------------------------------------------

    @patch.object(ElliotWaveSignalGenerator, "load_nifty200", return_value=["INFY"])
    @patch.object(ElliotWaveSignalGenerator, "_fetch_ohlcv")
    @patch("elliot_wave_signals.detect_swing_points", return_value=[])
    @patch("elliot_wave_signals.identify_wave_structures", return_value=[])
    @patch("elliot_wave_signals.generate_signals")
    @patch.object(ElliotWaveSignalGenerator, "_send_telegram_summary")
    def test_sl_no_contains_date_symbol_account(
        self, mock_telegram, mock_gen_signals, mock_identify, mock_detect,
        mock_fetch, mock_nifty
    ):
        """sl_no should encode date, symbol, and account."""
        mock_fetch.return_value = _make_dummy_ohlcv()
        mock_gen_signals.return_value = [_make_fake_signal("INFY")]

        self.generator.generate_daily_signals(self.csv_path)

        df = pd.read_csv(self.csv_path)
        today_str = date.today().strftime("%Y%m%d")
        for _, row in df.iterrows():
            self.assertIn(today_str, row["sl_no"])
            self.assertIn("INFY", row["sl_no"])
            self.assertIn(row["account"], row["sl_no"])


    # -----------------------------------------------------------------------
    # 6. sync_account_amounts — delta_change compound tracking
    # -----------------------------------------------------------------------

    def _make_remote_df(self, rows):
        """Build a fake remote accounts DataFrame (mimics Google Sheet download)."""
        return pd.DataFrame(rows)

    def _accounts_csv(self, rows=None):
        """Write a local elliot_accounts.csv and return its path.

        rows use the new multi-entry schema:
          account | sync_date | delta_change_pct | current_amount
        """
        path = os.path.join(self.tmp_dir, "elliot_accounts.csv")
        if rows is not None:
            pd.DataFrame(rows).to_csv(path, index=False)
        return path

    def test_sync_new_account_seeded_with_initial_amount(self):
        """First sync: account absent locally → one seed row appended."""
        accounts_path = self._accounts_csv()   # file does not exist yet

        remote_df = self._make_remote_df([{
            "account": "dummy", "initial_amount": 100000,
            "delta_change": 0, "date_sync": "19/04/2026",
        }])

        with patch("elliot_wave_signals._read_csv_from_url", return_value=remote_df):
            result = self.generator.sync_account_amounts(accounts_path)

        self.assertEqual(len(result), 1)
        acct = result[0]
        self.assertEqual(acct["account"], "dummy")
        self.assertAlmostEqual(acct["current_amount"], 100_000.0, places=0)
        # per_trade = 100000 / max_positions (default 10)
        self.assertAlmostEqual(acct["per_trade_amount"], 100_000.0 / 10, places=0)

        # Verify local CSV was created with exactly one row
        local_df = pd.read_csv(accounts_path)
        self.assertEqual(len(local_df), 1)
        self.assertAlmostEqual(local_df.iloc[0]["current_amount"], 100_000.0, places=0)
        self.assertEqual(local_df.iloc[0]["delta_change_pct"], 0)

    def test_sync_applies_delta_on_newer_date(self):
        """When remote date_sync > latest local sync_date, a new row is appended."""
        accounts_path = self._accounts_csv([{
            "account": "dummy",
            "sync_date": "2026-04-15",
            "delta_change_pct": 0,
            "current_amount": 100000.0,
        }])

        remote_df = self._make_remote_df([{
            "account": "dummy", "initial_amount": 100000,
            "delta_change": 10, "date_sync": "16/04/2026",
        }])

        with patch("elliot_wave_signals._read_csv_from_url", return_value=remote_df):
            result = self.generator.sync_account_amounts(accounts_path)

        acct = result[0]
        self.assertAlmostEqual(acct["current_amount"], 110_000.0, places=0)
        self.assertAlmostEqual(acct["per_trade_amount"], 110_000.0 / 10, places=0)

        # CSV now has 2 rows (history preserved)
        local_df = pd.read_csv(accounts_path)
        self.assertEqual(len(local_df), 2)
        latest = local_df.sort_values("sync_date").iloc[-1]
        self.assertAlmostEqual(latest["current_amount"], 110_000.0, places=0)
        self.assertEqual(latest["delta_change_pct"], 10)

    def test_sync_no_double_apply_same_date(self):
        """Calling sync twice with same date_sync must not append a second row."""
        accounts_path = self._accounts_csv([
            {"account": "dummy", "sync_date": "2026-04-15", "delta_change_pct": 0,  "current_amount": 100000.0},
            {"account": "dummy", "sync_date": "2026-04-16", "delta_change_pct": 10, "current_amount": 110000.0},
        ])

        remote_df = self._make_remote_df([{
            "account": "dummy", "initial_amount": 100000,
            "delta_change": 10, "date_sync": "16/04/2026",
        }])

        with patch("elliot_wave_signals._read_csv_from_url", return_value=remote_df):
            result = self.generator.sync_account_amounts(accounts_path)

        # Amount unchanged, no extra row written
        self.assertAlmostEqual(result[0]["current_amount"], 110_000.0, places=0)
        local_df = pd.read_csv(accounts_path)
        self.assertEqual(len(local_df), 2, "No new row should be appended on same date")

    def test_sync_no_apply_when_local_date_newer(self):
        """If local sync_date >= remote date_sync, amount must remain unchanged."""
        accounts_path = self._accounts_csv([{
            "account": "dummy",
            "sync_date": "2026-04-20",
            "delta_change_pct": 0,
            "current_amount": 120000.0,
        }])

        remote_df = self._make_remote_df([{
            "account": "dummy", "initial_amount": 100000,
            "delta_change": 10, "date_sync": "16/04/2026",
        }])

        with patch("elliot_wave_signals._read_csv_from_url", return_value=remote_df):
            result = self.generator.sync_account_amounts(accounts_path)

        self.assertAlmostEqual(result[0]["current_amount"], 120_000.0, places=0)
        local_df = pd.read_csv(accounts_path)
        self.assertEqual(len(local_df), 1, "No new row when remote date is older")

    def test_sync_multiple_accounts_tracked_independently(self):
        """Each account's delta is applied independently; history rows per account."""
        accounts_path = self._accounts_csv([
            {"account": "deepti", "sync_date": "2026-04-15", "delta_change_pct": 0, "current_amount": 200000.0},
            {"account": "dummy",  "sync_date": "2026-04-10", "delta_change_pct": 0, "current_amount": 100000.0},
        ])

        remote_df = self._make_remote_df([
            {"account": "deepti", "initial_amount": 200000, "delta_change": 5,  "date_sync": "16/04/2026"},
            {"account": "dummy",  "initial_amount": 100000, "delta_change": 10, "date_sync": "16/04/2026"},
        ])

        with patch("elliot_wave_signals._read_csv_from_url", return_value=remote_df):
            result = self.generator.sync_account_amounts(accounts_path)

        by_acct = {r["account"]: r for r in result}
        self.assertAlmostEqual(by_acct["deepti"]["current_amount"], 200000.0 * 1.05, places=0)
        self.assertAlmostEqual(by_acct["dummy"]["current_amount"],  100000.0 * 1.10, places=0)

        # CSV should now have 4 rows (2 original + 2 new)
        local_df = pd.read_csv(accounts_path)
        self.assertEqual(len(local_df), 4)

    def test_sync_negative_delta_reduces_amount(self):
        """Negative delta_change should reduce current_amount (e.g. loss period)."""
        accounts_path = self._accounts_csv([{
            "account": "dummy",
            "sync_date": "2026-04-15",
            "delta_change_pct": 0,
            "current_amount": 100000.0,
        }])

        remote_df = self._make_remote_df([{
            "account": "dummy", "initial_amount": 100000,
            "delta_change": -10, "date_sync": "16/04/2026",

        }])

        with patch("elliot_wave_signals._read_csv_from_url", return_value=remote_df):
            result = self.generator.sync_account_amounts(accounts_path)

        self.assertAlmostEqual(result[0]["current_amount"], 90_000.0, places=0)

    def test_generate_daily_signals_uses_per_trade_amount(self):
        """When accounts_csv_path supplied, amount in CSV rows = per_trade_amount."""
        accounts_path = self._accounts_csv([{
            "account": "dummy",
            "sync_date": "2026-04-15",
            "delta_change_pct": 0,
            "current_amount": 150000.0,
        }])

        remote_df = self._make_remote_df([{
            "account": "dummy", "initial_amount": 150000,
            "delta_change": 0, "date_sync": "15/04/2026",
        }])

        with patch("elliot_wave_signals._read_csv_from_url", return_value=remote_df), \
             patch.object(ElliotWaveSignalGenerator, "load_nifty200", return_value=["RELIANCE"]), \
             patch.object(ElliotWaveSignalGenerator, "_fetch_ohlcv", return_value=_make_dummy_ohlcv()), \
             patch("elliot_wave_signals.detect_swing_points", return_value=[]), \
             patch("elliot_wave_signals.identify_wave_structures", return_value=[]), \
             patch("elliot_wave_signals.generate_signals", return_value=[_make_fake_signal("RELIANCE")]), \
             patch.object(ElliotWaveSignalGenerator, "_send_telegram_summary"):
            self.generator.generate_daily_signals(self.csv_path, accounts_csv_path=accounts_path)

        df = pd.read_csv(self.csv_path)
        expected_per_trade = round(150_000.0 / 10, 2)
        self.assertAlmostEqual(df.iloc[0]["amount"], expected_per_trade, places=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
