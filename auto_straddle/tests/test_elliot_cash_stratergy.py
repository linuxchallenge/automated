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
import sys
import unittest
import tempfile
from datetime import datetime, date
from unittest.mock import MagicMock, patch

import pandas as pd

# sys.path is managed by conftest.py

from elliot_cash_stratergy import ElliotCashStratergy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_row(**kwargs):
    """Return a minimal CSV row dict with sensible defaults."""
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
        "profit_target": None,
        "buy_order_id": None,
        "buy_price": None,
        "quantity": None,
        "open_order_status": None,
        "open_date": None,
        "close_order_id": None,
        "close_order_status": None,
        "sell_price": None,
        "close_date": None,
    }
    defaults.update(kwargs)
    return defaults


def _write_csv(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)


def _read_csv(path):
    return pd.read_csv(path)


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
        # Bypass market-open check in tests
        self.strategy.nso_open = True

    # -----------------------------------------------------------------------
    # 1. sync_elliot_strategy — inserts new rows from remote
    # -----------------------------------------------------------------------

    def test_sync_inserts_new_rows(self):
        """Rows in remote sheet that are absent locally should be inserted."""
        # No local CSV — triggers FileNotFoundError path in sync
        if os.path.exists(self.csv_path):
            os.remove(self.csv_path)

        remote_df = pd.DataFrame([_base_row(date="2026-04-20")])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[
            remote_df,               # remote download
            FileNotFoundError(),     # local CSV missing → triggers create-new path
        ]):
            self.strategy.sync_elliot_strategy()

        result = _read_csv(self.csv_path)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["sl_no"], "EW_20260419_RELIANCE_dummy")
        self.assertEqual(result.iloc[0]["status"], "new")

    def test_sync_updates_row_when_remote_date_newer(self):
        """If remote date > local date, the local row should be updated."""
        local_row = _base_row(date="2026-04-19", status="new")
        _write_csv(self.csv_path, [local_row])

        # Remote has same sl_no but newer date and updated buy_price
        remote_row = _base_row(date="2026-04-20", status="open", buy_price=3050.0)
        remote_df = pd.DataFrame([remote_row])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[
            remote_df,
            _read_csv(self.csv_path),
        ]):
            self.strategy.sync_elliot_strategy()

        result = _read_csv(self.csv_path)
        self.assertEqual(result.iloc[0]["status"], "open")
        self.assertAlmostEqual(result.iloc[0]["buy_price"], 3050.0, places=0)

    def test_sync_does_not_update_when_local_date_newer(self):
        """If local date >= remote date, local row should NOT be overwritten."""
        local_row = _base_row(date="2026-04-21", status="open")
        _write_csv(self.csv_path, [local_row])

        remote_row = _base_row(date="2026-04-19", status="new")
        remote_df = pd.DataFrame([remote_row])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[
            remote_df,
            _read_csv(self.csv_path),
        ]):
            self.strategy.sync_elliot_strategy()

        result = _read_csv(self.csv_path)
        self.assertEqual(result.iloc[0]["status"], "open", "Local status should not be reverted")

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

    def test_process_open_positions_dummy_sends_telegram(self):
        """For dummy/non-API accounts, manual close request should be sent (no API call)."""
        row = _base_row(
            status="open", account="dummy",
            sl=2900.0, profit_target=3450.0,
            buy_price=3000.0, quantity=3,
        )
        _write_csv(self.csv_path, [row])
        data = _read_csv(self.csv_path)

        po = _make_place_order()

        with patch.object(self.strategy, "get_nse_ltp_with_fallback", return_value=2800.0), \
             patch.object(self.strategy.notifier, "send_manual_close_request") as mock_close, \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"dummy_telegram": "CHAT456"}):
            self.strategy._process_open_positions(data, po)

        # No API sell call for dummy
        po.place_cash_order.assert_not_called()
        # Telegram manual close request sent
        mock_close.assert_called_once_with("dummy", "RELIANCE")
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
    # 5. sync_manual_corrections — entry/exit from Google Sheet
    # -----------------------------------------------------------------------

    MANUAL_CORRECTIONS_URL = (
        "https://docs.google.com/spreadsheets/d/e/"
        "2PACX-1vTruc_tyeub2h90CDyKxbZ2eggT97R__8a3JLcavhEBhCdfjr9YxvK_U-trRNDQsiaQv8Ec1oHk4y3I"
        "/pub?output=csv"
    )

    def _corrections_df(self, rows):
        return pd.DataFrame(rows, columns=["sl_no", "account", "symbol", "entry_exit", "price", "date"])

    def test_manual_entry_updates_buy_fields(self):
        """Manual 'entry' row should set buy_price, open_date, profit_target, status=open."""
        row = _base_row(status="new", sl=2800.0, percent_increase=15.0, amount=10000.0)
        _write_csv(self.csv_path, [row])

        corrections = self._corrections_df([{
            "sl_no": "EW_20260419_RELIANCE_dummy",
            "account": "dummy", "symbol": "RELIANCE",
            "entry_exit": "entry", "price": 3000.0, "date": "2026-04-19",
        }])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[corrections, _read_csv(self.csv_path)]), \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"dummy_telegram": "CHAT456"}):
            self.strategy.sync_manual_corrections()

        result = _read_csv(self.csv_path)
        row_out = result.iloc[0]
        self.assertEqual(row_out["status"], "open")
        self.assertAlmostEqual(row_out["buy_price"], 3000.0, places=0)
        self.assertEqual(row_out["open_order_status"], "Complete")
        # profit_target = 3000 * 1.15 = 3450
        self.assertAlmostEqual(row_out["profit_target"], 3450.0, places=0)
        # quantity = int(10000 / 3000) = 3
        self.assertEqual(int(row_out["quantity"]), 3)

    def test_manual_exit_updates_sell_fields(self):
        """Manual 'exit' row should set sell_price, close_date, status=close."""
        row = _base_row(
            status="open", account="dummy",
            buy_price=3000.0, quantity=3,
            open_order_status="Complete",
        )
        _write_csv(self.csv_path, [row])

        corrections = self._corrections_df([{
            "sl_no": "EW_20260419_RELIANCE_dummy",
            "account": "dummy", "symbol": "RELIANCE",
            "entry_exit": "exit", "price": 3450.0, "date": "2026-04-25",
        }])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[corrections, _read_csv(self.csv_path)]), \
             patch("elliot_cash_stratergy.configuration.ConfigurationLoader.get_configuration",
                   return_value={"dummy_telegram": "CHAT456"}):
            self.strategy.sync_manual_corrections()

        result = _read_csv(self.csv_path)
        row_out = result.iloc[0]
        self.assertEqual(row_out["status"], "close")
        self.assertAlmostEqual(row_out["sell_price"], 3450.0, places=0)
        self.assertEqual(row_out["close_order_status"], "Complete")

    def test_manual_entry_no_double_apply(self):
        """Entry correction with same or older date must not overwrite existing open_date."""
        row = _base_row(
            status="open", buy_price=3000.0,
            open_order_status="Complete", open_date="2026-04-19",
        )
        _write_csv(self.csv_path, [row])

        corrections = self._corrections_df([{
            "sl_no": "EW_20260419_RELIANCE_dummy",
            "account": "dummy", "symbol": "RELIANCE",
            "entry_exit": "entry", "price": 2800.0, "date": "2026-04-18",  # older date
        }])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[corrections, _read_csv(self.csv_path)]):
            self.strategy.sync_manual_corrections()

        result = _read_csv(self.csv_path)
        # buy_price must remain at original 3000, not be overwritten with 2800
        self.assertAlmostEqual(result.iloc[0]["buy_price"], 3000.0, places=0)

    def test_manual_exit_no_double_apply(self):
        """Exit correction with same or older date must not overwrite existing close_date."""
        row = _base_row(
            status="close", buy_price=3000.0, sell_price=3450.0,
            open_order_status="Complete", close_order_status="Complete",
            close_date="2026-04-25",
        )
        _write_csv(self.csv_path, [row])

        corrections = self._corrections_df([{
            "sl_no": "EW_20260419_RELIANCE_dummy",
            "account": "dummy", "symbol": "RELIANCE",
            "entry_exit": "exit", "price": 2900.0, "date": "2026-04-20",  # older date
        }])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[corrections, _read_csv(self.csv_path)]):
            self.strategy.sync_manual_corrections()

        result = _read_csv(self.csv_path)
        self.assertAlmostEqual(result.iloc[0]["sell_price"], 3450.0, places=0)

    def test_manual_correction_unknown_sl_no_skipped(self):
        """Correction for an sl_no not in local CSV should be silently skipped."""
        row = _base_row(status="new")
        _write_csv(self.csv_path, [row])

        corrections = self._corrections_df([{
            "sl_no": "EW_UNKNOWN_XYZ_dummy",
            "account": "dummy", "symbol": "XYZ",
            "entry_exit": "entry", "price": 500.0, "date": "2026-04-19",
        }])

        with patch("elliot_cash_stratergy.pd.read_csv", side_effect=[corrections, _read_csv(self.csv_path)]):
            self.strategy.sync_manual_corrections()

        # Original row untouched
        result = _read_csv(self.csv_path)
        self.assertEqual(result.iloc[0]["status"], "new")

    def test_manual_correction_empty_sheet_no_op(self):
        """Empty corrections sheet should not modify local CSV."""
        row = _base_row(status="new")
        _write_csv(self.csv_path, [row])

        corrections = self._corrections_df([])   # empty

        with patch("elliot_cash_stratergy.pd.read_csv", return_value=corrections):
            self.strategy.sync_manual_corrections()

        result = _read_csv(self.csv_path)
        self.assertEqual(result.iloc[0]["status"], "new")


if __name__ == "__main__":
    unittest.main(verbosity=2)
