"""Map an ExperimentOutcome to plain row dicts for the DB models.

Kept dependency-free (no sqlalchemy) so the mapping — which the dashboard depends
on — is unit-testable. The orchestrator turns these dicts into ORM rows via
Model(**dict); every key here must match a column on the corresponding model.

Dashboard-safety: numeric fields the dashboard calls .toFixed() on are coalesced
to floats (never None), and profit_factor's inf case (no losses) maps to a large
sentinel instead of None.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from agents.research_runner import ExperimentOutcome, APPROVE, REVIEW

PF_INF_SENTINEL = 9999.99  # "no losses" — a real infinite PF, shown as a big number


def _dt(s) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d.replace(tzinfo=None)
    except ValueError:
        return datetime.now(timezone.utc)


def _num(x, default=0.0) -> float:
    return default if x is None else x


def _display_pf(m: dict) -> float:
    pf = m.get("profit_factor")
    if pf is None:
        gp, gl = m.get("gross_profit_usd"), m.get("gross_loss_usd")
        return PF_INF_SENTINEL if (gl == 0 and gp and gp > 0) else 0.0
    return pf


def outcome_to_rows(outcome: ExperimentOutcome) -> Optional[dict]:
    """Return {"strategy":..., "backtest":..., "candidate":...|None} or None.

    None when there's nothing to persist (ERROR outcome with no backtest)."""
    if not outcome.backtest:
        return None

    p = outcome.plan
    m = outcome.backtest.get("metrics", {}) or {}
    md = outcome.backtest.get("metadata", {}) or {}
    wf = ((outcome.robustness or {}).get("walk_forward") or {}) if outcome.robustness else {}
    wf_consistency = wf.get("consistency_pct")

    strategy = {
        "profile": "auto",
        "source": "auto",
        "name": p.strategy,
        "hypothesis": p.rationale or "existing-strategy experiment",
        "strategy_type": "existing",
        "symbol": p.symbol or "?",
        "timeframe": p.timeframe or "?",
        "parameters": p.params or {},
    }

    backtest = {
        "date_from": _dt(md.get("data_start")),
        "date_to": _dt(md.get("data_end")),
        "final_balance": _num(m.get("final_balance")),
        "net_profit": _num(m.get("total_pnl_usd")),
        "profit_factor": _display_pf(m),
        "sharpe_ratio": _num(m.get("sharpe_ratio")),
        "max_drawdown_pct": _num(m.get("max_drawdown_pct")),
        "total_trades": int(_num(m.get("total_trades"))),
        "winning_trades": int(_num(m.get("wins"))),
        "losing_trades": int(_num(m.get("losses"))),
        "win_rate": _num(m.get("winrate_pct")) / 100.0,
        "max_consecutive_losses": int(_num(m.get("max_consecutive_losses"))),
        "passes_prop_rules": outcome.verdict == APPROVE,
        # model comment says 0-1; consistency_pct is 0-100. None if no robustness.
        "walk_forward_score": (wf_consistency / 100.0) if wf_consistency is not None else None,
        "walk_forward_results": outcome.robustness,
    }

    candidate = None
    if outcome.verdict in (APPROVE, REVIEW):
        an = outcome.analysis
        candidate = {
            "overall_score": (an.score if an else None),
            "ai_analysis": ("\n".join(an.reasons) if an and an.reasons else None),
            "recommendation": outcome.verdict,
            "status": "pending",
            "notified_at": datetime.now(timezone.utc),
        }

    return {"strategy": strategy, "backtest": backtest, "candidate": candidate}
