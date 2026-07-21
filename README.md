# Quant Research Agent

An **autonomous** quant agent that researches trading strategies by orchestrating
two mature tools: **algo_framework** (backtest, walk-forward, Monte Carlo) and
**datasea** (gold data lake, Delta Lake). It doesn't reinvent the backtester and
no longer writes MQL5 code: *AI as researcher, not as coder*.

Status: **working end-to-end** — the autonomous loop proposes experiments, writes
its own strategies with Claude, backtests/validates them on real data, and
surfaces to the dashboard only the candidates that pass the gate.

---

## What it does (the loop)

```
StrategyResearcher  ── proposes ExperimentPlan (grounded) ─┐
   │  (or a UserIdea approved in the dashboard)            │
   ▼                                                       ▼
ResearchRunner ─(author_brief? → StrategyAuthor writes AI_*.py)─▶ backtest
   │                                                       │
   │                             (if the backtest is worth it)  ▼
   │                                                   robustness (WF + Monte Carlo)
   ▼
ResultEvaluator  ── deterministic gate, robustness MANDATORY ──▶ APPROVE / REVIEW / REJECT
   ▼
ResearchLoop  ── persistent history + on_outcome ──▶ DB ──▶ Dashboard (approve/reject)
```

Each cycle the orchestrator: (1) processes approved user ideas, (2) has the
researcher propose N experiments, (3) runs and judges them, (4) persists the
outcomes to the DB.

---

## Architecture

**Layer 2 — adapters (`adapters/`)**: talk to the tools via subprocess, no direct imports.
- `env_bridge` — subprocess runner (UTF-8/timeout), `JSON_EXPORT:` parsing, `is_setup_error`
- `algo_framework_client` — `list_strategies` / `get_strategy_info` / `run_backtest` / `run_robustness` (read the `--export-json` contract JSON)
- `datasea_client` + `scan_gold` — gold inventory (symbols/TF/dates/spread)

**Layer 3 — cognitive agents (`agents/`)**:
- `researcher` — proposes **grounded** `ExperimentPlan`s (AI_* strategy, symbol/TF ∈ inventory, params ⊆ config, WF sized to the span); can propose new strategies (`author_new`)
- `strategy_author` — from a brief, Claude writes a strategy conforming to `_template.py`, validated with `ast.parse` + safety AST + real **dry-import**, saved to `strategies/ai_generated/AI_*.py`
- `research_runner` — runs a plan: (author →) backtest → (if worth it) robustness → outcome
- `result_evaluator` — deterministic gate on `validation_criteria` + `robustness_gate` (robustness mandatory for APPROVE)
- `research_loop` — the cycle with persistent history (`experiment_results/history.jsonl`)
- `idea_evaluator` — evaluates ideas submitted from the dashboard (devil's advocate)

**Orchestrator / interface**:
- `orchestrator.py` — builds everything and runs the loop (`--once` or scheduled)
- `dashboard/api.py` — FastAPI: candidates with Approve/Reject, backtests, cycles, submit ideas (`/ideas`)
- `db/` — SQLAlchemy (`models`, `database`) + `mapping` (ExperimentOutcome → DB rows)

**Supporting tools (`research_cli.py`, `collaudo_db.py`, `smoke_test_adapters.py`)**: single
run, DB inspection, adapter smoke test.

---

## Environment

Three repos, **one conda env `workbench`** (`algo_framework/workbench-environment.yml`):
datasea is pip-installed, algo_framework runs from its own folder. Prod = Windows.

Prerequisite: in the **algo_framework** repo the machine-readable contract must be
active (`--export-json`, `--params`), otherwise the adapters fail.

---

## Setup

```bash
conda activate workbench
cd prop-agent-system
pip install -r requirements.txt          # agent deps (lightweight: no pandas/numpy/MT5)
cp config.example.yaml config.yaml       # then fill in the values
```

`config.yaml` — main sections:
- `claude.api_key` — used by researcher, author, narrative
- `tools` — `algo_framework_dir`, `datasea_data_root`, `datasea_table`, `python_exec`, `conda_env`
- `orchestrator` — `cycle_hours`, `max_experiments_per_cycle`, `only_ai_strategies`
- `validation_criteria` + `robustness_gate` — the gate thresholds
- `prop` — prop firm context for the idea evaluator

---

## Usage

```bash
# One test cycle
python orchestrator.py --once

# Scheduled 24/7 loop
python orchestrator.py

# Dashboard (candidates, backtests, cycles, ideas)
uvicorn dashboard.api:app --host 0.0.0.0 --port 8000     # http://localhost:8000

# Inspect the DB without the dashboard
python collaudo_db.py

# A single experiment on a specific strategy (console, saves JSON)
python research_cli.py --strategy NAME --symbol SYM --tf TF \
    --algo-dir ... --datasea ... --table ...

# Adapter smoke test on real data
python smoke_test_adapters.py --algo-dir ... --datasea ... --table ...
```

---

## Guarantees

- 🔒 **It doesn't touch your strategies.** With `only_ai_strategies: true` (default)
  the agent works ONLY on strategies it generates (`AI_*`); if there are none, it
  creates them. It writes exclusively to `strategies/ai_generated/AI_*.py`
  (versioned, never overwritten/deleted). Backtesting is read-only (params at runtime).
- 🛡️ **Robustness is mandatory** for an APPROVE — the defense against over-optimisation.
- 🧪 **Author safety**: `ast.parse` + import whitelist (no os/subprocess/eval/open) +
  real dry-import before a generated strategy can run.
- ♻️ **It learns**: persistent history; avoids (strategy/symbol) combos that ERROR.

---

## Status & TODO

Done and tested live: adapters, researcher, author (Claude writes valid
strategies), runner, evaluator, loop, orchestrator, DB persistence, dashboard.

To do / improvable:
- [ ] scheduled 24/7 run (`start()`, only `--once` tested live)
- [ ] **datasea data engineer**: ingest missing data (today the agent only *reads* the gold) — the one half of the design still missing
- [ ] retention/archive of AI strategies that never produce an APPROVE
- [ ] candidate notifications (the `on_candidate` hook exists, not wired)
- [ ] timeframe-awareness; inject target symbol/TF into the author brief

Data side (user): align strategies ↔ InstrumentSpec in `algo_framework/instruments.py`.
