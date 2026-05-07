"""Prop Validator — verifica se un backtest passerebbe i criteri di una prop firm specifica."""
from dataclasses import dataclass
from loguru import logger

from prop_rules import get_rules, PropRules
from agents.backtest_runner import BacktestResult


@dataclass
class ValidationReport:
    passes: bool
    violations: list[str]
    warnings: list[str]
    estimated_pass_days: int
    score: float                     # 0-100


class PropValidator:
    def __init__(self, prop_firm: str, prop_phase: str, account_size: float):
        self.rules: PropRules = get_rules(prop_firm, prop_phase)
        self.account_size = account_size
    
    def validate(
        self,
        backtest: BacktestResult,
        profile_thresholds: dict,
        wf_consistency_score: float = None,
    ) -> ValidationReport:
        """Valida un risultato backtest contro le regole prop e i threshold del profilo."""
        violations = []
        warnings = []
        
        # 1. Drawdown checks (HARD)
        if backtest.max_drawdown_pct > self.rules.max_total_dd_pct:
            violations.append(
                f"❌ Max DD {backtest.max_drawdown_pct:.2f}% > limite prop {self.rules.max_total_dd_pct}%"
            )
        elif backtest.max_drawdown_pct > self.rules.max_total_dd_pct * 0.7:
            warnings.append(
                f"⚠️  Max DD {backtest.max_drawdown_pct:.2f}% si avvicina al limite ({self.rules.max_total_dd_pct}%)"
            )
        
        # 2. Profile threshold checks
        thresh = profile_thresholds
        if backtest.profit_factor < thresh.get("min_profit_factor", 1.5):
            violations.append(
                f"❌ Profit factor {backtest.profit_factor:.2f} < richiesto {thresh['min_profit_factor']}"
            )
        
        if backtest.sharpe_ratio < thresh.get("min_sharpe", 1.0):
            violations.append(
                f"❌ Sharpe {backtest.sharpe_ratio:.2f} < richiesto {thresh['min_sharpe']}"
            )
        
        if backtest.win_rate < thresh.get("min_win_rate", 0.40):
            violations.append(
                f"❌ Win rate {backtest.win_rate*100:.1f}% < richiesto {thresh['min_win_rate']*100}%"
            )
        
        if backtest.max_drawdown_pct > thresh.get("max_drawdown_pct", 7.0):
            violations.append(
                f"❌ Max DD {backtest.max_drawdown_pct:.2f}% > soglia profilo {thresh['max_drawdown_pct']}%"
            )
        
        # 3. Trade count
        if backtest.total_trades < 30:
            warnings.append(
                f"⚠️  Solo {backtest.total_trades} trades — sample troppo piccolo per validità statistica"
            )
        
        # 4. Consecutive losses (rischio di hit daily DD)
        if backtest.max_consecutive_losses > 6:
            warnings.append(
                f"⚠️  Max {backtest.max_consecutive_losses} losses consecutive — rischio daily DD violato"
            )
        
        # 5. Walk-forward consistency
        if wf_consistency_score is not None:
            if wf_consistency_score < 0.5:
                violations.append(
                    f"❌ Walk-forward consistency {wf_consistency_score:.2f} < 0.5 — overfitting probabile"
                )
            elif wf_consistency_score < 0.7:
                warnings.append(
                    f"⚠️  WF consistency {wf_consistency_score:.2f} — robustezza moderata"
                )
        
        # 6. Estimated pass days
        if backtest.total_trades > 0 and backtest.net_profit > 0:
            # Approx: profit/giorno medio basato sul backtest
            # (assumendo backtest di ~1 anno per semplicità)
            avg_daily_profit_pct = (backtest.net_profit / backtest.initial_deposit) * 100 / 252
            if self.rules.profit_target_pct and avg_daily_profit_pct > 0:
                estimated_days = int(self.rules.profit_target_pct / avg_daily_profit_pct)
            else:
                estimated_days = 0
        else:
            estimated_days = 999
        
        # 7. Score composito (0-100)
        score = self._compute_score(backtest, wf_consistency_score, len(violations), len(warnings))
        
        passes = len(violations) == 0
        
        return ValidationReport(
            passes=passes,
            violations=violations,
            warnings=warnings,
            estimated_pass_days=estimated_days,
            score=round(score, 1),
        )
    
    def _compute_score(
        self,
        bt: BacktestResult,
        wf: float | None,
        n_violations: int,
        n_warnings: int,
    ) -> float:
        """Score composito 0-100."""
        if n_violations > 0:
            return max(0, 30 - n_violations * 10)
        
        # Base score
        score = 50
        
        # Profit factor bonus
        score += min(20, (bt.profit_factor - 1) * 15)
        
        # Sharpe bonus
        score += min(15, bt.sharpe_ratio * 5)
        
        # Drawdown bonus (lower = better)
        dd_ratio = bt.max_drawdown_pct / self.rules.max_total_dd_pct
        score += (1 - dd_ratio) * 10
        
        # Walk-forward bonus
        if wf is not None:
            score += wf * 15
        
        # Penalty per warnings
        score -= n_warnings * 3
        
        return max(0, min(100, score))
