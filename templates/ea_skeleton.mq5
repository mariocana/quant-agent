//+------------------------------------------------------------------+
//|  PROP AGENT EA — Template Skeleton                                |
//|  Questo è il template di riferimento generato dal CodeGen Agent.  |
//|  Ogni EA prodotto rispetta questa struttura per garantire         |
//|  compliance prop firm e robustezza operativa.                     |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property copyright "Prop Agent System"

#include <Trade\Trade.mqh>

//--- INPUT PARAMETERS (sempre esposti) ---
input group "=== Risk Management ==="
input double   InpRiskPercent       = 1.0;     // Risk per trade (% account)
input double   InpMaxDailyDDPct     = 4.5;     // Stop trading se DD giornaliero >= %
input double   InpMaxTotalDDPct     = 9.0;     // Emergency close se DD totale >= %
input int      InpMaxConcurrentTrades = 3;     // Max posizioni simultanee

input group "=== Filters ==="
input double   InpMaxSpreadPips     = 2.0;     // Skip trade se spread > pips
input int      InpNewsBlockMinutes  = 5;       // Blocca trade ±N min dalle news
input string   InpTradingHours      = "08:00-20:00";  // Range orario UTC

input group "=== Strategy Specific (qui vanno i parametri) ===" 
input int      InpEMAPeriodFast     = 12;
input int      InpEMAPeriodSlow     = 26;

input group "=== Misc ==="
input long     InpMagicNumber       = 20260507;
input bool     InpEnableLogging     = true;

//--- GLOBAL VARS ---
CTrade trade;
double g_StartBalance;
double g_DailyStartBalance;
datetime g_DailyResetTime;
int g_RequestCount;
datetime g_LastRequestReset;

//+------------------------------------------------------------------+
int OnInit()
{
    trade.SetExpertMagicNumber(InpMagicNumber);
    trade.SetMarginMode();
    trade.SetTypeFillingBySymbol(_Symbol);
    
    g_StartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
    g_DailyStartBalance = g_StartBalance;
    g_DailyResetTime = TimeCurrent();
    g_RequestCount = 0;
    g_LastRequestReset = TimeCurrent();
    
    PrintFormat("EA initialized | Magic: %d | Balance: %.2f", InpMagicNumber, g_StartBalance);
    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    PrintFormat("EA shutdown | Reason: %d | Final balance: %.2f", 
                reason, AccountInfoDouble(ACCOUNT_BALANCE));
}

//+------------------------------------------------------------------+
void OnTick()
{
    ResetDailyTrackerIfNeeded();
    ResetRequestCounterIfNeeded();
    
    // === HARD CHECKS ===
    if(!CheckTotalDrawdown()) {
        EmergencyCloseAll("Total DD breach");
        ExpertRemove();
        return;
    }
    
    if(!CheckDailyDrawdown()) {
        if(InpEnableLogging) Print("⚠️  Daily DD limit reached — no new trades today");
        return;
    }
    
    if(!CheckTradingHours()) return;
    if(!CheckSpread()) return;
    if(IsNewsTime()) return;
    if(!CheckRequestQuota()) return;
    if(CountOpenPositions() >= InpMaxConcurrentTrades) return;
    
    // === STRATEGY LOGIC (qui CodeGen inserisce la logica specifica) ===
    // Esempio: EMA crossover
    double emaFast = iMA(_Symbol, _Period, InpEMAPeriodFast, 0, MODE_EMA, PRICE_CLOSE);
    double emaSlow = iMA(_Symbol, _Period, InpEMAPeriodSlow, 0, MODE_EMA, PRICE_CLOSE);
    // ... entry logic
}

//+------------------------------------------------------------------+
//| HELPERS                                                          |
//+------------------------------------------------------------------+

bool CheckTotalDrawdown()
{
    double currentEquity = AccountInfoDouble(ACCOUNT_EQUITY);
    double ddPct = (g_StartBalance - currentEquity) / g_StartBalance * 100.0;
    return ddPct < InpMaxTotalDDPct;
}

bool CheckDailyDrawdown()
{
    double currentEquity = AccountInfoDouble(ACCOUNT_EQUITY);
    double ddPct = (g_DailyStartBalance - currentEquity) / g_DailyStartBalance * 100.0;
    return ddPct < InpMaxDailyDDPct;
}

void ResetDailyTrackerIfNeeded()
{
    MqlDateTime now;
    TimeToStruct(TimeCurrent(), now);
    MqlDateTime last;
    TimeToStruct(g_DailyResetTime, last);
    if(now.day != last.day) {
        g_DailyStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
        g_DailyResetTime = TimeCurrent();
        if(InpEnableLogging) PrintFormat("Daily reset | New baseline: %.2f", g_DailyStartBalance);
    }
}

void ResetRequestCounterIfNeeded()
{
    if(TimeCurrent() - g_LastRequestReset >= 86400) {
        g_RequestCount = 0;
        g_LastRequestReset = TimeCurrent();
    }
}

bool CheckRequestQuota()
{
    // FTMO: max 2000 server requests/giorno
    return g_RequestCount < 1800;  // safety margin
}

bool CheckSpread()
{
    double spread = (SymbolInfoInteger(_Symbol, SYMBOL_SPREAD)) * _Point;
    double spreadPips = spread / (_Point * 10);
    return spreadPips <= InpMaxSpreadPips;
}

bool CheckTradingHours()
{
    MqlDateTime now;
    TimeToStruct(TimeCurrent(), now);
    string hours = InpTradingHours;
    int dashPos = StringFind(hours, "-");
    int startH = (int)StringToInteger(StringSubstr(hours, 0, 2));
    int endH = (int)StringToInteger(StringSubstr(hours, dashPos+1, 2));
    return now.hour >= startH && now.hour < endH;
}

bool IsNewsTime()
{
    // Implementazione semplificata — in produzione usare news calendar
    // Esempio: blocca 5 min prima/dopo orari fissi (NFP, FOMC, ecc.)
    MqlDateTime now;
    TimeToStruct(TimeCurrent(), now);
    // High-impact: 12:30 UTC primo venerdì del mese (NFP)
    if(now.day_of_week == 5 && now.day <= 7) {
        if(now.hour == 12 && now.min >= 25 && now.min <= 35) return true;
    }
    return false;
}

int CountOpenPositions()
{
    int count = 0;
    for(int i = PositionsTotal() - 1; i >= 0; i--) {
        ulong ticket = PositionGetTicket(i);
        if(PositionSelectByTicket(ticket)) {
            if(PositionGetInteger(POSITION_MAGIC) == InpMagicNumber) count++;
        }
    }
    return count;
}

double CalculateLotSize(double slPoints)
{
    double balance = AccountInfoDouble(ACCOUNT_BALANCE);
    double riskAmount = balance * (InpRiskPercent / 100.0);
    double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
    double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
    double pointValue = tickValue / tickSize * _Point;
    double lots = riskAmount / (slPoints * pointValue);
    
    // Normalizza al lot step
    double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
    double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
    double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
    
    lots = MathFloor(lots / lotStep) * lotStep;
    lots = MathMax(minLot, MathMin(maxLot, lots));
    return lots;
}

void EmergencyCloseAll(string reason)
{
    PrintFormat("🚨 EMERGENCY CLOSE | Reason: %s", reason);
    for(int i = PositionsTotal() - 1; i >= 0; i--) {
        ulong ticket = PositionGetTicket(i);
        if(PositionSelectByTicket(ticket)) {
            if(PositionGetInteger(POSITION_MAGIC) == InpMagicNumber) {
                trade.PositionClose(ticket);
                g_RequestCount++;
            }
        }
    }
}
//+------------------------------------------------------------------+
