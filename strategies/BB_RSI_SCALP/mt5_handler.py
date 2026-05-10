"""
MetaTrader 5 Connection Handler
Manages connection, orders, positions, and account data.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import MetaTrader5 as mt5
import pandas as pd

from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH

logger = logging.getLogger(__name__)


class MT5Handler:
    """Interface to MetaTrader 5 terminal."""

    def __init__(self):
        self._connected = False

    # ── Connection ──────────────────────────────────────────

    def connect(self) -> bool:
        """Initialize and login to MT5."""
        if not mt5.initialize(path=MT5_PATH):
            logger.error(f"MT5 initialize failed: {mt5.last_error()}")
            return False

        authorized = mt5.login(
            login=MT5_LOGIN,
            password=MT5_PASSWORD,
            server=MT5_SERVER,
        )
        if not authorized:
            logger.error(f"MT5 login failed: {mt5.last_error()}")
            mt5.shutdown()
            return False

        info = mt5.account_info()
        logger.info(
            f"Connected to MT5 — Account: {info.login}, "
            f"Server: {info.server}, Balance: ${info.balance:.2f}"
        )
        self._connected = True
        return True

    def disconnect(self):
        """Shutdown MT5 connection."""
        mt5.shutdown()
        self._connected = False
        logger.info("MT5 disconnected")

    def ensure_connected(self) -> bool:
        """Reconnect if connection dropped."""
        if not self._connected or mt5.account_info() is None:
            logger.warning("MT5 connection lost — reconnecting...")
            return self.connect()
        return True

    # ── Account Data ────────────────────────────────────────

    def get_account_info(self) -> Optional[dict]:
        """Get current account info."""
        if not self.ensure_connected():
            return None

        info = mt5.account_info()
        if info is None:
            return None

        return {
            "login": info.login,
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "free_margin": info.margin_free,
            "profit": info.profit,
            "leverage": info.leverage,
            "currency": info.currency,
            "server": info.server,
        }

    def get_balance(self) -> float:
        """Get current balance."""
        info = mt5.account_info()
        return info.balance if info else 0.0

    def get_equity(self) -> float:
        """Get current equity."""
        info = mt5.account_info()
        return info.equity if info else 0.0

    # ── Symbol Info ─────────────────────────────────────────

    def get_all_symbols(self, filters: dict = None) -> list[dict]:
        """
        Fetch ALL symbols available on the connected broker.
        Applies optional filters to narrow down the list.

        filters dict keys (all optional):
            categories: list[str]   — "forex", "indices", "commodities", "crypto", "stocks"
            spread_max: int         — max spread in points
            has_volume: bool        — only symbols with volume > 0
            tradeable_only: bool    — only symbols allowed to trade (default True)
            name_contains: str      — filter by substring in symbol name
            exclude_contains: list  — exclude symbols containing these substrings

        Returns list of dicts with symbol details.
        """
        if filters is None:
            filters = {}

        all_symbols = mt5.symbols_get()
        if not all_symbols:
            logger.error("No symbols returned from broker")
            return []

        logger.info(f"Broker has {len(all_symbols)} total symbols")

        results = []
        tradeable_only = filters.get("tradeable_only", True)
        categories = [c.lower() for c in filters.get("categories", [])]
        spread_max = filters.get("spread_max")
        name_contains = filters.get("name_contains", "").upper()
        exclude_contains = [e.upper() for e in filters.get("exclude_contains", [])]

        for sym in all_symbols:
            # Only tradeable symbols
            if tradeable_only and not sym.trade_mode:
                continue

            name = sym.name.upper()

            # Name filter
            if name_contains and name_contains not in name:
                continue

            # Exclusion filter
            if any(exc in name for exc in exclude_contains):
                continue

            # Spread filter
            if spread_max is not None and sym.spread > spread_max:
                continue

            # Category classification based on symbol properties
            category = self._classify_symbol(sym)

            # Category filter
            if categories and category not in categories:
                continue

            results.append({
                "symbol": sym.name,
                "category": category,
                "description": sym.description,
                "spread": sym.spread,
                "point": sym.point,
                "digits": sym.digits,
                "volume_min": sym.volume_min,
                "volume_max": sym.volume_max,
                "trade_contract_size": sym.trade_contract_size,
                "currency_base": sym.currency_base,
                "currency_profit": sym.currency_profit,
                "path": sym.path,
            })

        logger.info(
            f"Filtered to {len(results)} symbols"
            + (f" (categories: {', '.join(categories)})" if categories else "")
        )
        return results

    @staticmethod
    def _classify_symbol(sym) -> str:
        """
        Classify a symbol into a category based on its properties.
        Works across all brokers regardless of naming conventions.
        """
        path = (sym.path or "").lower()
        name = sym.name.upper()
        desc = (sym.description or "").lower()
        base = (sym.currency_base or "").upper()
        profit = (sym.currency_profit or "").upper()

        # Known forex currencies
        forex_currencies = {
            "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD",
            "SEK", "NOK", "DKK", "HKD", "SGD", "TRY", "ZAR", "MXN",
            "PLN", "HUF", "CZK", "RUB", "CNY", "CNH", "INR", "THB",
        }

        # Crypto identifiers
        crypto_currencies = {
            "BTC", "ETH", "LTC", "XRP", "ADA", "DOT", "SOL", "DOGE",
            "BNB", "AVAX", "MATIC", "LINK", "UNI", "SHIB", "ATOM",
        }

        # Commodity identifiers
        commodity_names = {
            "XAU", "XAG", "GOLD", "SILVER", "OIL", "BRENT", "WTI",
            "XAUUSD", "XAGUSD", "NGAS", "UKOIL", "USOIL", "COPPER",
            "PLATINUM", "PALLADIUM",
        }

        # Index identifiers
        index_names = {
            "US30", "US100", "US500", "NAS100", "SPX500", "SP500",
            "DJ30", "DAX", "GER40", "GER30", "UK100", "FTSE",
            "JP225", "NI225", "AUS200", "FRA40", "EU50", "STOXX50",
            "HK50", "CHINA50", "USTEC", "USDX",
        }

        # Check path first (most reliable across brokers)
        if "forex" in path or "fx" in path:
            return "forex"
        if "index" in path or "indices" in path or "cfd index" in path:
            return "indices"
        if "commodity" in path or "metal" in path or "energy" in path:
            return "commodities"
        if "crypto" in path:
            return "crypto"
        if "stock" in path or "share" in path or "equity" in path:
            return "stocks"

        # Check by name patterns
        if any(idx in name for idx in index_names):
            return "indices"
        if any(comm in name for comm in commodity_names):
            return "commodities"
        if any(cr in base for cr in crypto_currencies) or any(cr in name[:3] for cr in crypto_currencies):
            return "crypto"

        # Forex: both base and profit are known forex currencies, name is 6 chars
        if (len(name) == 6
                and base in forex_currencies
                and profit in forex_currencies):
            return "forex"
        # Some brokers append suffixes: EURUSDm, EURUSD.r, etc.
        clean_name = ''.join(c for c in name if c.isalpha())
        if (len(clean_name) == 6
                and clean_name[:3] in forex_currencies
                and clean_name[3:6] in forex_currencies):
            return "forex"

        # Fallback: check description
        if any(w in desc for w in ["index", "indice", "nasdaq", "dow", "s&p"]):
            return "indices"
        if any(w in desc for w in ["gold", "silver", "oil", "crude", "gas"]):
            return "commodities"

        return "other"

    def get_symbol_names(self, filters: dict = None) -> list[str]:
        """Convenience: return just the symbol name strings."""
        symbols = self.get_all_symbols(filters)
        return [s["symbol"] for s in symbols]

    def print_symbol_catalog(self):
        """Print a formatted catalog of all broker symbols by category."""
        all_syms = self.get_all_symbols({"tradeable_only": True})

        by_cat = {}
        for s in all_syms:
            cat = s["category"]
            by_cat.setdefault(cat, []).append(s)

        print(f"\n{'═' * 60}")
        print(f"  📋 BROKER SYMBOL CATALOG — {len(all_syms)} tradeable symbols")
        print(f"{'═' * 60}")

        for cat in ["forex", "indices", "commodities", "crypto", "stocks", "other"]:
            syms = by_cat.get(cat, [])
            if not syms:
                continue
            emoji = {
                "forex": "💱", "indices": "📊", "commodities": "🥇",
                "crypto": "₿", "stocks": "📈", "other": "📦"
            }.get(cat, "•")
            print(f"\n  {emoji} {cat.upper()} ({len(syms)})")
            print(f"  {'─' * 55}")
            for s in sorted(syms, key=lambda x: x["symbol"]):
                desc = s["description"][:35] if s["description"] else ""
                print(f"    {s['symbol']:<15} spread: {s['spread']:>4}  │ {desc}")

        print(f"\n{'═' * 60}\n")

    def get_symbol_info(self, symbol: str) -> Optional[dict]:
        """Get symbol details (spread, point, digits, etc.)."""
        info = mt5.symbol_info(symbol)
        if info is None:
            logger.error(f"Symbol {symbol} not found")
            return None

        # Make sure symbol is visible in Market Watch
        if not info.visible:
            mt5.symbol_select(symbol, True)

        return {
            "symbol": symbol,
            "bid": info.bid,
            "ask": info.ask,
            "spread": info.spread,
            "point": info.point,
            "digits": info.digits,
            "volume_min": info.volume_min,
            "volume_max": info.volume_max,
            "volume_step": info.volume_step,
            "trade_contract_size": info.trade_contract_size,
            "currency_profit": info.currency_profit,
        }

    def get_spread(self, symbol: str) -> int:
        """Get current spread in points."""
        info = mt5.symbol_info(symbol)
        return info.spread if info else 999

    # ── Market Data ─────────────────────────────────────────

    TIMEFRAME_MAP = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
        "W1": mt5.TIMEFRAME_W1,
    }

    def get_candles(
        self, symbol: str, timeframe: str, count: int = 500
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV candles as a DataFrame.
        Columns: time, open, high, low, close, tick_volume, spread, real_volume
        """
        tf = self.TIMEFRAME_MAP.get(timeframe)
        if tf is None:
            logger.error(f"Unknown timeframe: {timeframe}")
            return None

        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            logger.error(f"No candle data for {symbol} {timeframe}")
            return None

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df

    def get_current_price(self, symbol: str) -> Optional[dict]:
        """Get current bid/ask."""
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        return {"bid": tick.bid, "ask": tick.ask, "time": tick.time}

    # ── Order Execution ─────────────────────────────────────

    def open_position(
        self,
        symbol: str,
        direction: str,     # "BUY" or "SELL"
        lots: float,
        sl: float = 0.0,
        tp: float = 0.0,
        comment: str = "PropBot",
        magic: int = 123456,
    ) -> Optional[dict]:
        """
        Open a market order.
        Returns order result dict or None on failure.
        """
        if not self.ensure_connected():
            return None

        sym_info = mt5.symbol_info(symbol)
        if sym_info is None or not sym_info.visible:
            mt5.symbol_select(symbol, True)
            time.sleep(0.2)

        order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
        price = sym_info.ask if direction == "BUY" else sym_info.bid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lots,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result is None:
            logger.error(f"Order send returned None: {mt5.last_error()}")
            return None

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(
                f"Order failed — {symbol} {direction} {lots}L: "
                f"retcode={result.retcode}, comment={result.comment}"
            )
            return None

        logger.info(
            f"✅ Order filled — {symbol} {direction} {lots}L @ {result.price}, "
            f"SL={sl}, TP={tp}, ticket={result.order}"
        )

        return {
            "ticket": result.order,
            "symbol": symbol,
            "direction": direction,
            "lots": lots,
            "price": result.price,
            "sl": sl,
            "tp": tp,
        }

    def close_position(self, ticket: int) -> bool:
        """Close a position by ticket number."""
        position = mt5.positions_get(ticket=ticket)
        if not position:
            logger.warning(f"Position {ticket} not found")
            return False

        pos = position[0]
        close_type = (
            mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY
            else mt5.ORDER_TYPE_BUY
        )
        price = (
            mt5.symbol_info_tick(pos.symbol).bid
            if pos.type == mt5.ORDER_TYPE_BUY
            else mt5.symbol_info_tick(pos.symbol).ask
        )

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": pos.magic,
            "comment": "PropBot_close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"✅ Closed position {ticket} @ {result.price}")
            return True

        logger.error(f"Failed to close {ticket}: {result}")
        return False

    def close_all_positions(self) -> int:
        """Close all open positions. Returns number of positions closed."""
        positions = mt5.positions_get()
        if not positions:
            return 0

        closed = 0
        for pos in positions:
            if self.close_position(pos.ticket):
                closed += 1
        return closed

    def modify_sl_tp(
        self, ticket: int, sl: float = None, tp: float = None
    ) -> bool:
        """Modify SL and/or TP of an existing position."""
        position = mt5.positions_get(ticket=ticket)
        if not position:
            return False

        pos = position[0]
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": ticket,
            "sl": sl if sl is not None else pos.sl,
            "tp": tp if tp is not None else pos.tp,
        }

        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"Modified {ticket}: SL={sl}, TP={tp}")
            return True

        logger.error(f"Modify failed for {ticket}: {result}")
        return False

    # ── Positions Query ─────────────────────────────────────

    def get_open_positions(self, symbol: str = None) -> list[dict]:
        """Get all open positions, optionally filtered by symbol."""
        if symbol:
            positions = mt5.positions_get(symbol=symbol)
        else:
            positions = mt5.positions_get()

        if not positions:
            return []

        return [
            {
                "ticket": p.ticket,
                "symbol": p.symbol,
                "direction": "BUY" if p.type == 0 else "SELL",
                "lots": p.volume,
                "open_price": p.price_open,
                "current_price": p.price_current,
                "sl": p.sl,
                "tp": p.tp,
                "profit": p.profit,
                "swap": p.swap,
                "magic": p.magic,
                "comment": p.comment,
                "time": datetime.fromtimestamp(p.time, tz=timezone.utc),
            }
            for p in positions
        ]

    # ── History ─────────────────────────────────────────────

    def get_today_closed_trades(self) -> list[dict]:
        """Get all trades closed today."""
        today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        deals = mt5.history_deals_get(today, datetime.now(timezone.utc))
        if not deals:
            return []

        return [
            {
                "ticket": d.ticket,
                "symbol": d.symbol,
                "direction": "BUY" if d.type == 0 else "SELL",
                "lots": d.volume,
                "price": d.price,
                "profit": d.profit,
                "swap": d.swap,
                "commission": d.commission,
                "time": datetime.fromtimestamp(d.time, tz=timezone.utc),
                "comment": d.comment,
            }
            for d in deals
            if d.entry == 1  # Only exits (closed trades)
        ]

    def get_today_pnl(self) -> float:
        """Get total realized P&L for today."""
        trades = self.get_today_closed_trades()
        return sum(t["profit"] + t.get("swap", 0) + t.get("commission", 0) for t in trades)
