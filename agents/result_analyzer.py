"""Result Analyzer — usa Claude per dare un giudizio finale qualitativo su un candidato EA."""
from anthropic import Anthropic
from loguru import logger
from dataclasses import asdict


SYSTEM_PROMPT = """Sei un risk officer esperto di prop firm trading. 

Analizzi i risultati di backtest + walk-forward di un Expert Advisor candidato per essere usato in una challenge prop, e dai un verdetto strutturato.

Output richiesto:

VERDETTO: [APPROVE | REVIEW | REJECT]

PUNTI FORTI:
- ...
- ...

RISCHI:
- ...
- ...

RACCOMANDAZIONI PRE-DEPLOY:
- ...

PROBABILITÀ STIMATA DI PASSARE LA CHALLENGE: XX%

Sii diretto, critico, evita di essere ottimista per default. Se ci sono red flags, segnalali chiaramente."""


class ResultAnalyzer:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5"):
        self.client = Anthropic(api_key=api_key)
        self.model = model
    
    def analyze(
        self,
        strategy: dict,
        backtest_result,
        validation_report,
        wf_result=None,
    ) -> str:
        """Genera analisi qualitativa finale."""
        wf_summary = ""
        if wf_result:
            wf_summary = f"""
WALK-FORWARD ANALYSIS ({wf_result.n_splits} splits):
- In-sample avg PF: {wf_result.in_sample_avg_pf}
- Out-sample avg PF: {wf_result.out_sample_avg_pf}
- Consistency score: {wf_result.consistency_score} (0=overfit, 1=robusto)
- Splits passed (OOS pf > 1.3): {wf_result.splits_passed}/{wf_result.n_splits}
"""
        
        user_msg = f"""Analizza questo candidato EA:

STRATEGIA: {strategy.get('name', '?')} ({strategy.get('strategy_type', '?')})
Ipotesi: {strategy.get('hypothesis', '')}

BACKTEST RESULTS:
- Net profit: ${backtest_result.net_profit:.2f} ({backtest_result.net_profit/backtest_result.initial_deposit*100:.2f}%)
- Profit factor: {backtest_result.profit_factor:.2f}
- Sharpe: {backtest_result.sharpe_ratio:.2f}
- Max drawdown: {backtest_result.max_drawdown_pct:.2f}%
- Total trades: {backtest_result.total_trades}
- Win rate: {backtest_result.win_rate*100:.1f}%
- Max consecutive losses: {backtest_result.max_consecutive_losses}
- Avg win/loss ratio: {abs(backtest_result.avg_win/backtest_result.avg_loss):.2f}

VALIDATION:
- Passes prop rules: {validation_report.passes}
- Score: {validation_report.score}/100
- Estimated pass days: {validation_report.estimated_pass_days}
- Violations: {validation_report.violations or 'nessuna'}
- Warnings: {validation_report.warnings or 'nessuno'}
{wf_summary}

Dai il tuo verdetto secondo lo schema definito."""
        
        logger.info(f"🧠 Analyzer evaluating: {strategy.get('name', '?')}")
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        
        analysis = response.content[0].text
        
        # Estrai verdetto
        verdict = "REVIEW"
        if "VERDETTO: APPROVE" in analysis or "VERDETTO:APPROVE" in analysis:
            verdict = "APPROVE"
        elif "VERDETTO: REJECT" in analysis or "VERDETTO:REJECT" in analysis:
            verdict = "REJECT"
        
        logger.info(f"   Verdict: {verdict}")
        return analysis, verdict
