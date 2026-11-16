"""Tests for the Nifty ML paper-trading strategy and its shared feature/model code.

All mocked: no broker, no option-chain fetch, no Telegram, no network.
"""
import importlib
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

import nifty_ml_features as nf

STUBS = ['TelegramSend', 'configuration', 'market_data', 'OptionChainData']


def chain(atm=23000.0, pe=40.0, ce=45.0, pcr=1.0, **extra):
    info = {'spot_price': atm + 10, 'pe_to_ce_ratio': pcr, 'atm_strike': atm,
            'atm_next_pe_price': pe, 'atm_next_ce_price': ce}
    info.update(extra)
    return info


class FakeOrders:
    """Stands in for PlaceOrder. Like the real dummy account it fills every order at the expected
    price, unless a test sets `status` (returned by order_status), `fill` (broker fill price)
    or `fail` (order placement returns -1)."""

    def __init__(self):
        self.placed = []   # (side, strike, option_type, qty)
        self.status = 'Complete'
        self.fill = None
        self.fail = False
        self.status_calls = 0
        self.open_ids = set()  # order ids that are still 'Open' at the broker

    def _place(self, side, account, strike, option_type, symbol, qty):
        assert account == 'dummy' and symbol == 'NIFTY'
        if self.fail:
            return -1
        self.placed.append((side, strike, option_type, qty))
        return 1000 + len(self.placed)

    def place_orders(self, account, strike, option_type, symbol, qty, intraday=True):
        return self._place('SELL', account, strike, option_type, symbol, qty)

    def close_orders(self, account, strike, option_type, symbol, qty, intraday=True):
        return self._place('BUY', account, strike, option_type, symbol, qty)

    def order_status(self, account, order_id, old_price):
        self.status_calls += 1
        if order_id in self.open_ids:
            return 'Open', -1
        if self.status == 'Complete':
            return 'Complete', self.fill if self.fill is not None else old_price
        return self.status, -1


@pytest.fixture
def strat(monkeypatch, tmp_path):
    for name in STUBS:
        monkeypatch.setitem(sys.modules, name, MagicMock())
    monkeypatch.delitem(sys.modules, 'NiftyMlStratergy', raising=False)
    mod = importlib.import_module('NiftyMlStratergy')
    (tmp_path / "csv").mkdir()
    monkeypatch.chdir(tmp_path)
    s = mod.NiftyMlStratergy()
    s.send_message = MagicMock()
    s.orders = FakeOrders()
    s.signals = {}  # bar time "HH:MM" -> signal returned by the stubbed get_signal
    s.held_seen = []  # the `held` argument of every get_signal call
    s.get_signal = MagicMock(side_effect=lambda info, po, bar, now, held='none':
                             (s.held_seen.append(held), s.signals.get(bar.strftime("%H:%M"), 'none'))[1])
    s.mod = mod
    return s


def at(hhmm, second=20):
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime(2026, 11, 16, hour, minute, second)


def run(s, hhmm, info, account='dummy', symbol='NIFTY', second=20):
    s.execute_strategy(info, symbol, account, 1, s.orders, now=at(hhmm, second))
    return s.read_trades(at(hhmm))


def test_bullish_sells_pe_100_below_atm(strat):
    strat.signals = {"10:00": 'bullish'}
    trades = run(strat, "10:00", chain(atm=23000.0, pe=40.0))
    assert len(trades) == 1
    row = trades.iloc[0]
    assert (row['option_type'], row['strike'], row['entry_price'], row['signal']) == ('PE', 22900.0, 40.0, 'bullish')
    assert pd.isna(row['exit_time'])


def test_bearish_sells_ce_100_above_atm(strat):
    strat.signals = {"10:00": 'bearish'}
    row = run(strat, "10:00", chain(atm=23000.0, ce=45.0)).iloc[0]
    assert (row['option_type'], row['strike'], row['entry_price'], row['signal']) == ('CE', 23100.0, 45.0, 'bearish')


def test_sideways_sells_both_legs(strat):
    strat.signals = {"10:00": 'sideways'}
    trades = run(strat, "10:00", chain(atm=23000.0, pe=40.0, ce=45.0))
    assert sorted(zip(trades['option_type'], trades['strike'], trades['entry_price'])) == \
        [('CE', 23100.0, 45.0), ('PE', 22900.0, 40.0)]
    assert trades['exit_time'].isna().all()


def test_sideways_to_bullish_closes_ce_and_keeps_pe(strat):
    strat.signals = {"10:00": 'sideways', "10:15": 'bullish'}
    run(strat, "10:00", chain(pe=40.0, ce=45.0))
    trades = run(strat, "10:15", chain(pe=30.0, ce=60.0)).set_index('option_type')
    assert len(trades) == 2
    assert trades.at['CE', 'exit_reason'] == "signal lost" and trades.at['CE', 'pnl_points'] == -15.0
    assert pd.isna(trades.at['PE', 'exit_time']) and trades.at['PE', 'entry_price'] == 40.0  # original PE kept


def test_bullish_to_sideways_keeps_pe_and_adds_ce(strat):
    strat.signals = {"10:00": 'bullish', "10:15": 'sideways'}
    run(strat, "10:00", chain(pe=40.0))
    trades = run(strat, "10:15", chain(pe=35.0, ce=50.0))
    assert list(trades['option_type']) == ['PE', 'CE'] and trades['exit_time'].isna().all()
    assert trades.iloc[0]['entry_price'] == 40.0 and trades.iloc[1]['entry_price'] == 50.0


def test_sideways_to_none_closes_both(strat):
    strat.signals = {"10:00": 'sideways', "10:15": 'none'}
    run(strat, "10:00", chain(pe=40.0, ce=45.0))
    trades = run(strat, "10:15", chain(pe=30.0, ce=50.0))
    assert list(trades['exit_reason']) == ["signal lost", "signal lost"]
    assert trades['pnl_points'].sum() == 5.0


def test_sideways_square_off_closes_both(strat):
    strat.signals = {"14:00": 'sideways'}
    run(strat, "14:00", chain(pe=40.0, ce=45.0))
    trades = run(strat, "15:15", chain(pe=10.0, ce=12.0))
    assert list(trades['exit_reason']) == ["15:15 square-off"] * 2 and trades['pnl_points'].sum() == 63.0


def test_strangle_waits_for_missing_leg_price_without_duplicating(strat):
    strat.signals = {"10:00": 'sideways'}
    assert list(run(strat, "10:00", chain(pe=40.0, ce=0.0))['option_type']) == ['PE']
    assert list(run(strat, "10:01", chain(pe=41.0, ce=44.0))['option_type']) == ['PE', 'CE']
    assert len(run(strat, "10:02", chain(pe=41.0, ce=44.0))) == 2
    assert strat.get_signal.call_count == 1


def test_decides_once_per_bar_and_holds_while_signal_persists(strat):
    strat.signals = {"10:00": 'bullish', "10:15": 'bullish'}
    run(strat, "10:00", chain())
    run(strat, "10:01", chain())
    run(strat, "10:14", chain())
    trades = run(strat, "10:15", chain(pe=30.0))
    assert len(trades) == 1 and pd.isna(trades.iloc[0]['exit_time'])
    assert strat.get_signal.call_count == 2  # one per 15-minute bar


def test_signal_lost_closes_position_with_pnl(strat):
    strat.signals = {"10:00": 'bullish', "10:15": 'none'}
    run(strat, "10:00", chain(pe=40.0))
    trades = run(strat, "10:15", chain(pe=25.0))
    row = trades.iloc[0]
    assert row['exit_reason'] == "signal lost" and row['exit_price'] == 25.0
    assert row['pnl_points'] == 15.0 and row['pnl_rupees'] == 15.0 * 65
    assert len(trades) == 1


def test_opposite_signal_closes_now_and_sells_other_side_after_cooling(strat):
    strat.signals = {"10:00": 'bullish', "10:15": 'bearish', "10:30": 'bearish'}
    run(strat, "10:00", chain(atm=23000.0, pe=40.0))
    # ATM moved to 22900: the held 22900 PE must be priced via the chain's prev_atm_next_pe_price
    strat.mod.OptionChainData.return_value.get_option_chain_info.return_value = {'prev_atm_next_pe_price': 70.0}
    trades = run(strat, "10:15", chain(atm=22900.0, pe=33.0, ce=50.0))
    strat.mod.OptionChainData.return_value.get_option_chain_info.assert_called_once_with(23000.0, 0, 0, 'NIFTY')
    assert len(trades) == 1 and trades.iloc[0]['exit_price'] == 70.0 and trades.iloc[0]['pnl_points'] == -30.0
    assert len(run(strat, "10:20", chain(atm=22900.0, ce=50.0))) == 1  # still cooling within the bar
    trades = run(strat, "10:30", chain(atm=22900.0, ce=48.0))
    assert (trades.iloc[1]['option_type'], trades.iloc[1]['strike'], trades.iloc[1]['entry_price']) == ('CE', 23000.0, 48.0)
    assert pd.isna(trades.iloc[1]['exit_time'])


def test_cooling_skips_entry_if_signal_gone_by_next_check(strat):
    strat.signals = {"10:00": 'bullish', "10:15": 'bearish', "10:30": 'none'}
    run(strat, "10:00", chain(pe=40.0))
    run(strat, "10:15", chain(pe=30.0, ce=50.0))
    assert len(run(strat, "10:30", chain(ce=48.0))) == 1


def test_no_cooling_when_adding_a_leg_without_an_exit(strat):
    strat.signals = {"10:00": 'bullish', "10:15": 'sideways'}
    run(strat, "10:00", chain(pe=40.0))
    assert len(run(strat, "10:15", chain(pe=35.0, ce=50.0))) == 2


def test_cooling_survives_restart(strat):
    strat.signals = {"10:00": 'bullish', "10:15": 'bearish'}
    run(strat, "10:00", chain(pe=40.0))
    run(strat, "10:15", chain(pe=30.0, ce=50.0))
    fresh = strat.mod.NiftyMlStratergy()
    fresh.send_message = MagicMock()
    fresh.get_signal = MagicMock(return_value='bearish')
    fresh.execute_strategy(chain(ce=50.0), 'NIFTY', 'dummy', 1, strat.orders, now=at("10:18"))
    assert len(fresh.read_trades(at("10:18"))) == 1


def test_square_off_at_1515_and_no_reentry(strat):
    strat.signals = {"14:00": 'bullish', "15:15": 'bullish'}
    run(strat, "14:00", chain(pe=40.0))
    trades = run(strat, "15:15", chain(pe=10.0))
    assert trades.iloc[0]['exit_reason'] == "15:15 square-off" and trades.iloc[0]['pnl_points'] == 30.0
    trades = run(strat, "15:16", chain(pe=10.0))
    assert len(trades) == 1


def test_no_new_entry_after_1400_but_exit_still_works(strat):
    strat.signals = {"14:00": 'bullish', "14:15": 'bearish', "14:30": 'sideways'}
    run(strat, "14:00", chain(pe=40.0))
    trades = run(strat, "14:15", chain(pe=20.0))
    assert len(trades) == 1 and trades.iloc[0]['exit_reason'] == "signal lost"
    assert len(run(strat, "14:30", chain())) == 1


def test_nothing_before_first_bar(strat):
    strat.signals = {"09:15": 'bullish'}
    assert len(run(strat, "09:25", chain())) == 0
    strat.get_signal.assert_not_called()


def test_only_dummy_account_and_nifty(strat):
    strat.signals = {"10:00": 'sideways'}
    assert len(run(strat, "10:00", chain(), account='deepti')) == 0
    assert len(run(strat, "10:00", chain(), symbol='BANKNIFTY')) == 0
    strat.get_signal.assert_not_called()


def test_unknown_signal_retries_then_keeps_position(strat):
    strat.signals = {"10:00": 'bullish', "10:15": None, "10:30": 'none'}
    run(strat, "10:00", chain(pe=40.0))
    run(strat, "10:15", chain())
    run(strat, "10:17", chain())
    assert strat.get_signal.call_count == 3  # retried while inside the retry window
    trades = run(strat, "10:21", chain())
    assert strat.get_signal.call_count == 4
    run(strat, "10:25", chain())
    assert strat.get_signal.call_count == 4  # gave up for this bar
    assert pd.isna(trades.iloc[0]['exit_time'])  # unknown is not "lost": position kept
    assert run(strat, "10:30", chain(pe=35.0)).iloc[0]['exit_reason'] == "signal lost"


def test_close_retries_when_price_missing_without_recomputing_signal(strat):
    strat.signals = {"10:00": 'bullish', "10:15": 'none'}
    run(strat, "10:00", chain(pe=40.0))
    trades = run(strat, "10:15", chain(pe=0.0))
    assert pd.isna(trades.iloc[0]['exit_time'])
    trades = run(strat, "10:16", chain(pe=22.0))
    assert trades.iloc[0]['exit_price'] == 22.0
    assert strat.get_signal.call_count == 2


def test_entry_waits_for_a_price(strat):
    strat.signals = {"10:00": 'bullish'}
    assert len(run(strat, "10:00", chain(pe=0.0))) == 0
    assert len(run(strat, "10:01", chain(pe=38.0))) == 1


def test_position_survives_restart(strat):
    strat.signals = {"10:00": 'sideways', "10:15": 'none'}
    run(strat, "10:00", chain(pe=40.0, ce=45.0))
    fresh = strat.mod.NiftyMlStratergy()
    fresh.send_message = MagicMock()
    fresh.get_signal = MagicMock(return_value='none')
    fresh.execute_strategy(chain(pe=30.0, ce=40.0), 'NIFTY', 'dummy', 1, strat.orders, now=at("10:15"))
    assert fresh.read_trades(at("10:15"))['pnl_points'].sum() == 15.0


def test_held_signal_comes_from_the_open_legs_and_survives_restart(strat):
    strat.signals = {"10:00": 'sideways', "10:15": 'sideways', "10:30": 'bullish', "10:45": 'bullish', "11:00": 'none'}
    for minute in ("10:00", "10:15", "10:30", "10:45", "11:00"):
        run(strat, minute, chain())
    # nothing held at 10:00, PE+CE at 10:15, PE only after CE was closed at 10:30, then PE only at 10:45 and 11:00
    assert strat.held_seen == ['none', 'sideways', 'sideways', 'bullish', 'bullish']
    fresh = strat.mod.NiftyMlStratergy()
    fresh.send_message = MagicMock()
    fresh.get_signal = MagicMock(return_value='none')
    fresh.execute_strategy(chain(), 'NIFTY', 'dummy', 1, strat.orders, now=at("11:15"))
    assert fresh.get_signal.call_args.args[-1] == 'none'  # PE was closed at 11:00
    open_legs = lambda *types: pd.DataFrame({'option_type': list(types), 'exit_time': [None] * len(types)})
    assert [fresh.held_signal(open_legs(*t)) for t in ((), ('PE',), ('CE',), ('PE', 'CE'))] == \
        ['none', 'bullish', 'bearish', 'sideways']


def test_a_strangle_entry_sends_one_telegram_message(strat):
    strat.signals = {"10:00": 'sideways'}
    run(strat, "10:00", chain(atm=23000.0, pe=40.0, ce=45.0))
    assert strat.send_message.call_count == 0  # orders placed, nothing confirmed yet
    run(strat, "10:01", chain(atm=23000.0, pe=40.0, ce=45.0))
    assert strat.send_message.call_count == 1
    message = strat.send_message.call_args.args[0]
    assert "sideways" in message and "22900 PE at 40.0" in message and "23100 CE at 45.0" in message
    run(strat, "10:02", chain())
    assert strat.send_message.call_count == 1  # not repeated


def test_entry_message_waits_for_the_slower_leg(strat):
    strat.signals = {"10:00": 'sideways'}
    run(strat, "10:00", chain(pe=40.0, ce=45.0))
    strat.orders.open_ids = {1002}  # the CE order is still open at the broker
    run(strat, "10:01", chain())
    assert strat.send_message.call_count == 0
    strat.orders.open_ids = set()
    run(strat, "10:02", chain())
    assert strat.send_message.call_count == 1
    assert "PE" in strat.send_message.call_args.args[0] and "CE" in strat.send_message.call_args.args[0]


def test_a_strangle_exit_sends_one_message_with_the_total(strat):
    strat.signals = {"10:00": 'sideways', "10:15": 'none'}
    run(strat, "10:00", chain(pe=40.0, ce=45.0))
    run(strat, "10:01", chain(pe=40.0, ce=45.0))
    strat.send_message.reset_mock()
    run(strat, "10:15", chain(pe=25.0, ce=50.0))
    assert strat.send_message.call_count == 0
    run(strat, "10:16", chain(pe=25.0, ce=50.0))
    assert strat.send_message.call_count == 1
    message = strat.send_message.call_args.args[0]
    assert "signal lost" in message and "22900 PE" in message and "23100 CE" in message
    assert "+15.0 points" in message and "-5.0 points" in message and "Total +10.0 points" in message


def test_exit_message_waits_for_the_slower_leg(strat):
    strat.signals = {"10:00": 'sideways', "10:15": 'none'}
    run(strat, "10:00", chain(pe=40.0, ce=45.0))
    run(strat, "10:01", chain(pe=40.0, ce=45.0))
    strat.send_message.reset_mock()
    run(strat, "10:15", chain(pe=25.0, ce=50.0))
    strat.orders.open_ids = {1004}  # the CE buy-back is slow
    run(strat, "10:16", chain())
    assert strat.send_message.call_count == 0
    strat.orders.open_ids = set()
    run(strat, "10:17", chain())
    assert strat.send_message.call_count == 1 and "Total +10.0 points" in strat.send_message.call_args.args[0]


def test_single_leg_exit_message_is_one_line(strat):
    strat.signals = {"10:00": 'bullish', "10:15": 'none'}
    run(strat, "10:00", chain(pe=40.0))
    run(strat, "10:01", chain(pe=40.0))
    strat.send_message.reset_mock()
    run(strat, "10:15", chain(pe=25.0))
    run(strat, "10:16", chain(pe=25.0))
    message = strat.send_message.call_args.args[0]
    assert "\n" not in message and "closed (signal lost)" in message and "+15.0 points" in message and "Rs +975" in message


def test_a_leg_added_later_is_a_separate_entry_message(strat):
    strat.signals = {"10:00": 'bullish', "10:15": 'sideways'}
    for minute in ("10:00", "10:01", "10:15", "10:16"):
        run(strat, minute, chain(pe=40.0, ce=45.0))
    messages = [c.args[0] for c in strat.send_message.call_args_list]
    assert len(messages) == 2 and "22900 PE" in messages[0] and "CE" not in messages[0]
    assert "23100 CE" in messages[1] and "PE" not in messages[1].split("sold")[1]


def test_rejected_leg_does_not_swallow_the_message_for_its_sibling(strat):
    strat.signals = {"10:00": 'sideways'}
    run(strat, "10:00", chain(pe=40.0, ce=45.0))
    strat.orders.open_ids = {1002}
    run(strat, "10:01", chain())          # PE confirmed, CE still open: nothing sent yet
    strat.orders.open_ids = set()
    strat.orders.status = 'Rejected'
    run(strat, "10:02", chain())          # CE rejected: error for the CE, and the PE entry is announced
    messages = [c.args[0] for c in strat.send_message.call_args_list]
    assert any("ERROR entry order" in m for m in messages)
    assert any("sold 22900 PE at 40.0" in m and "CE" not in m for m in messages)


def test_no_third_trade_in_a_day(strat):
    strat.signals = {"10:00": 'sideways', "10:15": 'none', "10:30": 'sideways', "10:45": 'none',
                     "11:00": 'sideways', "11:15": 'bullish'}
    for minute in ("10:00", "10:15", "10:30", "10:45"):
        run(strat, minute, chain(pe=40.0, ce=45.0))
    trades = run(strat, "10:46", chain(pe=40.0, ce=45.0))
    assert len(trades) == 4 and not trades['exit_time'].isna().any()  # two strangles entered and closed
    assert len(run(strat, "11:00", chain(pe=40.0, ce=45.0))) == 4      # third sideways: no entry
    assert len(run(strat, "11:15", chain(pe=40.0, ce=45.0))) == 4      # nor a directional one
    assert len(strat.orders.placed) == 8


def test_second_trade_is_still_managed_after_the_limit(strat):
    strat.signals = {"10:00": 'bullish', "10:15": 'none', "10:30": 'sideways', "10:45": 'bullish', "11:00": 'none'}
    for minute in ("10:00", "10:15", "10:30"):
        run(strat, minute, chain(pe=40.0, ce=45.0))
    trades = run(strat, "10:45", chain(pe=40.0, ce=50.0)).set_index('strike')  # sideways -> bullish: CE closed, PE kept
    assert trades.at[23100.0, 'exit_reason'] == "signal lost"
    trades = run(strat, "11:00", chain(pe=30.0, ce=50.0))
    assert not trades['exit_time'].isna().any() and len(trades) == 3


def test_adding_a_leg_at_a_later_check_counts_as_a_trade(strat):
    strat.signals = {"10:00": 'bullish', "10:15": 'sideways', "10:30": 'none', "10:45": 'sideways'}
    for minute in ("10:00", "10:15", "10:30"):
        run(strat, minute, chain(pe=40.0, ce=45.0))
    assert len(run(strat, "10:45", chain(pe=40.0, ce=45.0))) == 2  # PE was trade 1, the added CE was trade 2


def test_missing_leg_of_second_trade_can_still_be_sold_in_the_same_bar(strat):
    strat.signals = {"10:00": 'sideways', "10:15": 'none', "10:30": 'sideways'}
    for minute in ("10:00", "10:15"):
        run(strat, minute, chain(pe=40.0, ce=45.0))
    assert len(run(strat, "10:30", chain(pe=40.0, ce=0.0))) == 3
    assert len(run(strat, "10:31", chain(pe=40.0, ce=44.0))) == 4


def test_rejected_entry_does_not_use_up_a_trade(strat):
    strat.signals = {"10:00": 'bullish', "10:15": 'bullish', "10:30": 'none', "10:45": 'bullish'}
    run(strat, "10:00", chain(pe=40.0))
    strat.orders.status = 'Rejected'
    run(strat, "10:01", chain(pe=40.0))
    strat.orders.status = 'Complete'
    for minute in ("10:15", "10:30", "10:45"):
        trades = run(strat, minute, chain(pe=40.0))
    assert list(trades['entry_state']) == ['rejected', 'complete', 'pending']


def test_limit_survives_restart(strat):
    strat.signals = {"10:00": 'sideways', "10:15": 'none', "10:30": 'sideways', "10:45": 'none'}
    for minute in ("10:00", "10:15", "10:30", "10:45"):
        run(strat, minute, chain(pe=40.0, ce=45.0))
    fresh = strat.mod.NiftyMlStratergy()
    fresh.send_message = MagicMock()
    fresh.get_signal = MagicMock(return_value='sideways')
    fresh.execute_strategy(chain(pe=40.0, ce=45.0), 'NIFTY', 'dummy', 1, strat.orders, now=at("11:00"))
    assert len(fresh.read_trades(at("11:00"))) == 4


# ---- order placement and status checks -------------------------------------------------------

def test_entry_is_pending_until_broker_confirms_and_takes_fill_price(strat):
    strat.signals = {"10:00": 'bullish'}
    trades = run(strat, "10:00", chain(pe=40.0))
    assert trades.iloc[0]['entry_state'] == 'pending' and trades.iloc[0]['entry_order_id'] == 1001
    assert strat.orders.placed == [('SELL', 22900.0, 'PE', 1)]
    strat.orders.fill = 39.5
    trades = run(strat, "10:01", chain(pe=41.0))
    assert trades.iloc[0]['entry_state'] == 'complete' and trades.iloc[0]['entry_price'] == 39.5
    calls = strat.orders.status_calls
    run(strat, "10:02", chain(pe=41.0))
    assert strat.orders.status_calls == calls  # confirmed orders are not polled again


def test_exit_is_pending_until_confirmed_and_pnl_uses_fill_price(strat):
    strat.signals = {"10:00": 'bullish', "10:15": 'none'}
    run(strat, "10:00", chain(pe=40.0))
    trades = run(strat, "10:15", chain(pe=25.0))
    assert trades.iloc[0]['exit_state'] == 'pending' and trades.iloc[0]['exit_order_id'] == 1002
    assert strat.orders.placed[-1] == ('BUY', 22900.0, 'PE', 1)
    strat.orders.fill = 26.0
    trades = run(strat, "10:16", chain(pe=25.0))
    row = trades.iloc[0]
    assert row['exit_state'] == 'complete' and row['exit_price'] == 26.0 and row['pnl_points'] == 14.0
    assert row['pnl_rupees'] == 14.0 * 65


def test_rejected_entry_retires_leg_and_retries_at_next_check(strat):
    strat.signals = {"10:00": 'bullish', "10:15": 'bullish'}
    run(strat, "10:00", chain(pe=40.0))
    strat.orders.status = 'Rejected'
    trades = run(strat, "10:01", chain(pe=40.0))
    assert trades.iloc[0]['entry_state'] == 'rejected' and trades.iloc[0]['exit_reason'] == "entry rejected"
    assert trades.iloc[0]['pnl_points'] == 0.0
    assert len(run(strat, "10:05", chain(pe=40.0))) == 1  # no new order within the same bar
    strat.orders.status = 'Complete'
    trades = run(strat, "10:15", chain(pe=38.0))
    assert len(trades) == 2 and trades.iloc[1]['entry_state'] == 'pending' and pd.isna(trades.iloc[1]['exit_time'])
    assert [o[0] for o in strat.orders.placed] == ['SELL', 'SELL']  # the rejected leg is never bought back


def test_failed_entry_placement_records_nothing_and_retries_next_bar(strat):
    strat.signals = {"10:00": 'bullish', "10:15": 'bullish'}
    strat.orders.fail = True
    assert len(run(strat, "10:00", chain(pe=40.0))) == 0
    assert len(run(strat, "10:01", chain(pe=40.0))) == 0
    strat.orders.fail = False
    assert len(run(strat, "10:02", chain(pe=40.0))) == 0  # not retried within the bar
    assert len(run(strat, "10:15", chain(pe=40.0))) == 1


def test_rejected_close_reopens_leg_and_retries(strat):
    strat.signals = {"10:00": 'bullish', "10:15": 'none'}
    run(strat, "10:00", chain(pe=40.0))
    run(strat, "10:01", chain(pe=40.0))
    run(strat, "10:15", chain(pe=25.0))
    strat.orders.status = 'Rejected'
    trades = run(strat, "10:16", chain(pe=25.0))  # rejection seen, leg reopened and closed again
    assert trades.iloc[0]['exit_state'] == 'pending' and trades.iloc[0]['exit_order_id'] == 1003
    strat.orders.status = 'Complete'
    trades = run(strat, "10:17", chain(pe=24.0))
    assert trades.iloc[0]['exit_state'] == 'complete' and len(trades) == 1
    assert [o[0] for o in strat.orders.placed] == ['SELL', 'BUY', 'BUY']


def test_failed_close_placement_is_retried_every_loop(strat):
    strat.signals = {"10:00": 'bullish', "10:15": 'none'}
    run(strat, "10:00", chain(pe=40.0))
    run(strat, "10:01", chain(pe=40.0))
    strat.orders.fail = True
    assert pd.isna(run(strat, "10:15", chain(pe=25.0)).iloc[0]['exit_time'])
    strat.orders.fail = False
    assert run(strat, "10:16", chain(pe=24.0)).iloc[0]['exit_price'] == 24.0
    assert strat.send_message.call_count == 2  # entry fill + one close-failure alert (close fill not yet confirmed)


def test_unconfirmed_order_stays_pending_without_duplicates_and_alerts_once(strat):
    strat.signals = {"10:00": 'bullish'}
    strat.orders.status = 'Open'
    for minute in ("10:00", "10:01", "10:02", "10:04", "10:06"):
        trades = run(strat, minute, chain(pe=40.0))
    assert len(trades) == 1 and trades.iloc[0]['entry_state'] == 'pending'
    assert len(strat.orders.placed) == 1
    stuck = [c for c in strat.send_message.call_args_list if "still not confirmed" in c.args[0]]
    assert len(stuck) == 1


def test_leg_with_unconfirmed_entry_is_not_closed(strat):
    strat.signals = {"10:00": 'bullish', "10:15": 'none'}
    strat.orders.status = 'Open'
    run(strat, "10:00", chain(pe=40.0))
    trades = run(strat, "10:15", chain(pe=25.0))
    assert pd.isna(trades.iloc[0]['exit_time']) and len(strat.orders.placed) == 1
    strat.orders.status = 'Complete'
    trades = run(strat, "10:16", chain(pe=25.0))  # entry confirmed, then the postponed close goes out
    assert trades.iloc[0]['entry_state'] == 'complete' and trades.iloc[0]['exit_state'] == 'pending'


def test_square_off_summary_waits_for_exit_confirmation(strat):
    strat.signals = {"14:00": 'sideways'}
    run(strat, "14:00", chain(pe=40.0, ce=45.0))
    run(strat, "14:01", chain(pe=40.0, ce=45.0))
    strat.send_message.reset_mock()
    run(strat, "15:15", chain(pe=10.0, ce=12.0))
    assert strat.send_message.call_count == 0  # close orders placed, nothing confirmed yet
    run(strat, "15:16", chain(pe=10.0, ce=12.0))
    messages = [c.args[0] for c in strat.send_message.call_args_list]
    assert sum("closed" in m for m in messages) == 1 and sum("day total" in m for m in messages) == 1
    assert "Total +63.0 points" in messages[0] and "+63.0 points" in messages[-1]
    run(strat, "15:17", chain(pe=10.0, ce=12.0))
    assert strat.send_message.call_count == 2  # one exit message and the summary, each sent once


@pytest.mark.parametrize("proba,pcr,day_pcr,expected", [
    ([0.2, 0.2, 0.6], 1.45, [1.40, 1.45], 'bullish'),   # bullish, PCR above 1.4
    ([0.2, 0.2, 0.6], 1.10, [0.80, 1.10], 'bullish'),   # bullish, PCR 0.3 above the day's low
    ([0.2, 0.2, 0.6], 1.10, [0.95, 1.10], 'none'),      # bullish but PCR not confirming
    ([0.6, 0.2, 0.2], 0.65, [0.65, 0.70], 'bearish'),   # bearish, PCR below 0.7
    ([0.6, 0.2, 0.2], 0.90, [0.90, 1.20], 'bearish'),   # bearish, PCR 0.3 under the day's high
    ([0.6, 0.2, 0.2], 0.90, [0.90, 1.05], 'none'),      # bearish but PCR not confirming
    ([0.2, 0.6, 0.2], 1.00, [1.00, 1.00], 'sideways'),  # sideways needs no PCR confirmation
    ([0.3, 0.4, 0.3], 1.50, [0.50, 1.50], 'none'),      # sideways is top class but under the 0.45 threshold
    ([0.3, 0.26, 0.44], 1.50, [0.50, 1.50], 'none'),    # bullish is top class but under the 0.45 threshold
])
def test_signal_combines_model_and_pcr(monkeypatch, tmp_path, proba, pcr, day_pcr, expected):
    assert signal_for(monkeypatch, tmp_path, proba, pcr, day_pcr) == expected
    assert len(pd.read_csv("csv/ml_signals_2026-11-16_NIFTY.csv")) == 1


def signal_for(monkeypatch, tmp_path, proba, pcr, day_pcr, held='none'):
    """Run the real get_signal for one bar with the model stubbed to return `proba`."""
    for name in STUBS:
        monkeypatch.setitem(sys.modules, name, MagicMock())
    monkeypatch.delitem(sys.modules, 'NiftyMlStratergy', raising=False)
    mod = importlib.import_module('NiftyMlStratergy')
    (tmp_path / "csv").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    pd.DataFrame({'pe_to_ce_ratio': day_pcr}).to_csv("csv/options_chain_NIFTY_2026-11-16.csv", index=False)
    bar = pd.Timestamp("2026-11-16 10:00")
    frame = pd.DataFrame({'x': [1.0]}, index=[bar])
    monkeypatch.setattr(mod.nf, 'load_model', lambda path: {"bars_per_day": 25})
    mod.MarketData.return_value.get_ohlc.return_value = pd.DataFrame({'Date': [bar]})
    monkeypatch.setattr(mod.nf, 'session_bars', lambda *a, **k: frame)
    monkeypatch.setattr(mod.nf, 'recent_option_frame', lambda *a, **k: frame)
    monkeypatch.setattr(mod.nf, 'make_features', lambda *a, **k: frame)
    monkeypatch.setattr(mod.nf, 'predict_proba', lambda model, row: proba)
    s = mod.NiftyMlStratergy()
    return s.get_signal(chain(pcr=pcr), MagicMock(), bar, at("10:00"), held)


@pytest.mark.parametrize("proba,held,expected", [
    ([0.2698, 0.4473, 0.2829], 'none', 'none'),          # 13:30 on 2026-11-16: not entered below 0.45 ...
    ([0.2698, 0.4473, 0.2829], 'sideways', 'sideways'),  # ... but kept when already held
    ([0.30, 0.40, 0.30], 'sideways', 'sideways'),        # exactly at the exit threshold: kept
    ([0.31, 0.399, 0.291], 'sideways', 'none'),          # below the exit threshold: lost
    ([0.20, 0.43, 0.37], 'sideways', 'sideways'),        # kept although bullish is nearly as likely
    ([0.25, 0.40, 0.35], 'bullish', 'none'),             # held bullish at 0.35 is below the exit threshold
    ([0.25, 0.30, 0.42], 'bullish', 'bullish'),          # held bullish at 0.42: kept
    ([0.43, 0.30, 0.27], 'bearish', 'bearish'),          # held bearish at 0.43: kept
    ([0.20, 0.60, 0.20], 'bullish', 'sideways'),         # bullish -> sideways when sideways is above 0.45
])
def test_held_signal_is_kept_until_probability_falls_below_exit_threshold(monkeypatch, tmp_path, proba, held, expected):
    # PCR 1.0 with day range 0.9-1.1 confirms neither a bullish nor a bearish entry
    assert signal_for(monkeypatch, tmp_path, proba, 1.0, [0.9, 1.1], held) == expected


def test_a_new_class_above_the_entry_threshold_replaces_the_held_one(monkeypatch, tmp_path):
    # PCR 1.45 confirms a bullish entry
    assert signal_for(monkeypatch, tmp_path, [0.10, 0.35, 0.55], 1.45, [1.4, 1.45], 'sideways') == 'bullish'
    # the same probabilities without PCR confirmation: bullish is not entered and sideways at 0.35 is below the exit level
    assert signal_for(monkeypatch, tmp_path, [0.10, 0.35, 0.55], 1.0, [0.9, 1.1], 'sideways') == 'none'


def test_held_directional_signal_does_not_need_pcr_to_stay(monkeypatch, tmp_path):
    # bullish at 0.50 would not be entered (PCR 1.0 confirms nothing), but an already held PE is kept
    assert signal_for(monkeypatch, tmp_path, [0.2, 0.3, 0.5], 1.0, [0.9, 1.1], 'none') == 'none'
    assert signal_for(monkeypatch, tmp_path, [0.2, 0.3, 0.5], 1.0, [0.9, 1.1], 'bullish') == 'bullish'


def test_signals_file_records_what_was_held(monkeypatch, tmp_path):
    signal_for(monkeypatch, tmp_path, [0.2698, 0.4473, 0.2829], 1.0, [0.9, 1.1], 'sideways')
    row = pd.read_csv("csv/ml_signals_2026-11-16_NIFTY.csv").iloc[0]
    assert (row['model_call'], row['signal'], row['held']) == ('none', 'sideways', 'sideways')


def test_retrained_model_is_reloaded_without_restart(monkeypatch, tmp_path):
    for name in STUBS:
        monkeypatch.setitem(sys.modules, name, MagicMock())
    monkeypatch.delitem(sys.modules, 'NiftyMlStratergy', raising=False)
    mod = importlib.import_module('NiftyMlStratergy')
    monkeypatch.chdir(tmp_path)
    model_file = tmp_path / "model.json"
    model_file.write_text("{}")
    monkeypatch.setattr(mod, 'MODEL_PATH', str(model_file))
    loads = []
    monkeypatch.setattr(mod.nf, 'load_model', lambda path: loads.append(path) or {"bars_per_day": 25})
    mod.MarketData.return_value.get_ohlc.return_value = None  # stop right after the model step
    s = mod.NiftyMlStratergy()
    bar = pd.Timestamp("2026-11-16 10:00")
    s.get_signal(chain(), MagicMock(), bar, at("10:00"))
    s.get_signal(chain(), MagicMock(), bar, at("10:00"))
    assert len(loads) == 1
    os.utime(model_file, (1, 1))  # a new file arrives (git pull)
    s.get_signal(chain(), MagicMock(), bar, at("10:00"))
    assert len(loads) == 2


def test_signal_unavailable_until_closed_candle_arrives(monkeypatch, tmp_path):
    for name in STUBS:
        monkeypatch.setitem(sys.modules, name, MagicMock())
    monkeypatch.delitem(sys.modules, 'NiftyMlStratergy', raising=False)
    mod = importlib.import_module('NiftyMlStratergy')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mod.nf, 'load_model', lambda path: {"bars_per_day": 25})
    # broker has only delivered candles up to the one that opened at 09:30 (closes 09:45)
    candles = pd.DataFrame({'Date': pd.to_datetime(["2026-11-16 09:15", "2026-11-16 09:30"]),
                            'open': [1.0, 1.0], 'high': [1.0, 1.0], 'low': [1.0, 1.0], 'close': [1.0, 1.0]})
    mod.MarketData.return_value.get_ohlc.return_value = candles
    s = mod.NiftyMlStratergy()
    assert s.get_signal(chain(), MagicMock(), pd.Timestamp("2026-11-16 10:00"), at("10:00")) is None


# ---- shared feature / model code -------------------------------------------------------------

def test_predict_proba_walks_exported_trees():
    # one stump per class on feature "a": a <= 0.5 favours class 0, otherwise class 2
    model = {"features": ["a"], "classes": 3, "trees": [
        {"f": [0], "t": [0.5], "l": [-1], "r": [-2], "v": [2.0, 0.0]},
        {"f": [], "t": [], "l": [], "r": [], "v": [0.0]},
        {"f": [0], "t": [0.5], "l": [-1], "r": [-2], "v": [0.0, 2.0]},
    ]}
    low, high = nf.predict_proba(model, {"a": 0.5}), nf.predict_proba(model, {"a": 0.6})
    assert low[0] > low[2] and high[2] > high[0]
    assert abs(sum(low) - 1) < 1e-12
    assert abs(low[0] - np.exp(2) / (np.exp(2) + 2)) < 1e-12


def test_expiry_dte_thursday_then_tuesday_and_holiday_shift():
    days = pd.bdate_range("2025-08-25", "2025-09-05")
    dte = pd.Series(nf.expiry_dte(days), index=days)
    assert dte["2025-08-28"] == 0   # Thursday expiry before September 2025
    assert dte["2025-09-02"] == 0   # Tuesday expiry from September 2025
    assert dte["2025-09-01"] == 1 and dte["2025-08-29"] == 2
    # Thursday 2025-08-28 missing (holiday) -> expiry moves to Wednesday
    holiday_week = days[days != pd.Timestamp("2025-08-28")]
    assert pd.Series(nf.expiry_dte(holiday_week), index=holiday_week)["2025-08-27"] == 0


def test_session_bars_labels_by_close_and_keeps_partial_last_day():
    idx = pd.date_range("2026-11-13 09:15", periods=25, freq="15min").append(
        pd.DatetimeIndex(["2026-11-16 09:15", "2026-11-16 09:30"]))
    d = pd.DataFrame({'open': 1.0, 'high': 2.0, 'low': 0.5, 'close': 1.5}, index=idx)
    live = nf.session_bars(d, "15min", keep_last_day=True)
    assert live.index[0] == pd.Timestamp("2026-11-13 09:30") and live.index[-1] == pd.Timestamp("2026-11-16 09:45")
    assert list(live.columns) == ["spot", "open", "high", "low"]
    assert nf.session_bars(d, "15min").index[-1] == pd.Timestamp("2026-11-13 15:30")  # partial day dropped offline


def test_alligator_trend_follows_price():
    n = 80
    up = pd.Series(np.linspace(100, 180, n))
    trend, mouth = nf.alligator(up + 1, up - 1, up)
    assert trend.iloc[-1] == 1 and mouth.iloc[-1] > 0
    down = pd.Series(np.linspace(180, 100, n))
    assert nf.alligator(down + 1, down - 1, down)[0].iloc[-1] == -1
    assert trend.iloc[:20].isna().all()  # no state until the shifted lines exist


# ---- isolation from the rest of the engine ---------------------------------------------------

ENGINE_STUBS = [
    'PlaceOrder', 'OptionChainData', 'AutoStraddleStrategy', 'FarSellStratergy', 'configuration',
    'CommodityStratergy', 'cash_stratergy', 'IndexFutureStratergy', 'NiftyPositionalStrategy',
    'TelegramSend', 'ledger_calculation', 'update_cash_sl', 'elliot_wave_signals',
    'elliot_cash_stratergy', 'fund_optimizer', 'requests',
]


def load_engine(monkeypatch, ml_module):
    for name in ENGINE_STUBS:
        monkeypatch.setitem(sys.modules, name, MagicMock())
    monkeypatch.setitem(sys.modules, 'NiftyMlStratergy', ml_module)
    monkeypatch.delitem(sys.modules, 'AutoStraddle', raising=False)
    return importlib.import_module('AutoStraddle')


def test_engine_still_imports_when_ml_module_is_broken(monkeypatch):
    engine = load_engine(monkeypatch, None)  # None in sys.modules makes the import raise ImportError
    assert engine.NiftyMlStratergy is None


def test_ml_failure_does_not_stop_other_accounts_or_symbols(monkeypatch):
    engine = load_engine(monkeypatch, MagicMock())
    monkeypatch.setattr(engine, 'dump_option_chain_data_to_csv', MagicMock())
    engine.OptionChainData.return_value.get_option_chain_info.return_value = {'atm_strike': 23000.0}
    details = pd.DataFrame({'Account': ['dummy', 'deepti', 'deepti'], 'Symbol': ['NIFTY', 'NIFTY', 'BANKNIFTY'],
                            'Stratergy': ['ml', 'fr', 'fr'], 'quantity': [1, 1, 1]})
    ml, far = MagicMock(), MagicMock()
    ml.execute_strategy.side_effect = RuntimeError("boom")
    far.get_strangle_strike_price.return_value = (0, 0)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 16, 10, 0, 5)
    monkeypatch.setattr(engine, 'datetime', Frozen)
    engine.execute_option_stratergy(MagicMock(), far, ['dummy', 'deepti'], ['NIFTY', 'BANKNIFTY'],
                                    MagicMock(), details, MagicMock(), ml)
    assert ml.execute_strategy.call_count == 1
    assert far.execute_strategy.call_count == 2  # deepti NIFTY and BANKNIFTY still ran after the ML failure
