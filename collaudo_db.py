#!/usr/bin/env python
"""Collaudo helper — dump what the orchestrator wrote to the DB.

A quick way to verify persistence without opening the dashboard:

    python collaudo_db.py            # uses database.url from config.yaml
    python collaudo_db.py --db sqlite:///db/prop_agent.db

Shows counts + the latest backtests, pending candidates, cycles and ideas.
Note: REJECT/ERROR experiments create a Backtest (or nothing, for ERROR) but no
Candidate — only APPROVE/REVIEW become candidates. ERROR outcomes aren't in the
DB at all (see experiment_results/*.json and the logs for those).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from db.database import init_db, get_session_factory
from db.models import Strategy, Backtest, Candidate, CycleLog, UserIdea


def _db_url(cli_url):
    if cli_url:
        return cli_url
    try:
        from config import Config
        return Config("config.yaml").get("database.url")
    except Exception:
        return "sqlite:///db/prop_agent.db"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="database URL (else config.yaml)")
    ap.add_argument("--limit", type=int, default=8)
    args = ap.parse_args()

    url = _db_url(args.db)
    print(f"DB: {url}\n")
    s = get_session_factory(init_db(url))()
    try:
        # ── counts ──
        print("═" * 64)
        print("  COUNTS")
        print("═" * 64)
        print(f"  strategies : {s.query(Strategy).count()}")
        print(f"  backtests  : {s.query(Backtest).count()}")
        print(f"  cycles     : {s.query(CycleLog).count()}")
        print(f"  ideas      : {s.query(UserIdea).count()}")
        cand_total = s.query(Candidate).count()
        pending = s.query(Candidate).filter(Candidate.status == "pending").count()
        print(f"  candidates : {cand_total} ({pending} pending)")

        # ── latest backtests ──
        print("\n" + "═" * 64)
        print(f"  LATEST BACKTESTS (max {args.limit})")
        print("═" * 64)
        bts = s.query(Backtest).order_by(Backtest.id.desc()).limit(args.limit).all()
        if not bts:
            print("  (none)")
        for bt in bts:
            strat = s.get(Strategy, bt.strategy_id)
            nm = f"{strat.name}/{strat.symbol}/{strat.timeframe}" if strat else "?"
            wf = f"{bt.walk_forward_score:.2f}" if bt.walk_forward_score is not None else "—"
            print(f"  #{bt.id:<4} {nm:<32} PF={_f(bt.profit_factor)} "
                  f"Sh={_f(bt.sharpe_ratio)} DD={_f(bt.max_drawdown_pct)}% "
                  f"trades={bt.total_trades} pass={bt.passes_prop_rules} WF={wf}")

        # ── pending candidates ──
        print("\n" + "═" * 64)
        print("  PENDING CANDIDATES")
        print("═" * 64)
        cands = s.query(Candidate).filter(Candidate.status == "pending").all()
        if not cands:
            print("  (none) — no APPROVE/REVIEW yet")
        for c in cands:
            bt = s.get(Backtest, c.backtest_id)
            strat = s.get(Strategy, bt.strategy_id) if bt else None
            nm = f"{strat.name}/{strat.symbol}/{strat.timeframe}" if strat else "?"
            print(f"  #{c.id:<4} {nm:<32} {c.recommendation:<8} score={_f(c.overall_score)}")

        # ── cycles ──
        print("\n" + "═" * 64)
        print(f"  LATEST CYCLES (max {args.limit})")
        print("═" * 64)
        cs = s.query(CycleLog).order_by(CycleLog.id.desc()).limit(args.limit).all()
        if not cs:
            print("  (none)")
        for c in cs:
            print(f"  #{c.id:<4} {str(c.started_at)[:19]}  {c.status:<10} "
                  f"proposed={c.strategies_generated} ran={c.backtests_run} "
                  f"candidates={c.candidates_found}"
                  + (f"  ERR: {c.error_message[:60]}" if c.error_message else ""))

        # ── ideas ──
        ideas = s.query(UserIdea).order_by(UserIdea.id.desc()).limit(args.limit).all()
        if ideas:
            print("\n" + "═" * 64)
            print(f"  LATEST IDEAS (max {args.limit})")
            print("═" * 64)
            for i in ideas:
                print(f"  #{i.id:<4} {str(i.user_title or '')[:34]:<34} "
                      f"{str(i.verdict or ''):<24} status={i.status} "
                      f"linked_strategy={i.linked_strategy_id}")
    finally:
        s.close()


def _f(x):
    return f"{x:.2f}" if isinstance(x, (int, float)) else "—"


if __name__ == "__main__":
    main()
