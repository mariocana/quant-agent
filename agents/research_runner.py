"""ResearchRunner — run one experiment end-to-end and judge it.

Ties Layer 2 (AlgoFrameworkClient) to the ResultEvaluator: given an experiment
plan (which strategy, symbol, timeframe, param overrides), it runs the backtest,
runs robustness ONLY if the backtest is worth it, evaluates against the gates,
and returns a structured outcome. This is the skeleton the orchestrator will
drive once per cycle, and what StrategyResearcher will feed with plans.

Compute discipline: robustness (walk-forward + Monte Carlo) is the expensive
part. If the backtest already fails the criteria we REJECT without paying for
robustness. Robustness runs only when the backtest is at least REVIEW-worthy.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

from adapters.env_bridge import ToolError, is_setup_error
from agents.result_evaluator import ResultEvaluator, AnalysisResult, APPROVE, REVIEW, REJECT

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover
    import logging
    logger = logging.getLogger("agents.research_runner")

ERROR = "ERROR"  # runner-level status: the tool couldn't run (data/setup), NOT a judgment


@dataclass
class ExperimentPlan:
    strategy: str
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    params: Optional[dict] = None
    monte_carlo: int = 1000
    wf_train: int = 6
    wf_test: int = 2
    wf_step: int = 2
    enforce_prop: bool = False       # backtest run mode (diagnostics vs edge)
    rationale: str = ""              # filled by StrategyResearcher later

    @classmethod
    def from_dict(cls, d: dict) -> "ExperimentPlan":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


@dataclass
class ExperimentOutcome:
    plan: ExperimentPlan
    verdict: str                      # APPROVE | REVIEW | REJECT | ERROR
    analysis: Optional[AnalysisResult]
    backtest: Optional[dict] = None
    robustness: Optional[dict] = None
    ran_robustness: bool = False
    error: Optional[str] = None

    @property
    def is_candidate(self) -> bool:
        return self.verdict == APPROVE

    def to_dict(self) -> dict:
        d = asdict(self)
        # analysis is a dataclass; asdict already recursed it. Keep plan/analysis dicts.
        return d


class ResearchRunner:
    def __init__(self, algo, evaluator: ResultEvaluator,
                 robustness_on_reject: bool = False):
        """algo: an AlgoFrameworkClient (or anything with run_backtest/run_robustness)."""
        self.algo = algo
        self.evaluator = evaluator
        self.robustness_on_reject = robustness_on_reject

    def run(self, plan: ExperimentPlan) -> ExperimentOutcome:
        tag = f"{plan.strategy}/{plan.symbol}/{plan.timeframe}"
        logger.info(f"🔬 Experiment: {tag}  params={plan.params or {}}")

        # ── 1. Backtest ──
        try:
            backtest = self.algo.run_backtest(
                plan.strategy, symbol=plan.symbol, timeframe=plan.timeframe,
                params=plan.params, enforce_prop=plan.enforce_prop,
            )
        except ToolError as e:
            return self._error(plan, "backtest", e)

        # ── 2. Cheap gate on the backtest alone ──
        pre = self.evaluator.evaluate(backtest, robustness=None)
        # pre.verdict is REJECT (backtest fails criteria) or REVIEW (passes, no robustness yet)
        if pre.verdict == REJECT and not self.robustness_on_reject:
            logger.info(f"   ✗ backtest gate failed — skipping robustness ({tag})")
            return ExperimentOutcome(plan=plan, verdict=REJECT, analysis=pre,
                                     backtest=backtest, robustness=None, ran_robustness=False)

        # ── 3. Robustness (walk-forward + Monte Carlo) ──
        try:
            robustness = self.algo.run_robustness(
                plan.strategy, symbol=plan.symbol, timeframe=plan.timeframe,
                params=plan.params, monte_carlo=plan.monte_carlo,
                wf_train=plan.wf_train, wf_test=plan.wf_test, wf_step=plan.wf_step,
            )
        except ToolError as e:
            # Robustness couldn't run. If it's a data-setup reason (e.g. too little
            # history for walk-forward), we can't APPROVE — surface the backtest
            # verdict (REVIEW) and record why robustness is missing.
            if is_setup_error(str(e)):
                logger.warning(f"   robustness unavailable (setup) — verdict stays {pre.verdict} ({tag})")
                return ExperimentOutcome(plan=plan, verdict=pre.verdict, analysis=pre,
                                         backtest=backtest, robustness=None,
                                         ran_robustness=False, error=_tool_reason(e))
            return self._error(plan, "robustness", e, backtest=backtest)

        # ── 4. Full evaluation (backtest + robustness) ──
        final = self.evaluator.evaluate(backtest, robustness=robustness)
        logger.info(f"   → {final.verdict} (score {final.score}) [{tag}]")
        return ExperimentOutcome(plan=plan, verdict=final.verdict, analysis=final,
                                 backtest=backtest, robustness=robustness, ran_robustness=True)

    def _error(self, plan, stage, e: ToolError, backtest=None) -> ExperimentOutcome:
        msg = f"{stage}: {_tool_reason(e)}"
        logger.warning(f"   ⚠️ {stage} could not run: {msg}")
        return ExperimentOutcome(plan=plan, verdict=ERROR, analysis=None,
                                 backtest=backtest, robustness=None,
                                 ran_robustness=False, error=msg)


def _tool_reason(e: ToolError) -> str:
    """Last meaningful line of a ToolError (the real tool error, not the wrapper)."""
    lines = [ln.strip() for ln in str(e).splitlines() if ln.strip()]
    meaningful = [ln for ln in lines
                  if not ln.startswith("stderr tail") and "failed (rc=" not in ln]
    return meaningful[-1] if meaningful else (lines[0] if lines else "unknown error")
