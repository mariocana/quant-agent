"""
Trading Strategies — Signal generation for prop trading.

Strategies:
  1. EMA Cross — Trend-following with EMA 9/21 cross + 200 EMA filter
  2. RSI Mean Reversion — Counter-trend entries at extremes
  3. Breakout — Range breakout with ATR confirmation

All strategies output standardized Signal dicts.
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

from config import STRATEGY

logger = logging.getLogger(__name__)


def ema(series: pd.Series, period: int) -> pd.Series:
    """Calculate Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Calculate Simple Moving Average."""
    return series.rolling(window=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def bollinger_bands(series: pd.Series, period: int = 40, std_dev: float = 2.0) -> dict:
    """Calculate Bollinger Bands."""
    mid = sma(series, period)
    std = series.rolling(window=period).std()
    return {
        "upper": mid + std_dev * std,
        "mid": mid,
        "lower": mid - std_dev * std,
    }


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average Directional Index."""
    high = df["high"]
    low = df["low"]
    close = df["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr_val = tr.ewm(alpha=1 / period, min_periods=period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr_val)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr_val)

    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx_val = dx.ewm(alpha=1 / period, min_periods=period).mean()

    return adx_val


class Signal:
    """Standardized trade signal."""

    def __init__(
        self,
        symbol: str,
        direction: str,     # "BUY" or "SELL"
        entry: float,
        sl: float,
        tp: float,
        strategy: str,
        reason: str,
        strength: float = 1.0,  # 0-1 confidence
    ):
        self.symbol = symbol
        self.direction = direction
        self.entry = entry
        self.sl = sl
        self.tp = tp
        self.strategy = strategy
        self.reason = reason
        self.strength = strength

        # Compute risk/reward
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        self.rr_ratio = round(reward / risk, 2) if risk > 0 else 0

        # SL distance in points (for lot calculation)
        self.sl_distance_points = abs(entry - sl)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "entry": self.entry,
            "sl": self.sl,
            "tp": self.tp,
            "strategy": self.strategy,
            "reason": self.reason,
            "strength": self.strength,
            "rr_ratio": self.rr_ratio,
        }

    def __repr__(self):
        return (
            f"Signal({self.symbol} {self.direction} @ {self.entry:.5f} | "
            f"SL={self.sl:.5f} TP={self.tp:.5f} | RR={self.rr_ratio} | "
            f"{self.strategy})"
        )


class StrategyEngine:
    """Generates trade signals using configured strategies."""

    def __init__(self, mt5_handler):
        self.mt5 = mt5_handler
        self.active_strategy = STRATEGY["active_strategy"]
        self._resolved_symbols: Optional[list[str]] = None
        self._resolved_scalp_symbols: Optional[list[str]] = None

    def _resolve_symbols(self, key: str = "symbols", filters_key: str = "symbol_filters") -> list[str]:
        """Resolve symbol list — either manual list or AUTO from broker."""
        symbols_cfg = STRATEGY.get(key, [])
        if isinstance(symbols_cfg, list):
            return symbols_cfg
        if symbols_cfg == "AUTO":
            filters = STRATEGY.get(filters_key, {})
            symbols = self.mt5.get_symbol_names(filters)
            logger.info(f"AUTO symbols resolved: {len(symbols)} symbols from broker")
            return symbols
        return []

    def _get_symbols(self) -> list[str]:
        """Get symbols for the active strategy."""
        if self.active_strategy == "BB_RSI_SCALP":
            if self._resolved_scalp_symbols is None:
                bb_cfg = STRATEGY.get("bb_rsi", {})
                syms = bb_cfg.get("symbols", "AUTO")
                if isinstance(syms, list):
                    self._resolved_scalp_symbols = syms
                elif syms == "AUTO":
                    filters = bb_cfg.get("symbol_filters", STRATEGY.get("symbol_filters", {}))
                    self._resolved_scalp_symbols = self.mt5.get_symbol_names(filters)
                    logger.info(f"BB_RSI_SCALP AUTO symbols: {len(self._resolved_scalp_symbols)}")
                else:
                    self._resolved_scalp_symbols = self._resolve_symbols()
            return self._resolved_scalp_symbols
        else:
            if self._resolved_symbols is None:
                self._resolved_symbols = self._resolve_symbols()
            return self._resolved_symbols

    def scan_for_signals(self) -> list[Signal]:
        """Scan all configured symbols for entry signals."""
        signals = []
        symbols = self._get_symbols()

        for symbol in symbols:
            signal = self._analyze_symbol(symbol)
            if signal:
                signals.append(signal)

        return signals

    def _analyze_symbol(self, symbol: str) -> Optional[Signal]:
        """Run active strategy on a single symbol."""
        strategy_map = {
            "EMA_CROSS": self._strategy_ema_cross,
            "RSI_MEAN_REVERSION": self._strategy_rsi_reversion,
            "BREAKOUT": self._strategy_breakout,
            "BB_RSI_SCALP": self._strategy_bb_rsi_scalp,
        }

        func = strategy_map.get(self.active_strategy)
        if not func:
            logger.error(f"Unknown strategy: {self.active_strategy}")
            return None

        return func(symbol)

    # ── Strategy 1: EMA Cross ───────────────────────────────

    def _strategy_ema_cross(self, symbol: str) -> Optional[Signal]:
        """
        EMA 9/21 crossover with 200 EMA trend filter.

        BUY:  EMA9 crosses above EMA21 + price above EMA200
        SELL: EMA9 crosses below EMA21 + price below EMA200

        SL: ATR * multiplier
        TP: ATR * multiplier (targeting 2:1 RR)
        """
        # Get entry timeframe candles
        df = self.mt5.get_candles(symbol, STRATEGY["entry_timeframe"], 300)
        if df is None or len(df) < 200:
            return None

        # Calculate indicators
        df["ema_fast"] = ema(df["close"], STRATEGY["ema_fast"])
        df["ema_slow"] = ema(df["close"], STRATEGY["ema_slow"])
        df["ema_trend"] = ema(df["close"], STRATEGY["ema_trend"])
        df["atr"] = atr(df, STRATEGY["atr_period"])

        # Last two candles for cross detection
        curr = df.iloc[-1]
        prev = df.iloc[-2]

        atr_val = curr["atr"]
        if pd.isna(atr_val) or atr_val <= 0:
            return None

        price = curr["close"]
        sl_distance = atr_val * STRATEGY["sl_atr_multiplier"]
        tp_distance = atr_val * STRATEGY["tp_atr_multiplier"]

        # ── BUY signal ──
        if (
            prev["ema_fast"] <= prev["ema_slow"]      # Was below
            and curr["ema_fast"] > curr["ema_slow"]    # Now above (cross)
            and price > curr["ema_trend"]               # Above 200 EMA
        ):
            # Confirm with higher timeframe trend
            if self._htf_trend_confirms(symbol, "BUY"):
                return Signal(
                    symbol=symbol,
                    direction="BUY",
                    entry=price,
                    sl=round(price - sl_distance, 5),
                    tp=round(price + tp_distance, 5),
                    strategy="EMA_CROSS",
                    reason=(
                        f"EMA{STRATEGY['ema_fast']}/{STRATEGY['ema_slow']} bullish cross. "
                        f"Price above EMA{STRATEGY['ema_trend']}. HTF trend confirmed."
                    ),
                    strength=0.8,
                )

        # ── SELL signal ──
        if (
            prev["ema_fast"] >= prev["ema_slow"]
            and curr["ema_fast"] < curr["ema_slow"]
            and price < curr["ema_trend"]
        ):
            if self._htf_trend_confirms(symbol, "SELL"):
                return Signal(
                    symbol=symbol,
                    direction="SELL",
                    entry=price,
                    sl=round(price + sl_distance, 5),
                    tp=round(price - tp_distance, 5),
                    strategy="EMA_CROSS",
                    reason=(
                        f"EMA{STRATEGY['ema_fast']}/{STRATEGY['ema_slow']} bearish cross. "
                        f"Price below EMA{STRATEGY['ema_trend']}. HTF trend confirmed."
                    ),
                    strength=0.8,
                )

        return None

    # ── Strategy 2: RSI Mean Reversion ──────────────────────

    def _strategy_rsi_reversion(self, symbol: str) -> Optional[Signal]:
        """
        RSI extremes with trend filter.

        BUY:  RSI < 30 (oversold) + price above EMA200 (pullback in uptrend)
        SELL: RSI > 70 (overbought) + price below EMA200 (bounce in downtrend)
        """
        df = self.mt5.get_candles(symbol, STRATEGY["entry_timeframe"], 300)
        if df is None or len(df) < 200:
            return None

        df["rsi"] = rsi(df["close"], STRATEGY["rsi_period"])
        df["ema_trend"] = ema(df["close"], STRATEGY["ema_trend"])
        df["atr"] = atr(df, STRATEGY["atr_period"])

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        atr_val = curr["atr"]
        price = curr["close"]

        if pd.isna(atr_val) or atr_val <= 0:
            return None

        sl_distance = atr_val * STRATEGY["sl_atr_multiplier"]
        tp_distance = atr_val * STRATEGY["tp_atr_multiplier"]

        # BUY: RSI crossing back above oversold
        if (
            prev["rsi"] < STRATEGY["rsi_oversold"]
            and curr["rsi"] >= STRATEGY["rsi_oversold"]
            and price > curr["ema_trend"]
        ):
            return Signal(
                symbol=symbol,
                direction="BUY",
                entry=price,
                sl=round(price - sl_distance, 5),
                tp=round(price + tp_distance, 5),
                strategy="RSI_MEAN_REVERSION",
                reason=f"RSI({STRATEGY['rsi_period']}) exiting oversold ({prev['rsi']:.1f}→{curr['rsi']:.1f}). Uptrend pullback.",
                strength=0.7,
            )

        # SELL: RSI crossing back below overbought
        if (
            prev["rsi"] > STRATEGY["rsi_overbought"]
            and curr["rsi"] <= STRATEGY["rsi_overbought"]
            and price < curr["ema_trend"]
        ):
            return Signal(
                symbol=symbol,
                direction="SELL",
                entry=price,
                sl=round(price + sl_distance, 5),
                tp=round(price - tp_distance, 5),
                strategy="RSI_MEAN_REVERSION",
                reason=f"RSI({STRATEGY['rsi_period']}) exiting overbought ({prev['rsi']:.1f}→{curr['rsi']:.1f}). Downtrend bounce.",
                strength=0.7,
            )

        return None

    # ── Strategy 3: Breakout ────────────────────────────────

    def _strategy_breakout(self, symbol: str) -> Optional[Signal]:
        """
        Range breakout with ATR confirmation.

        BUY:  Close above N-bar high + current bar range > 1.5x ATR
        SELL: Close below N-bar low + current bar range > 1.5x ATR
        """
        lookback = STRATEGY["breakout_lookback"]
        df = self.mt5.get_candles(symbol, STRATEGY["entry_timeframe"], 300)
        if df is None or len(df) < lookback + 50:
            return None

        df["atr"] = atr(df, STRATEGY["atr_period"])
        df["highest"] = df["high"].rolling(window=lookback).max()
        df["lowest"] = df["low"].rolling(window=lookback).min()

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        atr_val = curr["atr"]
        price = curr["close"]

        if pd.isna(atr_val) or atr_val <= 0:
            return None

        bar_range = curr["high"] - curr["low"]
        atr_threshold = atr_val * STRATEGY["breakout_atr_multiplier"]

        sl_distance = atr_val * STRATEGY["sl_atr_multiplier"]
        tp_distance = atr_val * STRATEGY["tp_atr_multiplier"]

        # Bullish breakout
        prev_high = df["high"].iloc[-lookback - 1:-1].max()
        if price > prev_high and bar_range > atr_threshold:
            return Signal(
                symbol=symbol,
                direction="BUY",
                entry=price,
                sl=round(price - sl_distance, 5),
                tp=round(price + tp_distance, 5),
                strategy="BREAKOUT",
                reason=f"Bullish breakout above {lookback}-bar high ({prev_high:.5f}). Bar range {bar_range:.5f} > ATR threshold {atr_threshold:.5f}.",
                strength=0.75,
            )

        # Bearish breakout
        prev_low = df["low"].iloc[-lookback - 1:-1].min()
        if price < prev_low and bar_range > atr_threshold:
            return Signal(
                symbol=symbol,
                direction="SELL",
                entry=price,
                sl=round(price + sl_distance, 5),
                tp=round(price - tp_distance, 5),
                strategy="BREAKOUT",
                reason=f"Bearish breakout below {lookback}-bar low ({prev_low:.5f}). Bar range {bar_range:.5f} > ATR threshold {atr_threshold:.5f}.",
                strength=0.75,
            )

        return None

    # ── Strategy 4: BB + RSI Scalping (Challenge Pass) ──────

    def _strategy_bb_rsi_scalp(self, symbol: str) -> Optional[Signal]:
        """
        Bollinger Bands (40) + RSI (5) Mean Reversion Scalp.
        Optimized for high win rate, high frequency, ~1:1 RR.
        Designed for FTMO Challenge pass.

        Filtro: ADX < 25 (solo mercati in range)

        LONG:
          - ADX < 25
          - Price tocca/perfora lower BB
          - RSI(5) entra in oversold (< 30) poi esce (cross up 30)
          - SL = swing low recente - buffer
          - TP = upper Bollinger Band

        SHORT:
          - ADX < 25
          - Price tocca/perfora upper BB
          - RSI(5) entra in overbought (> 70) poi esce (cross down 70)
          - SL = swing high recente + buffer
          - TP = lower Bollinger Band
        """
        cfg = STRATEGY.get("bb_rsi", {})
        tf = cfg.get("entry_timeframe", "M5")

        df = self.mt5.get_candles(symbol, tf, 500)
        if df is None or len(df) < cfg.get("bb_period", 40) + 50:
            return None

        # ── Calculate indicators ──
        bb = bollinger_bands(df["close"], cfg.get("bb_period", 40), cfg.get("bb_std_dev", 2.0))
        df["bb_upper"] = bb["upper"]
        df["bb_mid"] = bb["mid"]
        df["bb_lower"] = bb["lower"]
        df["rsi"] = rsi(df["close"], cfg.get("rsi_period", 5))
        df["adx"] = adx(df, cfg.get("adx_period", 14))

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        # ── Filter: only range-bound markets ──
        adx_val = curr["adx"]
        if pd.isna(adx_val) or adx_val > cfg.get("adx_max", 25):
            return None

        price = curr["close"]
        low = curr["low"]
        high = curr["high"]
        bb_lower = curr["bb_lower"]
        bb_upper = curr["bb_upper"]
        rsi_curr = curr["rsi"]
        rsi_prev = prev["rsi"]

        if pd.isna(rsi_curr) or pd.isna(bb_lower):
            return None

        swing_lb = cfg.get("swing_lookback", 10)
        sym_info = self.mt5.get_symbol_info(symbol)
        point = sym_info["point"] if sym_info else 0.0001
        sl_buffer = cfg.get("sl_buffer_pips", 3) * point * 10  # pips → price

        rsi_os = cfg.get("rsi_oversold", 30)
        rsi_ob = cfg.get("rsi_overbought", 70)

        # ── LONG signal ──
        # Price touched/broke lower BB + RSI exiting oversold
        if low <= bb_lower and rsi_prev < rsi_os and rsi_curr >= rsi_os:
            # SL = lowest low of last N bars - buffer
            swing_low = df["low"].iloc[-swing_lb:].min()
            sl = round(swing_low - sl_buffer, 5)
            tp = round(bb_upper, 5)

            # Validate: min RR 0.8 to filter garbage
            risk = abs(price - sl)
            reward = abs(tp - price)
            if risk <= 0 or (reward / risk) < 0.8:
                return None

            return Signal(
                symbol=symbol,
                direction="BUY",
                entry=price,
                sl=sl,
                tp=tp,
                strategy="BB_RSI_SCALP",
                reason=(
                    f"BB({cfg.get('bb_period', 40)}) lower touch + "
                    f"RSI({cfg.get('rsi_period', 5)}) exit oversold "
                    f"({rsi_prev:.1f}→{rsi_curr:.1f}). "
                    f"ADX={adx_val:.1f} (ranging). "
                    f"TP=upper BB @ {bb_upper:.5f}"
                ),
                strength=0.85,
            )

        # ── SHORT signal ──
        # Price touched/broke upper BB + RSI exiting overbought
        if high >= bb_upper and rsi_prev > rsi_ob and rsi_curr <= rsi_ob:
            # SL = highest high of last N bars + buffer
            swing_high = df["high"].iloc[-swing_lb:].max()
            sl = round(swing_high + sl_buffer, 5)
            tp = round(bb_lower, 5)

            risk = abs(sl - price)
            reward = abs(price - tp)
            if risk <= 0 or (reward / risk) < 0.8:
                return None

            return Signal(
                symbol=symbol,
                direction="SELL",
                entry=price,
                sl=sl,
                tp=tp,
                strategy="BB_RSI_SCALP",
                reason=(
                    f"BB({cfg.get('bb_period', 40)}) upper touch + "
                    f"RSI({cfg.get('rsi_period', 5)}) exit overbought "
                    f"({rsi_prev:.1f}→{rsi_curr:.1f}). "
                    f"ADX={adx_val:.1f} (ranging). "
                    f"TP=lower BB @ {bb_lower:.5f}"
                ),
                strength=0.85,
            )

        return None

    # ── Higher Timeframe Filter ─────────────────────────────

    def _htf_trend_confirms(self, symbol: str, direction: str) -> bool:
        """
        Check if higher timeframe trend aligns with the signal.
        Uses EMA 9/21 on H4 timeframe.
        """
        df = self.mt5.get_candles(symbol, STRATEGY["htf_timeframe"], 100)
        if df is None or len(df) < 30:
            return True  # If no data, don't block

        df["ema_fast"] = ema(df["close"], STRATEGY["ema_fast"])
        df["ema_slow"] = ema(df["close"], STRATEGY["ema_slow"])

        curr = df.iloc[-1]

        if direction == "BUY":
            return curr["ema_fast"] > curr["ema_slow"]
        else:
            return curr["ema_fast"] < curr["ema_slow"]
