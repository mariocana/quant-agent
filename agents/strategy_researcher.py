"""Strategy Researcher Agent — genera ipotesi strategia adattate al mercato corrente.

VERSIONE 2: ora riceve uno snapshot del mercato dal MarketScanner e sceglie
autonomamente quale simbolo + timeframe + strategia generare per ottimizzare
l'abbinamento.
"""
import json
from loguru import logger
from typing import Optional

from prop_rules import get_rules
from agents.api_client import make_client, call_with_retry


SYSTEM_PROMPT = """Sei un quantitative strategist esperto in prop firm trading e regime detection.

A differenza di prima, ora ricevi uno SNAPSHOT REALE del mercato. Devi:

1. **SCEGLIERE TU** il miglior abbinamento simbolo + timeframe + tipo strategia
2. Basarti sui regimi di mercato attuali, non su preferenze astratte
3. Evitare simboli/TF con poor tradability
4. Sfruttare le opportunità migliori

Per ogni richiesta, ritorna SOLO un JSON valido con questa struttura:

{
  "selected_symbol": "EURUSD",
  "selected_timeframe": "H1",
  "selection_reason": "Spiegazione 2-3 frasi del perché hai scelto questa combo (regime, conditions, ecc.)",
  
  "name": "Nome breve identificativo",
  "strategy_type": "trend_following|breakout|mean_reversion|momentum|swing|ict_smc|range_trading",
  "hypothesis": "Descrizione discorsiva ipotesi mercato 2-3 frasi",
  "entry_logic": {
    "description": "Quando entrare in long/short",
    "long_conditions": ["condizione 1", "condizione 2"],
    "short_conditions": ["condizione 1", "condizione 2"]
  },
  "exit_logic": {
    "stop_loss": "Es: 1.5x ATR(14)",
    "take_profit": "Es: 3x ATR(14)",
    "trailing": "Logica trailing"
  },
  "indicators": [
    {"name": "EMA", "period": 20},
    {"name": "ATR", "period": 14}
  ],
  "parameters": {
    "param_nome": {"type": "int|double|bool", "default": 14, "min": 5, "max": 50, "description": "..."}
  },
  "expected_behavior": "R:R atteso, win rate stimato, drawdown atteso, num trades/giorno"
}

REGOLE:
- Niente martingale, grid, scalping HFT (vietati dalle prop)
- Hard stop loss SEMPRE definito
- La strategia DEVE essere coerente col regime di mercato del simbolo scelto
- Se "trending_strong" → trend_following o breakout
- Se "ranging" → mean_reversion
- Se "volatile" → breakout o ICT
- Se nessun simbolo è interessante, scegli il meno peggio MA segnalalo nel selection_reason
"""


class StrategyResearcher:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self.client = make_client(api_key, timeout_seconds=120)
        self.model = model
    
    def generate(
        self,
        profile: dict,
        prop_firm: str,
        prop_phase: str,
        market_summary: str,                  # NUOVO: snapshot mercato dal scanner
        previous_strategies: Optional[list] = None,
        force_strategy_type: Optional[str] = None,
    ) -> dict:
        """Genera nuova ipotesi strategia adattata al mercato corrente."""
        rules = get_rules(prop_firm, prop_phase)
        
        prev_context = ""
        forbidden_symbols = []
        if previous_strategies:
            recent = previous_strategies[-15:]
            recent_summary = ", ".join([
                f"{s.get('name', '?')} on {s.get('symbol', '?')} {s.get('timeframe', '?')}"
                for s in recent
            ])
            # Estrai i simboli più usati di recente per forzare diversità
            symbol_count = {}
            for s in recent[-5:]:
                sym = s.get('symbol')
                if sym:
                    symbol_count[sym] = symbol_count.get(sym, 0) + 1
            # Se un simbolo è stato usato 2+ volte negli ultimi 5, vietalo
            forbidden_symbols = [s for s, c in symbol_count.items() if c >= 2]
            
            prev_context = f"\n\n⚠️ STRATEGIE GIÀ GENERATE (NON duplicare nome, simbolo né tipo):\n{recent_summary}\n"
            if forbidden_symbols:
                prev_context += f"\n🚫 SIMBOLI VIETATI in questa iterazione (usati troppo spesso): {', '.join(forbidden_symbols)}\n"
                prev_context += "DEVI scegliere un simbolo diverso anche se non è il top score.\n"
        
        force_text = ""
        if force_strategy_type:
            force_text = f"\nFORZA tipo strategia: {force_strategy_type}\n"
        
        user_msg = f"""Genera una NUOVA strategia ottimale per le condizioni di mercato CORRENTI.

PROFILO TRADING: {profile['name']}
{profile['description']}
- Risk per trade: {profile['risk']['per_trade_pct']}%
- Max daily DD: {profile['risk']['max_daily_pct']}%
- Tipi strategia preferiti: {profile['strategy_preferences']['preferred_types']}
- Tipi da evitare: {profile['strategy_preferences']['avoid_types']}
- Timeframes preferiti per questo profilo: {profile['strategy_preferences']['preferred_timeframes']}

PROP CONSTRAINTS ({rules.name}):
- Max daily DD: {rules.max_daily_dd_pct}%
- Max total DD: {rules.max_total_dd_pct}%
- Target: {rules.profit_target_pct}%
- News block: {rules.news_block_minutes} min
- Hedging: {'permesso' if rules.hedging_allowed else 'VIETATO'}

═══════════════════════════════════════
{market_summary}
═══════════════════════════════════════
{prev_context}{force_text}

Decidi TU il miglior abbinamento simbolo + timeframe + tipo strategia basandoti sulle condizioni reali sopra.
Ritorna SOLO il JSON, nessun testo prima o dopo."""
        
        logger.info(f"🔬 Researcher generating strategy ({profile['name']}) — market-aware")
        
        # max_tokens aumentato per evitare troncamenti del JSON
        text = call_with_retry(
            self.client,
            model=self.model,
            max_tokens=3000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        ).strip()
        
        # Estrai JSON con metodi multipli (robustezza)
        text = self._extract_json_text(text)
        
        # Parsing con auto-fix di errori comuni
        try:
            strategy = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ JSON parse failed at char {e.pos}, attempting auto-fix...")
            fixed = self._try_fix_json(text, e)
            if fixed:
                try:
                    strategy = json.loads(fixed)
                    logger.info("✅ Auto-fix succeeded")
                except json.JSONDecodeError as e2:
                    logger.error(f"❌ Auto-fix failed: {e2}")
                    logger.debug(f"Raw response (first 1000 chars):\n{text[:1000]}")
                    raise e2
            else:
                logger.error(f"❌ Failed to parse JSON: {e}")
                logger.debug(f"Raw response (first 1000 chars):\n{text[:1000]}")
                raise
        
        logger.success(
            f"✅ Generated: {strategy['name']} ({strategy['strategy_type']}) "
            f"→ {strategy.get('selected_symbol', '?')} {strategy.get('selected_timeframe', '?')}"
        )
        logger.debug(f"   Reason: {strategy.get('selection_reason', '?')}")
        return strategy
    
    @staticmethod
    def _extract_json_text(text: str) -> str:
        """Estrae JSON da risposta che potrebbe avere code fence o testo extra."""
        text = text.strip()
        
        # Rimuovi code fence
        if text.startswith("```"):
            # Trova fine del primo fence
            lines = text.split("\n")
            start = 1  # skip fence riga
            end = len(lines)
            for i, line in enumerate(lines[1:], 1):
                if line.strip().startswith("```"):
                    end = i
                    break
            text = "\n".join(lines[start:end])
        
        # Trova { e } esterni
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            text = text[first_brace:last_brace + 1]
        
        return text.strip()
    
    @staticmethod
    def _try_fix_json(text: str, error) -> str | None:
        """Tenta fix automatico di errori JSON comuni:
        - Virgolette mancanti su chiavi
        - Trailing commas
        - Apici singoli al posto di doppi
        """
        import re
        fixed = text
        
        # Fix 1: chiavi senza virgolette ({key: value} -> {"key": value})
        fixed = re.sub(r'(\{|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1 "\2":', fixed)
        
        # Fix 2: trailing comma prima di } o ]
        fixed = re.sub(r',(\s*[\}\]])', r'\1', fixed)
        
        # Fix 3: apici singoli su valori stringa (semplificato, rischioso ma a volte aiuta)
        # NON applicato per default perché può rompere stringhe legittime con apostrofi
        
        return fixed if fixed != text else None
