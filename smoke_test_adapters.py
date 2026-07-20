#!/usr/bin/env python
"""Real-environment smoke test for the Layer 2 adapters.

Runs the REAL adapters against the REAL algo_framework + datasea, to prove the
plumbing (argv -> subprocess -> JSON contract) works on actual data before we
build the cognitive agents on top.

Run it on the machine that has the `workbench` conda env and the datasea gold
data:

    conda activate workbench
    cd <prop-agent-system>
    python smoke_test_adapters.py --algo-dir "C:\\Mac\\Home\\Documents\\Repo\\algo_framework" ^
                                  --datasea  "C:\\datasea_data" ^
                                  --table    mt5_ohlcv_ftmo

Paths default to config.yaml [tools] if that file exists, so usually you can just:

    python smoke_test_adapters.py

Pin a specific experiment (otherwise it auto-picks from the gold inventory):

    python smoke_test_adapters.py --strategy BB_RSI_AGGRO --symbol US100.cash --tf 5m

Exit code is non-zero if any check FAILS. SETUP-SKIP (missing instrument spec,
no data, too little history for walk-forward) is reported but does NOT fail the
run — that's a data-setup issue, not an adapter bug.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # make `adapters` importable

from adapters import env_bridge
from adapters.algo_framework_client import AlgoFrameworkClient
from adapters.datasea_client import DataseaClient
from adapters.env_bridge import ToolError

# stderr markers that mean "the tool refused for a data-setup reason", not a bug
_SETUP_MARKERS = (
    "Missing InstrumentSpec", "No data source", "Nessun dato", "Nessun dato caricato",
    "Insufficient data", "Dati insufficienti",
)

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_ICON = {PASS: "✅", FAIL: "❌", SKIP: "⏭️ "}


class Report:
    def __init__(self):
        self.rows: list[tuple[str, str, str]] = []

    def add(self, status: str, name: str, detail: str = ""):
        self.rows.append((status, name, detail))
        line = f"  {_ICON[status]} [{status}] {name}"
        if detail:
            line += f" — {detail}"
        print(line, flush=True)

    def failed(self) -> bool:
        return any(s == FAIL for s, _, _ in self.rows)

    def summary(self):
        n = {PASS: 0, FAIL: 0, SKIP: 0}
        for s, _, _ in self.rows:
            n[s] += 1
        print("\n" + "=" * 60)
        print(f"  SMOKE TEST: {n[PASS]} pass, {n[FAIL]} fail, {n[SKIP]} skip")
        print("=" * 60)
        if self.failed():
            print("  ❌ Adapter plumbing has a problem — see FAIL rows above.")
        elif n[SKIP]:
            print("  ⚠️  Plumbing OK, but some checks were skipped (data setup).")
        else:
            print("  🎉 All good — adapters work end-to-end on real data.")


def _is_setup_error(err: str) -> bool:
    return any(m in err for m in _SETUP_MARKERS)


def _reason(e: ToolError) -> str:
    """Extract the real tool error (last meaningful stderr line) from a ToolError."""
    lines = [ln.strip() for ln in str(e).splitlines() if ln.strip()]
    meaningful = [ln for ln in lines
                  if not ln.startswith("stderr tail") and "failed (rc=" not in ln]
    return meaningful[-1] if meaningful else (lines[0] if lines else "unknown error")


def load_tools_config() -> dict:
    """Read [tools] from config.yaml if present. Best-effort; returns {} otherwise."""
    p = Path(__file__).parent / "config.yaml"
    if not p.is_file():
        return {}
    try:
        import yaml
        with open(p) as f:
            data = yaml.safe_load(f) or {}
        return data.get("tools", {}) or {}
    except Exception as e:  # pragma: no cover
        print(f"  (could not read config.yaml tools: {e})")
        return {}


def introspect_default_config(algo_dir, python_exec, conda_env, strategy) -> dict | None:
    """Fetch a strategy's default_config so we can pick a real key for --params."""
    code = (
        "import sys, json; sys.path.insert(0, '.'); "
        "from core.registry import StrategyRegistry as R; "
        "R.discover('strategies'); s = R.get(%r); "
        "print('DEFCFG:' + json.dumps(getattr(s, 'default_config', {})))" % strategy
    )
    res = env_bridge.run(["-c", code], cwd=algo_dir, timeout=120,
                         python_exec=python_exec, conda_env=conda_env)
    if not res.ok:
        return None
    for line in res.stdout.splitlines():
        s = line.strip()
        if s.startswith("DEFCFG:"):
            import json
            return json.loads(s[len("DEFCFG:"):])
    return None


def pick_scalar_param(cfg: dict):
    """Return (key, override_value) for a numeric config key, or (None, None)."""
    for k, v in cfg.items():
        if isinstance(v, bool):
            continue  # bool is an int subclass — skip to avoid ambiguity
        if isinstance(v, int):
            return k, v + 1
        if isinstance(v, float):
            return k, round(v + 0.5, 4)
    return None, None


def main():
    ap = argparse.ArgumentParser(description="Real smoke test for Layer 2 adapters")
    cfg = load_tools_config()
    ap.add_argument("--algo-dir", default=cfg.get("algo_framework_dir"))
    ap.add_argument("--datasea", default=cfg.get("datasea_data_root"))
    ap.add_argument("--table", default=cfg.get("datasea_table", "mt5_ohlcv_ftmo"))
    ap.add_argument("--python-exec", default=cfg.get("python_exec", "python"))
    ap.add_argument("--conda-env", default=cfg.get("conda_env") or None)
    ap.add_argument("--strategy", default=None)
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--tf", default=None)
    ap.add_argument("--monte-carlo", type=int, default=200)
    args = ap.parse_args()

    if not args.algo_dir or not args.datasea:
        ap.error("--algo-dir and --datasea are required (or provide them in config.yaml [tools]).")

    print("Layer 2 adapters — REAL smoke test")
    print(f"  algo_dir = {args.algo_dir}")
    print(f"  datasea  = {args.datasea}  table={args.table}")
    print(f"  python   = {args.python_exec}  conda_env={args.conda_env or '(none)'}\n")

    algo = AlgoFrameworkClient(
        algo_dir=args.algo_dir, datasea_root=args.datasea, datasea_table=args.table,
        python_exec=args.python_exec, conda_env=args.conda_env,
    )
    sea = DataseaClient(args.datasea, python_exec=args.python_exec, conda_env=args.conda_env)
    r = Report()

    # 1) datasea inventory ----------------------------------------------------
    inventory = []
    print("[1] datasea gold inventory")
    try:
        inventory = sea.list_available()
        if inventory:
            r.add(PASS, "DataseaClient.list_available", f"{len(inventory)} entries")
            h = sea.health()
            print(f"      tables={h['tables']}")
            print(f"      timeframes={h['timeframes']}  symbols={len(h['symbols'])}  bars={h['total_bars']:,}")
        else:
            r.add(SKIP, "DataseaClient.list_available", "gold lake is empty")
    except ToolError as e:
        r.add(FAIL, "DataseaClient.list_available", str(e).splitlines()[0])

    # 2) registry -------------------------------------------------------------
    print("\n[2] algo_framework registry")
    strategies = []
    try:
        strategies = algo.list_strategies()
        if strategies:
            r.add(PASS, "AlgoFrameworkClient.list_strategies", ", ".join(strategies))
        else:
            r.add(FAIL, "AlgoFrameworkClient.list_strategies", "no strategies registered")
    except ToolError as e:
        r.add(FAIL, "AlgoFrameworkClient.list_strategies", str(e).splitlines()[0])

    # 3) choose an experiment -------------------------------------------------
    print("\n[3] choose experiment")
    strategy = args.strategy or (strategies[0] if strategies else None)
    chosen_table = args.table
    if args.symbol and args.tf:
        symbol, tf = args.symbol, args.tf
    elif inventory:
        # Prefer a row from the configured table (that's where the registered
        # strategies' symbols live); else fall back to the first real row and use
        # ITS table — otherwise we'd query a symbol in the wrong table (rc=1).
        rows = [x for x in inventory if not str(x["symbol"]).startswith("(error")]
        row = next((x for x in rows if x["table"] == args.table), None) or (rows[0] if rows else None)
        symbol = args.symbol or (row["symbol"] if row else None)
        tf = args.tf or (row["timeframe"] if row else None)
        chosen_table = row["table"] if row else args.table
    else:
        symbol, tf = args.symbol, args.tf

    # Point the client at the table the chosen symbol actually lives in.
    algo.datasea_table = chosen_table

    if strategy and symbol and tf:
        r.add(PASS, "experiment chosen", f"{strategy} / {symbol} / {tf} (table={chosen_table})")
    else:
        r.add(SKIP, "experiment chosen", "need strategy+symbol+tf (pass --strategy/--symbol/--tf)")
        r.summary()
        sys.exit(1 if r.failed() else 0)

    # 4) run_backtest (baseline) ---------------------------------------------
    print("\n[4] run_backtest (no params)")
    rep = None
    try:
        rep = algo.run_backtest(strategy, symbol=symbol, timeframe=tf)
        checks = [
            (rep.get("schema") == "algo_framework.backtest.v1", "schema"),
            (rep["metadata"].get("timeframe") == tf, "effective timeframe recorded"),
            (rep["metadata"].get("data_start") and rep["metadata"].get("data_end"), "data window recorded"),
            ("gross_profit_usd" in rep["metrics"] and "gross_loss_usd" in rep["metrics"], "gross fields present"),
        ]
        for ok, label in checks:
            r.add(PASS if ok else FAIL, f"backtest: {label}")
        m = rep["metrics"]
        print(f"      trades={m.get('total_trades')}  PF={m.get('profit_factor')}  "
              f"Sharpe={m.get('sharpe_ratio')}  maxDD%={m.get('max_drawdown_pct')}")
    except ToolError as e:
        (r.add(SKIP, "run_backtest", "tool refused: " + _reason(e))
         if _is_setup_error(str(e)) else
         r.add(FAIL, "run_backtest", _reason(e)))

    # 5) --params override reflected -----------------------------------------
    print("\n[5] --params override")
    defcfg = introspect_default_config(args.algo_dir, args.python_exec, args.conda_env, strategy)
    key, val = pick_scalar_param(defcfg or {})
    if not key:
        r.add(SKIP, "--params override", "no scalar key in default_config to tweak")
    else:
        try:
            rep2 = algo.run_backtest(strategy, symbol=symbol, timeframe=tf, params={key: val})
            got = rep2["metadata"]["params"].get(key)
            r.add(PASS if got == val else FAIL, "--params override reflected",
                  f"{key}={got} (sent {val})")
        except ToolError as e:
            (r.add(SKIP, "--params override", "tool refused: " + _reason(e))
             if _is_setup_error(str(e)) else
             r.add(FAIL, "--params override", _reason(e)))

    # 6) fail-hard on unknown --params key -----------------------------------
    print("\n[6] --params fail-hard on unknown key")
    try:
        algo.run_backtest(strategy, symbol=symbol, timeframe=tf, params={"__smoke_bogus__": 1})
        r.add(FAIL, "unknown --params key rejected", "expected ToolError, got success")
    except ToolError as e:
        # must fail because of the unknown key, not a setup issue
        if _is_setup_error(str(e)):
            r.add(SKIP, "unknown --params key rejected", "tool refused earlier (data setup)")
        else:
            r.add(PASS, "unknown --params key rejected", "ToolError raised")

    # 7) run_robustness -------------------------------------------------------
    print("\n[7] run_robustness (walk-forward + Monte Carlo)")
    try:
        rob = algo.run_robustness(strategy, symbol=symbol, timeframe=tf,
                                  monte_carlo=args.monte_carlo)
        checks = [
            (rob.get("schema") == "algo_framework.robustness.v1", "schema"),
            ("walk_forward" in rob, "walk_forward present"),
            (bool(rob["metadata"].get("walk_forward_windows")), "anti-cheat WF windows recorded"),
        ]
        for ok, label in checks:
            r.add(PASS if ok else FAIL, f"robustness: {label}")
        wf = rob.get("walk_forward", {})
        mc = rob.get("monte_carlo") or {}
        print(f"      WF consistency={wf.get('consistency_pct')}%  "
              f"MC prop_pass={mc.get('prop_pass_rate')}%  prob_profit={mc.get('prob_profitable')}%")
    except ToolError as e:
        (r.add(SKIP, "run_robustness", "tool refused: " + _reason(e))
         if _is_setup_error(str(e)) else
         r.add(FAIL, "run_robustness", _reason(e)))

    r.summary()
    sys.exit(1 if r.failed() else 0)


if __name__ == "__main__":
    main()
