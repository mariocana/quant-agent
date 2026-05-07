//+------------------------------------------------------------------+
//|                                       PropAgentFramework.mq5     |
//|                                  Generic Strategy Framework      |
//|  Un singolo EA che implementa multiple strategie via parametri.  |
//|                                                                  |
//|  Strategy types supportati:                                      |
//|   - "ema_cross"        : EMA fast/slow crossover                 |
//|   - "rsi_reversion"    : RSI oversold/overbought mean reversion  |
//|   - "bollinger_revert" : Bollinger band mean reversion           |
//|   - "donchian_breakout": Donchian channel breakout (turtle)      |
//|   - "atr_breakout"     : Volatility breakout su ATR              |
//|   - "ma_pullback"      : Pullback to MA in trend                 |
//|   - "macd_momentum"    : MACD-based momentum                     |
//|                                                                  |
//|  Caricato con .set file generato da Python.                      |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property copyright "Prop Agent System"
#property description "Generic strategy framework — config-driven via .set"

#include <Trade\Trade.mqh>

//=== STRATEGY TYPE ENUM ===
enum ENUM_STRATEGY_TYPE
{
    STRAT_EMA_CROSS,
    STRAT_RSI_REVERSION,
    STRAT_BOLLINGER_REVERT,
    STRAT_DONCHIAN_BREAKOUT,
    STRAT_ATR_BREAKOUT,
    STRAT_MA_PULLBACK,
    STRAT_MACD_MOMENTUM
};

//=== INPUTS: STRATEGY SELECTION ===
input group "=== Strategy Selection ==="
input ENUM_STRATEGY_TYPE InpStrategy = STRAT_EMA_CROSS;  // Strategia attiva

//=== INPUTS: COMMON PARAMETERS (tutte le strategie) ===
input group "=== Risk Management ==="
input double   InpRiskPercent       = 1.0;    // Risk per trade %
input double   InpMaxDailyDDPct     = 4.5;    // Max daily DD safety
input double   InpMaxTotalDDPct     = 9.0;    // Max total DD safety
input int      InpMaxConcurrentTrades = 2;    // Max posizioni simultanee

input group "=== Filters ==="
input double   InpMaxSpreadPips     = 2.0;    // Max spread accettabile (pips)
input int      InpATRPeriod         = 14;     // ATR period (usato da tutti)
input double   InpSLAtrMult         = 1.5;    // SL distance = ATR * mult
input double   InpTPAtrMult         = 3.0;    // TP distance = ATR * mult
input bool     InpUseTrailingStop   = false;
input double   InpTrailingAtrMult   = 1.0;    // Trailing distance = ATR * mult

input group "=== Trading Hours (UTC) ==="
input int      InpStartHour         = 7;      // Trade da ora (UTC)
input int      InpEndHour           = 20;     // Trade fino a ora (UTC)
input bool     InpFridayClose       = true;   // Chiudi posizioni venerdì sera

input group "=== Strategy-specific: EMA Cross ==="
input int      InpEMAFast           = 12;
input int      InpEMASlow           = 26;

input group "=== Strategy-specific: RSI Reversion ==="
input int      InpRSIPeriod         = 14;
input double   InpRSIOversold       = 30;
input double   InpRSIOverbought     = 70;

input group "=== Strategy-specific: Bollinger ==="
input int      InpBBPeriod          = 20;
input double   InpBBDeviation       = 2.0;

input group "=== Strategy-specific: Donchian / Breakout ==="
input int      InpDonchianPeriod    = 20;

input group "=== Strategy-specific: MA Pullback ==="
input int      InpPullbackMA        = 50;
input int      InpPullbackTrendMA   = 200;

input group "=== Strategy-specific: MACD ==="
input int      InpMACDFast          = 12;
input int      InpMACDSlow          = 26;
input int      InpMACDSignal        = 9;

input group "=== Misc ==="
input long     InpMagicNumber       = 20260507;
input bool     InpEnableLogging     = true;

//=== GLOBALS ===
CTrade   trade;
double   g_StartBalance;
double   g_DailyStartBalance;
datetime g_DailyResetTime;
datetime g_LastBarTime;

// Indicator handles
int      h_emaFast = INVALID_HANDLE;
int      h_emaSlow = INVALID_HANDLE;
int      h_rsi     = INVALID_HANDLE;
int      h_bb      = INVALID_HANDLE;
int      h_atr     = INVALID_HANDLE;
int      h_pullbackMA = INVALID_HANDLE;
int      h_pullbackTrendMA = INVALID_HANDLE;
int      h_macd    = INVALID_HANDLE;
int      h_donchianHigh = INVALID_HANDLE;  // simulato via iHigh/iLow

//+------------------------------------------------------------------+
int OnInit()
{
    trade.SetExpertMagicNumber(InpMagicNumber);
    trade.SetMarginMode();
    trade.SetTypeFillingBySymbol(_Symbol);
    
    g_StartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
    g_DailyStartBalance = g_StartBalance;
    g_DailyResetTime = TimeCurrent();
    g_LastBarTime = 0;
    
    // ATR sempre necessario
    h_atr = iATR(_Symbol, _Period, InpATRPeriod);
    if(h_atr == INVALID_HANDLE) { Print("ATR handle failed"); return INIT_FAILED; }
    
    // Crea handle solo per la strategia attiva
    switch(InpStrategy)
    {
        case STRAT_EMA_CROSS:
            h_emaFast = iMA(_Symbol, _Period, InpEMAFast, 0, MODE_EMA, PRICE_CLOSE);
            h_emaSlow = iMA(_Symbol, _Period, InpEMASlow, 0, MODE_EMA, PRICE_CLOSE);
            if(h_emaFast == INVALID_HANDLE || h_emaSlow == INVALID_HANDLE) return INIT_FAILED;
            break;
        
        case STRAT_RSI_REVERSION:
            h_rsi = iRSI(_Symbol, _Period, InpRSIPeriod, PRICE_CLOSE);
            if(h_rsi == INVALID_HANDLE) return INIT_FAILED;
            break;
        
        case STRAT_BOLLINGER_REVERT:
            h_bb = iBands(_Symbol, _Period, InpBBPeriod, 0, InpBBDeviation, PRICE_CLOSE);
            if(h_bb == INVALID_HANDLE) return INIT_FAILED;
            break;
        
        case STRAT_DONCHIAN_BREAKOUT:
        case STRAT_ATR_BREAKOUT:
            // Donchian si fa con iHigh/iLow direttamente, niente handle
            break;
        
        case STRAT_MA_PULLBACK:
            h_pullbackMA      = iMA(_Symbol, _Period, InpPullbackMA, 0, MODE_EMA, PRICE_CLOSE);
            h_pullbackTrendMA = iMA(_Symbol, _Period, InpPullbackTrendMA, 0, MODE_EMA, PRICE_CLOSE);
            if(h_pullbackMA == INVALID_HANDLE || h_pullbackTrendMA == INVALID_HANDLE) return INIT_FAILED;
            break;
        
        case STRAT_MACD_MOMENTUM:
            h_macd = iMACD(_Symbol, _Period, InpMACDFast, InpMACDSlow, InpMACDSignal, PRICE_CLOSE);
            if(h_macd == INVALID_HANDLE) return INIT_FAILED;
            break;
    }
    
    PrintFormat("Framework EA initialized | Strategy=%d | Magic=%d | Balance=%.2f",
                InpStrategy, InpMagicNumber, g_StartBalance);
    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    if(h_atr      != INVALID_HANDLE) IndicatorRelease(h_atr);
    if(h_emaFast  != INVALID_HANDLE) IndicatorRelease(h_emaFast);
    if(h_emaSlow  != INVALID_HANDLE) IndicatorRelease(h_emaSlow);
    if(h_rsi      != INVALID_HANDLE) IndicatorRelease(h_rsi);
    if(h_bb       != INVALID_HANDLE) IndicatorRelease(h_bb);
    if(h_pullbackMA != INVALID_HANDLE) IndicatorRelease(h_pullbackMA);
    if(h_pullbackTrendMA != INVALID_HANDLE) IndicatorRelease(h_pullbackTrendMA);
    if(h_macd     != INVALID_HANDLE) IndicatorRelease(h_macd);
}

//+------------------------------------------------------------------+
void OnTick()
{
    ResetDailyTrackerIfNeeded();
    
    // === HARD CHECKS ===
    if(!CheckTotalDrawdown()) {
        EmergencyCloseAll("Total DD breach");
        ExpertRemove();
        return;
    }
    if(!CheckDailyDrawdown()) return;
    if(!CheckTradingHours()) return;
    if(!CheckSpread()) return;
    
    // Trailing stop su posizioni esistenti
    if(InpUseTrailingStop) ManageTrailingStops();
    
    // Solo barra nuova per non spammare
    datetime currentBar = iTime(_Symbol, _Period, 0);
    if(currentBar == g_LastBarTime) return;
    g_LastBarTime = currentBar;
    
    if(CountOpenPositions() >= InpMaxConcurrentTrades) return;
    
    // Leggi ATR (sempre serve per SL/TP)
    double atrBuf[];
    if(CopyBuffer(h_atr, 0, 0, 1, atrBuf) <= 0) return;
    double atr = atrBuf[0];
    if(atr <= 0) return;
    
    // === STRATEGY DISPATCH ===
    int signal = 0;  // 0=nothing, 1=long, -1=short
    
    switch(InpStrategy)
    {
        case STRAT_EMA_CROSS:        signal = SignalEMACross(); break;
        case STRAT_RSI_REVERSION:    signal = SignalRSIReversion(); break;
        case STRAT_BOLLINGER_REVERT: signal = SignalBollingerRevert(); break;
        case STRAT_DONCHIAN_BREAKOUT:signal = SignalDonchianBreakout(); break;
        case STRAT_ATR_BREAKOUT:     signal = SignalATRBreakout(atr); break;
        case STRAT_MA_PULLBACK:      signal = SignalMAPullback(); break;
        case STRAT_MACD_MOMENTUM:    signal = SignalMACDMomentum(); break;
    }
    
    if(signal == 1)       OpenLong(atr);
    else if(signal == -1) OpenShort(atr);
}

//+------------------------------------------------------------------+
//| STRATEGY IMPLEMENTATIONS                                         |
//+------------------------------------------------------------------+

int SignalEMACross()
{
    double fast[], slow[];
    if(CopyBuffer(h_emaFast, 0, 0, 3, fast) <= 0) return 0;
    if(CopyBuffer(h_emaSlow, 0, 0, 3, slow) <= 0) return 0;
    ArraySetAsSeries(fast, true);
    ArraySetAsSeries(slow, true);
    
    if(fast[2] <= slow[2] && fast[1] > slow[1]) return 1;   // golden cross
    if(fast[2] >= slow[2] && fast[1] < slow[1]) return -1;  // death cross
    return 0;
}

int SignalRSIReversion()
{
    double rsi[];
    if(CopyBuffer(h_rsi, 0, 0, 3, rsi) <= 0) return 0;
    ArraySetAsSeries(rsi, true);
    
    if(rsi[2] < InpRSIOversold && rsi[1] >= InpRSIOversold) return 1;
    if(rsi[2] > InpRSIOverbought && rsi[1] <= InpRSIOverbought) return -1;
    return 0;
}

int SignalBollingerRevert()
{
    double upper[], lower[];
    if(CopyBuffer(h_bb, 1, 0, 2, upper) <= 0) return 0;  // buffer 1 = upper
    if(CopyBuffer(h_bb, 2, 0, 2, lower) <= 0) return 0;  // buffer 2 = lower
    ArraySetAsSeries(upper, true);
    ArraySetAsSeries(lower, true);
    
    double close = iClose(_Symbol, _Period, 1);
    if(close <= lower[1]) return 1;
    if(close >= upper[1]) return -1;
    return 0;
}

int SignalDonchianBreakout()
{
    double highest = 0, lowest = DBL_MAX;
    for(int i = 1; i <= InpDonchianPeriod; i++) {
        double h = iHigh(_Symbol, _Period, i);
        double l = iLow(_Symbol, _Period, i);
        if(h > highest) highest = h;
        if(l < lowest) lowest = l;
    }
    
    double close = iClose(_Symbol, _Period, 1);
    double prevClose = iClose(_Symbol, _Period, 2);
    
    if(prevClose <= highest && close > highest) return 1;
    if(prevClose >= lowest && close < lowest) return -1;
    return 0;
}

int SignalATRBreakout(double atr)
{
    double close = iClose(_Symbol, _Period, 1);
    double open = iOpen(_Symbol, _Period, 1);
    double range = MathAbs(close - open);
    
    if(range > atr * 1.2) {
        if(close > open) return 1;
        else return -1;
    }
    return 0;
}

int SignalMAPullback()
{
    double ma[], trendMA[];
    if(CopyBuffer(h_pullbackMA, 0, 0, 3, ma) <= 0) return 0;
    if(CopyBuffer(h_pullbackTrendMA, 0, 0, 3, trendMA) <= 0) return 0;
    ArraySetAsSeries(ma, true);
    ArraySetAsSeries(trendMA, true);
    
    double low1 = iLow(_Symbol, _Period, 1);
    double high1 = iHigh(_Symbol, _Period, 1);
    double close1 = iClose(_Symbol, _Period, 1);
    
    bool uptrend = ma[1] > trendMA[1];
    bool downtrend = ma[1] < trendMA[1];
    
    if(uptrend && low1 <= ma[1] && close1 > ma[1]) return 1;
    if(downtrend && high1 >= ma[1] && close1 < ma[1]) return -1;
    return 0;
}

int SignalMACDMomentum()
{
    double macdMain[], macdSig[];
    if(CopyBuffer(h_macd, 0, 0, 3, macdMain) <= 0) return 0;
    if(CopyBuffer(h_macd, 1, 0, 3, macdSig) <= 0) return 0;
    ArraySetAsSeries(macdMain, true);
    ArraySetAsSeries(macdSig, true);
    
    if(macdMain[2] <= macdSig[2] && macdMain[1] > macdSig[1] && macdMain[1] > 0) return 1;
    if(macdMain[2] >= macdSig[2] && macdMain[1] < macdSig[1] && macdMain[1] < 0) return -1;
    return 0;
}

//+------------------------------------------------------------------+
//| TRADE EXECUTION                                                  |
//+------------------------------------------------------------------+

void OpenLong(double atr)
{
    double price = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
    double slDist = atr * InpSLAtrMult;
    double tpDist = atr * InpTPAtrMult;
    double sl = price - slDist;
    double tp = price + tpDist;
    double lots = CalculateLotSize(slDist);
    if(lots <= 0) return;
    
    if(trade.Buy(lots, _Symbol, price, sl, tp, "PropAgentFramework")) {
        if(InpEnableLogging) PrintFormat("LONG opened: %.2f lots @ %.5f, SL=%.5f, TP=%.5f", lots, price, sl, tp);
    }
}

void OpenShort(double atr)
{
    double price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
    double slDist = atr * InpSLAtrMult;
    double tpDist = atr * InpTPAtrMult;
    double sl = price + slDist;
    double tp = price - tpDist;
    double lots = CalculateLotSize(slDist);
    if(lots <= 0) return;
    
    if(trade.Sell(lots, _Symbol, price, sl, tp, "PropAgentFramework")) {
        if(InpEnableLogging) PrintFormat("SHORT opened: %.2f lots @ %.5f, SL=%.5f, TP=%.5f", lots, price, sl, tp);
    }
}

//+------------------------------------------------------------------+
//| HELPERS                                                          |
//+------------------------------------------------------------------+

bool CheckTotalDrawdown()
{
    double equity = AccountInfoDouble(ACCOUNT_EQUITY);
    double ddPct = (g_StartBalance - equity) / g_StartBalance * 100.0;
    return ddPct < InpMaxTotalDDPct;
}

bool CheckDailyDrawdown()
{
    double equity = AccountInfoDouble(ACCOUNT_EQUITY);
    double ddPct = (g_DailyStartBalance - equity) / g_DailyStartBalance * 100.0;
    return ddPct < InpMaxDailyDDPct;
}

void ResetDailyTrackerIfNeeded()
{
    MqlDateTime now, last;
    TimeToStruct(TimeCurrent(), now);
    TimeToStruct(g_DailyResetTime, last);
    if(now.day != last.day) {
        g_DailyStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
        g_DailyResetTime = TimeCurrent();
    }
}

bool CheckTradingHours()
{
    MqlDateTime now;
    TimeToStruct(TimeCurrent(), now);
    if(now.hour < InpStartHour || now.hour >= InpEndHour) return false;
    if(InpFridayClose && now.day_of_week == 5 && now.hour >= 20) {
        EmergencyCloseAll("Friday close");
        return false;
    }
    return true;
}

bool CheckSpread()
{
    long spreadPoints = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
    double spreadPips = spreadPoints * _Point / (_Point * 10);
    return spreadPips <= InpMaxSpreadPips;
}

int CountOpenPositions()
{
    int count = 0;
    for(int i = PositionsTotal() - 1; i >= 0; i--) {
        ulong ticket = PositionGetTicket(i);
        if(PositionSelectByTicket(ticket) && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
            count++;
    }
    return count;
}

double CalculateLotSize(double slDistance)
{
    double balance = AccountInfoDouble(ACCOUNT_BALANCE);
    double riskAmount = balance * (InpRiskPercent / 100.0);
    double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
    double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
    if(tickSize == 0 || tickValue == 0) return 0;
    
    double slPoints = slDistance / _Point;
    double pointValue = tickValue / (tickSize / _Point);
    double lots = riskAmount / (slPoints * pointValue);
    
    double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
    double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
    double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
    
    if(lotStep > 0) lots = MathFloor(lots / lotStep) * lotStep;
    lots = MathMax(minLot, MathMin(maxLot, lots));
    return lots;
}

void ManageTrailingStops()
{
    double atrBuf[];
    if(CopyBuffer(h_atr, 0, 0, 1, atrBuf) <= 0) return;
    double trailDist = atrBuf[0] * InpTrailingAtrMult;
    
    for(int i = PositionsTotal() - 1; i >= 0; i--) {
        ulong ticket = PositionGetTicket(i);
        if(!PositionSelectByTicket(ticket)) continue;
        if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
        
        long type = PositionGetInteger(POSITION_TYPE);
        double currentSL = PositionGetDouble(POSITION_SL);
        double currentTP = PositionGetDouble(POSITION_TP);
        double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
        
        if(type == POSITION_TYPE_BUY) {
            double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
            double newSL = bid - trailDist;
            if(newSL > currentSL && newSL > openPrice) trade.PositionModify(ticket, newSL, currentTP);
        }
        else if(type == POSITION_TYPE_SELL) {
            double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
            double newSL = ask + trailDist;
            if((newSL < currentSL || currentSL == 0) && newSL < openPrice) trade.PositionModify(ticket, newSL, currentTP);
        }
    }
}

void EmergencyCloseAll(string reason)
{
    PrintFormat("EMERGENCY CLOSE | Reason: %s", reason);
    for(int i = PositionsTotal() - 1; i >= 0; i--) {
        ulong ticket = PositionGetTicket(i);
        if(PositionSelectByTicket(ticket) && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
            trade.PositionClose(ticket);
    }
}
//+------------------------------------------------------------------+
