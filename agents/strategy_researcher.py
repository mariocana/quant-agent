"""Strategy Researcher Agent — genera nuove ipotesi di strategia via Claude."""
import json
from anthropic import Anthropic
from loguru import logger
from typing import Optional

from prop_rules import get_rules


SYSTEM_PROMPT = """Sei un quantitative strategist esperto in prop firm trading.

Il tuo compito: generare ipotesi di strategie di trading NUOVE e DIFFERENZIATE per Expert Advisor MQL5.

Per ogni richiesta, ritorna SOLO un JSON valido con questa struttura:

{
  "name": "Nome breve identificativo",
  "strategy_type": "trend_following|breakout|mean_reversion|momentum|swing|ict_smc",
  "hypothesis": "Descrizione discorsiva dell'ipotesi di mercato in 2-3 frasi",
  "entry_logic": {
    "description": "Quando entrare in long/short",
    "long_conditions": ["condizione 1", "condizione 2"],
    "short_conditions": ["condizione 1", "condizione 2"]
  },
  "exit_logic": {
    "stop_loss": "Come è calcolato lo SL (es. 1.5x ATR(14))",
    "take_profit": "Come è calcolato il TP",
    "trailing": "Logica del trailing stop"
  },
  "indicators": [
    {"name": "EMA", "period": 20},
    {"name": "ATR", "period": 14}
  ],
  "parameters": {
    "param_nome": {"type": "int|double|bool", "default": 14, "min": 5, "max": 50, "description": "..."}
  },
  "expected_behavior": "Descrizione del comportamento atteso (R:R, win rate stimato, drawdown atteso)"
}

REGOLE:
- Niente martingale, grid, scalping HFT (vietati dalle prop)
- Hard stop loss SEMPRE definito
- Strategie replicabili in MQL5
- Differenziati: ogni chiamata deve generare qualcosa di diverso dalle precedenti
"""


class StrategyResearcher:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5"):
        self.client = Anthropic(api_key=api_key)
        self.model = model
    
    def generate(
        self,
        profile: dict,
        symbol: str,
        timeframe: str,
        prop_firm: str,
        prop_phase: str,
        previous_strategies: Optional[list] = None,
    ) -> dict:
        """Genera una nuova ipotesi di strategia."""
        rules = get_rules(prop_firm, prop_phase)
        
        # Costruisci context per evitare duplicati
        prev_context = ""
        if previous_strategies:
            recent_names = [s.get("name", "?") for s in previous_strategies[-10:]]
            recent_types = [s.get("strategy_type", "?") for s in previous_strategies[-10:]]
            prev_context = f"""
STRATEGIE GIÀ GENERATE RECENTEMENTE (NON DUPLICARE):
Nomi: {', '.join(recent_names)}
Tipi: {', '.join(recent_types)}
"""
        
        user_msg = f"""Genera una NUOVA ipotesi di strategia con questi vincoli:

PROFILO TRADING: {profile['name']}
{profile['description']}
- Risk per trade: {profile['risk']['per_trade_pct']}%
- Max daily: {profile['risk']['max_daily_pct']}%
- Tipi preferiti: {profile['strategy_preferences']['preferred_types']}
- Tipi da evitare: {profile['strategy_preferences']['avoid_types']}
- Timeframe preferiti: {profile['strategy_preferences']['preferred_timeframes']}

MERCATO:
- Simbolo: {symbol}
- Timeframe: {timeframe}

PROP CONSTRAINTS ({rules.name}):
- Max daily DD: {rules.max_daily_dd_pct}%
- Max total DD: {rules.max_total_dd_pct}%
- Target: {rules.profit_target_pct}%
- News block: {rules.news_block_minutes} min
- Hedging: {'permesso' if rules.hedging_allowed else 'VIETATO'}
{prev_context}

Ritorna SOLO il JSON, nessun testo prima o dopo."""
        
        logger.info(f"🔬 Researcher generating strategy for {symbol} {timeframe} ({profile['name']})")
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        
        text = response.content[0].text.strip()
        
        # Rimuovi eventuali code fence
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        
        try:
            strategy = json.loads(text.strip())
            logger.success(f"✅ Generated: {strategy['name']} ({strategy['strategy_type']})")
            return strategy
        except json.JSONDecodeError as e:
            logger.error(f"❌ Failed to parse JSON: {e}")
            logger.debug(f"Raw response: {text}")
            raise
