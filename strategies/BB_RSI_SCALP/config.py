"""
Prop Trading Bot — Configuration
Supports: FTMO, FundedNext
Platform: MetaTrader 5 (CFD)

IMPORTANT: Fill in your credentials and adjust settings before running.
"""

# ============================================================
# METATRADER 5 CONNECTION
# ============================================================

MT5_LOGIN =               # Your MT5 account number
MT5_PASSWORD = ""    # MT5 password
MT5_SERVER = "FTMO-Demo"        # Broker/prop server name
MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"  # MT5 install path

# ============================================================
# TELEGRAM NOTIFICATIONS
# ============================================================

TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""

# ============================================================
# PROP FIRM SELECTION
# ============================================================
# Options: "FTMO" or "FUNDEDNEXT"
ACTIVE_PROP = "FTMO"

# Current phase: "CHALLENGE", "VERIFICATION", "FUNDED"
CURRENT_PHASE = "CHALLENGE"

# ============================================================
# PROP FIRM RULES — FTMO
# ============================================================

FTMO_RULES = {
    "CHALLENGE": {
        "account_size": 100_000,        # Account size in USD
        "profit_target_pct": 10.0,      # 10% profit target
        "max_daily_loss_pct": 5.0,      # 5% max daily loss
        "max_total_loss_pct": 10.0,     # 10% max total loss
        "min_trading_days": 4,          # Minimum 4 trading days
        "max_trading_days": None,       # No time limit (unlimited)
        "max_leverage": 100,            # 1:100
        "weekend_holding": True,        # Allowed
        "news_trading": True,           # Allowed
    },
    "VERIFICATION": {
        "account_size": 100_000,
        "profit_target_pct": 5.0,       # 5% profit target
        "max_daily_loss_pct": 5.0,
        "max_total_loss_pct": 10.0,
        "min_trading_days": 4,
        "max_trading_days": None,
        "max_leverage": 100,
        "weekend_holding": True,
        "news_trading": True,
    },
    "FUNDED": {
        "account_size": 100_000,
        "profit_target_pct": None,      # No target — just trade
        "max_daily_loss_pct": 5.0,
        "max_total_loss_pct": 10.0,
        "min_trading_days": 4,
        "max_trading_days": None,
        "max_leverage": 100,
        "weekend_holding": True,
        "news_trading": True,
    },
}

# ============================================================
# PROP FIRM RULES — FUNDEDNEXT
# ============================================================

FUNDEDNEXT_RULES = {
    "CHALLENGE": {
        "account_size": 100_000,
        "profit_target_pct": 10.0,      # 10% Phase 1
        "max_daily_loss_pct": 5.0,
        "max_total_loss_pct": 10.0,
        "min_trading_days": 5,          # Min 5 calendar days
        "max_trading_days": 30,         # 30 days limit
        "max_leverage": 100,
        "weekend_holding": False,       # Must close before weekend
        "news_trading": False,          # Restricted on major news
    },
    "VERIFICATION": {
        "account_size": 100_000,
        "profit_target_pct": 5.0,
        "max_daily_loss_pct": 5.0,
        "max_total_loss_pct": 10.0,
        "min_trading_days": 5,
        "max_trading_days": 60,
        "max_leverage": 100,
        "weekend_holding": False,
        "news_trading": False,
    },
    "FUNDED": {
        "account_size": 100_000,
        "profit_target_pct": None,
        "max_daily_loss_pct": 5.0,
        "max_total_loss_pct": 10.0,
        "min_trading_days": 5,
        "max_trading_days": None,
        "max_leverage": 100,
        "weekend_holding": False,
        "news_trading": False,
    },
}

# ============================================================
# RISK MANAGEMENT SETTINGS
# ============================================================

RISK = {
    # Max risk per trade as % of current balance
    "risk_per_trade_pct": 1.0,

    # Max simultaneous open positions
    "max_open_positions": 3,

    # Max total exposure as % of balance
    "max_exposure_pct": 5.0,

    # Daily loss safety buffer — bot stops before hitting prop limit
    # e.g. 0.8 = bot stops at 80% of the daily loss limit
    "daily_loss_buffer": 0.80,

    # Total loss safety buffer
    "total_loss_buffer": 0.80,

    # Trailing stop settings
    "trailing_stop_enabled": True,
    "trailing_stop_pct": 0.5,       # Trail by 0.5%

    # Break-even settings
    "breakeven_enabled": True,
    "breakeven_trigger_pct": 0.3,   # Move SL to BE after 0.3% profit
    "breakeven_offset_pips": 2,     # SL offset above/below entry

    # Max spread allowed to open a trade (in points)
    "max_spread_points": 30,
}

# ============================================================
# TRADING STRATEGY SETTINGS
# ============================================================

STRATEGY = {
    # Strategy to use: "EMA_CROSS", "RSI_MEAN_REVERSION", "BREAKOUT", "BB_RSI_SCALP"
    "active_strategy": "BB_RSI_SCALP",

    # ── Symbol Selection ──
    # "AUTO" = fetch all tradeable symbols from broker automatically
    # list  = use a manual list (e.g. ["EURUSD", "GBPUSD"])
    "symbols": "AUTO",

    # Filters applied when symbols = "AUTO"
    # Categories: "forex", "indices", "commodities", "crypto", "stocks"
    "symbol_filters": {
        "categories": ["forex", "indices", "commodities", "crypto", "stocks"],  # Which categories to trade
        "spread_max": 50,                # Skip symbols with spread > 50 points
        "tradeable_only": True,          # Only tradeable symbols
        "exclude_contains": ["_"],       # Exclude symbols containing these strings
    },

    # Timeframes
    "entry_timeframe": "M5",             # M5 per scalping
    "trend_timeframe": "H1",             # Trend filter
    "htf_timeframe": "H4",              # Higher timeframe confirmation

    # EMA Cross settings
    "ema_fast": 9,
    "ema_slow": 21,
    "ema_trend": 200,                    # 200 EMA for trend direction

    # RSI settings
    "rsi_period": 14,
    "rsi_oversold": 30,
    "rsi_overbought": 70,

    # Breakout settings
    "breakout_lookback": 20,             # Bars for high/low range
    "breakout_atr_multiplier": 1.5,      # ATR filter for valid breakout

    # ATR for SL/TP
    "atr_period": 14,
    "sl_atr_multiplier": 1.5,           # SL = 1.5x ATR
    "tp_atr_multiplier": 3.0,           # TP = 3.0x ATR (2:1 RR minimum)

    # ── BB + RSI Scalping Strategy Settings ──
    "bb_rsi": {
        "bb_period": 40,                 # Bollinger Bands period
        "bb_std_dev": 2.0,               # BB standard deviations
        "rsi_period": 5,                 # RSI period (fast, reactive)
        "rsi_oversold": 30,              # RSI oversold threshold
        "rsi_overbought": 70,            # RSI overbought threshold
        "adx_period": 14,                # ADX period for range filter
        "adx_max": 25,                   # Max ADX (above = trending, skip)
        "swing_lookback": 10,            # Bars to find swing low/high for SL
        "sl_buffer_pips": 3,             # Extra pips beyond swing for SL
        "entry_timeframe": "M5",         # Scalping timeframe
        # "AUTO" = same as main symbols; or provide a manual list
        "symbols": "AUTO",
        # Filters for BB_RSI_SCALP when symbols = "AUTO"
        # (scalping requires low spread, so we override spread_max)
        "symbol_filters": {
            "categories": ["forex"],     # Scalping solo su forex (spread bassi)
            "spread_max": 25,            # Max spread più stretto per scalping
            "tradeable_only": True,
            "exclude_contains": ["_"],
        },
    },
}

# ============================================================
# TRADING SESSIONS (UTC)
# ============================================================

SESSIONS = {
    "london_open": {"start": "07:00", "end": "11:00"},
    "new_york_open": {"start": "12:00", "end": "16:00"},
    "overlap": {"start": "12:00", "end": "15:00"},
    # Set which sessions to trade
    "active_sessions": ["london_open", "new_york_open"],
}

# ============================================================
# SCHEDULER
# ============================================================

# How often the bot checks for signals (seconds)
SCAN_INTERVAL_SECONDS = 30

# Log level: "DEBUG", "INFO", "WARNING"
LOG_LEVEL = "INFO"
