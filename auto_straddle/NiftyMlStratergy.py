"""Module providing the Nifty ML option-selling strategy (paper on dummy, live on deepti / Angel One)"""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
import logging
import os
import traceback
from datetime import datetime, time
import pandas as pd
import TelegramSend
import configuration
import nifty_ml_features as nf
from market_data import MarketData
from OptionChainData import OptionChainData

logger = logging.getLogger(__name__)

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nifty_ml_model.json")
TRADE_COLUMNS = ['entry_time', 'signal', 'option_type', 'strike', 'entry_atm', 'quantity', 'entry_price',
                 'exit_time', 'exit_price', 'exit_reason', 'pnl_points', 'pnl_rupees',
                 'entry_order_id', 'entry_state', 'exit_order_id', 'exit_state']
# columns that hold text (or broker order ids) and must not be read back as float when empty
TEXT_COLUMNS = ['exit_time', 'exit_reason', 'entry_order_id', 'entry_state', 'exit_order_id', 'exit_state']
EXIT_COLUMNS = ['exit_time', 'exit_price', 'exit_reason', 'pnl_points', 'pnl_rupees', 'exit_order_id', 'exit_state']
# option legs that should be sold for each signal
LEGS = {'bullish': ('PE',), 'bearish': ('CE',), 'sideways': ('PE', 'CE'), 'none': ()}
# the signal that the legs currently held correspond to
HELD_SIGNAL = {frozenset(('PE',)): 'bullish', frozenset(('CE',)): 'bearish', frozenset(('PE', 'CE')): 'sideways'}


class NiftyMlStratergy:
    """Every 15 minutes the LightGBM model decides bullish / bearish / sideways.
    Bullish (confirmed by PCR) sells the PE 100 points below ATM, bearish (confirmed by PCR) sells
    the CE 100 points above ATM, sideways (model only) sells both. A leg is closed when the new
    signal no longer wants it, and everything is closed at 15:15. After any exit there is a
    cooling period: new legs are sold at the next 15-minute check at the earliest.
    A signal is entered when its class probability is above PROB_THRESHOLD (0.45) but, once held,
    it is only given up when that probability falls below PROB_EXIT (0.40), so a dip just under
    0.45 does not close and re-open the same position. Each entry and each exit sends one
    Telegram message for all the legs of that trade. At most MAX_TRADES_PER_DAY (2) trades are
    entered per day; exits and the square-off are never blocked.

    Orders go through PlaceOrder like the other option strategies: every entry and exit is
    placed as an order, recorded as 'pending', and confirmed on the following loops with
    order_status (fill price taken from the broker, rejections handled). It runs for the
    accounts in PAPER_ACCOUNTS (filled by PlaceOrder without a broker) and LIVE_ACCOUNTS
    (real orders) and refuses every other account. Each account has its own trades file,
    signals file, Telegram chat and per-bar state; the model runs once per bar for all of them.

    Extra protection on live accounts:
    - entries are refused when the sheet quantity is above MAX_LIVE_LOTS (exits still run);
    - if a sell order cannot be placed, new entries stop for the day (the order may have gone
      through at the broker, so retrying could double the position) and an alert asks for a check;
    - a close is placed even when the option chain has no price (the buy-back is a MARKET order;
      P&L comes from the broker's fill price);
    - an entry still unconfirmed at the square-off gets a "close manually" alert.
    """
    PAPER_ACCOUNTS = ('dummy',)
    LIVE_ACCOUNTS = ('deepti',)  # Angel One: its order status strings are mapped by PlaceOrder.order_status
    MAX_LIVE_LOTS = 1            # a live account trades only if its sheet quantity is at most this
    SYMBOL = 'NIFTY'
    STRIKE_OFFSET = 100          # = 2 strike intervals: the option chain's atm_next_* prices
    LOT_SIZE = 65
    PROB_THRESHOLD = 0.45        # entry: the model's top class must be above this
    PROB_EXIT = 0.40             # exit: a held signal is kept until its class probability falls below this
    MAX_TRADES_PER_DAY = 2       # a trade = the leg(s) sold at one 15-minute check; no new entries after this many
    PCR_BULLISH, PCR_BEARISH, PCR_SWING = 1.4, 0.7, 0.3
    FIRST_BAR = time(9, 30)
    LAST_ENTRY = time(14, 0)     # last bar that may open a trade: the model looks 90 minutes ahead and is trained on bars up to 14:00
    SQUARE_OFF = time(15, 15)
    RETRY_SECONDS = 300          # how long to wait for the just-closed candle before skipping a bar
    STUCK_SECONDS = 120          # alert when an order is still unconfirmed after this long

    def __init__(self):
        self.model = None
        self.model_mtime = None
        self.market_data = None
        self.proba_bar = None    # bar whose model probabilities are cached in self.proba
        self.proba = None
        self.rejected = set()
        self.states = {}         # per-account state, see state()
        self.account = self.PAPER_ACCOUNTS[0]  # account being processed by the current call

    def state(self):
        """Per-account state for the account being processed."""
        return self.states.setdefault(self.account, {
            'signal_bar': None,    # bar the current signal belongs to
            'signal': None,        # 'bullish' / 'bearish' / 'sideways' / 'none', or None when unknown
            'acted_bar': None,     # bar whose signal has been fully acted on
            'summary_date': None,
            'alerted': set(),      # order problems already reported, so each is sent once
        })

    signal_bar = property(lambda self: self.state()['signal_bar'], lambda self, v: self.state().__setitem__('signal_bar', v))
    signal = property(lambda self: self.state()['signal'], lambda self, v: self.state().__setitem__('signal', v))
    acted_bar = property(lambda self: self.state()['acted_bar'], lambda self, v: self.state().__setitem__('acted_bar', v))
    summary_date = property(lambda self: self.state()['summary_date'], lambda self, v: self.state().__setitem__('summary_date', v))
    alerted = property(lambda self: self.state()['alerted'])

    def is_live(self):
        return self.account in self.LIVE_ACCOUNTS

    def execute_strategy(self, option_chain_info, symbol, account, quantity, place_order_obj, now=None):
        if account not in self.PAPER_ACCOUNTS + self.LIVE_ACCOUNTS or symbol != self.SYMBOL:
            if (account, symbol) not in self.rejected:
                self.rejected.add((account, symbol))
                logger.error(f"NiftyMl runs only for {self.PAPER_ACCOUNTS + self.LIVE_ACCOUNTS}/{self.SYMBOL}, "
                             f"ignoring {account}/{symbol}")
            return
        self.account = account
        now = now or datetime.now()
        trades = self.check_orders(self.read_trades(now), place_order_obj, now)
        open_legs = list(trades.index[trades['exit_time'].isna()])

        if now.time() >= self.SQUARE_OFF:
            for leg in open_legs:
                if trades.at[leg, 'entry_state'] == 'pending' and (leg, 'square-off') not in self.alerted:
                    self.alerted.add((leg, 'square-off'))
                    self.send_message(f"NiftyMl {account} {symbol} ERROR at square-off the sell order "
                                      f"{trades.at[leg, 'entry_order_id']} for {float(trades.at[leg, 'strike']):.0f} "
                                      f"{trades.at[leg, 'option_type']} is still unconfirmed, so it cannot be closed: "
                                      f"check the broker and close it manually")
                self.close_position(trades, leg, option_chain_info, now, "15:15 square-off", place_order_obj)
            pending = (trades['exit_state'] == 'pending').any()
            if not open_legs and not pending and len(trades) and self.summary_date != now.date():
                self.summary_date = now.date()
                self.send_message(f"NiftyMl {account} {symbol} day total: {len(trades)} legs, "
                                  f"{trades['pnl_points'].astype(float).sum():+.1f} points, "
                                  f"Rs {trades['pnl_rupees'].astype(float).sum():+.0f} (before costs)")
            return
        if now.time() < self.FIRST_BAR:
            return

        bar = pd.Timestamp(now).floor("15min")
        if self.signal_bar != bar:
            result = self.get_signal(option_chain_info, place_order_obj, bar, now, self.held_signal(trades))
            if result is None:
                if (now - bar).total_seconds() < self.RETRY_SECONDS:
                    return  # candle or chain data not ready yet: try again on the next loop
                logger.warning(f"NiftyMl no signal for bar {bar}: inputs unavailable, position left unchanged")
            self.signal_bar, self.signal = bar, result
        if self.signal is None or self.acted_bar == bar:
            return

        wanted = LEGS[self.signal]
        done = True
        for leg in open_legs:  # close the legs the new signal no longer wants
            if trades.at[leg, 'option_type'] not in wanted:
                done = self.close_position(trades, leg, option_chain_info, now, "signal lost", place_order_obj) and done
        if bar.time() <= self.LAST_ENTRY and not self.in_cooling(trades, bar) and self.entries_allowed(trades, bar) \
                and self.size_ok(quantity, now) and not self.entries_blocked(now):
            held = set(trades.loc[trades['exit_time'].isna(), 'option_type'])
            for option_type in wanted:  # sell the legs it wants and does not hold yet
                if option_type not in held:
                    new_trades = self.enter_position(trades, option_type, option_chain_info, quantity, now, place_order_obj)
                    done = new_trades is not None and done
                    trades = trades if new_trades is None else new_trades
        if done:  # otherwise a price was missing or an order could not be placed: retry on the next loop
            self.acted_bar = bar

    def size_ok(self, quantity, now):
        """Live accounts sell only if the sheet quantity is at most MAX_LIVE_LOTS (alerted once a day)."""
        if not self.is_live() or float(quantity) <= self.MAX_LIVE_LOTS:
            return True
        if (now.date(), 'size') not in self.alerted:
            self.alerted.add((now.date(), 'size'))
            self.send_message(f"NiftyMl {self.account} {self.SYMBOL} ERROR sheet quantity {quantity} is above the "
                              f"{self.MAX_LIVE_LOTS}-lot limit for live accounts: no new entries (exits still run)")
        return False

    def get_blocked_file_path(self, now):
        return f"csv/ml_blocked_{now.strftime('%Y-%m-%d')}_{self.account}_{self.SYMBOL}.txt"

    def entries_blocked(self, now):
        """True when new entries were stopped for the day on this account (survives a restart)."""
        return os.path.exists(self.get_blocked_file_path(now))

    def held_signal(self, trades):
        """The signal the open legs correspond to, read from the trades file so it survives a restart:
        PE and CE = sideways, PE only = bullish, CE only = bearish, nothing open = none."""
        open_types = frozenset(trades.loc[trades['exit_time'].isna(), 'option_type'])
        return HELD_SIGNAL.get(open_types, 'none')

    def check_orders(self, trades, place_order_obj, now):
        """Confirm pending entry and exit orders with the broker; returns the updated trades.
        One Telegram message is sent per entry and per exit, once every leg of it is confirmed."""
        changed = False
        entry_bars, exit_bars = set(), set()  # 15-minute bars of the entries / exits that changed state
        for leg in trades.index:
            name = f"{float(trades.at[leg, 'strike']):.0f} {trades.at[leg, 'option_type']}"
            if trades.at[leg, 'entry_state'] == 'pending':
                order_id = trades.at[leg, 'entry_order_id']
                state, price = self.order_state(place_order_obj, order_id, trades.at[leg, 'entry_price'])
                if state == 'complete':
                    trades.at[leg, 'entry_state'] = 'complete'
                    trades.at[leg, 'entry_price'] = price
                    changed = True
                    entry_bars.add(self.bar_of(trades.at[leg, 'entry_time']))
                elif state == 'rejected':
                    # nothing was sold: retire the leg; the cooling period delays the next attempt to the next check
                    trades.at[leg, 'entry_state'] = 'rejected'
                    trades.loc[leg, ['exit_time', 'exit_reason', 'pnl_points', 'pnl_rupees']] = \
                        [now.strftime("%Y-%m-%d %H:%M:%S"), "entry rejected", 0.0, 0.0]
                    changed = True
                    entry_bars.add(self.bar_of(trades.at[leg, 'entry_time']))  # its sibling leg may now be complete
                    self.send_message(f"NiftyMl {self.account} {self.SYMBOL} ERROR entry order {order_id} for {name} was rejected")
                else:
                    self.alert_if_stuck(order_id, name, "entry", trades.at[leg, 'entry_time'], now)
            elif trades.at[leg, 'exit_state'] == 'pending':
                order_id = trades.at[leg, 'exit_order_id']
                state, price = self.order_state(place_order_obj, order_id, trades.at[leg, 'exit_price'])
                if state == 'complete':
                    pnl_points = float(trades.at[leg, 'entry_price']) - price
                    pnl_rupees = pnl_points * self.LOT_SIZE * float(trades.at[leg, 'quantity'])
                    trades.loc[leg, ['exit_price', 'pnl_points', 'pnl_rupees', 'exit_state']] = \
                        [price, round(pnl_points, 2), round(pnl_rupees, 2), 'complete']
                    changed = True
                    exit_bars.add(self.bar_of(trades.at[leg, 'exit_time']))
                elif state == 'rejected':
                    # the option is still sold at the broker: reopen the leg so it is closed again
                    exit_bars.add(self.bar_of(trades.at[leg, 'exit_time']))  # its sibling leg may now be complete
                    trades.loc[leg, EXIT_COLUMNS] = [None] * len(EXIT_COLUMNS)
                    self.acted_bar = None
                    changed = True
                    self.send_message(f"NiftyMl {self.account} {self.SYMBOL} ERROR close order {order_id} for {name} was rejected, retrying")
                else:
                    self.alert_if_stuck(order_id, name, "close", trades.at[leg, 'exit_time'], now)
        if changed:
            trades.to_csv(self.get_trades_file_path(now), index=False)
        self.announce_entries(trades, entry_bars)
        self.announce_exits(trades, exit_bars)
        return trades

    def bar_of(self, stamp):
        return pd.Timestamp(stamp).floor("15min")

    def announce_entries(self, trades, bars):
        """One message per entry: the legs sold in the same 15-minute bar, sent once none is still pending."""
        entry_bar = pd.to_datetime(trades['entry_time']).dt.floor("15min")
        for bar in sorted(bars):
            group = trades[entry_bar == bar]
            if group.empty or (group['entry_state'] == 'pending').any():
                continue
            legs = group[group['entry_state'] == 'complete']
            if legs.empty:
                continue
            sold = " and ".join(f"{float(r['strike']):.0f} {r['option_type']} at {float(r['entry_price'])}"
                                for _, r in legs.iterrows())
            first = legs.iloc[0]
            self.send_message(f"NiftyMl {self.account} {self.SYMBOL} {first['signal']}: sold {sold} "
                              f"(ATM {float(first['entry_atm']):.0f}, {first['quantity']} lot)")

    def announce_exits(self, trades, bars):
        """One message per exit: the legs closed in the same 15-minute bar, sent once none is still pending."""
        exit_bar = pd.to_datetime(trades['exit_time']).dt.floor("15min")
        for bar in sorted(bars):
            group = trades[(exit_bar == bar) & trades['exit_state'].isin(['pending', 'complete'])]
            if group.empty or (group['exit_state'] == 'pending').any():
                continue
            lines = [f"NiftyMl {self.account} {self.SYMBOL} closed ({group.iloc[0]['exit_reason']})"]
            for _, r in group.iterrows():
                lines.append(f"{float(r['strike']):.0f} {r['option_type']}: sold {float(r['entry_price'])}, "
                             f"bought {float(r['exit_price'])}, {float(r['pnl_points']):+.1f} points")
            points = group['pnl_points'].astype(float).sum()
            rupees = group['pnl_rupees'].astype(float).sum()
            if len(group) == 1:
                lines = [f"{lines[0]} {lines[1]}, Rs {rupees:+.0f} (before costs)"]
            else:
                lines.append(f"Total {points:+.1f} points, Rs {rupees:+.0f} (before costs)")
            self.send_message("\n".join(lines))

    def order_state(self, place_order_obj, order_id, expected_price):
        """('complete', fill price) / ('rejected', None) / ('pending', None) for one order."""
        status, price = place_order_obj.order_status(self.account, order_id, float(expected_price))
        if status == 'Complete':
            price = float(price) if price is not None and float(price) > 0 else float(expected_price)
            return 'complete', price
        if 'reject' in str(status).lower() or 'cancel' in str(status).lower():
            return 'rejected', None
        return 'pending', None  # open, not found yet, or the status call failed: ask again on the next loop

    def alert_if_stuck(self, order_id, name, kind, placed_at, now):
        waited = (now - pd.Timestamp(placed_at).to_pydatetime()).total_seconds()
        if waited > self.STUCK_SECONDS and (order_id, 'stuck') not in self.alerted:
            self.alerted.add((order_id, 'stuck'))
            self.send_message(f"NiftyMl {self.account} {self.SYMBOL} WARNING {kind} order {order_id} for {name} "
                              f"still not confirmed after {waited / 60:.0f} minutes")

    def entries_allowed(self, trades, bar):
        """False once MAX_TRADES_PER_DAY trades have been entered today. A trade is the leg(s) sold at
        one 15-minute check, so a strangle is one trade; rejected entries do not count. The current
        bar stays allowed if it already has an entry, so a missing second leg can still be sold."""
        placed = trades[trades['entry_state'] != 'rejected']
        entry_bars = set(pd.to_datetime(placed['entry_time']).dt.floor("15min"))
        return bar in entry_bars or len(entry_bars) < self.MAX_TRADES_PER_DAY

    def in_cooling(self, trades, bar):
        """True when a leg was closed during this 15-minute bar: after any exit, new legs
        are sold at the next 15-minute check at the earliest."""
        exits = pd.to_datetime(trades['exit_time'].dropna())
        return len(exits) > 0 and exits.max().floor("15min") >= bar

    def get_signal(self, option_chain_info, place_order_obj, bar, now, held='none'):
        """'bullish' / 'bearish' / 'sideways' / 'none' for the bar that closed at `bar`,
        or None when the inputs are not available. `held` is the signal the open legs correspond to:
        a new signal needs its probability above PROB_THRESHOLD, but a held one is kept until its
        probability falls below PROB_EXIT. PCR confirmation is needed to enter only."""
        probabilities = self.bar_probabilities(place_order_obj, bar)
        if probabilities is None:
            return None
        p_bear, p_side, p_bull = probabilities
        names, probs = ('bearish', 'sideways', 'bullish'), (p_bear, p_side, p_bull)
        top = max(range(3), key=lambda i: probs[i])
        model_call = names[top] if probs[top] > self.PROB_THRESHOLD else 'none'

        pcr = float(option_chain_info['pe_to_ce_ratio'])
        pcr_low, pcr_high = self.day_pcr_range(now, pcr)
        # 1e-9: a swing of exactly 0.3 must count despite float rounding
        bullish_ok = pcr > self.PCR_BULLISH or pcr - pcr_low >= self.PCR_SWING - 1e-9
        bearish_ok = pcr < self.PCR_BEARISH or pcr_high - pcr >= self.PCR_SWING - 1e-9
        signal = model_call  # sideways needs no PCR confirmation
        if (model_call == 'bullish' and not bullish_ok) or (model_call == 'bearish' and not bearish_ok):
            signal = 'none'
        if signal == 'none' and held != 'none':
            held_probability = {'bearish': p_bear, 'sideways': p_side, 'bullish': p_bull}[held]
            if held_probability >= self.PROB_EXIT:
                signal = held  # still above the exit threshold: keep what is held

        row = {'bar': bar, 'spot': float(option_chain_info['spot_price']), 'p_bear': round(p_bear, 4),
               'p_side': round(p_side, 4), 'p_bull': round(p_bull, 4), 'model_call': model_call, 'pcr': round(pcr, 4),
               'pcr_low': round(pcr_low, 4), 'pcr_high': round(pcr_high, 4), 'signal': signal, 'held': held}
        logger.info(f"NiftyMl signal {row}")
        print(f"NiftyMl signal {row}")
        file_name = f"csv/ml_signals_{now.strftime('%Y-%m-%d')}_{self.account}_{self.SYMBOL}.csv"
        pd.DataFrame([row]).to_csv(file_name, mode='a', header=not os.path.exists(file_name), index=False)
        return signal

    def bar_probabilities(self, place_order_obj, bar):
        """Model probabilities (bearish, sideways, bullish) for the bar that closed at `bar`, or None when the
        inputs are not available yet. Computed once per bar and shared by every account."""
        if self.proba_bar == bar:
            return self.proba
        mtime = os.path.getmtime(MODEL_PATH)
        if self.model is None or mtime != self.model_mtime:  # picks up a retrained model without a restart
            self.model, self.model_mtime = nf.load_model(MODEL_PATH), mtime
            logger.info(f"NiftyMl loaded model trained {self.model.get('train_from')} -> {self.model.get('train_to')}")
        if self.market_data is None:
            self.market_data = MarketData(place_order_obj)
        bars_per_day = self.model["bars_per_day"]

        ohlc = self.market_data.get_ohlc(self.SYMBOL, segment='NSE_INDEX', interval='15m')
        if ohlc is None or ohlc.empty:
            return None
        bars = nf.session_bars(ohlc.set_index('Date'), "15min", keep_last_day=True)
        bars = bars[bars.index <= bar]  # drops the candle still in progress
        if bars.empty or bars.index[-1] != bar:
            return None
        opt = nf.recent_option_frame("csv", "15min")
        if opt is None:
            return None
        X = nf.make_features(bars, opt, bars_per_day)
        if bar not in X.index or X.loc[bar].isna().any():
            return None
        self.proba_bar, self.proba = bar, tuple(nf.predict_proba(self.model, X.loc[bar]))
        return self.proba

    def day_pcr_range(self, now, pcr):
        """Lowest and highest PCR seen today, from today's option chain dump."""
        low = high = pcr
        file_name = f"csv/options_chain_{self.SYMBOL}_{now.strftime('%Y-%m-%d')}.csv"
        try:
            if os.path.exists(file_name):
                values = pd.to_numeric(pd.read_csv(file_name)['pe_to_ce_ratio'], errors='coerce')
                values = values[values > 0]
                if len(values):
                    low, high = min(low, values.min()), max(high, values.max())
        except Exception as e:
            logger.error(f"NiftyMl could not read {file_name}: {e}")
        return float(low), float(high)

    def get_trades_file_path(self, now):
        return f"csv/ml_trades_{now.strftime('%Y-%m-%d')}_{self.account}_{self.SYMBOL}.csv"

    def read_trades(self, now):
        file_name = self.get_trades_file_path(now)
        if os.path.exists(file_name):
            # order ids as text: read as numbers, long broker ids lose digits and can no longer be found
            trades = pd.read_csv(file_name, dtype={'entry_order_id': str, 'exit_order_id': str})
        else:
            trades = pd.DataFrame(columns=TRADE_COLUMNS)
        trades[TEXT_COLUMNS] = trades[TEXT_COLUMNS].astype(object)  # empty columns are read back as float
        return trades

    def enter_position(self, trades, option_type, option_chain_info, quantity, now, place_order_obj):
        """Place the sell order for one leg; returns the updated trades frame, or None when the
        chain has no price for it yet (the caller then retries on the next loop)."""
        atm = float(option_chain_info['atm_strike'])
        if option_type == 'PE':
            strike, price = atm - self.STRIKE_OFFSET, float(option_chain_info['atm_next_pe_price'])
        else:
            strike, price = atm + self.STRIKE_OFFSET, float(option_chain_info['atm_next_ce_price'])
        if not price > 0:
            logger.warning(f"NiftyMl no price for {strike} {option_type}, entry skipped for now")
            return None
        order_id = place_order_obj.place_orders(self.account, strike, option_type, self.SYMBOL, quantity)
        if order_id in (-1, 0, None):
            if self.is_live():
                # the order may still have reached the broker: never sell again blind, stop entries for today
                with open(self.get_blocked_file_path(now), 'w', encoding='utf-8') as fh:
                    fh.write(f"{now:%Y-%m-%d %H:%M:%S} sell order for {strike:.0f} {option_type} could not be placed\n")
                self.send_message(f"NiftyMl {self.account} {self.SYMBOL} ERROR could not place sell order for {strike:.0f} "
                                  f"{option_type}. New entries are stopped for today: check the broker for an "
                                  f"unexpected open position. Open legs are still managed.")
                return trades
            # not retried within this bar; the next 15-minute check tries again if the signal still wants it
            self.send_message(f"NiftyMl {self.account} {self.SYMBOL} ERROR could not place sell order for {strike:.0f} {option_type}")
            return trades
        order_id = str(order_id)
        row = {'entry_time': now.strftime("%Y-%m-%d %H:%M:%S"), 'signal': self.signal,
               'option_type': option_type, 'strike': strike, 'entry_atm': atm,
               'quantity': quantity, 'entry_price': price, 'entry_order_id': order_id, 'entry_state': 'pending'}
        trades = pd.concat([trades, pd.DataFrame([row])], ignore_index=True)[TRADE_COLUMNS]
        trades[TEXT_COLUMNS] = trades[TEXT_COLUMNS].astype(object)
        trades.to_csv(self.get_trades_file_path(now), index=False)
        logger.info(f"NiftyMl sell order {order_id} placed for {strike:.0f} {option_type} at about {price}")
        return trades

    def held_price(self, trade, option_chain_info):
        """Current price of the sold option, or None if the chain has no quote for it."""
        key = 'atm_next_pe_price' if trade['option_type'] == 'PE' else 'atm_next_ce_price'
        if float(option_chain_info['atm_strike']) != float(trade['entry_atm']):
            # ATM has moved: ask the chain for the prices around the entry ATM instead
            option_chain_info = OptionChainData(self.SYMBOL).get_option_chain_info(float(trade['entry_atm']), 0, 0, self.SYMBOL)
            key = 'prev_' + key
        if option_chain_info is None:
            return None
        price = float(option_chain_info.get(key, 0) or 0)
        return price if price > 0 else None

    def close_position(self, trades, position, option_chain_info, now, reason, place_order_obj):
        """Place the buy-back order for one leg; False when it could not be placed yet (retried on the next loop)."""
        trade = trades.loc[position]
        name = f"{float(trade['strike']):.0f} {trade['option_type']}"
        if trade['entry_state'] == 'pending':
            logger.warning(f"NiftyMl entry order for {name} not confirmed yet, close ({reason}) postponed")
            return False
        price = self.held_price(trade, option_chain_info)
        if price is None:
            if not self.is_live():
                logger.warning(f"NiftyMl no price to close {name} ({reason}), will retry")
                return False
            # live: never keep a real position open for want of a quote; the buy-back is a MARKET order
            # and the fill price replaces this estimate once the broker confirms it
            price = float(trade['entry_price'])
            logger.warning(f"NiftyMl no option chain price for {name} ({reason}), closing anyway at market")
        order_id = place_order_obj.close_orders(self.account, float(trade['strike']), trade['option_type'],
                                                self.SYMBOL, trade['quantity'])
        if order_id in (-1, 0, None):
            if (position, 'close') not in self.alerted:
                self.alerted.add((position, 'close'))
                self.send_message(f"NiftyMl {self.account} {self.SYMBOL} ERROR could not place close order for {name} ({reason}), retrying")
            return False
        order_id = str(order_id)
        pnl_points = float(trade['entry_price']) - price
        pnl_rupees = pnl_points * self.LOT_SIZE * float(trade['quantity'])
        trades.loc[position, EXIT_COLUMNS] = \
            [now.strftime("%Y-%m-%d %H:%M:%S"), price, reason, round(pnl_points, 2), round(pnl_rupees, 2), order_id, 'pending']
        trades.to_csv(self.get_trades_file_path(now), index=False)
        logger.info(f"NiftyMl close order {order_id} placed for {name} ({reason}) at about {price}")
        return True

    def send_message(self, message):
        logger.info(message)
        print(message)
        try:
            chat_id = configuration.ConfigurationLoader.get_configuration().get(self.account + "_telegram")
            if chat_id:
                TelegramSend.telegram_send_api().send_message(chat_id, message)
        except Exception as e:
            logger.error(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
