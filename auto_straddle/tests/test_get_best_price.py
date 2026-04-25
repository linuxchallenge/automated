"""Unit tests for get_best_price and its usage in order placement methods.

Tests cover:
  - get_best_price: market depth success (BUY / SELL)
  - get_best_price: market depth fails → LTP fallback (BUY / SELL)
  - get_best_price: both fail → returns 0
  - place_order_commodity: passes price from get_best_price (not 0)
  - place_order: passes price from get_best_price (not 0)
  - place_order_synthetic_future: passes price from get_best_price (not 0)

Run with:
    python -m pytest fivepaisa/test_get_best_price.py -v
"""

import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
from datetime import datetime, timedelta


def _make_api():
    """Build a fivepaise_api instance without calling __init__ (no real login)."""
    import sys

    # Stub out heavy imports before loading the module
    for mod in ['py5paisa', 'pyotp', 'TelegramSend',
                'fivepaisa.credentials_2', 'fivepaisa.credentials_3']:
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()

    from fivepaisa.fivepaise_api import fivepaise_api

    api = object.__new__(fivepaise_api)
    api.account = 'leelu'
    api.obj = MagicMock()
    api.session = 'test-session'
    api.scrip_master_df = pd.DataFrame()
    return api


def _depth_response(buy_price, sell_price):
    # order_request already unwraps res["body"], so the return value IS the body.
    # Real structure: MarketDepthData list with BbBuySellFlag 66=Bid, 83=Ask
    return {
        'Status': 0, 'Message': 'Success',
        'MarketDepthData': [
            {'BbBuySellFlag': 66, 'Price': buy_price, 'Quantity': 100, 'NumberOfOrders': 1},
            {'BbBuySellFlag': 83, 'Price': sell_price, 'Quantity': 100, 'NumberOfOrders': 1},
        ]
    }


def _ltp_response(ltp):
    # fetch_market_feed_scrip also returns res["body"]; field is LastRate
    return {'Status': 0, 'Message': 'Success', 'Data': [{'LastRate': ltp}]}


def _token_row(exchange='N', scrip_code=12345, lot=50, expiry_days=30):
    expiry = (datetime.now() + timedelta(days=expiry_days)).strftime('%Y-%m-%d')
    return pd.Series({
        'SymbolRoot': 'NIFTY', 'ScripCode': scrip_code,
        'LotSize': lot, 'Expiry': expiry, 'Name': 'NIFTY CE'
    })


# ---------------------------------------------------------------------------
# get_best_price tests
# ---------------------------------------------------------------------------

class TestGetBestPrice(unittest.TestCase):

    def setUp(self):
        self.api = _make_api()

    def test_buy_returns_sell_price_from_depth(self):
        self.api.obj.fetch_market_depth_by_scrip.return_value = _depth_response(450.0, 451.5)
        price = self.api.get_best_price(12345, 'N', 'D', 'B')
        self.assertEqual(price, 451.5)

    def test_sell_returns_buy_price_from_depth(self):
        self.api.obj.fetch_market_depth_by_scrip.return_value = _depth_response(450.0, 451.5)
        price = self.api.get_best_price(12345, 'N', 'D', 'S')
        self.assertEqual(price, 450.0)

    def test_zero_depth_price_falls_back_to_ltp(self):
        # Market depth returns 0 price → should fall back to LTP
        self.api.obj.fetch_market_depth_by_scrip.return_value = _depth_response(0, 0)
        self.api.obj.fetch_market_feed_scrip.return_value = _ltp_response(500.0)
        price = self.api.get_best_price(12345, 'N', 'D', 'B')
        # BUY fallback = LTP * 1.005
        self.assertAlmostEqual(price, 502.5, places=1)

    def test_depth_exception_falls_back_to_ltp_buy(self):
        self.api.obj.fetch_market_depth_by_scrip.side_effect = Exception("network error")
        self.api.obj.fetch_market_feed_scrip.return_value = _ltp_response(200.0)
        price = self.api.get_best_price(12345, 'N', 'D', 'B')
        self.assertAlmostEqual(price, 201.0, places=1)

    def test_depth_exception_falls_back_to_ltp_sell(self):
        self.api.obj.fetch_market_depth_by_scrip.side_effect = Exception("network error")
        self.api.obj.fetch_market_feed_scrip.return_value = _ltp_response(200.0)
        price = self.api.get_best_price(12345, 'N', 'D', 'S')
        self.assertAlmostEqual(price, 199.0, places=1)

    def test_both_fail_returns_zero(self):
        self.api.obj.fetch_market_depth_by_scrip.side_effect = Exception("depth error")
        self.api.obj.fetch_market_feed_scrip.side_effect = Exception("ltp error")
        price = self.api.get_best_price(12345, 'N', 'D', 'B')
        self.assertEqual(price, 0)

    def test_mcx_exchange_passed_correctly(self):
        self.api.obj.fetch_market_depth_by_scrip.return_value = _depth_response(5500.0, 5502.0)
        self.api.get_best_price(99999, 'M', 'D', 'B')
        self.api.obj.fetch_market_depth_by_scrip.assert_called_once_with(
            Exchange='M', ExchangeType='D', ScripCode=99999
        )

    def test_ltp_fallback_passes_correct_list_format(self):
        self.api.obj.fetch_market_depth_by_scrip.side_effect = Exception("depth error")
        self.api.obj.fetch_market_feed_scrip.return_value = _ltp_response(300.0)
        self.api.get_best_price(12345, 'N', 'D', 'B')
        self.api.obj.fetch_market_feed_scrip.assert_called_once_with(
            [{"Exch": 'N', "ExchType": 'D', "ScripCode": 12345}]
        )


# ---------------------------------------------------------------------------
# place_order_commodity uses get_best_price (not hardcoded 0)
# ---------------------------------------------------------------------------

class TestPlaceOrderCommodityUsesPrice(unittest.TestCase):

    def setUp(self):
        self.api = _make_api()
        # Stub get_commodity_symbol to return a valid token row
        self.api.get_commodity_symbol = MagicMock(return_value=_token_row(scrip_code=55555, lot=1))
        # Stub place_order to return a valid response
        self.api.obj.place_order.return_value = {'BrokerOrderID': 1001, 'Message': 'Success'}

    def test_buy_commodity_uses_best_price(self):
        with patch.object(self.api, 'get_best_price', return_value=5510.0) as mock_price, \
             patch.object(self.api, '_fix_shared_payload_bug'):
            _order_id, _ = self.api.place_order_commodity('GOLD', 1, 'BUY')
        mock_price.assert_called_once_with(55555, 'M', 'D', 'B')
        # Verify Price kwarg forwarded to broker call
        _, kwargs = self.api.obj.place_order.call_args
        self.assertEqual(kwargs['Price'], 5510.0)

    def test_sell_commodity_uses_best_price(self):
        with patch.object(self.api, 'get_best_price', return_value=5490.0) as mock_price, \
             patch.object(self.api, '_fix_shared_payload_bug'):
            _order_id, _ = self.api.place_order_commodity('GOLD', 1, 'SELL')
        mock_price.assert_called_once_with(55555, 'M', 'D', 'S')
        _, kwargs = self.api.obj.place_order.call_args
        self.assertEqual(kwargs['Price'], 5490.0)

    def test_price_never_hardcoded_zero(self):
        with patch.object(self.api, 'get_best_price', return_value=100.0), \
             patch.object(self.api, '_fix_shared_payload_bug'):
            self.api.place_order_commodity('GOLD', 1, 'BUY')
        for c in self.api.obj.place_order.call_args_list:
            self.assertNotEqual(c.kwargs.get('Price'), 0, "Price=0 (market order) must not be passed")


# ---------------------------------------------------------------------------
# place_order uses get_best_price
# ---------------------------------------------------------------------------

class TestPlaceOrderUsesPrice(unittest.TestCase):

    def setUp(self):
        self.api = _make_api()
        self.api.getTokenInfo = MagicMock(return_value=_token_row(scrip_code=77777, lot=50))
        self.api.obj.place_order.return_value = {'BrokerOrderID': 2001, 'Message': 'Success'}

    def test_option_buy_uses_best_price(self):
        with patch.object(self.api, 'get_best_price', return_value=250.0) as mock_price, \
             patch.object(self.api, '_fix_shared_payload_bug'):
            self.api.place_order('NIFTY', 50, 'BUY', 24000, 'CE')
        mock_price.assert_called_once_with(77777, 'N', 'D', 'B')
        _, kwargs = self.api.obj.place_order.call_args
        self.assertEqual(kwargs['Price'], 250.0)

    def test_option_sell_uses_best_price(self):
        with patch.object(self.api, 'get_best_price', return_value=245.0) as mock_price, \
             patch.object(self.api, '_fix_shared_payload_bug'):
            self.api.place_order('NIFTY', 50, 'SELL', 24000, 'CE')
        mock_price.assert_called_once_with(77777, 'N', 'D', 'S')
        _, kwargs = self.api.obj.place_order.call_args
        self.assertEqual(kwargs['Price'], 245.0)

    def test_sensex_uses_bfo_exchange(self):
        self.api.getTokenInfo = MagicMock(return_value=_token_row(scrip_code=88888, lot=20))
        with patch.object(self.api, 'get_best_price', return_value=300.0) as mock_price, \
             patch.object(self.api, '_fix_shared_payload_bug'):
            self.api.place_order('SENSEX', 20, 'BUY', 75000, 'CE')
        mock_price.assert_called_once_with(88888, 'B', 'D', 'B')

    def test_price_zero_triggers_session_refresh_and_retries(self):
        """When price=0, session refresh should be attempted before aborting."""
        # First get_best_price call returns 0, second (after refresh) returns valid price
        self.api.get_best_price = MagicMock(side_effect=[0, 250.0])
        self.api.obj.place_order.return_value = {'BrokerOrderID': 9001, 'Message': 'Success'}
        with patch.object(self.api, '_refresh_session', return_value=True) as mock_refresh, \
             patch.object(self.api, '_fix_shared_payload_bug'), \
             patch('fivepaisa.fivepaise_api.time') as _mock_time:
            _order_id, _ = self.api.place_order('NIFTY', 50, 'SELL', 24000, 'CE')
        mock_refresh.assert_called_once()
        self.assertEqual(self.api.get_best_price.call_count, 2)
        _, kwargs = self.api.obj.place_order.call_args
        self.assertEqual(kwargs['Price'], 250.0)

    def test_price_zero_after_refresh_aborts(self):
        """If price still 0 after session refresh, order should be aborted."""
        self.api.get_best_price = MagicMock(return_value=0)
        with patch.object(self.api, '_refresh_session', return_value=True), \
             patch.object(self.api, '_fix_shared_payload_bug'), \
             patch('fivepaisa.fivepaise_api.time'):
            result = self.api.place_order('NIFTY', 50, 'SELL', 24000, 'CE')
        self.assertEqual(result, (-1, None))
        self.api.obj.place_order.assert_not_called()


# ---------------------------------------------------------------------------
# place_order_synthetic_future uses get_best_price
# ---------------------------------------------------------------------------

class TestPlaceOrderSyntheticFutureUsesPrice(unittest.TestCase):

    def setUp(self):
        self.api = _make_api()
        expiry = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        self.api.scrip_master_df = pd.DataFrame([{
            'SymbolRoot': 'NIFTY', 'StrikeRate': 24000.0, 'ScripType': 'CE',
            'ScripCode': 66666, 'LotSize': 50, 'Expiry': expiry,
            'Name': 'NIFTY CE 24000'
        }])
        self.api.obj.place_order.return_value = {'BrokerOrderID': 3001, 'Message': 'Success'}

    def test_synthetic_future_buy_uses_best_price(self):
        with patch.object(self.api, 'get_best_price', return_value=180.0) as mock_price, \
             patch.object(self.api, '_fix_shared_payload_bug'):
            self.api.place_order_synthetic_future('NIFTY', 50, 'BUY', 24000.0, 'CE')
        mock_price.assert_called_once_with(66666, 'N', 'D', 'B')
        _, kwargs = self.api.obj.place_order.call_args
        self.assertEqual(kwargs['Price'], 180.0)

    def test_synthetic_future_sell_uses_best_price(self):
        with patch.object(self.api, 'get_best_price', return_value=175.0) as mock_price, \
             patch.object(self.api, '_fix_shared_payload_bug'):
            self.api.place_order_synthetic_future('NIFTY', 50, 'SELL', 24000.0, 'CE')
        mock_price.assert_called_once_with(66666, 'N', 'D', 'S')
        _, kwargs = self.api.obj.place_order.call_args
        self.assertEqual(kwargs['Price'], 175.0)


if __name__ == '__main__':
    unittest.main()
