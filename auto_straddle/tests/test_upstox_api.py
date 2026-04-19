"""Unit tests for upstox_api module.

All HTTP calls and file I/O are mocked so no real credentials or network
access is required.  Run with:
    python -m pytest upstox/test_upstox_api.py -v
"""

import unittest
from unittest.mock import patch, MagicMock, mock_open
from datetime import datetime, timedelta
import pandas as pd
import json


# ---------------------------------------------------------------------------
# Helper: build a minimal instrument DataFrame that covers every code-path
# ---------------------------------------------------------------------------
def _make_token_df():
    """Return a small DataFrame that mimics Upstox instrument CSV columns."""
    far_expiry = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    near_expiry = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
    rows = [
        # NSE equity
        {"exchange": "NSE_EQ", "instrument_type": "EQUITY", "name": "RELIANCE",
         "instrument_key": "NSE_EQ|INE002A01018", "lot_size": 1,
         "strike": "", "option_type": "", "expiry": ""},
        # NSE_FO OPTIDX – NIFTY CE
        {"exchange": "NSE_FO", "instrument_type": "OPTIDX", "name": "NIFTY",
         "instrument_key": "NSE_FO|123456", "lot_size": 50,
         "strike": "24000.0", "option_type": "CE", "expiry": far_expiry},
        # NSE_FO OPTIDX – NIFTY PE
        {"exchange": "NSE_FO", "instrument_type": "OPTIDX", "name": "NIFTY",
         "instrument_key": "NSE_FO|123457", "lot_size": 50,
         "strike": "24000.0", "option_type": "PE", "expiry": far_expiry},
        # NSE_FO FUTIDX
        {"exchange": "NSE_FO", "instrument_type": "FUTIDX", "name": "NIFTY",
         "instrument_key": "NSE_FO|111111", "lot_size": 50,
         "strike": "", "option_type": "", "expiry": far_expiry},
        # NSE_FO FUTIDX near expiry (<=10 days)
        {"exchange": "NSE_FO", "instrument_type": "FUTIDX", "name": "NIFTY",
         "instrument_key": "NSE_FO|111112", "lot_size": 50,
         "strike": "", "option_type": "", "expiry": near_expiry},
        # MCX_FO FUTCOM
        {"exchange": "MCX_FO", "instrument_type": "FUTCOM", "name": "GOLDM",
         "instrument_key": "MCX_FO|222222", "lot_size": 100,
         "strike": "", "option_type": "", "expiry": far_expiry},
        # MCX_FO FUTCOM near expiry
        {"exchange": "MCX_FO", "instrument_type": "FUTCOM", "name": "GOLDM",
         "instrument_key": "MCX_FO|222223", "lot_size": 100,
         "strike": "", "option_type": "", "expiry": near_expiry},
    ]
    df = pd.DataFrame(rows)
    df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# Patch list used by every test to avoid real __init__ side-effects
# ---------------------------------------------------------------------------
_INIT_PATCHES = [
    "upstox.upstox_api.credentials",
    "upstox.upstox_api.requests",
]


def _build_api(mock_requests, mock_creds):
    """Instantiate upstox_api with mocked auth + instrument download."""
    mock_creds.API_KEY = "test_key"
    mock_creds.API_SECRET = "test_secret"
    mock_creds.REDIRECT_URI = "https://localhost/"

    # _authenticate now calls _headless_login — mock it to return a fake token
    with patch("upstox.upstox_api.upstox_api._headless_login", return_value="fake_access_token"):
        with patch("builtins.open", mock_open()):
            with patch("upstox.upstox_api.pd.read_csv") as mock_read_csv:
                mock_read_csv.return_value = _make_token_df()
                with patch.object(pd.DataFrame, "to_csv"):  # skip writing CSV
                    from upstox.upstox_api import upstox_api
                    api = upstox_api()
    return api


# ===================================================================
# TEST CLASSES
# ===================================================================

class TestAuthentication(unittest.TestCase):
    """Test _authenticate delegates to _headless_login and retries on failure."""

    @patch("upstox.upstox_api.credentials")
    @patch("upstox.upstox_api.requests")
    def test_auth_calls_headless_login(self, mock_requests, mock_creds):
        """_authenticate should call _headless_login and store the returned token."""
        api = _build_api(mock_requests, mock_creds)
        self.assertEqual(api.access_token, "fake_access_token")

    @patch("upstox.upstox_api.credentials")
    @patch("upstox.upstox_api.requests")
    def test_auth_retries_on_failure(self, mock_requests, mock_creds):
        """_authenticate should retry _headless_login up to 3 times on exception."""
        mock_creds.API_KEY = "key"
        mock_creds.API_SECRET = "secret"
        mock_creds.REDIRECT_URI = "http://localhost/"

        call_count = {"n": 0}

        def headless_side_effect(self_inner):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise Exception("transient error")
            return "retried_token"

        with patch("upstox.upstox_api.upstox_api._headless_login", headless_side_effect):
            with patch("builtins.open", mock_open()):
                with patch("upstox.upstox_api.pd.read_csv", return_value=_make_token_df()):
                    with patch.object(pd.DataFrame, "to_csv"):
                        with patch("upstox.upstox_api.time.sleep"):
                            from upstox.upstox_api import upstox_api
                            api = upstox_api()

        self.assertEqual(api.access_token, "retried_token")
        self.assertEqual(call_count["n"], 3)


class TestGetHeaders(unittest.TestCase):
    @patch("upstox.upstox_api.credentials")
    @patch("upstox.upstox_api.requests")
    def test_headers_contain_bearer_token(self, mock_requests, mock_creds):
        api = _build_api(mock_requests, mock_creds)
        headers = api.get_headers()
        self.assertIn("Bearer", headers["Authorization"])
        self.assertEqual(headers["Content-Type"], "application/json")


class TestGetTokenInfo(unittest.TestCase):
    @patch("upstox.upstox_api.credentials")
    @patch("upstox.upstox_api.requests")
    def setUp(self, mock_requests, mock_creds):  # pylint: disable=arguments-differ
        self.api = _build_api(mock_requests, mock_creds)

    def test_nse_equity_lookup(self):
        result = self.api.getTokenInfo("NSE", "EQUITY", "RELIANCE", 0, "X")
        self.assertFalse(result.empty)
        self.assertEqual(result.iloc[0]["instrument_key"], "NSE_EQ|INE002A01018")

    def test_nfo_optidx_lookup(self):
        result = self.api.getTokenInfo("NFO", "OPTIDX", "NIFTY", 24000, "CE")
        self.assertFalse(result.empty)
        self.assertEqual(result.iloc[0]["instrument_key"], "NSE_FO|123456")

    def test_mcx_futcom_lookup(self):
        result = self.api.getTokenInfo("MCX", "FUTCOM", "GOLDM", 0, "X")
        self.assertFalse(result.empty)

    def test_nfo_futidx_lookup(self):
        result = self.api.getTokenInfo("NFO", "FUTIDX", "NIFTY", 0, "X")
        self.assertFalse(result.empty)

    def test_unknown_exchange_returns_empty(self):
        result = self.api.getTokenInfo("UNKNOWN", "XX", "XYZ", 0, "X")
        self.assertTrue(result.empty)


class TestPlaceUpstoxOrder(unittest.TestCase):
    @patch("upstox.upstox_api.credentials")
    @patch("upstox.upstox_api.requests")
    def setUp(self, mock_requests, mock_creds):  # pylint: disable=arguments-differ
        self.api = _build_api(mock_requests, mock_creds)

    @patch("upstox.upstox_api.requests.post")
    def test_successful_order(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "success",
            "data": {"order_id": "ORD123"}
        }
        mock_post.return_value = mock_resp

        result = self.api._place_upstox_order({"quantity": 1})
        self.assertEqual(result, "ORD123")
        # Verify correct HFT order endpoint is used (not api.upstox.com)
        call_url = mock_post.call_args[0][0]
        self.assertEqual(call_url, "https://api-hft.upstox.com/v2/order/place")

    @patch("upstox.upstox_api.requests.post")
    def test_failed_order_returns_none(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"status": "error", "message": "Bad request"}
        mock_post.return_value = mock_resp

        result = self.api._place_upstox_order({"quantity": 1})
        self.assertIsNone(result)

    @patch("upstox.upstox_api.requests.post")
    def test_timeout_propagates(self, mock_post):
        """_place_upstox_order now propagates Timeout so callers can retry."""
        import requests as real_requests
        mock_post.side_effect = real_requests.exceptions.Timeout("timeout")
        with self.assertRaises(real_requests.exceptions.Timeout):
            self.api._place_upstox_order({"quantity": 1})


class TestPlaceOrderCash(unittest.TestCase):
    @patch("upstox.upstox_api.credentials")
    @patch("upstox.upstox_api.requests")
    def setUp(self, mock_requests, mock_creds):  # pylint: disable=arguments-differ
        self.api = _build_api(mock_requests, mock_creds)

    @patch("upstox.upstox_api.requests.post")
    def test_place_order_cash_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "data": {"order_id": "CASH1"}}
        mock_post.return_value = mock_resp

        result = self.api.place_order_cash("RELIANCE", 1, "BUY")
        self.assertEqual(result, "CASH1")

    def test_place_order_cash_unknown_symbol(self):
        result = self.api.place_order_cash("NONEXISTENT", 1, "BUY")
        self.assertEqual(result, -1)


class TestPlaceOrderCommodity(unittest.TestCase):
    @patch("upstox.upstox_api.credentials")
    @patch("upstox.upstox_api.requests")
    def setUp(self, mock_requests, mock_creds):  # pylint: disable=arguments-differ
        self.api = _build_api(mock_requests, mock_creds)

    @patch("upstox.upstox_api.requests.post")
    def test_place_commodity_gold_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "data": {"order_id": "COM1"}}
        mock_post.return_value = mock_resp

        order_id, expiry = self.api.place_order_commodity("GOLD", 1, "BUY")
        self.assertEqual(order_id, "COM1")
        self.assertIsNotNone(expiry)

    def test_place_commodity_unknown_returns_error(self):
        order_id, expiry = self.api.place_order_commodity("UNKNOWN_COMMODITY", 1, "BUY")
        self.assertEqual(order_id, -1)


class TestPlaceOrder(unittest.TestCase):
    @patch("upstox.upstox_api.credentials")
    @patch("upstox.upstox_api.requests")
    def setUp(self, mock_requests, mock_creds):  # pylint: disable=arguments-differ
        self.api = _build_api(mock_requests, mock_creds)

    @patch("upstox.upstox_api.requests.post")
    def test_place_order_option_sell_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "data": {"order_id": "OPT1"}}
        mock_post.return_value = mock_resp

        result = self.api.place_order("NIFTY", 50, "SELL", 24000, "CE", intraday=True)
        self.assertEqual(result, "OPT1")

    @patch("upstox.upstox_api.requests.post")
    def test_place_order_carryforward(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "data": {"order_id": "OPT2"}}
        mock_post.return_value = mock_resp

        result = self.api.place_order("NIFTY", 50, "SELL", 24000, "CE", intraday=False)
        self.assertEqual(result, "OPT2")

    def test_place_order_bad_lot_size(self):
        result = self.api.place_order("NIFTY", 17, "SELL", 24000, "CE")
        self.assertEqual(result, -1)

    def test_place_order_no_token(self):
        result = self.api.place_order("BANKNIFTY", 50, "BUY", 99999, "CE")
        self.assertEqual(result, -1)


class TestPlaceOrderOptionBuy(unittest.TestCase):
    @patch("upstox.upstox_api.credentials")
    @patch("upstox.upstox_api.requests")
    def setUp(self, mock_requests, mock_creds):  # pylint: disable=arguments-differ
        self.api = _build_api(mock_requests, mock_creds)

    @patch("upstox.upstox_api.requests.post")
    def test_option_buy_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "data": {"order_id": "OPTBUY1"}}
        mock_post.return_value = mock_resp

        result = self.api.place_order_option_buy("NIFTY", 50, "BUY", 24000, "PE")
        self.assertEqual(result, "OPTBUY1")

    def test_option_buy_bad_lot(self):
        result = self.api.place_order_option_buy("NIFTY", 3, "BUY", 24000, "PE")
        self.assertEqual(result, -1)


class TestPlaceOrderSyntheticFuture(unittest.TestCase):
    @patch("upstox.upstox_api.credentials")
    @patch("upstox.upstox_api.requests")
    def setUp(self, mock_requests, mock_creds):  # pylint: disable=arguments-differ
        self.api = _build_api(mock_requests, mock_creds)

    @patch("upstox.upstox_api.requests.post")
    def test_synthetic_future_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "data": {"order_id": "SYN1"}}
        mock_post.return_value = mock_resp

        order_id, expiry = self.api.place_order_synthetic_future("NIFTY", 50, "BUY", 24000, "CE")
        self.assertEqual(order_id, "SYN1")
        self.assertIsNotNone(expiry)

    def test_synthetic_future_bad_lot(self):
        order_id, expiry = self.api.place_order_synthetic_future("NIFTY", 3, "BUY", 24000, "CE")
        self.assertEqual(order_id, -1)
        self.assertIsNone(expiry)


class TestGetCommodityPosition(unittest.TestCase):
    @patch("upstox.upstox_api.credentials")
    @patch("upstox.upstox_api.requests")
    def setUp(self, mock_requests, mock_creds):  # pylint: disable=arguments-differ
        self.api = _build_api(mock_requests, mock_creds)

    @patch("upstox.upstox_api.requests.get")
    def test_long_position_found(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"tradingsymbol": "GOLDM25APR26FUT", "quantity": 100, "average_price": 72500.0}
            ]
        }
        mock_get.return_value = mock_resp

        pos_type, avg = self.api.get_commodity_position("GOLD", "long")
        self.assertEqual(pos_type, "long")
        self.assertEqual(avg, 72500.0)

    @patch("upstox.upstox_api.requests.get")
    def test_short_position_found(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"tradingsymbol": "GOLDM25APR26FUT", "quantity": -100, "average_price": 72000.0}
            ]
        }
        mock_get.return_value = mock_resp

        pos_type, avg = self.api.get_commodity_position("GOLD", "short")
        self.assertEqual(pos_type, "short")
        self.assertEqual(avg, 72000.0)

    @patch("upstox.upstox_api.requests.get")
    def test_no_position(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": []}
        mock_get.return_value = mock_resp

        pos_type, avg = self.api.get_commodity_position("GOLD", "long")
        self.assertIsNone(pos_type)
        self.assertEqual(avg, 0)

    @patch("upstox.upstox_api.requests.get")
    def test_api_error(self, mock_get):
        mock_get.side_effect = Exception("Network error")
        pos_type, avg = self.api.get_commodity_position("GOLD", "long")
        self.assertIsNone(pos_type)
        self.assertEqual(avg, 0)


class TestGetLedgerBalance(unittest.TestCase):
    @patch("upstox.upstox_api.credentials")
    @patch("upstox.upstox_api.requests")
    def setUp(self, mock_requests, mock_creds):  # pylint: disable=arguments-differ
        self.api = _build_api(mock_requests, mock_creds)

    @patch("upstox.upstox_api.requests.get")
    def test_balance_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {"equity": {"available_margin": 150000.50}}
        }
        mock_get.return_value = mock_resp

        balance = self.api.get_ledger_balance()
        self.assertEqual(balance, 150000.50)

    @patch("upstox.upstox_api.requests.get")
    def test_balance_api_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp

        balance = self.api.get_ledger_balance()
        self.assertEqual(balance, 0.0)

    @patch("upstox.upstox_api.requests.get")
    def test_balance_exception(self, mock_get):
        mock_get.side_effect = Exception("Connection refused")
        balance = self.api.get_ledger_balance()
        self.assertEqual(balance, 0.0)


class TestGetOrderStatus(unittest.TestCase):
    @patch("upstox.upstox_api.credentials")
    @patch("upstox.upstox_api.requests")
    def setUp(self, mock_requests, mock_creds):  # pylint: disable=arguments-differ
        self.api = _build_api(mock_requests, mock_creds)

    @patch("upstox.upstox_api.requests.get")
    def test_order_complete(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {"status": "complete", "average_price": 250.5}
        }
        mock_get.return_value = mock_resp

        status, price = self.api.get_order_status("ORD123")
        self.assertEqual(status, "Complete")
        self.assertEqual(price, 250.5)
        mock_get.assert_called_once_with(
            "https://api.upstox.com/v2/order/details?order_id=ORD123",
            headers=self.api.get_headers(),
            timeout=10,
        )

    @patch("upstox.upstox_api.requests.get")
    def test_order_open(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {"status": "open", "average_price": 0.0}
        }
        mock_get.return_value = mock_resp

        status, price = self.api.get_order_status("ORD124")
        self.assertEqual(status, "Open")

    @patch("upstox.upstox_api.requests.get")
    def test_order_rejected(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {"status": "rejected", "average_price": 0.0}
        }
        mock_get.return_value = mock_resp

        status, price = self.api.get_order_status("ORD125")
        self.assertEqual(status, "Rejected")

    @patch("upstox.upstox_api.requests.get")
    def test_order_cancelled(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {"status": "cancelled", "average_price": 0.0}
        }
        mock_get.return_value = mock_resp

        status, price = self.api.get_order_status("ORD126")
        self.assertEqual(status, "Cancelled")

    @patch("upstox.upstox_api.requests.get")
    def test_order_not_found_empty_data(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": None}
        mock_get.return_value = mock_resp

        status, price = self.api.get_order_status("MISSING")
        self.assertEqual(status, "NotFound")
        self.assertEqual(price, -1)

    @patch("upstox.upstox_api.requests.get")
    def test_order_not_found_non_200(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        status, price = self.api.get_order_status("MISSING")
        self.assertEqual(status, "NotFound")
        self.assertEqual(price, -1)

    @patch("upstox.upstox_api.requests.get")
    def test_order_unknown_status_treated_as_open(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {"status": "after_market_order", "average_price": 0.0}
        }
        mock_get.return_value = mock_resp

        status, price = self.api.get_order_status("ORD127")
        self.assertEqual(status, "Open")

    @patch("upstox.upstox_api.requests.get")
    def test_order_list_response_still_handled(self, mock_get):
        """Backwards compat: if data is a list, take the first element."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [{"status": "complete", "average_price": 100.0}]
        }
        mock_get.return_value = mock_resp

        status, price = self.api.get_order_status("ORD128")
        self.assertEqual(status, "Complete")
        self.assertEqual(price, 100.0)

    @patch("upstox.upstox_api.requests.get")
    def test_order_status_exception(self, mock_get):
        mock_get.side_effect = Exception("Network error")
        status, price = self.api.get_order_status("ORD999")
        self.assertEqual(status, "NotFound")
        self.assertEqual(price, -1)


if __name__ == "__main__":
    unittest.main()
