"""
Spec Generator Agent — converte ipotesi strategia in JSON spec per il framework MQL5.

A differenza del codegen, qui Claude NON scrive codice: scrive solo parametri
che il framework PropAgentFramework.mq5 sa interpretare.

Vantaggi:
- Niente compile errors (il framework è pre-testato)
- Costo API ridotto (token ~10x in meno vs codegen)
- Output deterministico

Limite: copre solo strategie "standard". Per idee custom complesse
ricadiamo nel CodeGen tradizionale.
"""
import json
from pathlib import Path
from loguru import logger
from typing import Optional

from agents.api_client import make_client, call_with_retry


# Tipi di strategia supportati dal framework MQL5 generico
SUPPORTED_STRATEGY_TYPES = {
    "ema_cross": {
        "enum_value": 0,
        "description": "EMA fast/slow crossover",
        "params_required": ["EMAFast", "EMASlow"],
        "params_default": {"EMAFast": 12, "EMASlow": 26},
    },
    "rsi_reversion": {
        "enum_value": 1,
        "description": "RSI oversold/overbought mean reversion",
        "params_required": ["RSIPeriod", "RSIOversold", "RSIOverbought"],
        "params_default": {"RSIPeriod": 14, "RSIOversold": 30, "RSIOverbought": 70},
    },
    "bollinger_revert": {
        "enum_value": 2,
        "description": "Bollinger band mean reversion",
        "params_required": ["BBPeriod", "BBDeviation"],
        "params_default": {"BBPeriod": 20, "BBDeviation": 2.0},
    },
    "donchian_breakout": {
        "enum_value": 3,
        "description": "Donchian channel breakout (turtle-style)",
        "params_required": ["DonchianPeriod"],
        "params_default": {"DonchianPeriod": 20},
    },
    "atr_breakout": {
        "enum_value": 4,
        "description": "Volatility breakout based on ATR",
        "params_required": [],
        "params_default": {},
    },
    "ma_pullback": {
        "enum_value": 5,
        "description": "Pullback to MA in established trend",
        "params_required": ["PullbackMA", "PullbackTrendMA"],
        "params_default": {"PullbackMA": 50, "PullbackTrendMA": 200},
    },
    "macd_momentum": {
        "enum_value": 6,
        "description": "MACD-based momentum signals",
        "params_required": ["MACDFast", "MACDSlow", "MACDSignal"],
        "params_default": {"MACDFast": 12, "MACDSlow": 26, "MACDSignal": 9},
    },
}


SYSTEM_PROMPT = """Sei un quantitative analyst che configura strategie di trading via parametri.

L'utente ti propone un'ipotesi di strategia. Tu devi decidere:

1. **Si può implementare con il framework standard?** (cioè è una strategia "classica")
   → Output MODE: "spec" + parametri JSON

2. **Richiede logica custom complessa?** (multi-condizione, indicatori esotici, logica complessa)
   → Output MODE: "custom" + spiegazione del perché

STRATEGIE SUPPORTATE DAL FRAMEWORK:

- **ema_cross**: EMA fast/slow crossover
- **rsi_reversion**: RSI oversold/overbought mean reversion
- **bollinger_revert**: Bollinger band mean reversion
- **donchian_breakout**: Donchian channel breakout
- **atr_breakout**: Volatility breakout su candle ATR
- **ma_pullback**: Pullback a MA in trend (con MA + trend MA)
- **macd_momentum**: MACD signal line crossover con filtro

OGNI strategia ha SEMPRE:
- Risk per trade in %
- Stop loss = ATR * multiplier
- Take profit = ATR * multiplier
- Filtri: max spread, trading hours, max DD daily/total
- Trailing stop opzionale

OUTPUT JSON FORMAT (MODE: spec):

{
  "mode": "spec",
  "name": "Nome breve",
  "strategy_type": "ema_cross",        // uno tra quelli sopra
  "selected_symbol": "EURUSD",
  "selected_timeframe": "H1",
  "selection_reason": "Perché questa combinazione",
  "hypothesis": "Spiegazione discorsiva 2-3 frasi",
  
  "framework_params": {
    "InpStrategy": 0,                  // enum (vedi mapping)
    "InpRiskPercent": 1.0,
    "InpMaxDailyDDPct": 4.5,
    "InpMaxTotalDDPct": 9.0,
    "InpMaxConcurrentTrades": 2,
    "InpMaxSpreadPips": 2.0,
    "InpATRPeriod": 14,
    "InpSLAtrMult": 1.5,
    "InpTPAtrMult": 3.0,
    "InpUseTrailingStop": false,
    "InpTrailingAtrMult": 1.0,
    "InpStartHour": 7,
    "InpEndHour": 20,
    "InpFridayClose": true,
    
    // Specifici per la strategia scelta:
    "InpEMAFast": 12,
    "InpEMASlow": 26,
    // (oppure InpRSIPeriod, InpBBPeriod, ecc. — solo quelli rilevanti)
    
    "InpMagicNumber": 12345
  },
  
  "expected_behavior": "Win rate atteso, R:R, num trades/giorno, max DD"
}

OUTPUT JSON FORMAT (MODE: custom):

{
  "mode": "custom",
  "name": "Nome breve",
  "strategy_type": "ict_smc | hedging | multi_indicator | other",
  "selected_symbol": "...",
  "selected_timeframe": "...",
  "selection_reason": "Perché questa combinazione",
  "hypothesis": "Descrizione completa",
  "custom_required_reason": "Perché il framework standard non basta — sii specifico",
  
  "entry_logic": {
    "long_conditions": [...],
    "short_conditions": [...]
  },
  "exit_logic": {
    "stop_loss": "...",
    "take_profit": "...",
    "trailing": "..."
  },
  "indicators": [...],
  "parameters": {...},
  "expected_behavior": "..."
}

REGOLE PER DECIDERE LA MODE:

✅ USA "spec" se:
- L'idea è una variante di una delle 7 strategie standard
- Si può esprimere con i parametri previsti
- I segnali entry/exit derivano da indicatori standard

⚠️ USA "custom" SOLO se:
- L'idea richiede logiche multi-step non lineari (es: pattern recognition complesso)
- Usa indicatori non nel framework (es: VWAP, ICT order blocks, market profile)
- Combina segnali in modo non standard (es: confluence di 4+ filtri)
- È esplicitamente richiesto dall'utente con dettagli specifici

In dubbio → usa "spec" e adatta i parametri. Custom è L'eccezione.

Mapping enum InpStrategy:
- ema_cross         → 0
- rsi_reversion     → 1
- bollinger_revert  → 2
- donchian_breakout → 3
- atr_breakout      → 4
- ma_pullback       → 5
- macd_momentum     → 6

Output: SOLO JSON, nessun testo prima o dopo."""


class SpecGenerator:
    """Genera spec JSON per il framework MQL5 generico."""
    
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self.client = make_client(api_key, timeout_seconds=90)
        self.model = model
    
    def generate(
        self,
        profile: dict,
        prop_firm: str,
        prop_phase: str,
        market_summary: str,
        previous_strategies: Optional[list] = None,
        force_mode: Optional[str] = None,        # "spec" | "custom" | None (auto)
    ) -> dict:
        """Decide tra spec/custom e genera la struttura.
        
        Returns dict con campo "mode" che dirà come procedere.
        """
        from prop_rules import get_rules
        rules = get_rules(prop_firm, prop_phase)
        
        prev_context = ""
        forbidden_symbols = []
        if previous_strategies:
            recent = previous_strategies[-15:]
            recent_summary = ", ".join([
                f"{s.get('name', '?')} on {s.get('symbol', '?')} {s.get('timeframe', '?')}"
                for s in recent
            ])
            symbol_count = {}
            for s in recent[-5:]:
                sym = s.get('symbol')
                if sym:
                    symbol_count[sym] = symbol_count.get(sym, 0) + 1
            forbidden_symbols = [s for s, c in symbol_count.items() if c >= 2]
            
            prev_context = f"\n\n⚠️ STRATEGIE GIÀ GENERATE (NON duplicare):\n{recent_summary}\n"
            if forbidden_symbols:
                prev_context += f"\n🚫 SIMBOLI VIETATI: {', '.join(forbidden_symbols)} — usa altri.\n"
        
        force_text = ""
        if force_mode:
            force_text = f"\n⭐ FORZA MODE: {force_mode}\n"
        
        user_msg = f"""Genera una nuova strategia per le condizioni di mercato attuali.

PROFILO: {profile['name']}
{profile['description']}
- Risk per trade: {profile['risk']['per_trade_pct']}%
- Max daily DD: {profile['risk']['max_daily_pct']}%
- Tipi preferiti: {profile['strategy_preferences']['preferred_types']}
- TF preferiti: {profile['strategy_preferences']['preferred_timeframes']}

PROP CONSTRAINTS ({rules.name}):
- Daily DD limit: {rules.max_daily_dd_pct}%
- Total DD limit: {rules.max_total_dd_pct}%
- Target: {rules.profit_target_pct}%

═══════════════════════════════════════
{market_summary}
═══════════════════════════════════════
{prev_context}{force_text}

Decidi mode (spec o custom) + abbinamento simbolo/TF/strategia.
Output: SOLO JSON."""
        
        logger.info(f"📋 SpecGenerator generating ({profile['name']})")
        
        text = call_with_retry(
            self.client,
            model=self.model,
            max_tokens=2500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        ).strip()
        
        # Parse robusto
        text = self._extract_json(text)
        try:
            spec = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON parse failed: {e}")
            logger.debug(f"Raw: {text[:500]}")
            raise
        
        mode = spec.get("mode", "spec")
        symbol = spec.get("selected_symbol", "?")
        tf = spec.get("selected_timeframe", "?")
        
        logger.success(
            f"✅ {mode.upper()}-mode: {spec.get('name', '?')} "
            f"({spec.get('strategy_type', '?')}) → {symbol} {tf}"
        )
        if mode == "custom":
            logger.info(f"   Custom reason: {spec.get('custom_required_reason', '?')}")
        
        return spec
    
    def build_set_file(self, spec: dict, output_path: Path) -> Path:
        """Genera un .set file MT5 dai framework_params della spec.
        
        Il file .set viene caricato nel Strategy Tester per parametrizzare
        l'EA framework senza ricompilarlo.
        """
        params = spec.get("framework_params", {})
        if not params:
            raise ValueError("Spec ha mode='spec' ma manca framework_params")
        
        lines = []
        for key, val in params.items():
            # Format MT5 .set: nome=valore||...
            if isinstance(val, bool):
                val_str = "true" if val else "false"
            elif isinstance(val, float):
                val_str = f"{val:.4f}"
            else:
                val_str = str(val)
            lines.append(f"{key}={val_str}")
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"📝 Set file saved: {output_path}")
        return output_path
    
    @staticmethod
    def _extract_json(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            start = 1
            end = len(lines)
            for i in range(len(lines) - 1, 0, -1):
                if lines[i].strip().startswith("```"):
                    end = i
                    break
            text = "\n".join(lines[start:end])
        
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last != -1 and last > first:
            text = text[first:last + 1]
        return text.strip()
