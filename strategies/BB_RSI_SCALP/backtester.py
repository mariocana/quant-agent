"""
Prop Bot — Backtesting Engine (MT5 Historical Data)

Testa le stesse strategie del bot su dati storici MT5,
simulando regole prop, risk management, e generando report completi.

Usage:
    python backtester.py                              # Tutte le strategie, tutti i simboli, 6 mesi
    python backtester.py --strategy EMA_CROSS         # Singola strategia
    python backtester.py --symbol EURUSD              # Singolo simbolo
    python backtester.py --months 12                  # 12 mesi di lookback
    python backtester.py --timeframe M15              # Timeframe specifico
    python backtester.py --export                     # Esporta trade in CSV
    python backtester.py --no-prop                    # Ignora regole prop (test puro)
"""

import argparse
import logging
import sys
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from config import (
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH,
    ACTIVE_PROP, CURRENT_PHASE,
    FTMO_RULES, FUNDEDNEXT_RULES,
    RISK, STRATEGY,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backtester")


# ════════════════════════════════════════════════════════════
#  INDICATORI
# ════════════════════════════════════════════════════════════

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr1 = df["high"] - df["low"]
    tr2 = abs(df["high"] - df["close"].shift(1))
    tr3 = abs(df["low"] - df["close"].shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def bollinger_bands(series: pd.Series, period: int = 40, std_dev: float = 2.0):
    """Returns upper, mid, lower BB as Series."""
    mid = sma(series, period)
    std = series.rolling(window=period).std()
    return mid + std_dev * std, mid, mid - std_dev * std


def adx_indicator(df: pd.DataFrame, period: int = 14) -> pd.Series:
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
    return dx.ewm(alpha=1 / period, min_periods=period).mean()


# ════════════════════════════════════════════════════════════
#  MT5 DATA LOADER
# ════════════════════════════════════════════════════════════

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


def connect_mt5() -> bool:
    if not mt5.initialize(path=MT5_PATH):
        logger.error(f"MT5 initialize failed: {mt5.last_error()}")
        return False
    if not mt5.login(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
        logger.error(f"MT5 login failed: {mt5.last_error()}")
        mt5.shutdown()
        return False
    logger.info(f"MT5 connected — {mt5.account_info().server}")
    return True


def load_candles(symbol: str, timeframe: str, months: int) -> Optional[pd.DataFrame]:
    """Scarica candele storiche da MT5."""
    tf = TIMEFRAME_MAP.get(timeframe)
    if tf is None:
        logger.error(f"Timeframe sconosciuto: {timeframe}")
        return None

    # Assicura che il simbolo sia visibile
    if not mt5.symbol_select(symbol, True):
        logger.error(f"Impossibile selezionare {symbol}")
        return None

    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=months * 30)

    rates = mt5.copy_rates_range(symbol, tf, utc_from, utc_to)
    if rates is None or len(rates) == 0:
        logger.error(f"Nessun dato per {symbol} {timeframe}")
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df.set_index("time", inplace=True)

    logger.info(f"  {symbol} {timeframe}: {len(df)} candele ({df.index[0].date()} → {df.index[-1].date()})")
    return df


def load_htf_candles(symbol: str, months: int) -> Optional[pd.DataFrame]:
    """Carica candele HTF per filtro trend."""
    return load_candles(symbol, STRATEGY["htf_timeframe"], months)


# ════════════════════════════════════════════════════════════
#  SIGNAL GENERATOR (replica strategies.py su dati storici)
# ════════════════════════════════════════════════════════════

def generate_signals(
    df: pd.DataFrame,
    htf_df: Optional[pd.DataFrame],
    strategy_name: str,
) -> pd.DataFrame:
    """
    Genera segnali su tutto il DataFrame storico.
    Aggiunge colonne: signal, sl, tp, reason
    """
    df = df.copy()
    df["signal"] = None
    df["sl"] = np.nan
    df["tp"] = np.nan
    df["reason"] = ""

    # Calcola indicatori comuni
    df["ema_fast"] = ema(df["close"], STRATEGY["ema_fast"])
    df["ema_slow"] = ema(df["close"], STRATEGY["ema_slow"])
    df["ema_trend"] = ema(df["close"], STRATEGY["ema_trend"])
    df["rsi"] = rsi(df["close"], STRATEGY["rsi_period"])
    df["atr"] = atr(df, STRATEGY["atr_period"])

    # BB+RSI specific indicators
    bb_cfg = STRATEGY.get("bb_rsi", {})
    if strategy_name == "BB_RSI_SCALP":
        bb_period = bb_cfg.get("bb_period", 40)
        bb_std = bb_cfg.get("bb_std_dev", 2.0)
        df["bb_upper"], df["bb_mid"], df["bb_lower"] = bollinger_bands(
            df["close"], bb_period, bb_std
        )
        df["rsi_scalp"] = rsi(df["close"], bb_cfg.get("rsi_period", 5))
        df["adx_val"] = adx_indicator(df, bb_cfg.get("adx_period", 14))

    # HTF trend map (nearest HTF candle per ogni entry candle)
    htf_trend = None
    if htf_df is not None and len(htf_df) > 0:
        htf_df = htf_df.copy()
        htf_df["htf_ema_fast"] = ema(htf_df["close"], STRATEGY["ema_fast"])
        htf_df["htf_ema_slow"] = ema(htf_df["close"], STRATEGY["ema_slow"])
        htf_df["htf_trend"] = np.where(
            htf_df["htf_ema_fast"] > htf_df["htf_ema_slow"], "BUY", "SELL"
        )
        # Reindex to entry timeframe (forward fill)
        htf_trend = htf_df["htf_trend"].reindex(df.index, method="ffill")

    for i in range(1, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i - 1]
        atr_val = curr["atr"]

        if pd.isna(atr_val) or atr_val <= 0:
            continue

        price = curr["close"]
        sl_dist = atr_val * STRATEGY["sl_atr_multiplier"]
        tp_dist = atr_val * STRATEGY["tp_atr_multiplier"]

        signal = None
        reason = ""

        # ── EMA CROSS ──
        if strategy_name == "EMA_CROSS":
            # BUY cross
            if (prev["ema_fast"] <= prev["ema_slow"]
                    and curr["ema_fast"] > curr["ema_slow"]
                    and price > curr["ema_trend"]):
                if htf_trend is None or htf_trend.iloc[i] == "BUY":
                    signal = "BUY"
                    reason = f"EMA{STRATEGY['ema_fast']}/{STRATEGY['ema_slow']} bullish cross"

            # SELL cross
            elif (prev["ema_fast"] >= prev["ema_slow"]
                    and curr["ema_fast"] < curr["ema_slow"]
                    and price < curr["ema_trend"]):
                if htf_trend is None or htf_trend.iloc[i] == "SELL":
                    signal = "SELL"
                    reason = f"EMA{STRATEGY['ema_fast']}/{STRATEGY['ema_slow']} bearish cross"

        # ── RSI MEAN REVERSION ──
        elif strategy_name == "RSI_MEAN_REVERSION":
            if (prev["rsi"] < STRATEGY["rsi_oversold"]
                    and curr["rsi"] >= STRATEGY["rsi_oversold"]
                    and price > curr["ema_trend"]):
                signal = "BUY"
                reason = f"RSI exit oversold ({prev['rsi']:.1f}→{curr['rsi']:.1f})"

            elif (prev["rsi"] > STRATEGY["rsi_overbought"]
                    and curr["rsi"] <= STRATEGY["rsi_overbought"]
                    and price < curr["ema_trend"]):
                signal = "SELL"
                reason = f"RSI exit overbought ({prev['rsi']:.1f}→{curr['rsi']:.1f})"

        # ── BREAKOUT ──
        elif strategy_name == "BREAKOUT":
            lookback = STRATEGY["breakout_lookback"]
            if i < lookback + 1:
                continue

            atr_threshold = atr_val * STRATEGY["breakout_atr_multiplier"]
            bar_range = curr["high"] - curr["low"]

            prev_high = df["high"].iloc[i - lookback:i].max()
            prev_low = df["low"].iloc[i - lookback:i].min()

            if price > prev_high and bar_range > atr_threshold:
                signal = "BUY"
                reason = f"Breakout above {lookback}-bar high ({prev_high:.5f})"

            elif price < prev_low and bar_range > atr_threshold:
                signal = "SELL"
                reason = f"Breakout below {lookback}-bar low ({prev_low:.5f})"

        # ── BB_RSI_SCALP ──
        elif strategy_name == "BB_RSI_SCALP":
            adx_max = bb_cfg.get("adx_max", 25)
            rsi_os = bb_cfg.get("rsi_oversold", 30)
            rsi_ob = bb_cfg.get("rsi_overbought", 70)
            swing_lb = bb_cfg.get("swing_lookback", 10)
            sl_buffer_pips = bb_cfg.get("sl_buffer_pips", 3)

            adx_now = curr.get("adx_val", 99)
            if pd.isna(adx_now) or adx_now > adx_max:
                continue

            bb_lower = curr.get("bb_lower", np.nan)
            bb_upper = curr.get("bb_upper", np.nan)
            rsi_now = curr.get("rsi_scalp", np.nan)
            rsi_prv = prev.get("rsi_scalp", np.nan)

            if pd.isna(bb_lower) or pd.isna(rsi_now) or pd.isna(rsi_prv):
                continue

            # Estimate point value for SL buffer
            # For forex ~0.0001, for indices/gold adapt
            if price > 100:
                point_est = 0.01
            elif price > 10:
                point_est = 0.001
            else:
                point_est = 0.0001
            sl_buf = sl_buffer_pips * point_est * 10

            # LONG: price touch lower BB + RSI exit oversold
            if curr["low"] <= bb_lower and rsi_prv < rsi_os and rsi_now >= rsi_os:
                start_idx = max(0, i - swing_lb)
                swing_low = df["low"].iloc[start_idx:i + 1].min()
                sl_val = round(swing_low - sl_buf, 5)
                tp_val = round(bb_upper, 5)

                risk = abs(price - sl_val)
                reward = abs(tp_val - price)
                if risk > 0 and (reward / risk) >= 0.8:
                    signal = "BUY"
                    reason = (
                        f"BB lower touch + RSI({bb_cfg.get('rsi_period', 5)}) "
                        f"exit oversold ({rsi_prv:.1f}→{rsi_now:.1f}). ADX={adx_now:.1f}"
                    )
                    # Override SL/TP directly (don't use ATR-based)
                    df.iloc[i, df.columns.get_loc("signal")] = signal
                    df.iloc[i, df.columns.get_loc("reason")] = reason
                    df.iloc[i, df.columns.get_loc("sl")] = sl_val
                    df.iloc[i, df.columns.get_loc("tp")] = tp_val
                    continue

            # SHORT: price touch upper BB + RSI exit overbought
            if curr["high"] >= bb_upper and rsi_prv > rsi_ob and rsi_now <= rsi_ob:
                start_idx = max(0, i - swing_lb)
                swing_high = df["high"].iloc[start_idx:i + 1].max()
                sl_val = round(swing_high + sl_buf, 5)
                tp_val = round(bb_lower, 5)

                risk = abs(sl_val - price)
                reward = abs(price - tp_val)
                if risk > 0 and (reward / risk) >= 0.8:
                    signal = "SELL"
                    reason = (
                        f"BB upper touch + RSI({bb_cfg.get('rsi_period', 5)}) "
                        f"exit overbought ({rsi_prv:.1f}→{rsi_now:.1f}). ADX={adx_now:.1f}"
                    )
                    df.iloc[i, df.columns.get_loc("signal")] = signal
                    df.iloc[i, df.columns.get_loc("reason")] = reason
                    df.iloc[i, df.columns.get_loc("sl")] = sl_val
                    df.iloc[i, df.columns.get_loc("tp")] = tp_val
                    continue

        # Assegna segnale
        if signal:
            df.iloc[i, df.columns.get_loc("signal")] = signal
            df.iloc[i, df.columns.get_loc("reason")] = reason

            if signal == "BUY":
                df.iloc[i, df.columns.get_loc("sl")] = round(price - sl_dist, 5)
                df.iloc[i, df.columns.get_loc("tp")] = round(price + tp_dist, 5)
            else:
                df.iloc[i, df.columns.get_loc("sl")] = round(price + sl_dist, 5)
                df.iloc[i, df.columns.get_loc("tp")] = round(price - tp_dist, 5)

    total = df["signal"].notna().sum()
    buys = (df["signal"] == "BUY").sum()
    sells = (df["signal"] == "SELL").sum()
    logger.info(f"  Segnali generati: {total} (BUY: {buys}, SELL: {sells})")

    return df


# ════════════════════════════════════════════════════════════
#  TRADE SIMULATOR
# ════════════════════════════════════════════════════════════

class TradeSimulator:
    """
    Simula l'esecuzione dei trade con:
    - Risk per trade (lot sizing)
    - Max posizioni aperte
    - Break-even e trailing stop
    - Regole prop (daily/total drawdown)
    """

    def __init__(self, initial_balance: float, prop_rules: dict, enforce_prop: bool = True):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.equity = initial_balance
        self.prop_rules = prop_rules
        self.enforce_prop = enforce_prop

        # Tracking
        self.trades: list[dict] = []
        self.open_positions: list[dict] = []
        self.equity_curve: list[dict] = []
        self.daily_pnl: dict[str, float] = {}

        # High water marks
        self.peak_balance = initial_balance
        self.max_drawdown = 0.0
        self.max_drawdown_pct = 0.0

        # Prop tracking
        self.day_start_balance = initial_balance
        self.current_day = None
        self.daily_locked = False
        self.prop_violated = False
        self.prop_violation_reason = ""
        self.trading_days: set[str] = set()

    def run(self, df: pd.DataFrame, symbol: str) -> list[dict]:
        """Esegui simulazione su un DataFrame con segnali."""
        for i in range(len(df)):
            row = df.iloc[i]
            ts = df.index[i]
            day_str = ts.strftime("%Y-%m-%d")

            # New day reset
            if day_str != self.current_day:
                self.current_day = day_str
                self.day_start_balance = self.balance
                self.daily_locked = False

            # Update open positions (check SL/TP hits)
            self._update_positions(row, ts)

            # Record equity
            unrealized = sum(self._calc_unrealized(p, row) for p in self.open_positions)
            self.equity = self.balance + unrealized
            self.equity_curve.append({
                "time": ts,
                "balance": self.balance,
                "equity": self.equity,
                "open_positions": len(self.open_positions),
            })

            # Drawdown tracking
            if self.equity > self.peak_balance:
                self.peak_balance = self.equity
            dd = self.peak_balance - self.equity
            dd_pct = (dd / self.peak_balance * 100) if self.peak_balance > 0 else 0
            if dd > self.max_drawdown:
                self.max_drawdown = dd
                self.max_drawdown_pct = dd_pct

            # Prop rule checks
            if self.enforce_prop and not self.prop_violated:
                violation = self._check_prop_rules()
                if violation:
                    self.prop_violated = True
                    self.prop_violation_reason = violation
                    # Close all positions
                    for pos in list(self.open_positions):
                        self._close_position(pos, row["close"], ts, "PROP_VIOLATION")
                    logger.warning(f"  ⛔ PROP VIOLATION @ {ts}: {violation}")
                    break  # Stop simulation

            # Skip if locked or violated
            if self.daily_locked or self.prop_violated:
                continue

            # Process signal
            signal = row.get("signal")
            if signal and pd.notna(signal):
                self._try_open_trade(row, ts, symbol, signal)

        # Close remaining positions at last price
        if len(df) > 0:
            last_row = df.iloc[-1]
            last_ts = df.index[-1]
            for pos in list(self.open_positions):
                self._close_position(pos, last_row["close"], last_ts, "END_OF_DATA")

        return self.trades

    def _try_open_trade(self, row, ts, symbol: str, direction: str):
        """Prova ad aprire un trade con risk checks."""
        # Max positions
        if len(self.open_positions) >= RISK["max_open_positions"]:
            return

        # Already in position for this symbol?
        if any(p["symbol"] == symbol for p in self.open_positions):
            return

        entry = row["close"]
        sl = row["sl"]
        tp = row["tp"]

        if pd.isna(sl) or pd.isna(tp):
            return

        # Lot sizing
        sl_distance = abs(entry - sl)
        if sl_distance <= 0:
            return

        risk_usd = self.balance * RISK["risk_per_trade_pct"] / 100
        # Simplified lot calc: risk_usd / sl_distance_in_price
        # For forex pairs, 1 standard lot = 100,000 units, 1 pip ≈ $10
        lots = risk_usd / (sl_distance * 100_000)
        lots = max(round(lots, 2), 0.01)

        position = {
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry,
            "sl": sl,
            "tp": tp,
            "lots": lots,
            "entry_time": ts,
            "risk_usd": risk_usd,
            "reason": row.get("reason", ""),
            "be_applied": False,
        }

        self.open_positions.append(position)
        self.trading_days.add(ts.strftime("%Y-%m-%d"))

    def _update_positions(self, row, ts):
        """Check SL/TP/BE/Trailing for each open position."""
        to_close = []

        for pos in self.open_positions:
            high = row["high"]
            low = row["low"]
            close = row["close"]

            # ── SL hit? ──
            if pos["direction"] == "BUY" and low <= pos["sl"]:
                to_close.append((pos, pos["sl"], "SL_HIT"))
                continue
            if pos["direction"] == "SELL" and high >= pos["sl"]:
                to_close.append((pos, pos["sl"], "SL_HIT"))
                continue

            # ── TP hit? ──
            if pos["direction"] == "BUY" and high >= pos["tp"]:
                to_close.append((pos, pos["tp"], "TP_HIT"))
                continue
            if pos["direction"] == "SELL" and low <= pos["tp"]:
                to_close.append((pos, pos["tp"], "TP_HIT"))
                continue

            # ── Break-even ──
            if RISK["breakeven_enabled"] and not pos["be_applied"]:
                trigger = pos["entry_price"] * RISK["breakeven_trigger_pct"] / 100
                if pos["direction"] == "BUY" and close >= pos["entry_price"] + trigger:
                    pos["sl"] = pos["entry_price"] + 0.00002  # Small offset
                    pos["be_applied"] = True
                elif pos["direction"] == "SELL" and close <= pos["entry_price"] - trigger:
                    pos["sl"] = pos["entry_price"] - 0.00002
                    pos["be_applied"] = True

            # ── Trailing stop ──
            if RISK["trailing_stop_enabled"] and pos["be_applied"]:
                trail = close * RISK["trailing_stop_pct"] / 100
                if pos["direction"] == "BUY" and close > pos["entry_price"]:
                    new_sl = close - trail
                    if new_sl > pos["sl"]:
                        pos["sl"] = new_sl
                elif pos["direction"] == "SELL" and close < pos["entry_price"]:
                    new_sl = close + trail
                    if new_sl < pos["sl"] or pos["sl"] == 0:
                        pos["sl"] = new_sl

        # Close marked positions
        for pos, exit_price, exit_reason in to_close:
            self._close_position(pos, exit_price, ts, exit_reason)

    def _close_position(self, pos: dict, exit_price: float, ts, exit_reason: str):
        """Chiudi una posizione e registra il trade."""
        if pos["direction"] == "BUY":
            pnl_pips = (exit_price - pos["entry_price"])
        else:
            pnl_pips = (pos["entry_price"] - exit_price)

        # P&L in USD (simplified: pnl_pips * lots * 100,000)
        pnl_usd = pnl_pips * pos["lots"] * 100_000

        self.balance += pnl_usd

        # Daily P&L tracking
        day_str = ts.strftime("%Y-%m-%d") if hasattr(ts, 'strftime') else str(ts)[:10]
        self.daily_pnl[day_str] = self.daily_pnl.get(day_str, 0) + pnl_usd

        trade = {
            "symbol": pos["symbol"],
            "direction": pos["direction"],
            "lots": pos["lots"],
            "entry_price": pos["entry_price"],
            "exit_price": exit_price,
            "sl": pos["sl"],
            "tp": pos["tp"],
            "entry_time": pos["entry_time"],
            "exit_time": ts,
            "pnl_usd": round(pnl_usd, 2),
            "pnl_pips": round(pnl_pips / 0.0001, 1) if pnl_pips != 0 else 0,
            "exit_reason": exit_reason,
            "reason": pos["reason"],
            "balance_after": round(self.balance, 2),
        }

        self.trades.append(trade)

        if pos in self.open_positions:
            self.open_positions.remove(pos)

    def _calc_unrealized(self, pos: dict, row) -> float:
        """Calcola P&L non realizzato di una posizione."""
        if pos["direction"] == "BUY":
            pnl = (row["close"] - pos["entry_price"]) * pos["lots"] * 100_000
        else:
            pnl = (pos["entry_price"] - row["close"]) * pos["lots"] * 100_000
        return pnl

    def _check_prop_rules(self) -> Optional[str]:
        """Controlla violazioni delle regole prop."""
        # Daily drawdown
        daily_loss = self.day_start_balance - self.equity
        max_daily = self.day_start_balance * self.prop_rules["max_daily_loss_pct"] / 100
        if daily_loss >= max_daily:
            return f"Daily drawdown {daily_loss:.2f} >= limit {max_daily:.2f}"

        # Total drawdown
        total_loss = self.initial_balance - self.equity
        max_total = self.initial_balance * self.prop_rules["max_total_loss_pct"] / 100
        if total_loss >= max_total:
            return f"Total drawdown {total_loss:.2f} >= limit {max_total:.2f}"

        # Daily buffer (lock but don't violate)
        buffer = max_daily * RISK["daily_loss_buffer"]
        if daily_loss >= buffer:
            self.daily_locked = True

        return None


# ════════════════════════════════════════════════════════════
#  METRICS CALCULATOR
# ════════════════════════════════════════════════════════════

def calculate_metrics(trades: list[dict], simulator: TradeSimulator) -> dict:
    """Calcola tutte le metriche di performance."""
    if not trades:
        return {"error": "Nessun trade generato"}

    pnls = [t["pnl_usd"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_pnl = sum(pnls)
    win_count = len(wins)
    loss_count = len(losses)
    total_count = len(trades)
    winrate = (win_count / total_count * 100) if total_count > 0 else 0

    avg_win = np.mean(wins) if wins else 0
    avg_loss = abs(np.mean(losses)) if losses else 0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")

    # Payoff ratio (avg win / avg loss)
    payoff = (avg_win / avg_loss) if avg_loss > 0 else float("inf")

    # Expectancy
    expectancy = (winrate / 100 * avg_win) - ((1 - winrate / 100) * avg_loss)

    # Max consecutive wins/losses
    max_consec_wins = 0
    max_consec_losses = 0
    curr_wins = 0
    curr_losses = 0
    for p in pnls:
        if p > 0:
            curr_wins += 1
            curr_losses = 0
            max_consec_wins = max(max_consec_wins, curr_wins)
        else:
            curr_losses += 1
            curr_wins = 0
            max_consec_losses = max(max_consec_losses, curr_losses)

    # Sharpe Ratio (annualized, assuming ~252 trading days)
    if len(pnls) > 1:
        daily_returns = pd.Series(pnls)
        sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252) if daily_returns.std() > 0 else 0
    else:
        sharpe = 0

    # Equity curve stats
    eq = pd.DataFrame(simulator.equity_curve)

    # Return on account
    roa = (total_pnl / simulator.initial_balance * 100) if simulator.initial_balance > 0 else 0

    # Duration
    if trades:
        first_entry = trades[0]["entry_time"]
        last_exit = trades[-1]["exit_time"]
        duration_days = (last_exit - first_entry).days if hasattr(last_exit, 'days') else 0
    else:
        duration_days = 0

    # Exit reason breakdown
    exit_reasons = {}
    for t in trades:
        r = t["exit_reason"]
        exit_reasons[r] = exit_reasons.get(r, 0) + 1

    # Best/Worst trade
    best = max(trades, key=lambda t: t["pnl_usd"])
    worst = min(trades, key=lambda t: t["pnl_usd"])

    # Prop compliance
    prop_passed = not simulator.prop_violated
    min_days_met = len(simulator.trading_days) >= simulator.prop_rules.get("min_trading_days", 0)

    target_pct = simulator.prop_rules.get("profit_target_pct")
    target_met = False
    if target_pct:
        target_usd = simulator.initial_balance * target_pct / 100
        target_met = total_pnl >= target_usd

    return {
        # Core
        "total_trades": total_count,
        "wins": win_count,
        "losses": loss_count,
        "winrate_pct": round(winrate, 2),
        "total_pnl_usd": round(total_pnl, 2),
        "roa_pct": round(roa, 2),

        # Averages
        "avg_win_usd": round(avg_win, 2),
        "avg_loss_usd": round(avg_loss, 2),
        "payoff_ratio": round(payoff, 2),
        "profit_factor": round(profit_factor, 2),
        "expectancy_usd": round(expectancy, 2),

        # Risk
        "max_drawdown_usd": round(simulator.max_drawdown, 2),
        "max_drawdown_pct": round(simulator.max_drawdown_pct, 2),
        "sharpe_ratio": round(sharpe, 2),

        # Streaks
        "max_consecutive_wins": max_consec_wins,
        "max_consecutive_losses": max_consec_losses,

        # Best/Worst
        "best_trade_usd": round(best["pnl_usd"], 2),
        "best_trade_symbol": f"{best['symbol']} {best['direction']}",
        "worst_trade_usd": round(worst["pnl_usd"], 2),
        "worst_trade_symbol": f"{worst['symbol']} {worst['direction']}",

        # Exit reasons
        "exit_reasons": exit_reasons,

        # Prop compliance
        "trading_days": len(simulator.trading_days),
        "prop_passed": prop_passed,
        "prop_violation": simulator.prop_violation_reason,
        "min_days_met": min_days_met,
        "target_met": target_met,

        # Duration
        "duration_days": duration_days,
        "final_balance": round(simulator.balance, 2),
    }


# ════════════════════════════════════════════════════════════
#  REPORT PRINTER
# ════════════════════════════════════════════════════════════

def print_report(
    metrics: dict,
    strategy_name: str,
    symbols: list[str],
    prop: str,
    phase: str,
):
    """Stampa report formattato in console."""

    def bar(value, max_val, width=30):
        pct = min(value / max_val, 1.0) if max_val > 0 else 0
        filled = int(pct * width)
        return "█" * filled + "░" * (width - filled)

    sep = "═" * 62

    print(f"\n{sep}")
    print(f"  📊 BACKTEST REPORT — {strategy_name}")
    print(f"{sep}")
    print(f"  Prop: {prop} {phase}")
    print(f"  Symbols: {', '.join(symbols)}")
    print(f"  Duration: {metrics['duration_days']} days")
    print(f"{sep}\n")

    # ── Performance ──
    print("  ╔══════════════════════════════════════════════════════╗")
    print(f"  ║  PERFORMANCE                                        ║")
    print("  ╠══════════════════════════════════════════════════════╣")
    pnl_icon = "🟢" if metrics["total_pnl_usd"] >= 0 else "🔴"
    print(f"  ║  {pnl_icon} P&L Totale:     ${metrics['total_pnl_usd']:>+12,.2f}  ({metrics['roa_pct']:+.2f}%)    ║")
    print(f"  ║     Balance Finale: ${metrics['final_balance']:>12,.2f}               ║")
    print(f"  ║     Profit Factor:  {metrics['profit_factor']:>8.2f}                       ║")
    print(f"  ║     Sharpe Ratio:   {metrics['sharpe_ratio']:>8.2f}                       ║")
    print(f"  ║     Expectancy:     ${metrics['expectancy_usd']:>+8.2f}/trade                 ║")
    print("  ╚══════════════════════════════════════════════════════╝\n")

    # ── Trade Stats ──
    print("  ╔══════════════════════════════════════════════════════╗")
    print(f"  ║  TRADE STATISTICS                                   ║")
    print("  ╠══════════════════════════════════════════════════════╣")
    print(f"  ║  Trade totali:  {metrics['total_trades']:>6}                              ║")
    print(f"  ║  Vinti:         {metrics['wins']:>6}   Persi: {metrics['losses']:>6}              ║")
    wr = metrics["winrate_pct"]
    wr_bar = bar(wr, 100, 20)
    print(f"  ║  Winrate:       {wr_bar} {wr:.1f}%    ║")
    print(f"  ║  Avg Win:       ${metrics['avg_win_usd']:>+10,.2f}                      ║")
    print(f"  ║  Avg Loss:      ${-metrics['avg_loss_usd']:>+10,.2f}                      ║")
    print(f"  ║  Payoff Ratio:  {metrics['payoff_ratio']:>8.2f}  (avg_win/avg_loss)       ║")
    print(f"  ║  Best Trade:    ${metrics['best_trade_usd']:>+10,.2f}  ({metrics['best_trade_symbol']})  ║")
    print(f"  ║  Worst Trade:   ${metrics['worst_trade_usd']:>+10,.2f}  ({metrics['worst_trade_symbol']})  ║")
    print(f"  ║  Max Win Streak:  {metrics['max_consecutive_wins']:>3}                            ║")
    print(f"  ║  Max Loss Streak: {metrics['max_consecutive_losses']:>3}                            ║")
    print("  ╚══════════════════════════════════════════════════════╝\n")

    # ── Risk ──
    print("  ╔══════════════════════════════════════════════════════╗")
    print(f"  ║  RISK                                               ║")
    print("  ╠══════════════════════════════════════════════════════╣")
    dd_icon = "🟢" if metrics["max_drawdown_pct"] < 5 else ("🟡" if metrics["max_drawdown_pct"] < 8 else "🔴")
    print(f"  ║  {dd_icon} Max Drawdown:  ${metrics['max_drawdown_usd']:>10,.2f}  ({metrics['max_drawdown_pct']:.2f}%)    ║")
    print("  ╚══════════════════════════════════════════════════════╝\n")

    # ── Exit Reasons ──
    print("  ╔══════════════════════════════════════════════════════╗")
    print(f"  ║  EXIT REASONS                                       ║")
    print("  ╠══════════════════════════════════════════════════════╣")
    for reason, count in metrics["exit_reasons"].items():
        pct = count / metrics["total_trades"] * 100
        icon = {"TP_HIT": "🎯", "SL_HIT": "🛑", "END_OF_DATA": "⏹️", "PROP_VIOLATION": "⛔"}.get(reason, "•")
        print(f"  ║  {icon} {reason:<18} {count:>4} ({pct:>5.1f}%)                 ║")
    print("  ╚══════════════════════════════════════════════════════╝\n")

    # ── Prop Compliance ──
    print("  ╔══════════════════════════════════════════════════════╗")
    print(f"  ║  PROP COMPLIANCE — {prop} {phase:<30}  ║")
    print("  ╠══════════════════════════════════════════════════════╣")
    dd_ok = "✅" if metrics["prop_passed"] else "❌"
    days_ok = "✅" if metrics["min_days_met"] else "❌"
    target_ok = "✅" if metrics["target_met"] else "❌"
    print(f"  ║  {dd_ok} Drawdown Rules:   {'PASSED' if metrics['prop_passed'] else 'VIOLATED'}                    ║")
    if metrics["prop_violation"]:
        print(f"  ║     ⛔ {metrics['prop_violation']:<48} ║")
    print(f"  ║  {days_ok} Min Trading Days: {metrics['trading_days']} days                        ║")
    print(f"  ║  {target_ok} Profit Target:    {'REACHED' if metrics['target_met'] else 'NOT YET'}                    ║")

    # Overall verdict
    passed_all = metrics["prop_passed"] and metrics["min_days_met"] and metrics["target_met"]
    verdict = "🏆 CHALLENGE SUPERATA!" if passed_all else "❌ CHALLENGE NON SUPERATA"
    print(f"  ║                                                      ║")
    print(f"  ║  {'═' * 50}  ║")
    print(f"  ║  {verdict:<52} ║")
    print("  ╚══════════════════════════════════════════════════════╝\n")


# ════════════════════════════════════════════════════════════
#  MULTI-STRATEGY COMPARISON
# ════════════════════════════════════════════════════════════

def print_comparison(results: dict):
    """Stampa tabella comparativa tra strategie."""
    if len(results) < 2:
        return

    print("\n" + "═" * 72)
    print("  📊 CONFRONTO STRATEGIE")
    print("═" * 72)

    header = f"  {'Metrica':<25}"
    for name in results:
        header += f" {name:>14}"
    print(header)
    print("  " + "─" * 68)

    rows = [
        ("Trade totali", "total_trades", "d"),
        ("Winrate %", "winrate_pct", ".1f"),
        ("P&L Totale $", "total_pnl_usd", "+,.2f"),
        ("ROA %", "roa_pct", "+.2f"),
        ("Profit Factor", "profit_factor", ".2f"),
        ("Sharpe Ratio", "sharpe_ratio", ".2f"),
        ("Expectancy $", "expectancy_usd", "+.2f"),
        ("Max DD %", "max_drawdown_pct", ".2f"),
        ("Payoff Ratio", "payoff_ratio", ".2f"),
        ("Prop Passed", "prop_passed", ""),
    ]

    for label, key, fmt in rows:
        line = f"  {label:<25}"
        for name, m in results.items():
            val = m.get(key, "—")
            if key == "prop_passed":
                line += f" {'✅':>14}" if val else f" {'❌':>14}"
            elif isinstance(val, float):
                line += f" {val:>14{fmt}}"
            else:
                line += f" {val:>14}"
        print(line)

    print("═" * 72)

    # Winner
    best_name = max(results, key=lambda k: results[k].get("total_pnl_usd", 0))
    print(f"\n  🏆 Strategia migliore per P&L: {best_name}")
    best_sharpe = max(results, key=lambda k: results[k].get("sharpe_ratio", 0))
    print(f"  🏆 Strategia migliore per Sharpe: {best_sharpe}\n")


# ════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Prop Bot — Backtester")
    parser.add_argument("--strategy", type=str, default=None,
                        help="Strategia: EMA_CROSS, RSI_MEAN_REVERSION, BREAKOUT, BB_RSI_SCALP (default: tutte)")
    parser.add_argument("--symbol", type=str, default=None,
                        help="Simbolo singolo (default: tutti da config)")
    parser.add_argument("--months", type=int, default=6,
                        help="Mesi di lookback (default: 6)")
    parser.add_argument("--timeframe", type=str, default=None,
                        help="Timeframe (default: da config)")
    parser.add_argument("--export", action="store_true",
                        help="Esporta trade e equity curve in CSV")
    parser.add_argument("--no-prop", action="store_true",
                        help="Ignora regole prop (test puro)")
    args = parser.parse_args()

    # ── Strategies to test ──
    if args.strategy:
        strategies = [args.strategy.upper()]
    else:
        strategies = ["EMA_CROSS", "RSI_MEAN_REVERSION", "BREAKOUT", "BB_RSI_SCALP"]

    # ── Prop rules ──
    rules_map = {"FTMO": FTMO_RULES, "FUNDEDNEXT": FUNDEDNEXT_RULES}
    prop_rules = rules_map[ACTIVE_PROP][CURRENT_PHASE]
    initial_balance = prop_rules["account_size"]

    # ── Connect MT5 ──
    print()
    logger.info("Connessione a MT5...")
    if not connect_mt5():
        sys.exit(1)

    # ── Preload all needed data ──
    # Collect all symbols across strategies
    all_symbols = set()
    bb_cfg = STRATEGY.get("bb_rsi", {})
    bb_symbols = bb_cfg.get("symbols", [])
    general_symbols = STRATEGY["symbols"]

    if args.symbol:
        all_symbols.add(args.symbol.upper())
    else:
        all_symbols.update(general_symbols)
        all_symbols.update(bb_symbols)

    all_symbols = sorted(all_symbols)

    # Determine timeframes to load
    general_tf = args.timeframe or STRATEGY["entry_timeframe"]
    scalp_tf = bb_cfg.get("entry_timeframe", "M5")
    timeframes_needed = {general_tf}
    if "BB_RSI_SCALP" in strategies:
        timeframes_needed.add(scalp_tf)

    logger.info(f"Caricamento dati: {', '.join(all_symbols)} | TF: {', '.join(timeframes_needed)} | {args.months} mesi")
    # data_cache keyed by (symbol, timeframe)
    data_cache = {}
    htf_cache = {}

    for symbol in all_symbols:
        for tf in timeframes_needed:
            df = load_candles(symbol, tf, args.months)
            if df is not None:
                data_cache[(symbol, tf)] = df
        htf = load_htf_candles(symbol, args.months)
        if htf is not None:
            htf_cache[symbol] = htf

    mt5.shutdown()

    if not data_cache:
        logger.error("Nessun dato caricato — controlla simboli e connessione MT5")
        sys.exit(1)

    # ── Run backtests ──
    all_results = {}

    for strat_name in strategies:
        logger.info(f"\n{'='*50}")
        logger.info(f"BACKTEST: {strat_name}")
        logger.info(f"{'='*50}")

        simulator = TradeSimulator(
            initial_balance=initial_balance,
            prop_rules=prop_rules,
            enforce_prop=not args.no_prop,
        )

        # Select symbols and timeframe for this strategy
        if strat_name == "BB_RSI_SCALP":
            strat_symbols = [args.symbol.upper()] if args.symbol else bb_symbols or general_symbols
            strat_tf = scalp_tf
        else:
            strat_symbols = [args.symbol.upper()] if args.symbol else general_symbols
            strat_tf = general_tf

        for symbol in strat_symbols:
            cache_key = (symbol, strat_tf)
            if cache_key not in data_cache:
                logger.warning(f"  No data for {symbol} {strat_tf} — skipping")
                continue

            logger.info(f"  Generazione segnali {strat_name} su {symbol} ({strat_tf})...")
            df = data_cache[cache_key]
            htf = htf_cache.get(symbol)

            df_signals = generate_signals(df, htf, strat_name)
            simulator.run(df_signals, symbol)

        # Calculate metrics
        metrics = calculate_metrics(simulator.trades, simulator)
        all_results[strat_name] = metrics

        # Print report
        print_report(metrics, strat_name, strat_symbols, ACTIVE_PROP, CURRENT_PHASE)

        # Export CSV
        if args.export and simulator.trades:
            os.makedirs("backtest_results", exist_ok=True)

            trades_df = pd.DataFrame(simulator.trades)
            trades_path = f"backtest_results/{strat_name}_trades.csv"
            trades_df.to_csv(trades_path, index=False)
            logger.info(f"  📁 Trade esportati: {trades_path}")

            eq_df = pd.DataFrame(simulator.equity_curve)
            eq_path = f"backtest_results/{strat_name}_equity.csv"
            eq_df.to_csv(eq_path, index=False)
            logger.info(f"  📁 Equity curve esportata: {eq_path}")

    # ── Comparison ──
    if len(all_results) > 1:
        print_comparison(all_results)

    logger.info("Backtest completato ✅")


if __name__ == "__main__":
    main()
