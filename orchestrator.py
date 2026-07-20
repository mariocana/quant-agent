"""
Orchestrator — the h24 loop, now built on the ResearchLoop cognitive cycle.

The agent no longer authors MQL5. Each cycle it:
  proposes experiments (StrategyResearcher) -> runs them on the user's tools
  (algo_framework via the adapters) -> judges them (ResultEvaluator) -> persists
  outcomes to the DB so the dashboard shows them.

Run with:
    python orchestrator.py            # scheduled loop
    python orchestrator.py --once     # a single cycle (test)
Configure config.yaml first (tools:, validation_criteria:, robustness_gate:,
claude:, orchestrator:).

Note: user ideas approved in the dashboard (status=approved_for_dev) are NOT yet
turned into strategies — that needs StrategyAuthor (next), so they wait.
"""
import sys
from datetime import datetime, timedelta
from loguru import logger
from apscheduler.schedulers.blocking import BlockingScheduler

from config import Config
from db.database import init_db, get_session_factory
from db.models import Strategy, Backtest, Candidate, CycleLog
from db.mapping import outcome_to_rows

from adapters.algo_framework_client import AlgoFrameworkClient
from adapters.datasea_client import DataseaClient
from agents.result_evaluator import ResultEvaluator
from agents.research_runner import ResearchRunner
from agents.researcher import StrategyResearcher
from agents.research_loop import ResearchLoop


# Setup logging
logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level:8}</level> | {message}")
logger.add("logs/orchestrator_{time:YYYY-MM-DD}.log", rotation="00:00", retention="30 days", level="DEBUG")


class Orchestrator:
    def __init__(self, config_path: str = "config.yaml"):
        logger.info("🚀 Initializing Quant Research Orchestrator")
        self.config = Config(config_path)

        # Database
        self.engine = init_db(self.config.get("database.url"))
        self.SessionFactory = get_session_factory(self.engine)

        api_key = self.config.get("claude.api_key")
        model = self.config.get("claude.model", "claude-sonnet-4-6")
        conda_env = self.config.get("tools.conda_env") or None

        # Layer 2 — tool adapters
        self.algo = AlgoFrameworkClient(
            algo_dir=self.config.get("tools.algo_framework_dir"),
            datasea_root=self.config.get("tools.datasea_data_root"),
            datasea_table=self.config.get("tools.datasea_table", "mt5_ohlcv_ftmo"),
            python_exec=self.config.get("tools.python_exec", "python"),
            conda_env=conda_env,
            backtest_timeout_s=self.config.get("tools.backtest_timeout_s", 1800),
        )
        self.sea = DataseaClient(
            self.config.get("tools.datasea_data_root"),
            python_exec=self.config.get("tools.python_exec", "python"),
            conda_env=conda_env,
        )

        # Cognitive agents
        self.evaluator = ResultEvaluator(
            criteria=self.config.get("validation_criteria"),
            robustness_gate=self.config.get("robustness_gate"),
            api_key=api_key, model=model,
        )
        self.runner = ResearchRunner(self.algo, self.evaluator)
        self.researcher = StrategyResearcher(api_key, model)

        self.loop = ResearchLoop(
            self.researcher, self.runner, self.sea,
            n_per_cycle=self.config.get("orchestrator.max_experiments_per_cycle", 2),
            on_outcome=self._persist_outcome,
        )
        logger.info("✅ Orchestrator ready")

    # ── one cycle ─────────────────────────────────────────────────────
    def run_cycle(self):
        session = self.SessionFactory()
        cycle_log = CycleLog(cycle_number=self.loop.cycle_count + 1, status="running")
        session.add(cycle_log)
        session.commit()
        cid = cycle_log.id
        session.close()

        logger.info(f"\n{'='*60}\n🔄 CYCLE START\n{'='*60}")
        try:
            report = self.loop.run_once()
            self._finalize_cycle(cid, status="completed",
                                 proposed=report.proposed,
                                 ran=len(report.outcomes),
                                 candidates=len(report.candidates))
            logger.info(f"✅ Cycle done: {report.counts()} | {len(report.candidates)} candidate(s)")
        except Exception as e:
            logger.exception(f"Cycle failed: {e}")
            self._finalize_cycle(cid, status="failed", error=str(e))

    def _finalize_cycle(self, cid, status, proposed=0, ran=0, candidates=0, error=None):
        s = self.SessionFactory()
        try:
            cl = s.query(CycleLog).get(cid)
            if cl:
                cl.completed_at = datetime.utcnow()
                cl.status = status
                cl.strategies_generated = proposed
                cl.backtests_run = ran
                cl.candidates_found = candidates
                cl.error_message = error
                s.commit()
        finally:
            s.close()

    # ── persist one experiment outcome (dashboard reads these) ─────────
    def _persist_outcome(self, outcome):
        rows = outcome_to_rows(outcome)
        if rows is None:
            return  # ERROR outcome: nothing to persist
        s = self.SessionFactory()
        try:
            strat = Strategy(**rows["strategy"])
            s.add(strat)
            s.flush()
            bt = Backtest(strategy_id=strat.id, **rows["backtest"])
            s.add(bt)
            s.flush()
            if rows["candidate"]:
                s.add(Candidate(backtest_id=bt.id, **rows["candidate"]))
            s.commit()
        except Exception as e:
            logger.exception(f"persist failed: {e}")
            s.rollback()
        finally:
            s.close()

    # ── scheduler ─────────────────────────────────────────────────────
    def start(self, run_now: bool = True):
        cycle_hours = self.config.get("orchestrator.cycle_hours", 4)
        scheduler = BlockingScheduler()
        scheduler.add_job(
            self.run_cycle, "interval", hours=cycle_hours,
            next_run_time=datetime.now() + timedelta(seconds=5 if run_now else cycle_hours * 3600),
            id="main_cycle", misfire_grace_time=300,
        )
        logger.info(f"⏰ Scheduler started: cycle every {cycle_hours}h. Ctrl+C to stop.")
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("👋 Shutdown requested")
            scheduler.shutdown()


def main():
    config_path = "config.yaml"
    run_now = True
    args = sys.argv[1:]
    if "--no-immediate" in args:
        run_now = False
        args.remove("--no-immediate")
    if "--once" in args:
        args.remove("--once")
        if args:
            config_path = args[0]
        Orchestrator(config_path).run_cycle()
        return
    if args:
        config_path = args[0]
    Orchestrator(config_path).start(run_now=run_now)


if __name__ == "__main__":
    main()
