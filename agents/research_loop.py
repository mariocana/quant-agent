"""ResearchLoop — the cognitive cycle that ties the agents together.

    propose (StrategyResearcher) -> run (ResearchRunner) -> judge (ResultEvaluator)
    -> append to history -> repeat

This is the new orchestrator core that replaces the legacy MQL5 pipeline. It
keeps a persistent experiment history (JSONL) so the researcher learns across
cycles and runs, and surfaces APPROVE outcomes as candidates via a callback
(DB/dashboard/notify wiring stays outside — that's the caller's job).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from agents.researcher import ResearchContext
from agents.research_runner import ExperimentOutcome, APPROVE

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover
    import logging
    logger = logging.getLogger("agents.research_loop")


@dataclass
class CycleReport:
    cycle: int
    proposed: int
    outcomes: list[ExperimentOutcome] = field(default_factory=list)
    candidates: list[ExperimentOutcome] = field(default_factory=list)

    def counts(self) -> dict:
        tally: dict = {}
        for o in self.outcomes:
            tally[o.verdict] = tally.get(o.verdict, 0) + 1
        return tally


class ResearchLoop:
    def __init__(
        self,
        researcher,
        runner,
        sea=None,
        *,
        n_per_cycle: int = 1,
        only_ai_strategies: bool = True,
        out_dir: str = "experiment_results",
        history_path: Optional[str] = None,
        on_candidate: Optional[Callable[[ExperimentOutcome], None]] = None,
        on_outcome: Optional[Callable[[ExperimentOutcome], None]] = None,
        context_builder: Optional[Callable[[list], ResearchContext]] = None,
    ):
        self.researcher = researcher
        self.runner = runner
        self.sea = sea
        self.n_per_cycle = n_per_cycle
        self.only_ai_strategies = only_ai_strategies
        self.out_dir = Path(out_dir)
        self.history_path = Path(history_path) if history_path else (self.out_dir / "history.jsonl")
        self.on_candidate = on_candidate
        self.on_outcome = on_outcome
        self._context_builder = context_builder
        self._configs: Optional[dict] = None            # cached across cycles
        self._symbols: Optional[dict] = None            # strategy -> declared symbols()
        self.cycle_count = 0
        self.history: list[dict] = self._load_history()
        logger.info(f"ResearchLoop ready — {len(self.history)} past experiments in history")

    # ── the cycle ─────────────────────────────────────────────────────
    def run_once(self, n: Optional[int] = None) -> CycleReport:
        self.cycle_count += 1
        n = n or self.n_per_cycle
        logger.info(f"🔄 Cycle #{self.cycle_count} — proposing {n} experiment(s)")

        ctx = self._context()
        plans = self.researcher.propose(ctx, n=n)
        report = CycleReport(cycle=self.cycle_count, proposed=len(plans))

        for plan in plans:
            outcome = self.runner.run(plan)
            report.outcomes.append(outcome)
            self._record(outcome)
            if self.on_outcome:
                self.on_outcome(outcome)
            if outcome.verdict == APPROVE:
                report.candidates.append(outcome)
                logger.info(f"🎯 CANDIDATE: {plan.strategy}/{plan.symbol}/{plan.timeframe} "
                            f"(score {outcome.analysis.score if outcome.analysis else '?'})")
                if self.on_candidate:
                    self.on_candidate(outcome)

        logger.info(f"   cycle #{self.cycle_count} done: {report.counts()} "
                    f"| {len(report.candidates)} candidate(s)")
        return report

    def run(self, cycles: int = 1, n: Optional[int] = None, sleep_s: float = 0) -> list[CycleReport]:
        reports = []
        for i in range(cycles):
            reports.append(self.run_once(n))
            if sleep_s and i < cycles - 1:
                time.sleep(sleep_s)
        return reports

    # ── context ───────────────────────────────────────────────────────
    def _context(self) -> ResearchContext:
        if self._context_builder:
            return self._context_builder(list(self.history))
        strategies = self.runner.algo.list_strategies()
        if self.only_ai_strategies:
            strategies = [s for s in strategies if s.upper().startswith("AI_")]
        inventory = [r for r in self.sea.list_available()
                     if not str(r.get("symbol", "")).startswith("(error")]
        if self._configs is None:                       # introspect once, reuse
            self._configs, self._symbols = {}, {}
            for s in strategies:
                try:
                    info = self.runner.algo.get_strategy_info(s)
                    self._configs[s] = info.get("default_config", {})
                    self._symbols[s] = info.get("symbols", [])
                except Exception:
                    self._configs[s], self._symbols[s] = {}, []
        return ResearchContext(strategies, inventory, self._configs,
                               list(self.history), self._symbols)

    # ── persistence ───────────────────────────────────────────────────
    def _record(self, outcome: ExperimentOutcome):
        summary = self._summarize(outcome)
        self.history.append(summary)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with open(self.history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
        # full outcome record for auditing / dashboard
        p = outcome.plan
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        fname = f"{p.strategy}_{p.symbol}_{p.timeframe}_{ts}.json".replace("/", "-")
        with open(self.out_dir / fname, "w", encoding="utf-8") as f:
            json.dump(outcome.to_dict(), f, indent=2, ensure_ascii=False, default=str)

    @staticmethod
    def _summarize(outcome: ExperimentOutcome) -> dict:
        p = outcome.plan
        return {
            "strategy": p.strategy, "symbol": p.symbol, "timeframe": p.timeframe,
            "table": p.table, "params": p.params or {},
            "verdict": outcome.verdict,
            "score": outcome.analysis.score if outcome.analysis else None,
            "ran_robustness": outcome.ran_robustness,
            "ts": datetime.now().isoformat(timespec="seconds"),
        }

    def _load_history(self) -> list[dict]:
        if not self.history_path.is_file():
            return []
        out = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return out
