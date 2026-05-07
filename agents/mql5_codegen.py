"""MQL5 Code Generator Agent — converte ipotesi strategia in codice .mq5 compilabile."""
from pathlib import Path
from loguru import logger

from prop_rules import get_rules
from agents.api_client import make_client, call_with_retry


SYSTEM_PROMPT = """Sei un esperto sviluppatore MQL5. Converti ipotesi di strategia in Expert Advisor .mq5 production-ready.

REGOLE TASSATIVE per ogni EA:

1. STRUTTURA OBBLIGATORIA:
   - #property strict, #property version, copyright
   - input parameters chiari e documentati
   - OnInit() con check compatibilità
   - OnDeinit() pulito
   - OnTick() con tutti i filtri prima della logica
   - Funzioni helper modulari

2. RISK MANAGEMENT (HARD-CODED):
   - Hard stop loss su OGNI ordine, mai market order senza SL
   - CheckEquityCircuitBreaker(): chiude tutto se daily DD raggiunge soglia safety
   - CheckTotalDrawdown(): blocca apertura nuove posizioni
   - CalculateLotSize(): position sizing basato su RiskPercent

3. FILTRI OBBLIGATORI:
   - IsNewsTime(): blocca trading durante news (array di orari)
   - CheckSpread(): skip se spread > MaxSpread
   - CheckTradingHours(): rispetta sessioni configurate
   - CountServerRequests(): per FTMO, throttle se vicini a 2000/giorno

4. LOGGING:
   - PrintFormat() per ogni decisione importante
   - Log apertura/chiusura/modifica trade
   - Log violazioni evitate (per audit)

5. MAGIC NUMBER:
   - Input parameter unico, default randomizzato

6. COMMENTI:
   - In italiano
   - Spiegano il PERCHÉ, non solo il cosa

OUTPUT: SOLO codice MQL5 raw, senza markdown fence, senza spiegazioni prima/dopo. Deve compilare in MetaEditor 5 senza errori.
"""


class MQL5CodeGenerator:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        # Timeout esteso a 180s — la generazione MQL5 può essere lenta
        self.client = make_client(api_key, timeout_seconds=180)
        self.model = model
    
    def generate(
        self,
        strategy: dict,
        profile: dict,
        prop_firm: str,
        prop_phase: str,
        symbol: str,
        output_dir: Path,
    ) -> tuple[str, Path]:
        """Genera il codice MQL5 e lo salva su disco. Ritorna (code, path)."""
        rules = get_rules(prop_firm, prop_phase)
        
        # Costruisci prompt user dettagliato
        ea_name = f"PA_{profile['name'].split()[0]}_{strategy['name']}_{symbol}".replace(" ", "_")[:50]
        ea_name = "".join(c for c in ea_name if c.isalnum() or c == "_")
        
        user_msg = f"""Genera l'Expert Advisor MQL5 completo per questa strategia:

STRATEGIA:
{strategy.get('hypothesis', '')}

ENTRY LOGIC:
{strategy.get('entry_logic', {})}

EXIT LOGIC:
{strategy.get('exit_logic', {})}

INDICATORI:
{strategy.get('indicators', [])}

PARAMETRI INPUT da esporre:
{strategy.get('parameters', {})}

PROFILO RISK:
- Risk per trade: {profile['risk']['per_trade_pct']}%
- Max daily DD: {profile['risk']['max_daily_pct']}% (sotto soglia prop)
- Max concurrent trades: {profile['risk']['max_concurrent_trades']}
- News block: {profile['filters']['news_block_minutes_before']} min prima/dopo
- Max spread pips: {profile['filters']['max_spread_pips']}
- Min ATR pips: {profile['filters'].get('min_atr_pips', 5)}

PROP CONSTRAINTS HARD-CODED ({rules.name}):
- Daily DD limite prop: {rules.max_daily_dd_pct}% → safety stop a {profile['risk']['max_daily_pct']}%
- Total DD limite prop: {rules.max_total_dd_pct}% → safety stop a {rules.max_total_dd_pct - 1}%
- News restriction prop: {rules.news_block_minutes} min
- Hedging: {'OK intra-account' if rules.hedging_allowed else 'VIETATO'}

NOME EA: {ea_name}
SIMBOLO TARGET: {symbol}

Genera il codice .mq5 completo e compilabile."""
        
        logger.info(f"⚙️  CodeGen producing MQL5 for: {ea_name}")
        
        # call_with_retry ritorna direttamente il testo
        code = call_with_retry(
            self.client,
            model=self.model,
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        ).strip()
        
        # Rimuovi eventuali code fence
        if code.startswith("```"):
            lines = code.split("\n")
            code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        
        # Salva su disco
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / f"{ea_name}.mq5"
        file_path.write_text(code, encoding="utf-8")
        
        logger.success(f"✅ MQL5 saved: {file_path}")
        return code, file_path
