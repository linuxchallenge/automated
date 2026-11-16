"""Unit tests for ElliotCashStratergy.

Tests:
  1. sync_elliot_strategy() — new rows inserted, updated rows synced by date
  2. _process_new_orders() — BUY placed, profit_target computed from percent_increase
  3. _process_open_positions() — SELL triggered on SL hit / target hit
  4. _process_pending_orders() — order status confirmed, PnL recorded
  5. Dummy account — manual flow (Telegram notification, no API call)

Run:
  cd auto_straddle
  python -m pytest tests/test_elliot_cash_stratergy.py -v
  # or run all tests:
  python -m pytest tests/ -v
"""

import os
import unittest
import tempfile
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd

# sys.path is managed by conftest.py

from elliot_cash_stratergy import ElliotCashStratergy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_row(**kwargs):
    """Return a minimal CSV row dict with sensible defaults.

    String columns use empty string (not None) so pandas infers object dtype
    rather than float64, avoiding TypeError on later string assignment.
    """
    defaults = {
        "sl_no": "EW_20260419_RELIANCE_dummy",
        "symbol": "RELIANCE",
        "account": "dummy",
        "amount": 10000.0,
        "sl": 2800.0,
        "percent_increase": 15.0,
        "date": "2026-04-19",
        "status": "new",
        "signal_type": "Wave3_Entry",
        "confidence": 72.0,
        "exchange": "NSE",
        "profit_target": "",
        "buy_order_id": "",
        "buy_price": "",
        "quantity": "",
        "open_order_status": "",
        "open_date": "",
        "close_order_id": "",
        "close_order_status": "",
        "sell_price": "",
        "close_date": "",
        "trailing_stop": "",
        "highest_close": "",
        "days_held": "",
    }
    defaults.update(kwargs)
    return defaults


def _write_csv(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)


def _read_csv(path):
    df = pd.read_csv(path)
    str_cols = ['open_order_status', 'close_order_status', 'open_date', 'close_date',
                'buy_order_id', 'close_order_id', 'sell_price', 'buy_price',
                'profit_target', 'quantity', 'trailing_stop', 'highest_close', 'days_held']
    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].astype(object)
    return df


def _make_place_order(buy_order_id="ORD001", order_status="Complete", fill_price=None):
    """Return a mock PlaceOrder with configurable behaviour."""
    po = MagicMock()
    po.place_cash_order.return_value = buy_order_id
    po.order_status.return_value = (order_status, fill_price or 3000.0)
    return po


# ---------------------------------------------------------------------------
# Fixtures shared across tests
# ---------------------------------------------------------------------------

class TestElliotCashStratergy(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.csv_path = os.path.join(self.tmp_dir, "elliot_cash_stratergy.csv")

        # Patch Telegram and configuration so tests run without network
        self.mock_telegram = MagicMock()
        with patch("elliot_cash_stratergy.TelegramSend.telegram_send_api", return_value=self.mock_telegram), \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"deepti_telegram": "CHAT123", "dummy_telegram": "CHAT456"}):
            self.strategy = ElliotCashStratergy()

        self.strategy.csv_path = self.csv_path
        # Use a single real-looking URL so the PLACEHOLDER guard doesn't skip sync
        # in tests and mocked pd.read_csv side_effect lists stay one-read-per-sheet
        self.strategy.remote_csv_urls = {
            "deepti": "https://docs.google.com/spreadsheets/d/TEST_SHEET/export?format=csv"
        }
        # Bypass market-open check in tests
        self.strategy.nso_open = True

    # -----------------------------------------------------------------------
    # 1. sync_elliot_strategy — apply corrections from Google Sheet
    # -----------------------------------------------------------------------

    def test_sync_exit_closes_open_position(self):
        """Exit correction should set status=close and record sell_price."""
        row = _base_row(
            status="open", account="deepti",
            buy_price=3000.0, quantity=3,
            open_order_status="Complete",
        )
        _write_csv(self.csv_path, [row])

        corrections = pd.DataFrame([{
            "sl_no": 1, "account": "deepti", "symbol": "RELIANCE",
            "entry_exit": "exit", "price": 3450.0, "date": "2026-04-25",
        }])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[corrections, _read_csv(self.csv_path)]), \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"deepti_telegram": "CHAT123"}), \
             patch("elliot_cash_stratergy.brokrage_calculator.calculate_equity_delivery",
                   return_value={"total_charges": 30.0}), \
             patch("elliot_cash_stratergy.os.path.exists", return_value=False):
            self.strategy.sync_elliot_strategy()

        result = _read_csv(self.csv_path)
        self.assertEqual(result.iloc[0]["status"], "close")
        self.assertAlmostEqual(result.iloc[0]["sell_price"], 3450.0, places=0)
        self.assertEqual(result.iloc[0]["close_order_status"], "Complete")

    def test_sync_entry_opens_new_position(self):
        """Entry correction should set buy_price, status=open, profit_target."""
        row = _base_row(status="new", sl=2800.0, percent_increase=15.0, amount=10000.0)
        _write_csv(self.csv_path, [row])

        corrections = pd.DataFrame([{
            "sl_no": 1, "account": "dummy", "symbol": "RELIANCE",
            "entry_exit": "entry", "price": 3000.0, "date": "2026-04-19",
        }])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[corrections, _read_csv(self.csv_path)]):
            self.strategy.sync_elliot_strategy()

        result = _read_csv(self.csv_path)
        self.assertEqual(result.iloc[0]["status"], "open")
        self.assertAlmostEqual(result.iloc[0]["buy_price"], 3000.0, places=0)
        self.assertAlmostEqual(result.iloc[0]["profit_target"], 3450.0, places=0)

    def test_sync_does_not_reclose_already_closed(self):
        """Exit correction should not re-apply to already closed positions."""
        row = _base_row(
            status="close", account="deepti",
            buy_price=3000.0, sell_price=3450.0,
            close_order_status="Complete",
        )
        _write_csv(self.csv_path, [row])

        corrections = pd.DataFrame([{
            "sl_no": 1, "account": "deepti", "symbol": "RELIANCE",
            "entry_exit": "exit", "price": 2900.0, "date": "2026-04-20",
        }])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[corrections, _read_csv(self.csv_path)]):
            self.strategy.sync_elliot_strategy()

        result = _read_csv(self.csv_path)
        self.assertAlmostEqual(result.iloc[0]["sell_price"], 3450.0, places=0)

    # -----------------------------------------------------------------------
    # 2. _process_new_orders — BUY, profit_target, quantity
    # -----------------------------------------------------------------------

    def test_process_new_orders_places_buy_and_computes_target(self):
        """BUY order should be placed, profit_target computed as percent_increase above buy price."""
        row = _base_row(sl=2800.0, percent_increase=15.0)
        _write_csv(self.csv_path, [row])
        data = _read_csv(self.csv_path)

        po = _make_place_order(buy_order_id="ORD001")

        with patch.object(self.strategy, "get_nse_ltp_with_fallback", return_value=3000.0), \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"dummy_telegram": "CHAT456"}):
            self.strategy._process_new_orders(data, po)

        row_out = data.iloc[0]
        self.assertEqual(row_out["status"], "open")
        self.assertEqual(row_out["buy_order_id"], "ORD001")
        self.assertAlmostEqual(row_out["buy_price"], 3000.0, places=0)
        # profit_target = 3000 * 1.15 = 3450
        self.assertAlmostEqual(row_out["profit_target"], 3450.0, places=0)
        # quantity = int(10000 / 3000) = 3
        self.assertEqual(int(row_out["quantity"]), 3)

    def test_process_new_orders_skips_if_price_below_sl(self):
        """If last_price <= sl, the row should remain 'new' (don't buy into falling stock)."""
        row = _base_row(sl=3100.0, percent_increase=15.0)  # SL above current price
        _write_csv(self.csv_path, [row])
        data = _read_csv(self.csv_path)

        po = _make_place_order()

        with patch.object(self.strategy, "get_nse_ltp_with_fallback", return_value=3000.0):
            self.strategy._process_new_orders(data, po)

        self.assertEqual(data.iloc[0]["status"], "new")
        po.place_cash_order.assert_not_called()

    # -----------------------------------------------------------------------
    # 3. _process_open_positions — SELL on SL/target hit
    # -----------------------------------------------------------------------

    def test_process_open_positions_triggers_close_on_sl(self):
        """When last_price drops to SL, SELL order should fire for deepti account."""
        row = _base_row(
            status="open", account="deepti",
            sl=2900.0, profit_target=3450.0,
            buy_price=3000.0, quantity=3,
        )
        _write_csv(self.csv_path, [row])
        data = _read_csv(self.csv_path)

        po = _make_place_order(buy_order_id="CLOSE001")

        with patch.object(self.strategy, "get_nse_ltp_with_fallback", return_value=2850.0), \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"deepti_telegram": "CHAT123"}):
            self.strategy._process_open_positions(data, po)

        row_out = data.iloc[0]
        self.assertEqual(row_out["close_order_id"], "CLOSE001")
        self.assertEqual(row_out["close_order_status"], "close_pending")

    def test_process_open_positions_triggers_close_on_target(self):
        """When last_price hits profit_target, SELL order should fire for deepti."""
        row = _base_row(
            status="open", account="deepti",
            sl=2800.0, profit_target=3450.0,
            buy_price=3000.0, quantity=3,
        )
        _write_csv(self.csv_path, [row])
        data = _read_csv(self.csv_path)

        po = _make_place_order(buy_order_id="CLOSE002")

        with patch.object(self.strategy, "get_nse_ltp_with_fallback", return_value=3500.0), \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"deepti_telegram": "CHAT123"}):
            self.strategy._process_open_positions(data, po)

        self.assertEqual(data.iloc[0]["close_order_id"], "CLOSE002")

    def test_process_open_positions_dummy_tries_api(self):
        """All accounts (including dummy) try the API sell. Dummy succeeds via mock."""
        row = _base_row(
            status="open", account="dummy",
            sl=2900.0, profit_target=3450.0,
            buy_price=3000.0, quantity=3,
        )
        _write_csv(self.csv_path, [row])
        data = _read_csv(self.csv_path)

        po = _make_place_order(buy_order_id="SELL_DUMMY")

        with patch.object(self.strategy, "get_nse_ltp_with_fallback", return_value=2800.0), \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"dummy_telegram": "CHAT456"}):
            self.strategy._process_open_positions(data, po)

        po.place_cash_order.assert_called_once_with("dummy", "RELIANCE", 3, "SELL")
        self.assertEqual(data.iloc[0]["close_order_id"], "SELL_DUMMY")
        self.assertEqual(data.iloc[0]["close_order_status"], "close_pending")

    def test_process_open_positions_no_action_when_between_sl_and_target(self):
        """When price is between SL and target, no close should be triggered."""
        row = _base_row(
            status="open", account="deepti",
            sl=2800.0, profit_target=3450.0,
            buy_price=3000.0, quantity=3,
        )
        _write_csv(self.csv_path, [row])
        data = _read_csv(self.csv_path)

        po = _make_place_order()

        with patch.object(self.strategy, "get_nse_ltp_with_fallback", return_value=3100.0):
            self.strategy._process_open_positions(data, po)

        po.place_cash_order.assert_not_called()
        self.assertTrue(pd.isna(data.iloc[0].get("close_order_id")))

    # -----------------------------------------------------------------------
    # 4. _process_pending_orders — order confirmed, PnL recorded
    # -----------------------------------------------------------------------

    def test_process_pending_orders_confirms_open_order(self):
        """When open order status = Complete, buy_price updated and profit_target recomputed."""
        row = _base_row(
            status="open",
            buy_order_id="ORD001",
            buy_price=3000.0,
            quantity=3,
            percent_increase=15.0,
            open_order_status="open_pending",
        )
        _write_csv(self.csv_path, [row])
        data = _read_csv(self.csv_path)

        po = _make_place_order(order_status="Complete", fill_price=3020.0)

        self.strategy._process_pending_orders(data, po)

        row_out = data.iloc[0]
        self.assertEqual(row_out["open_order_status"], "Complete")
        self.assertEqual(row_out["status"], "open")
        # profit_target recomputed with actual fill: 3020 * 1.15 = 3473
        self.assertAlmostEqual(row_out["profit_target"], 3020.0 * 1.15, places=0)

    def test_process_pending_orders_confirms_close_and_records_pnl(self):
        """When close order status = Complete for deepti, status → 'close' and PnL written."""
        pnl_dir = os.path.join(self.tmp_dir, "pnl")
        os.makedirs(pnl_dir, exist_ok=True)

        row = _base_row(
            status="open", account="deepti",
            buy_order_id="ORD001",
            buy_price=3000.0,
            quantity=3,
            percent_increase=15.0,
            open_order_status="Complete",
            close_order_id="CLOSE001",
            close_order_status="close_pending",
        )
        _write_csv(self.csv_path, [row])
        data = _read_csv(self.csv_path)

        po = _make_place_order(order_status="Complete", fill_price=3450.0)

        with patch("elliot_cash_stratergy.brokrage_calculator.calculate_equity_delivery",
                   return_value={"total_charges": 50.0}), \
             patch("elliot_cash_stratergy.os.makedirs"), \
             patch("elliot_cash_stratergy.os.path.exists", return_value=False), \
             patch("elliot_cash_stratergy.pd.read_csv") as mock_read, \
             patch("pandas.DataFrame.to_csv"), \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"deepti_telegram": "CHAT123"}):
            mock_read.side_effect = FileNotFoundError
            self.strategy._process_pending_orders(data, po)

        row_out = data.iloc[0]
        self.assertEqual(row_out["status"], "close")
        self.assertAlmostEqual(row_out["sell_price"], 3450.0, places=0)
        self.assertEqual(row_out["close_order_status"], "Complete")


    # -----------------------------------------------------------------------
    # 5. sync_elliot_strategy — entry/exit corrections from Google Sheet
    # -----------------------------------------------------------------------

    def _corrections_df(self, rows):
        return pd.DataFrame(rows, columns=["sl_no", "account", "symbol", "entry_exit", "price", "date"])

    def test_sync_manual_entry_updates_buy_fields(self):
        """Manual 'entry' row should set buy_price, open_date, profit_target, status=open."""
        row = _base_row(status="new", sl=2800.0, percent_increase=15.0, amount=10000.0)
        _write_csv(self.csv_path, [row])

        corrections = self._corrections_df([{
            "sl_no": 1,
            "account": "dummy", "symbol": "RELIANCE",
            "entry_exit": "entry", "price": 3000.0, "date": "2026-04-19",
        }])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[corrections, _read_csv(self.csv_path)]):
            self.strategy.sync_elliot_strategy()

        result = _read_csv(self.csv_path)
        row_out = result.iloc[0]
        self.assertEqual(row_out["status"], "open")
        self.assertAlmostEqual(row_out["buy_price"], 3000.0, places=0)
        self.assertEqual(row_out["open_order_status"], "Complete")
        self.assertAlmostEqual(row_out["profit_target"], 3450.0, places=0)
        self.assertEqual(int(row_out["quantity"]), 3)

    def test_sync_manual_exit_updates_sell_fields(self):
        """Manual 'exit' row should set sell_price, close_date, status=close."""
        row = _base_row(
            status="open", account="dummy",
            buy_price=3000.0, quantity=3,
            open_order_status="Complete",
        )
        _write_csv(self.csv_path, [row])

        corrections = self._corrections_df([{
            "sl_no": 1,
            "account": "dummy", "symbol": "RELIANCE",
            "entry_exit": "exit", "price": 3450.0, "date": "2026-04-25",
        }])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[corrections, _read_csv(self.csv_path)]), \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"dummy_telegram": "CHAT456"}), \
             patch("elliot_cash_stratergy.brokrage_calculator.calculate_equity_delivery",
                   return_value={"total_charges": 30.0}), \
             patch("elliot_cash_stratergy.os.path.exists", return_value=False):
            self.strategy.sync_elliot_strategy()

        result = _read_csv(self.csv_path)
        row_out = result.iloc[0]
        self.assertEqual(row_out["status"], "close")
        self.assertAlmostEqual(row_out["sell_price"], 3450.0, places=0)
        self.assertEqual(row_out["close_order_status"], "Complete")

    def test_sync_entry_no_double_apply(self):
        """Entry correction should not overwrite already-open positions."""
        row = _base_row(
            status="open", buy_price=3000.0,
            open_order_status="Complete", open_date="2026-04-19",
        )
        _write_csv(self.csv_path, [row])

        corrections = self._corrections_df([{
            "sl_no": 1,
            "account": "dummy", "symbol": "RELIANCE",
            "entry_exit": "entry", "price": 2800.0, "date": "2026-04-18",
        }])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[corrections, _read_csv(self.csv_path)]):
            self.strategy.sync_elliot_strategy()

        result = _read_csv(self.csv_path)
        self.assertAlmostEqual(result.iloc[0]["buy_price"], 3000.0, places=0)

    def test_sync_exit_no_double_apply(self):
        """Exit correction should not re-apply to already closed positions."""
        row = _base_row(
            status="close", buy_price=3000.0, sell_price=3450.0,
            open_order_status="Complete", close_order_status="Complete",
            close_date="2026-04-25",
        )
        _write_csv(self.csv_path, [row])

        corrections = self._corrections_df([{
            "sl_no": 1,
            "account": "dummy", "symbol": "RELIANCE",
            "entry_exit": "exit", "price": 2900.0, "date": "2026-04-20",
        }])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[corrections, _read_csv(self.csv_path)]):
            self.strategy.sync_elliot_strategy()

        result = _read_csv(self.csv_path)
        self.assertAlmostEqual(result.iloc[0]["sell_price"], 3450.0, places=0)

    def test_sync_unknown_symbol_skipped(self):
        """Correction for a symbol not in local CSV should be silently skipped."""
        row = _base_row(status="new")
        _write_csv(self.csv_path, [row])

        corrections = self._corrections_df([{
            "sl_no": 1,
            "account": "dummy", "symbol": "XYZ",
            "entry_exit": "entry", "price": 500.0, "date": "2026-04-19",
        }])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[corrections, _read_csv(self.csv_path)]):
            self.strategy.sync_elliot_strategy()

        result = _read_csv(self.csv_path)
        self.assertEqual(result.iloc[0]["status"], "new")

    def test_sync_exit_matches_by_symbol_account(self):
        """Exit correction matches by account+symbol (sheet sl_no is just a row number)."""
        row = _base_row(
            status="open", account="deepti",
            sl_no="EW_20260506_M&M_deepti", symbol="M&M",
            buy_price=3000.0, quantity=3,
            open_order_status="Complete",
        )
        _write_csv(self.csv_path, [row])

        corrections = self._corrections_df([{
            "sl_no": 1,
            "account": "deepti", "symbol": "M&M",
            "entry_exit": "exit", "price": 3120.0, "date": "2026-05-18",
        }])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[corrections, _read_csv(self.csv_path)]), \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"deepti_telegram": "CHAT123"}), \
             patch("elliot_cash_stratergy.brokrage_calculator.calculate_equity_delivery",
                   return_value={"total_charges": 30.0}), \
             patch("elliot_cash_stratergy.os.path.exists", return_value=False):
            self.strategy.sync_elliot_strategy()

        result = _read_csv(self.csv_path)
        row_out = result.iloc[0]
        self.assertEqual(row_out["status"], "close")
        self.assertAlmostEqual(row_out["sell_price"], 3120.0, places=0)
        self.assertEqual(row_out["close_order_status"], "Complete")

    def test_sync_empty_sheet_no_op(self):
        """Empty corrections sheet should not modify local CSV."""
        row = _base_row(status="new")
        _write_csv(self.csv_path, [row])

        corrections = self._corrections_df([])

        with patch("elliot_cash_stratergy.pd.read_csv", return_value=corrections):
            self.strategy.sync_elliot_strategy()

        result = _read_csv(self.csv_path)
        self.assertEqual(result.iloc[0]["status"], "new")

    def test_sync_symbol_variant_matching(self):
        """ASIANPAINTS in sheet should match ASIANPAINT in local CSV."""
        row = _base_row(
            status="open", account="deepti",
            sl_no="EW_20260514_ASIANPAINT_deepti", symbol="ASIANPAINT",
            buy_price=2579.0, quantity=3,
            open_order_status="Complete",
        )
        _write_csv(self.csv_path, [row])

        corrections = self._corrections_df([{
            "sl_no": 1,
            "account": "deepti", "symbol": "ASIANPAINTS",
            "entry_exit": "exit", "price": 2700.0, "date": "2026-06-01",
        }])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[corrections, _read_csv(self.csv_path)]), \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"deepti_telegram": "CHAT123"}), \
             patch("elliot_cash_stratergy.brokrage_calculator.calculate_equity_delivery",
                   return_value={"total_charges": 30.0}), \
             patch("elliot_cash_stratergy.os.path.exists", return_value=False):
            self.strategy.sync_elliot_strategy()

        result = _read_csv(self.csv_path)
        self.assertEqual(result.iloc[0]["status"], "close")
        self.assertAlmostEqual(result.iloc[0]["sell_price"], 2700.0, places=0)


    # -----------------------------------------------------------------------
    # 6. Sell failure — telegram, retry, sync resolution
    # -----------------------------------------------------------------------

    def test_sell_fails_sends_telegram(self):
        """When SELL order fails after retries, sell_failed status is set and telegram sent."""
        row = _base_row(
            status="open", account="deepti",
            sl=2900.0, profit_target=3450.0,
            buy_price=3000.0, quantity=3,
        )
        _write_csv(self.csv_path, [row])
        data = _read_csv(self.csv_path)

        po = _make_place_order()
        po.place_cash_order.return_value = None  # sell always fails

        with patch.object(self.strategy, "get_nse_ltp_with_fallback", return_value=2800.0), \
             patch.object(self.strategy.notifier, "send_sell_failed") as mock_notify, \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"deepti_telegram": "CHAT123"}):
            self.strategy._process_open_positions(data, po)

        self.assertEqual(data.iloc[0]["close_order_status"], "sell_failed")
        self.assertEqual(data.iloc[0]["status"], "open")
        mock_notify.assert_called_once_with("deepti", "RELIANCE", 3, 2800.0)

    def test_sell_fails_retry_succeeds(self):
        """sell_failed row is re-evaluated next cycle; if exit still triggered and sell succeeds, close_pending is set."""
        row = _base_row(
            status="open", account="deepti",
            sl=2900.0, profit_target=3450.0,
            buy_price=3000.0, quantity=3,
            close_order_status="sell_failed",
        )
        _write_csv(self.csv_path, [row])
        data = _read_csv(self.csv_path)

        po = _make_place_order(buy_order_id="RETRY_SELL_OK")

        with patch.object(self.strategy, "get_nse_ltp_with_fallback", return_value=2800.0), \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"deepti_telegram": "CHAT123"}):
            self.strategy._process_open_positions(data, po)

        self.assertEqual(data.iloc[0]["close_order_id"], "RETRY_SELL_OK")
        self.assertEqual(data.iloc[0]["close_order_status"], "close_pending")

    def test_sell_failed_keeps_sending_telegram(self):
        """sell_failed row retried next cycle — if sell fails again, telegram is sent again."""
        row = _base_row(
            status="open", account="deepti",
            sl=2900.0, profit_target=3450.0,
            buy_price=3000.0, quantity=3,
            close_order_status="sell_failed",
        )
        _write_csv(self.csv_path, [row])
        data = _read_csv(self.csv_path)

        po = _make_place_order()
        po.place_cash_order.return_value = None  # sell fails again

        with patch.object(self.strategy, "get_nse_ltp_with_fallback", return_value=2800.0), \
             patch.object(self.strategy.notifier, "send_sell_failed") as mock_notify, \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"deepti_telegram": "CHAT123"}):
            self.strategy._process_open_positions(data, po)

        self.assertEqual(data.iloc[0]["close_order_status"], "sell_failed")
        mock_notify.assert_called_once()

    def test_sell_fails_sync_resolves_with_pnl(self):
        """sell_failed row resolved by Google Sheet exit correction — P&L generated."""
        row = _base_row(
            status="open", account="deepti",
            sl_no="EW_20260601_RELIANCE_deepti",
            buy_price=3000.0, quantity=3,
            open_order_status="Complete",
            close_order_status="sell_failed",
        )
        _write_csv(self.csv_path, [row])

        corrections = self._corrections_df([{
            "sl_no": 1,
            "account": "deepti", "symbol": "RELIANCE",
            "entry_exit": "exit", "price": 3200.0, "date": "2026-06-10",
        }])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[corrections, _read_csv(self.csv_path)]), \
             patch.object(self.strategy.notifier, "send_success") as mock_pnl, \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"deepti_telegram": "CHAT123"}), \
             patch("elliot_cash_stratergy.brokrage_calculator.calculate_equity_delivery",
                   return_value={"total_charges": 30.0}), \
             patch("elliot_cash_stratergy.os.path.exists", return_value=False):
            self.strategy.sync_elliot_strategy()

        result = _read_csv(self.csv_path)
        self.assertEqual(result.iloc[0]["status"], "close")
        self.assertEqual(result.iloc[0]["close_order_status"], "Complete")
        self.assertAlmostEqual(result.iloc[0]["sell_price"], 3200.0, places=0)
        mock_pnl.assert_called_once()
        pnl_args = mock_pnl.call_args[0]
        self.assertEqual(pnl_args[0], "deepti")
        self.assertEqual(pnl_args[2], "p/l")

    def test_non_deepti_sell_tries_api(self):
        """Non-deepti accounts also try the API sell, not immediate manual close."""
        row = _base_row(
            status="open", account="dummy",
            sl=2900.0, profit_target=3450.0,
            buy_price=3000.0, quantity=3,
        )
        _write_csv(self.csv_path, [row])
        data = _read_csv(self.csv_path)

        po = _make_place_order()
        po.place_cash_order.return_value = None  # API fails for this account

        with patch.object(self.strategy, "get_nse_ltp_with_fallback", return_value=2800.0), \
             patch.object(self.strategy.notifier, "send_sell_failed") as mock_notify, \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"dummy_telegram": "CHAT456"}):
            self.strategy._process_open_positions(data, po)

        po.place_cash_order.assert_called()
        self.assertEqual(data.iloc[0]["close_order_status"], "sell_failed")
        self.assertEqual(data.iloc[0]["status"], "open")
        mock_notify.assert_called_once()

    # -----------------------------------------------------------------------
    # 7. Buy failure — retry, sync resolution
    # -----------------------------------------------------------------------

    def test_buy_fails_sends_telegram(self):
        """When BUY fails, buy_failed is set and send_buy_failed telegram is sent."""
        row = _base_row(sl=2800.0, percent_increase=15.0)
        _write_csv(self.csv_path, [row])
        data = _read_csv(self.csv_path)

        po = _make_place_order()
        po.place_cash_order.return_value = None  # buy always fails

        with patch.object(self.strategy, "get_nse_ltp_with_fallback", return_value=3000.0), \
             patch.object(self.strategy.notifier, "send_buy_failed") as mock_notify, \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"dummy_telegram": "CHAT456"}):
            self.strategy._process_new_orders(data, po)

        self.assertEqual(data.iloc[0]["open_order_status"], "buy_failed")
        self.assertEqual(data.iloc[0]["status"], "new")
        mock_notify.assert_called_once()

    def test_buy_fails_retry_succeeds(self):
        """buy_failed row retried next cycle — if buy succeeds, status becomes open."""
        row = _base_row(sl=2800.0, percent_increase=15.0, open_order_status="buy_failed")
        _write_csv(self.csv_path, [row])
        data = _read_csv(self.csv_path)

        po = _make_place_order(buy_order_id="RETRY_BUY_OK")

        with patch.object(self.strategy, "get_nse_ltp_with_fallback", return_value=3000.0), \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"dummy_telegram": "CHAT456"}):
            self.strategy._process_new_orders(data, po)

        self.assertEqual(data.iloc[0]["status"], "open")
        self.assertEqual(data.iloc[0]["buy_order_id"], "RETRY_BUY_OK")

    def test_buy_fails_sync_resolves(self):
        """buy_failed row resolved by Google Sheet entry correction."""
        row = _base_row(
            status="new", open_order_status="buy_failed",
            sl=2800.0, percent_increase=15.0, amount=10000.0,
        )
        _write_csv(self.csv_path, [row])

        corrections = self._corrections_df([{
            "sl_no": 1,
            "account": "dummy", "symbol": "RELIANCE",
            "entry_exit": "entry", "price": 3100.0, "date": "2026-06-10",
        }])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[corrections, _read_csv(self.csv_path)]):
            self.strategy.sync_elliot_strategy()

        result = _read_csv(self.csv_path)
        self.assertEqual(result.iloc[0]["status"], "open")
        self.assertEqual(result.iloc[0]["open_order_status"], "Complete")
        self.assertAlmostEqual(result.iloc[0]["buy_price"], 3100.0, places=0)

    # -----------------------------------------------------------------------
    # 8. Execution order — sync before processing
    # -----------------------------------------------------------------------

    def test_sync_runs_before_order_processing(self):
        """execute_strategy calls sync_elliot_strategy before _process_new_orders."""
        call_order = []

        fake_now = datetime(2026, 6, 17, 9, 28, 0)

        with patch.object(self.strategy, "sync_elliot_strategy",
                          side_effect=lambda: call_order.append("sync")), \
             patch.object(self.strategy, "_process_new_orders",
                          side_effect=lambda *a: call_order.append("new")), \
             patch.object(self.strategy, "_process_open_positions",
                          side_effect=lambda *a, **kw: (call_order.append("open"), (None, True))[-1]), \
             patch.object(self.strategy, "_process_pending_orders",
                          side_effect=lambda *a: call_order.append("pending")), \
             patch("elliot_cash_stratergy.pd.read_csv",
                   return_value=pd.DataFrame([_base_row()])), \
             patch("pandas.DataFrame.to_csv"), \
             patch("elliot_cash_stratergy.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.strptime.side_effect = datetime.strptime
            self.strategy.execute_strategy(_make_place_order())

        self.assertTrue(len(call_order) >= 2)
        self.assertEqual(call_order[0], "sync")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestRejectedBuyIsNotManagedAsOpen(unittest.TestCase):
    """A broker-rejected BUY must not leave the row 'open' (deepti TCS, Oct 2026)."""

    def _run(self, status, is_buy=True):
        s = ElliotCashStratergy.__new__(ElliotCashStratergy)
        s.csv_path = os.path.join(tempfile.mkdtemp(), 'x.csv')
        s.notifier = MagicMock()
        row = _base_row(status='open', account='deepti', symbol='TCS',
                        open_order_status='open_pending' if is_buy else 'Complete',
                        close_order_status='' if is_buy else 'close_pending',
                        buy_order_id=111, close_order_id=222)
        data = pd.DataFrame([row])
        po = MagicMock()
        po.order_status.return_value = (status, 0)
        s._process_pending_orders(data, po)
        return data.loc[0]

    def test_rejected_buy_closes_row(self):
        r = self._run('Rejected')
        self.assertEqual(r['open_order_status'], 'rejected')
        self.assertEqual(r['status'], 'close')

    def test_rejected_sell_keeps_row_open_for_retry(self):
        r = self._run('Rejected', is_buy=False)
        self.assertEqual(r['close_order_status'], 'rejected')
        self.assertEqual(r['status'], 'open')

    def test_pending_buy_stays_open(self):
        self.assertEqual(self._run('Open')['status'], 'open')


class TestStaleNotFoundOrders(unittest.TestCase):
    """Orders the broker can no longer find must be surfaced, not polled silently forever."""

    def _strategy(self):
        s = ElliotCashStratergy.__new__(ElliotCashStratergy)
        s.csv_path = os.path.join(tempfile.mkdtemp(), 'x.csv')
        s.notifier = MagicMock()
        s._notfound_counts = {}
        s._notfound_alert_day = {}
        return s

    def _data(self):
        return pd.DataFrame([
            _base_row(sl_no='A', status='open', account='deepti', symbol='SBIN', open_order_status='Complete',
                      close_order_status='close_pending', buy_order_id=1, close_order_id=260824000000001),
            _base_row(sl_no='B', status='open', account='deepti', symbol='INFY', open_order_status='Complete',
                      close_order_status='close_pending', buy_order_id=2, close_order_id=260907000000002),
        ])

    def _poll(self, s, data, status='NotFound'):
        po = MagicMock()
        po.order_status.return_value = (status, -1)
        s._process_pending_orders(data, po)

    def test_alerts_once_after_three_notfound_polls_with_all_rows(self):
        s, data = self._strategy(), self._data()
        self._poll(s, data); self._poll(s, data)
        s.notifier.send_stale_orders.assert_not_called()
        self._poll(s, data)
        s.notifier.send_stale_orders.assert_called_once()
        account, rows = s.notifier.send_stale_orders.call_args.args
        self.assertEqual(account, 'deepti')
        self.assertEqual({r[0] for r in rows}, {'SBIN', 'INFY'})
        self._poll(s, data); self._poll(s, data)          # same day: no repeat
        s.notifier.send_stale_orders.assert_called_once()

    def test_alerts_again_next_day(self):
        s, data = self._strategy(), self._data()
        for _ in range(3):
            self._poll(s, data)
        s._notfound_alert_day['deepti'] = datetime.now().date().replace(year=2020)
        self._poll(s, data)
        self.assertEqual(s.notifier.send_stale_orders.call_count, 2)

    def test_rows_are_left_untouched(self):
        s, data = self._strategy(), self._data()
        for _ in range(4):
            self._poll(s, data)
        self.assertTrue((data['status'] == 'open').all())
        self.assertTrue((data['close_order_status'] == 'close_pending').all())

    def test_other_status_resets_the_counter(self):
        s, data = self._strategy(), self._data()
        self._poll(s, data); self._poll(s, data)
        self._poll(s, data, status='Open')
        self._poll(s, data); self._poll(s, data)
        s.notifier.send_stale_orders.assert_not_called()

    def test_failed_alert_is_retried_next_cycle(self):
        s, data = self._strategy(), self._data()
        s.notifier.send_stale_orders.return_value = False
        for _ in range(4):
            self._poll(s, data)
        self.assertEqual(s.notifier.send_stale_orders.call_count, 2)   # polls 3 and 4
