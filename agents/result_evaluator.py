"""ResultEvaluator — turns the algo_framework JSON contract into a verdict.

Supersedes the legacy Claude-only ResultAnalyzer (which read MQL5 dataclasses).
The core here is DETERMINISTIC and API-free: it checks the backtest metrics
against validation_criteria and — mandatorily — the robustness summary against
robustness_gate. This is the guardrail against industrial over-optimisation: a
strategy with a pretty backtest but no proven robustness never gets APPROVEd.

An optional Claude narrative layer adds qualitative colour, but it can NEVER
turn a failed gate into an APPROVE — the gate is the authority.

Consumes:
  backtest  = AlgoFrameworkClient.run_backtest(...)   -> schema algo_framework.backtest.v1
  robustness= AlgoFrameworkClient.run_robustness(...) -> schema algo_framework.robustness.v1
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Optional

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover
    import logging
    logger = logging.getLogger("agents.result_evaluator")

APPROVE, REVIEW, REJECT = "APPROVE", "REVIEW", "REJECT"

DEFAULT_CRITERIA = {
    "min_profit_factor": 1.5,
    "min_sharpe": 1.0,
    "max_drawdown_pct": 7.0,
    "min_trades": 50,
    "min_win_rate": 0.40,          # fraction (0-1)
    "max_consecutive_losses": 6,
}
DEFAULT_ROBUSTNESS_GATE = {
    "min_wf_consistency_pct": 70,
    "min_mc_prop_pass_pct": 70,
    "min_mc_prob_profitable_pct": 60,
}


@dataclass
class Check:
    name: str
    actual: Optional[float]
    op: str          # ">=" or "<="
    threshold: float
    passed: bool

    def describe(self) -> str:
        a = "n/a" if self.actual is None else (
            "inf" if (isinstance(self.actual, float) and math.isinf(self.actual))
            else round(self.actual, 3))
        return f"{self.name} {a} {self.op} {self.threshold} -> {'ok' if self.passed else 'FAIL'}"


@dataclass
class AnalysisResult:
    verdict: str                       # APPROVE | REVIEW | REJECT
    score: int                         # 0-100 (share of checks passed)
    robustness_evaluated: bool
    monte_carlo_confidence: Optional[float]  # MC prop_pass_rate, if available
    checks: list[Check] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    narrative: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _cmp(name, actual, op, threshold) -> Check:
    if actual is None:
        return Check(name, None, op, threshold, False)
    passed = actual >= threshold if op == ">=" else actual <= threshold
    return Check(name, actual, op, threshold, bool(passed))


def _effective_pf(metrics: dict) -> Optional[float]:
    """Resolve profit_factor without the inf/null ambiguity, using the raw gross
    figures the contract exports: no losses & some profit => infinite (great);
    no trades => undefined (None)."""
    gp = metrics.get("gross_profit_usd")
    gl = metrics.get("gross_loss_usd")
    if gl == 0 and gp and gp > 0:
        return math.inf
    pf = metrics.get("profit_factor")
    if pf is None:
        return None
    return float(pf)


class ResultEvaluator:
    def __init__(
        self,
        criteria: Optional[dict] = None,
        robustness_gate: Optional[dict] = None,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-6",
    ):
        self.criteria = {**DEFAULT_CRITERIA, **(criteria or {})}
        self.gate = {**DEFAULT_ROBUSTNESS_GATE, **(robustness_gate or {})}
        self.api_key = api_key
        self.model = model

    # ── deterministic gate ────────────────────────────────────────────
    def evaluate(self, backtest: dict, robustness: Optional[dict] = None,
                 with_narrative: bool = False) -> AnalysisResult:
        m = (backtest or {}).get("metrics", {}) or {}
        c = self.criteria
        reasons: list[str] = []

        trades = m.get("total_trades", 0) or 0
        pf = _effective_pf(m)
        # win rate: metrics is 0-100 percent; criterion is a 0-1 fraction
        wr_pct = m.get("winrate_pct")

        bt_checks = [
            _cmp("total_trades", trades, ">=", c["min_trades"]),
            _cmp("profit_factor", pf, ">=", c["min_profit_factor"]),
            _cmp("sharpe_ratio", m.get("sharpe_ratio"), ">=", c["min_sharpe"]),
            _cmp("max_drawdown_pct", m.get("max_drawdown_pct"), "<=", c["max_drawdown_pct"]),
            _cmp("win_rate_pct", wr_pct, ">=", c["min_win_rate"] * 100),
            _cmp("max_consecutive_losses", m.get("max_consecutive_losses"), "<=",
                 c["max_consecutive_losses"]),
        ]
        backtest_ok = all(ck.passed for ck in bt_checks)

        # robustness gate (mandatory for APPROVE)
        rob_checks: list[Check] = []
        mc_conf = None
        robustness_evaluated = bool(robustness)
        if robustness_evaluated:
            wf = robustness.get("walk_forward", {}) or {}
            mc = robustness.get("monte_carlo", {}) or {}
            mc_conf = mc.get("prop_pass_rate")
            g = self.gate
            rob_checks = [
                _cmp("wf_consistency_pct", wf.get("consistency_pct"), ">=", g["min_wf_consistency_pct"]),
                _cmp("mc_prop_pass_pct", mc.get("prop_pass_rate"), ">=", g["min_mc_prop_pass_pct"]),
                _cmp("mc_prob_profitable_pct", mc.get("prob_profitable"), ">=", g["min_mc_prob_profitable_pct"]),
            ]
        robustness_ok = all(ck.passed for ck in rob_checks) if robustness_evaluated else None

        checks = bt_checks + rob_checks
        passed_n = sum(1 for ck in checks if ck.passed)
        score = round(100 * passed_n / len(checks)) if checks else 0

        # ── verdict ──
        if trades < c["min_trades"]:
            verdict = REJECT
            reasons.append(f"campione insufficiente: {trades} trade < {c['min_trades']}")
        elif not backtest_ok:
            verdict = REJECT
            for ck in bt_checks:
                if not ck.passed:
                    reasons.append("backtest fallito: " + ck.describe())
        elif not robustness_evaluated:
            verdict = REVIEW
            reasons.append("backtest supera i criteri, ma la robustness non è stata "
                           "eseguita: non promuovibile ad APPROVE (gate obbligatorio)")
        elif not robustness_ok:
            verdict = REJECT
            for ck in rob_checks:
                if not ck.passed:
                    reasons.append("robustness fallita: " + ck.describe())
        else:
            verdict = APPROVE
            reasons.append("tutti i criteri backtest + gate robustness superati")

        result = AnalysisResult(
            verdict=verdict, score=score, robustness_evaluated=robustness_evaluated,
            monte_carlo_confidence=mc_conf, checks=checks, reasons=reasons,
        )
        logger.info(f"🧭 Evaluator: {verdict} (score {score}) — "
                    f"{'; '.join(reasons[:2])}")

        if with_narrative and self.api_key:
            try:
                result.narrative = self._narrative(backtest, robustness, result)
            except Exception as e:  # narrative is best-effort; never blocks the gate
                logger.warning(f"narrative skipped: {e}")
        return result

    # ── optional Claude qualitative layer ─────────────────────────────
    def _narrative(self, backtest, robustness, result: AnalysisResult) -> str:
        from agents.api_client import make_client, call_with_retry
        client = make_client(self.api_key, timeout_seconds=120)
        meta = (backtest or {}).get("metadata", {})
        system = (
            "Sei un risk officer di prop firm. Ti do il verdetto DETERMINISTICO già "
            "calcolato (non ribaltarlo) e i dati. Spiega in modo critico e sintetico "
            "PERCHÉ, elenca punti forti, rischi e (se REVIEW) cosa manca. Non essere "
            "ottimista per default."
        )
        user = (
            f"VERDETTO (fisso): {result.verdict} | score {result.score}\n"
            f"Strategia: {meta.get('strategy')} su {meta.get('symbols')} {meta.get('timeframe')}\n"
            f"Finestra dati: {meta.get('data_start')} → {meta.get('data_end')}\n"
            f"Check:\n" + "\n".join("  - " + ck.describe() for ck in result.checks) + "\n"
            f"Motivi: {result.reasons}\n"
            f"Robustness valutata: {result.robustness_evaluated} | "
            f"MC prop_pass: {result.monte_carlo_confidence}\n"
        )
        return call_with_retry(client, model=self.model, max_tokens=800,
                               system=system, messages=[{"role": "user", "content": user}])
