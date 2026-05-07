"""MQL5 Code Generator Agent — converte ipotesi strategia in codice .mq5 compilabile.

VERSIONE 2:
- Prompt rinforzato con esempio di EA funzionante
- Auto-fix iterativo: se compile fallisce, manda errori a Claude e riprova
"""
from pathlib import Path
from loguru import logger

from prop_rules import get_rules
from agents.api_client import make_client, call_with_retry


# Esempio di EA MT5 minimale e funzionante che Claude usa come reference
EXAMPLE_MQL5 = '''//+------------------------------------------------------------------+
//|                                          EsempioFunzionante.mq5  |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property copyright "Esempio"

#include <Trade\\Trade.mqh>

//--- input
input double InpRisk        = 1.0;
input int    InpEMAFast     = 12;
input int    InpEMASlow     = 26;
input int    InpATRPeriod   = 14;
input long   InpMagic       = 12345;

//--- globals
CTrade   trade;
int      handleEMAFast;
int      handleEMASlow;
int      handleATR;
double   g_StartBalance;

//+------------------------------------------------------------------+
int OnInit()
{
    trade.SetExpertMagicNumber(InpMagic);
    trade.SetMarginMode();
    trade.SetTypeFillingBySymbol(_Symbol);
    
    handleEMAFast = iMA(_Symbol, _Period, InpEMAFast, 0, MODE_EMA, PRICE_CLOSE);
    handleEMASlow = iMA(_Symbol, _Period, InpEMASlow, 0, MODE_EMA, PRICE_CLOSE);
    handleATR     = iATR(_Symbol, _Period, InpATRPeriod);
    
    if(handleEMAFast == INVALID_HANDLE || handleEMASlow == INVALID_HANDLE || handleATR == INVALID_HANDLE) {
        Print("Failed to create indicator handles");
        return INIT_FAILED;
    }
    
    g_StartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
    return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
    IndicatorRelease(handleEMAFast);
    IndicatorRelease(handleEMASlow);
    IndicatorRelease(handleATR);
}

void OnTick()
{
    double ema_fast[], ema_slow[], atr[];
    
    if(CopyBuffer(handleEMAFast, 0, 0, 3, ema_fast) <= 0) return;
    if(CopyBuffer(handleEMASlow, 0, 0, 3, ema_slow) <= 0) return;
    if(CopyBuffer(handleATR, 0, 0, 1, atr) <= 0) return;
    
    ArraySetAsSeries(ema_fast, true);
    ArraySetAsSeries(ema_slow, true);
    
    if(PositionsTotal() > 0) return;
    
    double price = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
    double sl_dist = atr[0] * 1.5;
    double tp_dist = atr[0] * 3.0;
    
    // Cross up
    if(ema_fast[1] <= ema_slow[1] && ema_fast[0] > ema_slow[0]) {
        double sl = price - sl_dist;
        double tp = price + tp_dist;
        double lots = CalculateLots(sl_dist);
        trade.Buy(lots, _Symbol, price, sl, tp);
    }
    // Cross down
    else if(ema_fast[1] >= ema_slow[1] && ema_fast[0] < ema_slow[0]) {
        price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
        double sl = price + sl_dist;
        double tp = price - tp_dist;
        double lots = CalculateLots(sl_dist);
        trade.Sell(lots, _Symbol, price, sl, tp);
    }
}

double CalculateLots(double slDistance)
{
    double balance = AccountInfoDouble(ACCOUNT_BALANCE);
    double riskAmount = balance * (InpRisk / 100.0);
    double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
    double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
    if(tickSize == 0) return 0.01;
    double pointValue = tickValue / tickSize;
    double slPoints = slDistance / _Point;
    double lots = riskAmount / (slPoints * pointValue * _Point);
    
    double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
    double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
    double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
    
    lots = MathFloor(lots / lotStep) * lotStep;
    lots = MathMax(minLot, MathMin(maxLot, lots));
    return lots;
}
'''


SYSTEM_PROMPT = """Sei un esperto sviluppatore MQL5 (MetaTrader 5, NON MetaTrader 4). Scrivi Expert Advisor che compilano al primo tentativo.

⚠️ REGOLE CRITICHE — VIOLARLE = COMPILE ERROR:

1. **MQL5 (NON MQL4)**: usa SEMPRE `CTrade` da `<Trade/Trade.mqh>`. NON usare `OrderSend()` stile MT4.

2. **INDICATORI**: in MT5 si usano HANDLE creati in `OnInit()`, non funzioni dirette in `OnTick()`.
   - GIUSTO:
     ```
     int handle;
     OnInit() { handle = iMA(_Symbol, _Period, 14, 0, MODE_EMA, PRICE_CLOSE); }
     OnTick() { double buf[]; CopyBuffer(handle, 0, 0, 3, buf); ArraySetAsSeries(buf, true); }
     ```
   - SBAGLIATO: `double ema = iMA(...);` in OnTick (è sintassi MT4)
   
3. **`iCustom`, `iMA`, `iATR`, `iRSI`, `iADX`, `iBands`** etc → ritornano sempre `int handle`, mai valori.

4. **CopyBuffer** è il modo corretto per leggere valori. Sempre check `if(CopyBuffer(...) <= 0) return;`

5. **ArraySetAsSeries(array, true)** dopo CopyBuffer per accedere come [0] = ultimo valore.

6. **CTrade**: per aprire trade usa `trade.Buy(lots, symbol, price, sl, tp)` o `trade.Sell(...)`.

7. **Stop loss e Take Profit** sono PREZZI assoluti, non distanze. Calcolali come `price ± distance`.

8. **PositionsTotal()** per contare posizioni aperte (no `OrdersTotal` di MT4).

9. **Iterazione posizioni**: `for(int i = PositionsTotal()-1; i >= 0; i--) { ulong ticket = PositionGetTicket(i); if(PositionSelectByTicket(ticket)) { ... } }`

10. **ENUM_TIMEFRAMES**: `PERIOD_M15`, `PERIOD_H1`, `PERIOD_H4`, `PERIOD_D1` (NON `M15` o `H1` come stringhe).

11. **NO funzioni MQL4 deprecate**: `Bid`, `Ask`, `Point`, `Digits`, `OrdersTotal`, `OrderSelect`, `OrderSend`, `OrderClose` → tutte SBAGLIATE.
    - Usa: `SymbolInfoDouble(_Symbol, SYMBOL_BID)`, `_Point`, `_Digits`, `PositionsTotal`, ecc.

12. **#property strict** all'inizio (obbligatorio in MQL5).

13. **OnDeinit**: rilascia gli handle indicatori con `IndicatorRelease(handle)`.

14. **Path include**: `#include <Trade\\Trade.mqh>` con backslash doppi.

📋 STRUTTURA OBBLIGATORIA dell'EA:

1. Header `#property` (strict, version, copyright)
2. `#include <Trade\\Trade.mqh>`
3. `input` parameters (Risk, MaxDailyDD, MaxTotalDD, MaxSpread, NewsBlock, Magic, etc + parametri specifici strategia)
4. Variabili globali (CTrade trade, handle indicatori, g_StartBalance, g_DailyStart)
5. `OnInit()` — crea handle indicatori, init balance tracking
6. `OnDeinit()` — rilascia handle
7. `OnTick()` — flusso: check DD → check filtri → leggi indicatori → logica entry/exit
8. Funzioni helper: CheckDailyDD, CheckTotalDD, CheckSpread, IsNewsTime, CountOpenPositions, CalculateLots, EmergencyCloseAll

📌 ESEMPIO DI EA MQL5 FUNZIONANTE (usalo come reference per la sintassi):

```mql5
""" + EXAMPLE_MQL5 + """
```

OUTPUT: SOLO codice .mq5 raw, senza markdown fence, senza spiegazioni prima/dopo. DEVE compilare in MetaEditor 5 senza errori.
"""


FIX_PROMPT = """Il codice MQL5 che hai generato NON compila. Ecco gli errori riportati da MetaEditor:

ERRORI:
{errors}

CODICE GENERATO (con numeri di riga):
{code_with_lines}

Riscrivi il codice MQL5 COMPLETO con gli errori corretti. Mantieni la stessa logica di trading, fixa solo gli errori di sintassi/API.
Output: SOLO il codice .mq5 corretto, senza markdown fence."""


class MQL5CodeGenerator:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
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

PARAMETRI da esporre come input:
{strategy.get('parameters', {})}

PROFILO RISK:
- Risk per trade: {profile['risk']['per_trade_pct']}%
- Max daily DD: {profile['risk']['max_daily_pct']}%
- Max concurrent trades: {profile['risk']['max_concurrent_trades']}
- News block: {profile['filters']['news_block_minutes_before']} min
- Max spread pips: {profile['filters']['max_spread_pips']}

PROP CONSTRAINTS ({rules.name}):
- Daily DD prop: {rules.max_daily_dd_pct}% → safety stop a {profile['risk']['max_daily_pct']}%
- Total DD prop: {rules.max_total_dd_pct}% → safety stop a {rules.max_total_dd_pct - 1}%
- Hedging: {'OK' if rules.hedging_allowed else 'VIETATO'}

NOME EA: {ea_name}
SIMBOLO: {symbol}

Genera il codice .mq5 completo, compilabile, MQL5 NATIVO (no MT4 syntax)."""
        
        logger.info(f"⚙️  CodeGen producing MQL5 for: {ea_name}")
        
        code = call_with_retry(
            self.client,
            model=self.model,
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        ).strip()
        
        code = self._strip_fences(code)
        
        # Salva su disco
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / f"{ea_name}.mq5"
        file_path.write_text(code, encoding="utf-8")
        
        logger.success(f"✅ MQL5 saved: {file_path}")
        return code, file_path
    
    def fix_compile_errors(
        self,
        original_code: str,
        compile_errors: str,
        mq5_path: Path,
    ) -> str:
        """Auto-fix: manda errori di compile a Claude e riceve codice corretto."""
        # Aggiungi numeri di riga al codice per facilitare diagnosis
        lines = original_code.split("\n")
        code_with_lines = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))
        
        # Estrai solo le righe di errore (non tutto il log)
        error_lines = []
        for line in compile_errors.split("\n"):
            line = line.strip()
            if "error" in line.lower() or ": '" in line:
                error_lines.append(line)
        errors_summary = "\n".join(error_lines[:20]) or compile_errors[:1500]
        
        logger.info("🔧 Attempting auto-fix of compile errors...")
        
        fixed_code = call_with_retry(
            self.client,
            model=self.model,
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": FIX_PROMPT.format(
                errors=errors_summary,
                code_with_lines=code_with_lines,
            )}],
        ).strip()
        
        fixed_code = self._strip_fences(fixed_code)
        
        # Sovrascrivi il file
        mq5_path.write_text(fixed_code, encoding="utf-8")
        logger.info(f"   Fix applied to: {mq5_path.name}")
        
        return fixed_code
    
    @staticmethod
    def _strip_fences(code: str) -> str:
        """Rimuove eventuali markdown code fences."""
        if code.startswith("```"):
            lines = code.split("\n")
            # Skip prima riga (```mql5 o ```)
            start = 1
            # Find closing fence
            end = len(lines)
            for i in range(len(lines) - 1, 0, -1):
                if lines[i].strip().startswith("```"):
                    end = i
                    break
            code = "\n".join(lines[start:end])
        return code.strip()
