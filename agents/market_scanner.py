"""
Market Scanner Agent — analizza tutti i simboli e determina il regime di mercato.

Per ogni simbolo:
- Volatilità attuale (ATR normalizzato)
- Trend strength (ADX)
- Regime: trending_strong | trending_weak | ranging | volatile | quiet
- Spread medio attuale (filtro per prop)
- Best timeframes consigliati per quel regime

Output: mappa che lo StrategyResearcher userà per fare matching strategia↔simbolo↔TF
"""
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger

try:
    import MetaTrader5 as mt5
    import pandas as pd
    import numpy as np
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False


# Watchlist completa di simboli da scansionare
DEFAULT_WATCHLIST = {
    # Major forex
    "EURUSD": "forex_major",
    "GBPUSD": "forex_major",
    "USDJPY": "forex_major",
    "USDCHF": "forex_major",
    "AUDUSD": "forex_major",
    "USDCAD": "forex_major",
    "NZDUSD": "forex_major",
    
    # Crosses
    "EURJPY": "forex_cross",
    "GBPJPY": "forex_cross",
    "EURGBP": "forex_cross",
    "AUDNZD": "forex_cross",
    "EURCHF": "forex_cross",
    
    # Metals
    "XAUUSD": "metal",
    "XAGUSD": "metal",
    
    # Indices
    "US30": "index",
    "US100": "index",
    "US500": "index",
    "GER40": "index",
    
    # Crypto (se broker supporta)
    "BTCUSD": "crypto",
    "ETHUSD": "crypto",
}

# Timeframes da valutare per ogni simbolo
TIMEFRAMES_TO_SCAN = ["M15", "H1", "H4", "D1"]


@dataclass
class MarketSnapshot:
    """Snapshot completo di un simbolo in un momento."""
    symbol: str
    asset_class: str
    timeframe: str
    
    # Volatilità
    atr_pips: float                    # ATR in pips
    atr_normalized: float              # ATR / prezzo (volatilità relativa %)
    volatility_regime: str             # low | normal | high | extreme
    
    # Trend
    adx: float                         # ADX attuale
    trend_strength: str                # strong | weak | none
    trend_direction: str               # up | down | sideways
    
    # Price action
    range_pct: float                   # (high - low) / close ultimi 20 periodi
    momentum: float                    # % change ultimi 10 periodi
    
    # Regime composito
    market_regime: str                 # trending_strong | trending_weak | ranging | volatile | quiet
    
    # Trading conditions
    avg_spread_pips: float
    spread_quality: str                # excellent | good | acceptable | poor
    
    # Score per quel TF
    tradability_score: float           # 0-100 quanto è tradabile su questo TF


@dataclass
class SymbolAnalysis:
    """Analisi multi-timeframe di un simbolo."""
    symbol: str
    asset_class: str
    snapshots: dict[str, MarketSnapshot]   # tf -> snapshot
    best_timeframe: str
    best_strategies: list[str]              # strategie più adatte
    overall_score: float                    # 0-100
    notes: list[str] = field(default_factory=list)


class MarketScanner:
    """Scanner che analizza tutti i mercati e suggerisce abbinamenti strategia/simbolo/TF."""
    
    # Mapping regime → strategie ottimali
    REGIME_TO_STRATEGIES = {
        "trending_strong": ["trend_following", "momentum", "breakout"],
        "trending_weak":   ["trend_following", "swing"],
        "ranging":         ["mean_reversion", "range_trading"],
        "volatile":        ["breakout", "ict_smc", "news_trading"],
        "quiet":           ["range_trading"],
    }
    
    # Regime preferito per timeframe
    REGIME_TF_AFFINITY = {
        "M15": ["volatile", "trending_strong"],
        "H1":  ["trending_strong", "trending_weak", "volatile"],
        "H4":  ["trending_weak", "ranging", "trending_strong"],
        "D1":  ["trending_weak", "ranging"],
    }
    
    def __init__(
        self,
        mt5_path: Optional[str] = None,
        mt5_login: Optional[int] = None,
        mt5_password: Optional[str] = None,
        mt5_server: Optional[str] = None,
    ):
        """
        Se passa credenziali MT5, inizializza la connessione.
        Altrimenti assume che MT5 sia già inizializzato altrove.
        """
        self.mt5_ready = False
        
        if not MT5_AVAILABLE:
            logger.warning("⚠️  MetaTrader5 module not installed — scanner in stub mode")
            return
        
        # Tenta init
        if mt5_path:
            try:
                logger.info(f"📡 MT5 connecting: server={mt5_server} login={mt5_login}")
                init_kwargs = {"path": mt5_path}
                if mt5_login:
                    init_kwargs["login"] = int(mt5_login)
                if mt5_password:
                    init_kwargs["password"] = str(mt5_password)
                if mt5_server:
                    init_kwargs["server"] = str(mt5_server)
                
                if mt5.initialize(**init_kwargs):
                    self.mt5_ready = True
                    info = mt5.account_info()
                    if info:
                        logger.success(
                            f"✅ MT5 connected: {info.server} | "
                            f"login {info.login} | balance ${info.balance:.2f}"
                        )
                    else:
                        logger.warning("⚠️  MT5 initialized but account_info() returned None")
                else:
                    err = mt5.last_error()
                    logger.error(
                        f"❌ MT5 init failed: {err}\n"
                        f"   Verifica:\n"
                        f"   1. MT5 è installato in: {mt5_path}\n"
                        f"   2. Login: {mt5_login} (numerico)\n"
                        f"   3. Server: {mt5_server} (esatto come nel terminal)\n"
                        f"   4. Apri MT5 manualmente almeno una volta per accettare l'EULA"
                    )
            except Exception as e:
                logger.error(f"❌ MT5 init exception: {e}")
        else:
            # Assume già inizializzato — verifica
            try:
                if mt5.terminal_info() is not None:
                    self.mt5_ready = True
                    logger.info("📡 MT5 already initialized — using existing connection")
                else:
                    logger.warning("⚠️  MT5 path non specificato in config e MT5 non già inizializzato")
            except Exception:
                logger.warning("MT5 not initialized and no credentials provided")
    
    def scan_all(
        self,
        watchlist: Optional[dict] = None,
        timeframes: Optional[list] = None,
        history_periods: int = 200,
    ) -> dict[str, SymbolAnalysis]:
        """Scansiona tutti i simboli e ritorna analisi completa."""
        watchlist = watchlist or DEFAULT_WATCHLIST
        timeframes = timeframes or TIMEFRAMES_TO_SCAN
        
        logger.info(f"🔍 Market scan: {len(watchlist)} symbols × {len(timeframes)} TFs")
        
        results = {}
        for symbol, asset_class in watchlist.items():
            try:
                analysis = self.analyze_symbol(symbol, asset_class, timeframes, history_periods)
                if analysis:
                    results[symbol] = analysis
                    logger.info(
                        f"  {symbol:8s} → {analysis.best_timeframe} | "
                        f"score {analysis.overall_score:.0f} | "
                        f"strategies: {', '.join(analysis.best_strategies[:2])}"
                    )
            except Exception as e:
                logger.warning(f"  {symbol}: scan failed ({e})")
                continue
        
        return results
    
    def analyze_symbol(
        self,
        symbol: str,
        asset_class: str,
        timeframes: list[str],
        history_periods: int = 200,
    ) -> Optional[SymbolAnalysis]:
        """Analisi multi-timeframe di un simbolo."""
        if not self.mt5_ready:
            return self._stub_analysis(symbol, asset_class, timeframes)
        
        # Verifica che il simbolo esista
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info or not mt5.symbol_select(symbol, True):
            return None
        
        snapshots = {}
        for tf_name in timeframes:
            tf_const = self._tf_to_mt5(tf_name)
            if tf_const is None:
                continue
            
            # Scarica candele
            rates = mt5.copy_rates_from(symbol, tf_const, datetime.now(), history_periods)
            if rates is None or len(rates) < 50:
                continue
            
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            
            snapshot = self._analyze_timeframe(symbol, asset_class, tf_name, df, symbol_info)
            if snapshot:
                snapshots[tf_name] = snapshot
        
        if not snapshots:
            return None
        
        # Decidi best TF e best strategies
        best_tf = max(snapshots.keys(), key=lambda tf: snapshots[tf].tradability_score)
        best_snapshot = snapshots[best_tf]
        best_strategies = self.REGIME_TO_STRATEGIES.get(best_snapshot.market_regime, ["trend_following"])
        
        overall = sum(s.tradability_score for s in snapshots.values()) / len(snapshots)
        
        notes = []
        if best_snapshot.spread_quality == "poor":
            notes.append(f"⚠️ Spread alto ({best_snapshot.avg_spread_pips:.1f} pips) — costo trading elevato")
        if best_snapshot.volatility_regime == "extreme":
            notes.append(f"⚠️ Volatilità estrema — rischio elevato di slippage")
        if best_snapshot.adx < 15:
            notes.append("📊 Mercato in fase di accumulazione — strategie direzionali rischiose")
        
        return SymbolAnalysis(
            symbol=symbol,
            asset_class=asset_class,
            snapshots=snapshots,
            best_timeframe=best_tf,
            best_strategies=best_strategies,
            overall_score=round(overall, 1),
            notes=notes,
        )
    
    def _analyze_timeframe(
        self,
        symbol: str,
        asset_class: str,
        tf_name: str,
        df: pd.DataFrame,
        symbol_info,
    ) -> Optional[MarketSnapshot]:
        """Calcola tutti gli indicatori per un singolo timeframe."""
        try:
            # ATR
            high_low = df['high'] - df['low']
            high_close = (df['high'] - df['close'].shift()).abs()
            low_close = (df['low'] - df['close'].shift()).abs()
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            atr = tr.rolling(14).mean().iloc[-1]
            
            # Pip size
            pip_size = symbol_info.point * (10 if symbol_info.digits in [3, 5] else 1)
            atr_pips = atr / pip_size
            atr_normalized = (atr / df['close'].iloc[-1]) * 100
            
            # Volatility regime (basato su ATR normalizzato)
            if atr_normalized < 0.3:
                vol_regime = "low"
            elif atr_normalized < 0.8:
                vol_regime = "normal"
            elif atr_normalized < 1.5:
                vol_regime = "high"
            else:
                vol_regime = "extreme"
            
            # ADX (semplificato)
            adx = self._calc_adx(df)
            
            if adx > 30:
                trend_str = "strong"
            elif adx > 20:
                trend_str = "weak"
            else:
                trend_str = "none"
            
            # Trend direction (EMA 50 slope)
            ema50 = df['close'].ewm(span=50, adjust=False).mean()
            slope = (ema50.iloc[-1] - ema50.iloc[-20]) / ema50.iloc[-20] * 100
            if slope > 0.5:
                trend_dir = "up"
            elif slope < -0.5:
                trend_dir = "down"
            else:
                trend_dir = "sideways"
            
            # Range
            range_pct = ((df['high'].iloc[-20:].max() - df['low'].iloc[-20:].min()) / df['close'].iloc[-1]) * 100
            momentum = ((df['close'].iloc[-1] - df['close'].iloc[-10]) / df['close'].iloc[-10]) * 100
            
            # Composite regime
            if trend_str == "strong":
                regime = "trending_strong"
            elif trend_str == "weak" and trend_dir != "sideways":
                regime = "trending_weak"
            elif vol_regime in ["high", "extreme"]:
                regime = "volatile"
            elif vol_regime == "low" and trend_str == "none":
                regime = "quiet"
            else:
                regime = "ranging"
            
            # Spread
            avg_spread = symbol_info.spread * pip_size / pip_size  # in pips
            if asset_class == "metal" and avg_spread < 30:
                spread_q = "excellent"
            elif asset_class == "forex_major" and avg_spread < 1.5:
                spread_q = "excellent"
            elif avg_spread < 3.0:
                spread_q = "good"
            elif avg_spread < 5.0:
                spread_q = "acceptable"
            else:
                spread_q = "poor"
            
            # Tradability score
            score = 50.0
            if regime == "trending_strong": score += 25
            elif regime == "trending_weak": score += 15
            elif regime == "ranging": score += 10
            elif regime == "volatile": score += 5
            elif regime == "quiet": score -= 20
            
            if spread_q == "excellent": score += 15
            elif spread_q == "good": score += 5
            elif spread_q == "poor": score -= 20
            
            if vol_regime == "extreme": score -= 15
            elif vol_regime == "low": score -= 10
            
            # Bonus se TF e regime sono affini
            if regime in self.REGIME_TF_AFFINITY.get(tf_name, []):
                score += 10
            
            score = max(0, min(100, score))
            
            return MarketSnapshot(
                symbol=symbol,
                asset_class=asset_class,
                timeframe=tf_name,
                atr_pips=round(atr_pips, 1),
                atr_normalized=round(atr_normalized, 3),
                volatility_regime=vol_regime,
                adx=round(adx, 1),
                trend_strength=trend_str,
                trend_direction=trend_dir,
                range_pct=round(range_pct, 2),
                momentum=round(momentum, 2),
                market_regime=regime,
                avg_spread_pips=round(avg_spread, 1),
                spread_quality=spread_q,
                tradability_score=round(score, 1),
            )
        except Exception as e:
            logger.debug(f"Failed analyzing {symbol} {tf_name}: {e}")
            return None
    
    def _calc_adx(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calcolo ADX semplificato."""
        try:
            high = df['high']
            low = df['low']
            close = df['close']
            
            up_move = high.diff()
            down_move = -low.diff()
            
            plus_dm = ((up_move > down_move) & (up_move > 0)) * up_move
            minus_dm = ((down_move > up_move) & (down_move > 0)) * down_move
            
            tr = pd.concat([
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs()
            ], axis=1).max(axis=1)
            
            atr = tr.rolling(period).mean()
            plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
            minus_di = 100 * (minus_dm.rolling(period).mean() / atr)
            
            dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
            adx = dx.rolling(period).mean().iloc[-1]
            return float(adx) if not pd.isna(adx) else 0.0
        except Exception:
            return 0.0
    
    def _tf_to_mt5(self, tf_name: str):
        """Converte stringa TF in costante MT5."""
        if not MT5_AVAILABLE:
            return None
        mapping = {
            "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1, "W1": mt5.TIMEFRAME_W1,
        }
        return mapping.get(tf_name)
    
    def _stub_analysis(self, symbol, asset_class, timeframes) -> SymbolAnalysis:
        """Analisi fittizia per test senza MT5."""
        snaps = {}
        for tf in timeframes:
            snaps[tf] = MarketSnapshot(
                symbol=symbol, asset_class=asset_class, timeframe=tf,
                atr_pips=15.0, atr_normalized=0.6, volatility_regime="normal",
                adx=25.0, trend_strength="weak", trend_direction="up",
                range_pct=2.5, momentum=0.8,
                market_regime="trending_weak",
                avg_spread_pips=1.2, spread_quality="good",
                tradability_score=70.0,
            )
        return SymbolAnalysis(
            symbol=symbol, asset_class=asset_class,
            snapshots=snaps, best_timeframe="H1",
            best_strategies=["trend_following", "swing"],
            overall_score=70.0,
        )
    
    def get_market_summary(self, scan_results: dict[str, SymbolAnalysis]) -> str:
        """Riassunto leggibile del mercato per il prompt del Researcher."""
        lines = ["MARKET CONDITIONS SNAPSHOT (real-time):\n"]
        
        # Top opportunità
        sorted_syms = sorted(scan_results.values(), key=lambda x: x.overall_score, reverse=True)
        
        lines.append("🏆 TOP OPPORTUNITIES (best tradability):")
        for analysis in sorted_syms[:5]:
            best = analysis.snapshots[analysis.best_timeframe]
            lines.append(
                f"  • {analysis.symbol} ({analysis.asset_class}) on {analysis.best_timeframe}: "
                f"{best.market_regime} regime, ATR {best.atr_pips} pips, ADX {best.adx}, "
                f"spread {best.avg_spread_pips}p → suggested strategies: {', '.join(analysis.best_strategies[:2])}"
            )
        
        # Avoid list
        avoid = [a for a in sorted_syms if a.overall_score < 40]
        if avoid:
            lines.append("\n⛔ AVOID (poor conditions):")
            for a in avoid[:3]:
                best = a.snapshots[a.best_timeframe]
                lines.append(f"  • {a.symbol}: {best.market_regime}, score {a.overall_score}")
        
        return "\n".join(lines)
