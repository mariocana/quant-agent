#!/usr/bin/env python
"""Run ONE real experiment end-to-end and print the verdict.

The whole chain in one command: datasea inventory -> algo_framework backtest ->
(if worth it) robustness -> ResultEvaluator gate -> APPROVE/REVIEW/REJECT.

    conda activate workbench
    cd <prop-agent-system>
    python research_cli.py --strategy BB_RSI_AGGRO --symbol US100.cash --tf 5m
    python research_cli.py --strategy BB_RSI_AGGRO --params "{\"bb_period\": 21}"

Paths and criteria default to config.yaml ([tools], validation_criteria,
robustness_gate). If --symbol/--tf are omitted, they're auto-picked from the
gold inventory. The full outcome (backtest + robustness + verdict) is saved as
JSON under experiment_results/.

Exit code: 0 for a verdict (incl. REJECT), 2 if the tool couldn't run (ERROR).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from adapters.algo_framework_client import AlgoFrameworkClient
from adapters.datasea_client import DataseaClient
from adapters.env_bridge import ToolError
from agents.result_evaluator import ResultEvaluator, APPROVE, REVIEW, REJECT
from agents.research_runner import ResearchRunner, ExperimentPlan, ERROR

_ICON = {APPROVE: "🟢", REVIEW: "🟡", REJECT: "🔴", ERROR: "⚠️ "}


def load_config() -> dict:
    p = Path(__file__).parent / "config.yaml"
    if not p.is_file():
        return {}
    try:
        import yaml
        with open(p) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"  (could not read config.yaml: {e})")
        return {}


def span_months(row: dict | None) -> int | None:
    if not row:
        return None
    try:
        s = date.fromisoformat(str(row["start"])[:10])
        e = date.fromisoformat(str(row["end"])[:10])
        return max(0, (e.year - s.year) * 12 + (e.month - s.month))
    except Exception:
        return None


def fit_wf(months: int | None) -> tuple[int, int, int]:
    if not months or months >= 8:
        return 6, 2, 2
    test = 1
    return max(2, months - test - 1), test, 1


def main():
    cfg = load_config()
    tools = cfg.get("tools", {}) or {}

    ap = argparse.ArgumentParser(description="Run one real experiment end-to-end")
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--tf", default=None)
    ap.add_argument("--params", default=None, help='JSON overrides, e.g. {"bb_period": 21}')
    ap.add_argument("--monte-carlo", type=int, default=1000)
    ap.add_argument("--enforce-prop", action="store_true",
                    help="run the backtest WITH prop enforcement (diagnostics, not edge)")
    ap.add_argument("--algo-dir", default=tools.get("algo_framework_dir"))
    ap.add_argument("--datasea", default=tools.get("datasea_data_root"))
    ap.add_argument("--table", default=tools.get("datasea_table", "mt5_ohlcv_ftmo"))
    ap.add_argument("--python-exec", default=tools.get("python_exec", "python"))
    ap.add_argument("--conda-env", default=tools.get("conda_env") or None)
    ap.add_argument("--out-dir", default="experiment_results")
    args = ap.parse_args()

    if not args.algo_dir or not args.datasea:
        ap.error("--algo-dir and --datasea are required (or set them in config.yaml [tools]).")

    params = None
    if args.params:
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as e:
            ap.error(f"--params is not valid JSON: {e}")

    algo = AlgoFrameworkClient(
        algo_dir=args.algo_dir, datasea_root=args.datasea, datasea_table=args.table,
        python_exec=args.python_exec, conda_env=args.conda_env,
    )
    sea = DataseaClient(args.datasea, python_exec=args.python_exec, conda_env=args.conda_env)
    evaluator = ResultEvaluator(
        criteria=cfg.get("validation_criteria"),
        robustness_gate=cfg.get("robustness_gate"),
    )

    print("Quant research — one experiment")
    print(f"  strategy = {args.strategy}")

    # ── inventory: auto-pick symbol/tf/table + span for walk-forward sizing ──
    chosen_row = None
    symbol, tf, table = args.symbol, args.tf, args.table
    try:
        inventory = sea.list_available()
    except ToolError as e:
        inventory = []
        print(f"  ⚠️  datasea inventory unavailable ({str(e).splitlines()[0]}) — "
              f"need --symbol/--tf pinned.")

    if args.symbol and args.tf:
        chosen_row = next((x for x in inventory
                           if x.get("symbol") == symbol and x.get("timeframe") == tf), None)
    elif inventory:
        rows = [x for x in inventory if not str(x["symbol"]).startswith("(error")]
        row = next((x for x in rows if x["table"] == args.table), None) or (rows[0] if rows else None)
        if row:
            chosen_row, symbol, tf, table = row, row["symbol"], row["timeframe"], row["table"]

    if not symbol or not tf:
        ap.error("could not determine symbol/tf — pass --symbol and --tf.")

    algo.datasea_table = table
    span = span_months(chosen_row)
    wf_train, wf_test, wf_step = fit_wf(span)

    plan = ExperimentPlan(
        strategy=args.strategy, symbol=symbol, timeframe=tf, params=params,
        monte_carlo=args.monte_carlo, wf_train=wf_train, wf_test=wf_test, wf_step=wf_step,
        enforce_prop=args.enforce_prop,
    )
    print(f"  symbol   = {symbol}   tf = {tf}   table = {table}")
    print(f"  params   = {params or '(strategy defaults)'}")
    if span is not None:
        print(f"  data span ≈ {span} months -> wf {wf_train}m/{wf_test}m/{wf_step}m, MC {args.monte_carlo}")
    print("\nRunning… (backtest, then robustness if the backtest is worth it)\n")

    outcome = ResearchRunner(algo, evaluator).run(plan)

    # ── report ──
    print("=" * 64)
    print(f"  {_ICON.get(outcome.verdict, '')} VERDICT: {outcome.verdict}"
          + (f"   (score {outcome.analysis.score})" if outcome.analysis else ""))
    print("=" * 64)

    if outcome.verdict == ERROR:
        print(f"  The tool could not run: {outcome.error}")
        print("  This is a data/setup issue, not a judgment on the strategy.")

    if outcome.analysis:
        print("  Checks:")
        for ck in outcome.analysis.checks:
            print(f"    {'✅' if ck.passed else '❌'} {ck.describe()}")
        print("  Reasons:")
        for why in outcome.analysis.reasons:
            print(f"    • {why}")
        if outcome.analysis.monte_carlo_confidence is not None:
            print(f"  Monte Carlo confidence (prop pass): {outcome.analysis.monte_carlo_confidence}%")
    if outcome.error and outcome.verdict != ERROR:
        print(f"  Note: {outcome.error}")

    if outcome.backtest:
        m = outcome.backtest.get("metrics", {})
        md = outcome.backtest.get("metadata", {})
        print(f"  Backtest: trades={m.get('total_trades')} PF={m.get('profit_factor')} "
              f"Sharpe={m.get('sharpe_ratio')} maxDD%={m.get('max_drawdown_pct')}")
        print(f"  Data window: {md.get('data_start')} → {md.get('data_end')} "
              f"({md.get('data_bars')} bars)")

    # ── persist the full outcome ──
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = out_dir / f"{args.strategy}_{symbol}_{tf}_{ts}.json".replace("/", "-")
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(outcome.to_dict(), f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  saved → {fname.resolve()}")

    sys.exit(2 if outcome.verdict == ERROR else 0)


if __name__ == "__main__":
    main()
