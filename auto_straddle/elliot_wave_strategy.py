"""
===============================================================================
ELLIOTT WAVE LONG-ONLY STRATEGY — NIFTY 200 UNIVERSE
===============================================================================

Strategy Overview:
    - Detects Elliott Wave impulse patterns (5-wave structure) using swing
      highs/lows and Fibonacci relationships.
    - Enters LONG at the start of Wave 3 (strongest wave) or early Wave 5.
    - Confirms entries with RSI momentum + Volume expansion.
    - Positional timeframe: 1-3 month holding period.
    - Uses daily candlestick data.

Wave Identification Logic (Algorithmic Approximation):
    1. Detect swing highs and swing lows using a rolling window.
    2. Label alternating swings as potential wave pivots.
    3. Validate wave structure using Elliott Wave rules:
       - Wave 2 cannot retrace more than 100% of Wave 1
       - Wave 3 cannot be the shortest impulse wave
       - Wave 4 cannot overlap the price territory of Wave 1
    4. Validate Fibonacci relationships:
       - Wave 2 retraces 50%-78.6% of Wave 1
       - Wave 3 extends 1.618x-2.618x of Wave 1
       - Wave 4 retraces 23.6%-50% of Wave 3
    5. Confirm with RSI and Volume filters.

Entry Rules (Long Only):
    A) WAVE 3 ENTRY: Buy when Wave 2 pullback completes
       - Price has retraced 50-78.6% of Wave 1
       - RSI is between 40-60 (not overbought, recovering from dip)
       - Volume starts expanding (above 20-day avg)
       - Price breaks above the high of the last 3 bars

    B) WAVE 5 ENTRY: Buy when Wave 4 pullback completes
       - Price has retraced 23.6-50% of Wave 3
       - Wave 4 does NOT overlap Wave 1 high (Elliott rule)
       - RSI > 50 (momentum intact)
       - Volume confirmation

Exit Rules:
    - Target: Fibonacci extension of the impulse wave
      - Wave 3 target: 1.618x extension of Wave 1 from Wave 2 low
      - Wave 5 target: 0.618x-1.0x extension of Wave 1 from Wave 4 low
    - Stop Loss: Below the wave's origin pivot
      - Wave 3 trade: SL below Wave 2 low
      - Wave 5 trade: SL below Wave 4 low
    - Trailing Stop: 2x ATR(14) from highest close after entry
    - Time Stop: Exit after 65 trading days (~3 months) if no target/SL hit

Dependencies:
    pip install pandas numpy yfinance ta-lib matplotlib

Usage:
    python elliott_wave_strategy.py

NOTE: This is a framework/starting point. Elliott Wave is inherently
subjective — this algo approximates wave counts using quantitative rules.
Real-world usage should combine with discretionary oversight.
===============================================================================
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import warnings

warnings.filterwarnings("ignore")


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class StrategyConfig:
    """All tunable parameters in one place."""

    # --- Swing Detection ---
    swing_lookback: int = 5           # Bars to look left/right for swing pivots
    min_swing_pct: float = 3.0        # Minimum swing size (% move) to count

    # --- Fibonacci Validation ---
    wave2_retrace_min: float = 0.382  # Min retracement of Wave 1
    wave2_retrace_max: float = 0.786  # Max retracement of Wave 1
    wave4_retrace_min: float = 0.236  # Min retracement of Wave 3
    wave4_retrace_max: float = 0.500  # Max retracement of Wave 3

    # --- Wave 3 Extension ---
    wave3_ext_min: float = 1.618     # Minimum Wave 3 extension of Wave 1
    wave3_ext_max: float = 4.236     # Maximum Wave 3 extension

    # --- RSI Filter ---
    rsi_period: int = 14
    rsi_wave3_entry_min: float = 40   # RSI range for Wave 3 entry
    rsi_wave3_entry_max: float = 70
    rsi_wave5_entry_min: float = 45
    rsi_wave5_entry_max: float = 70

    # --- Volume Filter ---
    volume_ma_period: int = 20
    volume_expansion_factor: float = 1.1  # Volume must be > 1.1x avg

    # --- Entry Confirmation ---
    breakout_bars: int = 3            # Price must break N-bar high

    # --- Exit / Risk Management ---
    atr_period: int = 14
    trailing_atr_multiplier: float = 2.5
    time_stop_days: int = 90          # Max holding period
    wave3_target_extension: float = 1.618  # Target as extension of Wave 1
    wave5_target_extension: float = 0.786  # Smaller target for Wave 5
    risk_per_trade_pct: float = 2.0   # Max risk per trade as % of capital

    # --- Portfolio ---
    initial_capital: float = 1_000_000.0
    max_positions: int = 10
    max_allocation_pct: float = 10.0   # 10% of total equity per position

    # --- Transaction Costs ---
    transaction_cost_per_trade: float = 60.0   # ₹60 per trade (one side)
                                                # Applied on entry AND exit separately


# =============================================================================
# DATA STRUCTURES
# =============================================================================

class WaveType(Enum):
    WAVE_1 = "W1"
    WAVE_2 = "W2"
    WAVE_3 = "W3"
    WAVE_4 = "W4"
    WAVE_5 = "W5"


class SignalType(Enum):
    WAVE3_ENTRY = "Wave3_Entry"
    WAVE5_ENTRY = "Wave5_Entry"


@dataclass
class SwingPoint:
    """A detected swing high or low."""
    index: int           # Bar index in dataframe
    date: pd.Timestamp
    price: float
    is_high: bool        # True = swing high, False = swing low


@dataclass
class WaveStructure:
    """A validated 5-wave impulse structure (or partial)."""
    wave1_start: SwingPoint
    wave1_end: SwingPoint       # = Wave 1 high
    wave2_end: SwingPoint       # = Wave 2 low (retracement)
    wave3_end: Optional[SwingPoint] = None
    wave4_end: Optional[SwingPoint] = None
    wave5_end: Optional[SwingPoint] = None
    is_valid: bool = False
    current_wave: WaveType = WaveType.WAVE_2


@dataclass
class TradeSignal:
    """A generated trade signal."""
    date: pd.Timestamp
    symbol: str
    signal_type: SignalType
    entry_price: float
    stop_loss: float
    target_price: float
    wave_structure: WaveStructure
    rsi: float
    volume_ratio: float
    confidence: float           # 0-100 score


@dataclass
class Position:
    """An active position."""
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    shares: int
    stop_loss: float
    target_price: float
    trailing_stop: float
    signal_type: SignalType
    highest_close: float = 0.0
    days_held: int = 0
    _pending_exit: Optional[str] = None  # Exit reason, executed next day at open


@dataclass
class TradeResult:
    """Completed trade for performance tracking."""
    symbol: str
    signal_type: SignalType
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    shares: int
    pnl: float
    pnl_pct: float
    exit_reason: str
    days_held: int


# =============================================================================
# TECHNICAL INDICATORS
# =============================================================================

def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI without external dependencies."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series,
                period: int = 14) -> pd.Series:
    """Compute Average True Range."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def compute_volume_ma(volume: pd.Series, period: int = 20) -> pd.Series:
    """Simple moving average of volume."""
    return volume.rolling(window=period).mean()


# =============================================================================
# SWING POINT DETECTION
# =============================================================================

def detect_swing_points(df: pd.DataFrame, config: StrategyConfig) -> list:
    """
    Detect swing highs and lows using a STRICTLY BACKWARD-LOOKING approach.
    No look-ahead bias.

    Logic: A swing high at bar i is confirmed at bar i + lookback, when we
    have seen 'lookback' subsequent bars all with lower highs. Similarly,
    a swing low at bar i is confirmed at bar i + lookback when all subsequent
    bars have higher lows.

    The swing point records the PRICE at bar i, but its index is set to
    i + lookback (the bar when you'd actually KNOW it's a swing in real time).
    This means signals derived from this swing can only fire AFTER the
    confirmation bar — eliminating look-ahead bias.

    Specifically:
    - A swing HIGH at bar i requires:
        - Bar i's High >= all Highs in [i - lookback, i]  (highest in past window)
        - Bar i's High >= all Highs in [i+1, i + lookback] (confirmed by future bars being lower)
        - Detected/usable only at bar i + lookback

    - A swing LOW at bar i requires:
        - Bar i's Low <= all Lows in [i - lookback, i]
        - Bar i's Low <= all Lows in [i+1, i + lookback]
        - Detected/usable only at bar i + lookback
    """
    swings = []
    n = len(df)
    lb = config.swing_lookback

    for i in range(lb, n - lb):
        # Left window: [i - lb, i] (inclusive) — bars we already knew
        # Right window: [i+1, i + lb] (inclusive) — bars we had to WAIT for
        left_high = df["High"].iloc[i - lb: i + 1]    # i-lb to i inclusive
        right_high = df["High"].iloc[i + 1: i + lb + 1]  # i+1 to i+lb inclusive
        left_low = df["Low"].iloc[i - lb: i + 1]
        right_low = df["Low"].iloc[i + 1: i + lb + 1]

        # Swing High: bar i is the highest in both left and right windows
        if df["High"].iloc[i] >= left_high.max() and df["High"].iloc[i] >= right_high.max():
            swings.append(SwingPoint(
                index=i + lb,          # Confirmation bar (when you'd KNOW in real time)
                date=df.index[i + lb], # Confirmation date
                price=df["High"].iloc[i],  # Actual swing price
                is_high=True
            ))

        # Swing Low: bar i is the lowest in both left and right windows
        if df["Low"].iloc[i] <= left_low.min() and df["Low"].iloc[i] <= right_low.min():
            swings.append(SwingPoint(
                index=i + lb,          # Confirmation bar
                date=df.index[i + lb], # Confirmation date
                price=df["Low"].iloc[i],   # Actual swing price
                is_high=False
            ))

    # Sort by index, remove duplicates at same bar
    swings.sort(key=lambda s: s.index)

    # Filter: ensure alternating highs and lows, keep the most extreme
    filtered = _alternate_swings(swings)

    # Filter: minimum swing size
    filtered = _filter_min_swing(filtered, config.min_swing_pct)

    return filtered


def _alternate_swings(swings: list) -> list:
    """Ensure swings alternate between high and low."""
    if not swings:
        return []

    result = [swings[0]]
    for s in swings[1:]:
        if s.is_high == result[-1].is_high:
            # Same type — keep the more extreme one
            if s.is_high and s.price > result[-1].price:
                result[-1] = s
            elif not s.is_high and s.price < result[-1].price:
                result[-1] = s
        else:
            result.append(s)
    return result


def _filter_min_swing(swings: list, min_pct: float) -> list:
    """Remove swings that are too small (% move)."""
    if len(swings) < 2:
        return swings

    filtered = [swings[0]]
    for i in range(1, len(swings)):
        prev = filtered[-1]
        curr = swings[i]
        pct_move = abs(curr.price - prev.price) / prev.price * 100
        if pct_move >= min_pct:
            filtered.append(curr)
        else:
            # Merge: if same direction as the one before, extend it
            if curr.is_high == prev.is_high:
                if curr.is_high and curr.price > prev.price:
                    filtered[-1] = curr
                elif not curr.is_high and curr.price < prev.price:
                    filtered[-1] = curr
    return filtered


# =============================================================================
# WAVE STRUCTURE IDENTIFICATION
# =============================================================================

def identify_wave_structures(swings: list, config: StrategyConfig) -> list:
    """
    Scan swing points for potential Elliott Wave impulse structures.
    NO LOOK-AHEAD: Each structure only contains pivots that are confirmed
    at the time the signal would fire.

    Wave 3 entry structures: Only need W1_start, W1_end, W2_end
      - These are marked current_wave = WAVE_3
      - Signal fires after W2_end is confirmed
      - We do NOT check if W3 actually formed (that's the trade!)

    Wave 5 entry structures: Need W1_start, W1_end, W2_end, W3_end, W4_end
      - These are marked current_wave = WAVE_5
      - Signal fires after W4_end is confirmed
      - We do NOT check if W5 actually formed

    For a long-only strategy in an uptrend.
    """
    structures = []

    for i in range(len(swings) - 2):
        # Wave 1 start must be a swing low
        if swings[i].is_high:
            continue

        w1_start = swings[i]

        # Find Wave 1 end (next swing high)
        j = i + 1
        if j >= len(swings) or not swings[j].is_high:
            continue
        w1_end = swings[j]

        # Wave 1 must be an upward move
        if w1_end.price <= w1_start.price:
            continue

        # Find Wave 2 end (next swing low)
        k = j + 1
        if k >= len(swings) or swings[k].is_high:
            continue
        w2_end = swings[k]

        # --- VALIDATE WAVE 2 ---
        wave1_range = w1_end.price - w1_start.price
        wave2_retrace = (w1_end.price - w2_end.price) / wave1_range

        # Rule: Wave 2 cannot retrace 100% of Wave 1
        if w2_end.price <= w1_start.price:
            continue

        # Fibonacci check for Wave 2
        if not (config.wave2_retrace_min <= wave2_retrace <= config.wave2_retrace_max):
            continue

        # ── WAVE 3 ENTRY STRUCTURE (W1 + W2 only) ──
        # At this point W2 is confirmed. We can look for Wave 3 entry.
        # We do NOT look ahead to see if W3 forms — that's the bet.
        ws_w3 = WaveStructure(
            wave1_start=w1_start,
            wave1_end=w1_end,
            wave2_end=w2_end,
            current_wave=WaveType.WAVE_3,
            is_valid=True
        )
        structures.append(ws_w3)

        # ── WAVE 5 ENTRY STRUCTURE (W1 + W2 + W3 + W4) ──
        # Only build this if W3 and W4 are ALREADY confirmed swing points
        # (their confirmation dates are in the past relative to W4_end)
        if k + 1 < len(swings) and swings[k + 1].is_high:
            w3_end = swings[k + 1]
            wave3_range = w3_end.price - w2_end.price
            wave3_extension = wave3_range / wave1_range

            # Validate Wave 3: must be > Wave 1 (not the shortest)
            if wave3_range > wave1_range and wave3_extension >= config.wave3_ext_min:

                if k + 2 < len(swings) and not swings[k + 2].is_high:
                    w4_end = swings[k + 2]
                    wave4_retrace = (w3_end.price - w4_end.price) / wave3_range

                    # Rule: Wave 4 cannot overlap Wave 1 territory
                    if w4_end.price > w1_end.price:
                        if config.wave4_retrace_min <= wave4_retrace <= config.wave4_retrace_max:
                            # W4 is confirmed — we can look for Wave 5 entry
                            ws_w5 = WaveStructure(
                                wave1_start=w1_start,
                                wave1_end=w1_end,
                                wave2_end=w2_end,
                                wave3_end=w3_end,
                                wave4_end=w4_end,
                                current_wave=WaveType.WAVE_5,
                                is_valid=True
                            )
                            structures.append(ws_w5)

    return structures


# =============================================================================
# SIGNAL GENERATION
# =============================================================================

def generate_signals(df: pd.DataFrame, structures: list,
                     config: StrategyConfig) -> list:
    """
    Generate trade signals from identified wave structures.
    """
    signals = []
    symbol = df.attrs.get("symbol", "UNKNOWN")

    # Pre-compute indicators
    rsi = compute_rsi(df["Close"], config.rsi_period)
    vol_ma = compute_volume_ma(df["Volume"], config.volume_ma_period)
    atr = compute_atr(df["High"], df["Low"], df["Close"], config.atr_period)

    for ws in structures:
        if not ws.is_valid:
            continue

        wave1_range = ws.wave1_end.price - ws.wave1_start.price

        # =====================================================================
        # SIGNAL A: Wave 3 Entry (buy after Wave 2 completes)
        # Only uses W1 + W2 — no knowledge of whether W3 actually forms
        # =====================================================================
        if ws.current_wave == WaveType.WAVE_3:
            entry_bar = ws.wave2_end.index + 1

            # Look for breakout confirmation in the bars after Wave 2
            for bar_idx in range(entry_bar, min(entry_bar + 15, len(df))):
                if bar_idx < config.breakout_bars:
                    continue

                # Breakout: price breaks above recent N-bar high
                recent_high = df["High"].iloc[bar_idx - config.breakout_bars: bar_idx].max()
                current_close = df["Close"].iloc[bar_idx]

                if current_close <= recent_high:
                    continue

                # Need next bar to exist for entry at open
                if bar_idx + 1 >= len(df):
                    continue

                # RSI check
                current_rsi = rsi.iloc[bar_idx]
                if pd.isna(current_rsi):
                    continue
                if not (config.rsi_wave3_entry_min <= current_rsi <= config.rsi_wave3_entry_max):
                    continue

                # Volume check
                current_vol = df["Volume"].iloc[bar_idx]
                avg_vol = vol_ma.iloc[bar_idx]
                if pd.isna(avg_vol) or avg_vol == 0:
                    continue
                vol_ratio = current_vol / avg_vol
                if vol_ratio < config.volume_expansion_factor:
                    continue

                # Entry at NEXT DAY'S OPEN (realistic execution)
                entry_price = df["Open"].iloc[bar_idx + 1]
                stop_loss = ws.wave2_end.price * 0.99  # Slightly below Wave 2 low
                target_price = (ws.wave2_end.price +
                                wave1_range * config.wave3_target_extension)

                # Confidence scoring (0-100)
                confidence = _compute_confidence(
                    wave2_retrace=(ws.wave1_end.price - ws.wave2_end.price) / wave1_range,
                    rsi_val=current_rsi,
                    vol_ratio=vol_ratio,
                    signal_type=SignalType.WAVE3_ENTRY
                )

                risk_pct = (entry_price - stop_loss) / entry_price * 100
                if risk_pct > 10:  # Skip if SL too far
                    continue

                signals.append(TradeSignal(
                    date=df.index[bar_idx + 1],  # Entry on next day
                    symbol=symbol,
                    signal_type=SignalType.WAVE3_ENTRY,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    target_price=target_price,
                    wave_structure=ws,
                    rsi=current_rsi,
                    volume_ratio=vol_ratio,
                    confidence=confidence
                ))
                break  # One signal per structure

        # =====================================================================
        # SIGNAL B: Wave 5 Entry (buy after Wave 4 completes)
        # =====================================================================
        if ws.current_wave == WaveType.WAVE_5 and ws.wave4_end is not None:
            entry_bar = ws.wave4_end.index + 1

            for bar_idx in range(entry_bar, min(entry_bar + 15, len(df))):
                if bar_idx < config.breakout_bars:
                    continue

                recent_high = df["High"].iloc[bar_idx - config.breakout_bars: bar_idx].max()
                current_close = df["Close"].iloc[bar_idx]

                if current_close <= recent_high:
                    continue

                # Need next bar to exist for entry at open
                if bar_idx + 1 >= len(df):
                    continue

                current_rsi = rsi.iloc[bar_idx]
                if pd.isna(current_rsi):
                    continue
                if not (config.rsi_wave5_entry_min <= current_rsi <= config.rsi_wave5_entry_max):
                    continue

                current_vol = df["Volume"].iloc[bar_idx]
                avg_vol = vol_ma.iloc[bar_idx]
                if pd.isna(avg_vol) or avg_vol == 0:
                    continue
                vol_ratio = current_vol / avg_vol
                if vol_ratio < config.volume_expansion_factor:
                    continue

                # Entry at NEXT DAY'S OPEN
                entry_price = df["Open"].iloc[bar_idx + 1]
                stop_loss = ws.wave4_end.price * 0.99
                target_price = (ws.wave4_end.price +
                                wave1_range * config.wave5_target_extension)

                confidence = _compute_confidence(
                    wave2_retrace=0,  # Not relevant for W5
                    rsi_val=current_rsi,
                    vol_ratio=vol_ratio,
                    signal_type=SignalType.WAVE5_ENTRY
                )

                risk_pct = (entry_price - stop_loss) / entry_price * 100
                if risk_pct > 10:
                    continue

                signals.append(TradeSignal(
                    date=df.index[bar_idx + 1],  # Entry on next day
                    symbol=symbol,
                    signal_type=SignalType.WAVE5_ENTRY,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    target_price=target_price,
                    wave_structure=ws,
                    rsi=current_rsi,
                    volume_ratio=vol_ratio,
                    confidence=confidence
                ))
                break

    return signals


def _compute_confidence(wave2_retrace: float, rsi_val: float,
                        vol_ratio: float, signal_type: SignalType) -> float:
    """
    Compute a confidence score (0-100) for the signal.
    Higher = better quality setup.
    """
    score = 50.0  # Base

    if signal_type == SignalType.WAVE3_ENTRY:
        # Ideal Wave 2 retracement is 0.618
        retrace_score = max(0, 20 - abs(wave2_retrace - 0.618) * 100)
        score += retrace_score

        # Wave 3 entries are inherently higher confidence
        score += 10

    # RSI in sweet spot (around 50 for recovery)
    rsi_score = max(0, 10 - abs(rsi_val - 52) * 0.5)
    score += rsi_score

    # Volume expansion
    if vol_ratio > 1.5:
        score += 10
    elif vol_ratio > 1.2:
        score += 5

    return min(100, max(0, score))


# =============================================================================
# BACKTESTING ENGINE
# =============================================================================

class Backtester:
    """
    Simple event-driven backtester for the Elliott Wave strategy.
    """

    def __init__(self, config: StrategyConfig):
        self.config = config
        self.capital = config.initial_capital
        self.positions: list[Position] = []
        self.trades: list[TradeResult] = []
        self.equity_curve: list[dict] = []
        self.max_concurrent_positions: int = 0
        self.max_capital_deployed: float = 0.0
        # Signal tracking
        self.total_signals_received: int = 0
        self.signals_taken: int = 0
        self.signals_skipped_max_positions: int = 0
        self.signals_skipped_already_in_stock: int = 0
        self.signals_skipped_no_cash: int = 0
        self.skipped_signals_log: list[dict] = []

    def run(self, stock_data: dict[str, pd.DataFrame],
            all_signals: dict[str, list[TradeSignal]]) -> pd.DataFrame:
        """
        Run backtest across all stocks.

        Args:
            stock_data: {symbol: DataFrame} with OHLCV data
            all_signals: {symbol: [TradeSignal]} generated signals

        Returns:
            DataFrame of trade results
        """
        # Collect all signals, sort by date
        signal_list = []
        for symbol, signals in all_signals.items():
            for sig in signals:
                signal_list.append(sig)
        signal_list.sort(key=lambda s: s.date)

        # Get all unique dates across all stocks
        all_dates = set()
        for df in stock_data.values():
            all_dates.update(df.index)
        all_dates = sorted(all_dates)

        # Signal lookup by date
        signal_by_date = {}
        for sig in signal_list:
            signal_by_date.setdefault(sig.date, []).append(sig)

        # Simulate day by day
        for date in all_dates:
            # 1. Update existing positions
            self._update_positions(date, stock_data)

            # 2. Check for new signals
            if date in signal_by_date:
                for signal in signal_by_date[date]:
                    self._process_signal(signal, date, stock_data)

            # 3. Record equity
            total_equity = self.capital + self._positions_value(date, stock_data)
            num_positions = len(self.positions)
            capital_deployed = self._positions_cost_basis()

            # Track peaks
            if num_positions > self.max_concurrent_positions:
                self.max_concurrent_positions = num_positions
            if capital_deployed > self.max_capital_deployed:
                self.max_capital_deployed = capital_deployed

            self.equity_curve.append({
                "date": date,
                "equity": total_equity,
                "cash": self.capital,
                "positions": num_positions,
                "capital_deployed": capital_deployed,
            })

        # Close any remaining positions at last date
        if all_dates:
            self._close_all_positions(all_dates[-1], stock_data, "End_of_Backtest")

        return self._build_results()

    def _process_signal(self, signal: TradeSignal, date: pd.Timestamp,
                        stock_data: dict):
        """Process a new trade signal."""
        self.total_signals_received += 1

        # Check if already in this stock
        if any(p.symbol == signal.symbol for p in self.positions):
            self.signals_skipped_already_in_stock += 1
            self.skipped_signals_log.append({
                "date": date, "symbol": signal.symbol,
                "signal": signal.signal_type.value,
                "reason": "Already_In_Stock",
                "entry_price": signal.entry_price,
            })
            return

        # Check max positions
        if len(self.positions) >= self.config.max_positions:
            self.signals_skipped_max_positions += 1
            self.skipped_signals_log.append({
                "date": date, "symbol": signal.symbol,
                "signal": signal.signal_type.value,
                "reason": "Max_Positions_Reached",
                "entry_price": signal.entry_price,
            })
            return

        # Position sizing: fixed % of total equity per trade
        # Total equity = cash + current value of open positions
        total_equity = self.capital + self._positions_value(date, stock_data)
        allocation = total_equity * (self.config.max_allocation_pct / 100)

        # Number of shares we can buy with this allocation
        shares = int(allocation / signal.entry_price)

        if shares <= 0:
            self.signals_skipped_no_cash += 1
            self.skipped_signals_log.append({
                "date": date, "symbol": signal.symbol,
                "signal": signal.signal_type.value,
                "reason": "Insufficient_Cash",
                "entry_price": signal.entry_price,
            })
            return

        cost = shares * signal.entry_price
        if cost > self.capital:
            # Not enough cash — buy what we can afford
            shares = int(self.capital / signal.entry_price)
            cost = shares * signal.entry_price

        if shares <= 0:
            self.signals_skipped_no_cash += 1
            self.skipped_signals_log.append({
                "date": date, "symbol": signal.symbol,
                "signal": signal.signal_type.value,
                "reason": "Insufficient_Cash",
                "entry_price": signal.entry_price,
            })
            return

        self.signals_taken += 1
        # Deduct entry cost + transaction cost
        self.capital -= cost
        self.capital -= self.config.transaction_cost_per_trade  # Brokerage etc. on entry
        self.positions.append(Position(
            symbol=signal.symbol,
            entry_date=date,
            entry_price=signal.entry_price,
            shares=shares,
            stop_loss=signal.stop_loss,
            target_price=signal.target_price,
            trailing_stop=signal.stop_loss,
            signal_type=signal.signal_type,
            highest_close=signal.entry_price,
            days_held=0
        ))

    def _update_positions(self, date: pd.Timestamp, stock_data: dict):
        """Update positions — check exits.
        
        Exit logic: When a stop loss, trailing stop, or target is breached
        intraday, we flag it but execute at NEXT DAY'S OPEN for realism.
        Exception: Time stop exits at today's close (you decide end of day).
        
        We store pending exits and process them on the next trading day.
        """
        # First, execute any pending exits from yesterday at today's open
        pending_to_execute = []
        for pos in self.positions:
            if hasattr(pos, '_pending_exit') and pos._pending_exit is not None:
                if pos.symbol in stock_data and date in stock_data[pos.symbol].index:
                    exit_price = stock_data[pos.symbol].loc[date, "Open"]
                    pending_to_execute.append(
                        (pos, exit_price, pos._pending_exit, date)
                    )

        for pos, exit_price, reason, exit_date in pending_to_execute:
            self._close_position(pos, exit_price, reason, exit_date)

        # Now check for new exit signals on today's bar
        for pos in self.positions:
            if pos.symbol not in stock_data:
                continue

            df = stock_data[pos.symbol]
            if date not in df.index:
                continue

            bar = df.loc[date]
            pos.days_held += 1

            # Update highest close for trailing stop
            if bar["Close"] > pos.highest_close:
                pos.highest_close = bar["Close"]

            # Compute trailing stop
            atr = compute_atr(
                df["High"], df["Low"], df["Close"], self.config.atr_period
            )
            if date in atr.index and not pd.isna(atr[date]):
                new_trailing = pos.highest_close - (
                    self.config.trailing_atr_multiplier * atr[date]
                )
                pos.trailing_stop = max(pos.trailing_stop, new_trailing)

            # --- EXIT CHECKS (priority order) ---
            # Flag for next-day-open execution

            # Stop Loss hit intraday
            if bar["Low"] <= pos.stop_loss:
                pos._pending_exit = "Stop_Loss"

            # Trailing Stop hit intraday
            elif bar["Low"] <= pos.trailing_stop:
                pos._pending_exit = "Trailing_Stop"

            # Target hit intraday
            elif bar["High"] >= pos.target_price:
                pos._pending_exit = "Target_Hit"

            # Time stop — execute at TODAY's close (deliberate EOD decision)
            elif pos.days_held >= self.config.time_stop_days:
                self._close_position(pos, bar["Close"], "Time_Stop", date)

    def _close_position(self, pos: Position, exit_price: float,
                        reason: str, date: pd.Timestamp):
        """Close a position and record the trade. Includes transaction costs."""
        # Gross PnL (before costs)
        gross_pnl = (exit_price - pos.entry_price) * pos.shares

        # Total transaction costs: entry cost + exit cost (₹60 each side = ₹120 round trip)
        total_tx_cost = self.config.transaction_cost_per_trade * 2

        # Net PnL after costs
        pnl = gross_pnl - total_tx_cost
        pnl_pct = pnl / (pos.entry_price * pos.shares) * 100

        # Add exit proceeds, minus exit transaction cost
        self.capital += pos.shares * exit_price
        self.capital -= self.config.transaction_cost_per_trade  # Exit cost

        self.trades.append(TradeResult(
            symbol=pos.symbol,
            signal_type=pos.signal_type,
            entry_date=pos.entry_date,
            exit_date=date,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            shares=pos.shares,
            pnl=pnl,
            pnl_pct=pnl_pct,
            exit_reason=reason,
            days_held=pos.days_held
        ))

        self.positions.remove(pos)

    def _close_all_positions(self, date, stock_data, reason):
        """Close all open positions."""
        for pos in list(self.positions):
            if pos.symbol in stock_data and date in stock_data[pos.symbol].index:
                price = stock_data[pos.symbol].loc[date, "Close"]
            else:
                price = pos.entry_price
            self._close_position(pos, price, reason, date)

    def _positions_value(self, date, stock_data):
        """Calculate total value of open positions."""
        value = 0
        for pos in self.positions:
            if pos.symbol in stock_data and date in stock_data[pos.symbol].index:
                price = stock_data[pos.symbol].loc[date, "Close"]
            else:
                price = pos.entry_price
            value += pos.shares * price
        return value

    def _positions_cost_basis(self):
        """Calculate total capital deployed (entry cost) of open positions."""
        return sum(pos.shares * pos.entry_price for pos in self.positions)

    def _build_results(self) -> pd.DataFrame:
        """Build results DataFrame."""
        if not self.trades:
            return pd.DataFrame()

        records = []
        for t in self.trades:
            records.append({
                "Symbol": t.symbol,
                "Signal": t.signal_type.value,
                "Entry_Date": t.entry_date,
                "Exit_Date": t.exit_date,
                "Entry_Price": round(t.entry_price, 2),
                "Exit_Price": round(t.exit_price, 2),
                "Shares": t.shares,
                "PnL": round(t.pnl, 2),
                "PnL_%": round(t.pnl_pct, 2),
                "Exit_Reason": t.exit_reason,
                "Days_Held": t.days_held,
            })

        return pd.DataFrame(records)


# =============================================================================
# PERFORMANCE ANALYTICS
# =============================================================================

def compute_performance_metrics(trades_df: pd.DataFrame,
                                equity_curve: list,
                                initial_capital: float,
                                max_concurrent: int = 0,
                                max_capital_deployed: float = 0.0) -> dict:
    """Compute comprehensive performance metrics."""
    if trades_df.empty:
        return {"error": "No trades generated"}

    total_trades = len(trades_df)
    winners = trades_df[trades_df["PnL"] > 0]
    losers = trades_df[trades_df["PnL"] <= 0]

    win_rate = len(winners) / total_trades * 100 if total_trades > 0 else 0
    avg_win = winners["PnL_%"].mean() if len(winners) > 0 else 0
    avg_loss = losers["PnL_%"].mean() if len(losers) > 0 else 0
    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

    # Profit factor
    gross_profit = winners["PnL"].sum() if len(winners) > 0 else 0
    gross_loss = abs(losers["PnL"].sum()) if len(losers) > 0 else 1
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Equity curve metrics
    if equity_curve:
        eq_df = pd.DataFrame(equity_curve)
        eq_df.set_index("date", inplace=True)
        final_equity = eq_df["equity"].iloc[-1]
        total_return = (final_equity - initial_capital) / initial_capital * 100
        total_pnl = final_equity - initial_capital

        # Max drawdown
        running_max = eq_df["equity"].cummax()
        drawdown = (eq_df["equity"] - running_max) / running_max * 100
        max_drawdown = drawdown.min()
        max_dd_date = drawdown.idxmin()

        # Time period
        days = (eq_df.index[-1] - eq_df.index[0]).days
        years = days / 365.25 if days > 0 else 1

        # CAGR on full capital (traditional)
        cagr = ((final_equity / initial_capital) ** (1 / years) - 1) * 100

        # ── Capital-adjusted CAGR ──
        # Method: Time-weighted return on capital that was actually at work.
        # We compute daily returns ONLY on days capital was deployed,
        # scaled to the deployed amount (not total equity).
        avg_capital_deployed = eq_df["capital_deployed"].mean()
        avg_utilization = (avg_capital_deployed / eq_df["equity"].mean() * 100
                          ) if eq_df["equity"].mean() > 0 else 0

        # ROC on deployed: total PnL / avg capital deployed / years
        # This answers: "Per rupee deployed on average, what did I earn annually?"
        if avg_capital_deployed > 0:
            roc_on_avg_deployed = (total_pnl / avg_capital_deployed / years) * 100
        else:
            roc_on_avg_deployed = 0

        # ROC on max deployed: if you sized the fund to max deployment
        if max_capital_deployed > 0:
            roc_on_max_deployed = (total_pnl / max_capital_deployed / years) * 100
        else:
            roc_on_max_deployed = 0

        # Peak concurrent positions over time
        peak_positions_date = eq_df["positions"].idxmax()
        avg_positions = eq_df["positions"].mean()

        # Calmar ratio
        calmar = cagr / abs(max_drawdown) if max_drawdown != 0 else float("inf")

        # Sharpe-like ratio (annualized daily equity returns)
        eq_df["daily_ret"] = eq_df["equity"].pct_change()
        avg_daily_ret = eq_df["daily_ret"].mean()
        std_daily_ret = eq_df["daily_ret"].std()
        sharpe = (avg_daily_ret / std_daily_ret * np.sqrt(252)
                  ) if std_daily_ret > 0 else 0

        # Max consecutive wins/losses
        trade_results = (trades_df["PnL"] > 0).astype(int)
        max_consec_wins = _max_consecutive(trade_results, 1)
        max_consec_losses = _max_consecutive(trade_results, 0)

    else:
        total_return = max_drawdown = cagr = calmar = sharpe = 0
        roc_on_avg_deployed = roc_on_max_deployed = 0
        final_equity = initial_capital
        peak_positions_date = max_dd_date = None
        avg_positions = avg_capital_deployed = avg_utilization = 0
        max_consec_wins = max_consec_losses = 0
        years = 0

    # By signal type
    w3_trades = trades_df[trades_df["Signal"] == "Wave3_Entry"]
    w5_trades = trades_df[trades_df["Signal"] == "Wave5_Entry"]

    metrics = {
        "─── TRADE STATISTICS ───": "─" * 30,
        "Total Trades": total_trades,
        "Win Rate (%)": round(win_rate, 1),
        "Avg Win (%)": round(avg_win, 2),
        "Avg Loss (%)": round(avg_loss, 2),
        "Expectancy per Trade (%)": round(expectancy, 2),
        "Profit Factor": round(profit_factor, 2),
        "Max Consecutive Wins": max_consec_wins,
        "Max Consecutive Losses": max_consec_losses,
        "Avg Days Held": round(trades_df["Days_Held"].mean(), 1),
        "─── RETURNS ───": "─" * 30,
        "Total PnL (₹)": f"{total_pnl:,.2f}" if equity_curve else "0",
        "Total Return (%)": round(total_return, 2),
        "CAGR on Full Capital (%)": round(cagr, 2),
        "Sharpe Ratio (approx)": round(sharpe, 2),
        "Max Drawdown (%)": round(max_drawdown, 2),
        "Max DD Date": str(max_dd_date)[:10] if max_dd_date else "N/A",
        "Calmar Ratio": round(calmar, 2),
        "Initial Capital (₹)": f"{initial_capital:,.2f}",
        "Final Equity (₹)": f"{final_equity:,.2f}",
        "─── CAPITAL DEPLOYMENT ───": "─" * 30,
        "Max Concurrent Positions": max_concurrent,
        "Peak Positions Date": str(peak_positions_date)[:10] if peak_positions_date else "N/A",
        "Avg Open Positions": round(avg_positions, 1),
        "Max Capital Deployed (₹)": f"{max_capital_deployed:,.2f}",
        "Avg Capital Deployed (₹)": f"{avg_capital_deployed:,.2f}",
        "Avg Capital Utilization (%)": round(avg_utilization, 1),
        "─── CAGR ADJUSTED FOR DEPLOYMENT ───": "─" * 30,
        "CAGR if fund = Max Deployed (%)": round(roc_on_max_deployed, 2),
        "CAGR if fund = Avg Deployed (%)": round(roc_on_avg_deployed, 2),
        "─── WAVE BREAKDOWN ───": "─" * 30,
        "Wave 3 Trades": len(w3_trades),
        "Wave 3 Win Rate (%)": round(
            len(w3_trades[w3_trades["PnL"] > 0]) / len(w3_trades) * 100, 1
        ) if len(w3_trades) > 0 else 0,
        "Wave 5 Trades": len(w5_trades),
        "Wave 5 Win Rate (%)": round(
            len(w5_trades[w5_trades["PnL"] > 0]) / len(w5_trades) * 100, 1
        ) if len(w5_trades) > 0 else 0,
        "Exit Breakdown": trades_df["Exit_Reason"].value_counts().to_dict(),
    }

    return metrics


def _max_consecutive(series, value):
    """Count max consecutive occurrences of a value in a series."""
    max_count = 0
    count = 0
    for v in series:
        if v == value:
            count += 1
            max_count = max(max_count, count)
        else:
            count = 0
    return max_count


# =============================================================================
# DATA LOADING (NIFTY 200 UNIVERSE)
# =============================================================================

# Full Nifty 50 constituent list (as of April 2026)
# Yahoo Finance uses .NS suffix for NSE-listed stocks
NIFTY_50_STOCKS = [
    "RELIANCE.NS", "HDFCBANK.NS", "BHARTIARTL.NS", "SBIN.NS", "ICICIBANK.NS",
    "TCS.NS", "BAJFINANCE.NS", "LT.NS", "INFY.NS", "HINDUNILVR.NS",
    "MARUTI.NS", "AXISBANK.NS", "ITC.NS", "KOTAKBANK.NS", "TATAMOTORS.NS",
    "SUNPHARMA.NS", "ADANIENT.NS", "TITAN.NS", "ONGC.NS", "NTPC.NS",
    "HCLTECH.NS", "BAJAJ-AUTO.NS", "POWERGRID.NS", "M&M.NS", "ULTRACEMCO.NS",
    "ASIANPAINT.NS", "WIPRO.NS", "JSWSTEEL.NS", "TATASTEEL.NS", "NESTLEIND.NS",
    "GRASIM.NS", "INDUSINDBK.NS", "TECHM.NS", "DRREDDY.NS", "CIPLA.NS",
    "APOLLOHOSP.NS", "EICHERMOT.NS", "HEROMOTOCO.NS", "COALINDIA.NS",
    "DIVISLAB.NS", "BPCL.NS", "BRITANNIA.NS", "SHRIRAMFIN.NS", "TRENT.NS",
    "BAJAJFINSV.NS", "SBILIFE.NS", "HDFCLIFE.NS", "ETERNAL.NS",
    "ADANIPORTS.NS", "MAXHEALTH.NS",
]


def load_nifty200_from_csv(csv_path: str = "ind_nifty200list.csv") -> list:
    """
    Load Nifty 200 symbols from the official NSE CSV file.
    Reads the 'Symbol' column and appends '.NS' for Yahoo Finance.

    Args:
        csv_path: Path to ind_nifty200list.csv

    Returns:
        List of Yahoo Finance symbols (e.g., ['RELIANCE.NS', 'TCS.NS', ...])
    """
    import os
    if not os.path.exists(csv_path):
        print(f"  CSV not found at: {csv_path}")
        print(f"  Falling back to Nifty 50 list...")
        return NIFTY_50_STOCKS

    try:
        csv_df = pd.read_csv(csv_path)
        symbols = [f"{sym.strip()}.NS" for sym in csv_df["Symbol"].dropna()]
        print(f"  Loaded {len(symbols)} symbols from {csv_path}")
        return symbols
    except Exception as e:
        print(f"  Error reading CSV: {e}")
        print(f"  Falling back to Nifty 50 list...")
        return NIFTY_50_STOCKS


# Default: try to load from CSV, else use Nifty 50
NIFTY_200_SAMPLE = NIFTY_50_STOCKS  # Will be overridden by CSV in runner


def load_stock_data(symbols: list, start: str = "2020-01-01",
                    end: str = "2025-12-31",
                    cache_dir: str = ".cache") -> dict:
    """
    Load OHLCV data for a list of NSE symbols.
    Uses local .cache directory to avoid re-downloading.

    Cache structure: .cache/RELIANCE.NS_2016-01-01_2026-04-12.csv

    Args:
        symbols: List of Yahoo Finance symbols
        start: Start date string
        end: End date string
        cache_dir: Directory to cache downloaded data

    Returns: {symbol: DataFrame}
    """
    import os
    os.makedirs(cache_dir, exist_ok=True)

    try:
        import yfinance as yf
        has_yfinance = True
    except ImportError:
        has_yfinance = False
        print("WARNING: yfinance not installed. Will only use cached data.")

    stock_data = {}
    cached_count = 0
    downloaded_count = 0
    failed_count = 0

    print(f"\nLoading data for {len(symbols)} stocks (cache: {cache_dir}/)...")

    for sym in symbols:
        # Cache filename: symbol_start_end.csv
        safe_sym = sym.replace("&", "_AND_")  # Handle M&M.NS etc.
        cache_file = os.path.join(cache_dir, f"{safe_sym}_{start}_{end}.csv")

        # Try loading from cache first
        if os.path.exists(cache_file):
            try:
                df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
                if len(df) > 100:
                    df.attrs["symbol"] = sym
                    stock_data[sym] = df
                    cached_count += 1
                    continue
            except Exception:
                pass  # Cache corrupted, re-download

        # Not in cache — download
        if not has_yfinance:
            failed_count += 1
            continue

        try:
            df = yf.download(sym, start=start, end=end, progress=False)
            if df is not None and len(df) > 100:
                # Handle multi-level columns from yfinance
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

                # Save to cache
                df.to_csv(cache_file)

                df.attrs["symbol"] = sym
                stock_data[sym] = df
                downloaded_count += 1
                print(f"  ↓ {sym}: {len(df)} bars (downloaded & cached)")
            else:
                failed_count += 1
                print(f"  ✗ {sym}: insufficient data")
        except Exception as e:
            failed_count += 1
            print(f"  ✗ {sym}: {e}")

    print(f"\n  Loaded {len(stock_data)} stocks: "
          f"{cached_count} from cache, {downloaded_count} downloaded, "
          f"{failed_count} failed.\n")
    return stock_data


def _generate_synthetic_data(symbols: list, start: str, end: str) -> dict:
    """Generate synthetic OHLCV data for testing when yfinance unavailable."""
    dates = pd.bdate_range(start=start, end=end)
    stock_data = {}

    np.random.seed(42)

    for sym in symbols[:15]:  # Limit for speed
        n = len(dates)
        # Random walk with trend + mean reversion
        base_price = np.random.uniform(200, 3000)
        returns = np.random.normal(0.0005, 0.02, n)

        # Inject wave-like patterns
        cycle = np.sin(np.linspace(0, 8 * np.pi, n)) * 0.005
        returns += cycle

        prices = base_price * np.cumprod(1 + returns)

        df = pd.DataFrame({
            "Open": prices * (1 + np.random.uniform(-0.005, 0.005, n)),
            "High": prices * (1 + np.abs(np.random.normal(0, 0.015, n))),
            "Low": prices * (1 - np.abs(np.random.normal(0, 0.015, n))),
            "Close": prices,
            "Volume": np.random.lognormal(15, 1, n).astype(int),
        }, index=dates[:n])

        df.attrs["symbol"] = sym
        stock_data[sym] = df

    return stock_data


# =============================================================================
# MAIN PIPELINE
# =============================================================================

def run_strategy(symbols: list = None, start: str = "2021-01-01",
                 end: str = "2025-12-31", config: StrategyConfig = None):
    """
    Full pipeline: Load data → Detect waves → Generate signals → Backtest.
    """
    if config is None:
        config = StrategyConfig()
    if symbols is None:
        symbols = NIFTY_200_SAMPLE

    # 1. Load data
    print("=" * 70)
    print("ELLIOTT WAVE LONG-ONLY STRATEGY — NIFTY 200")
    print("=" * 70)
    stock_data = load_stock_data(symbols, start, end)

    if not stock_data:
        print("No data loaded. Exiting.")
        return

    # 2. Detect waves and generate signals for each stock
    all_signals = {}
    total_structures = 0
    total_signals = 0

    print("Scanning for Elliott Wave patterns...")
    for sym, df in stock_data.items():
        df.attrs["symbol"] = sym

        # Detect swing points
        swings = detect_swing_points(df, config)

        # Identify wave structures
        structures = identify_wave_structures(swings, config)
        total_structures += len(structures)

        # Generate signals
        signals = generate_signals(df, structures, config)
        total_signals += len(signals)

        if signals:
            all_signals[sym] = signals
            print(f"  {sym}: {len(structures)} structures, "
                  f"{len(signals)} signals")

    print(f"\nTotal: {total_structures} wave structures, "
          f"{total_signals} signals across {len(stock_data)} stocks\n")

    # 3. Backtest
    print("Running backtest...")
    bt = Backtester(config)
    trades_df = bt.run(stock_data, all_signals)

    # 4. Performance
    metrics = compute_performance_metrics(
        trades_df, bt.equity_curve, config.initial_capital,
        max_concurrent=bt.max_concurrent_positions,
        max_capital_deployed=bt.max_capital_deployed
    )

    print("\n" + "=" * 70)
    print("BACKTEST RESULTS")
    print("=" * 70)

    if "error" in metrics:
        print(f"\n{metrics['error']}")
        print("Try adjusting parameters (wider Fibonacci ranges, smaller "
              "swing lookback, etc.)")
        return trades_df, metrics, bt.equity_curve

    for key, val in metrics.items():
        if key != "Exit Breakdown":
            print(f"  {key:.<35} {val}")

    print(f"\n  Exit Breakdown:")
    for reason, count in metrics.get("Exit Breakdown", {}).items():
        print(f"    {reason:.<30} {count}")

    if not trades_df.empty:
        print(f"\n{'=' * 70}")
        print("SAMPLE TRADES (first 10)")
        print("=" * 70)
        print(trades_df.head(10).to_string(index=False))

    return trades_df, metrics, bt.equity_curve


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    # You can customize the config here
    config = StrategyConfig(
        swing_lookback=10,
        min_swing_pct=3.0,
        wave2_retrace_min=0.382,
        wave2_retrace_max=0.786,
        rsi_period=14,
        trailing_atr_multiplier=2.5,
        initial_capital=1_000_000,
        max_positions=10,
    )

    trades, metrics, equity = run_strategy(config=config)
