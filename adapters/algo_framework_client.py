"""AlgoFrameworkClient — talk to algo_framework via its CLIs.

Runs backtester.py / robustness.py as subprocesses from the algo_framework
folder and returns the machine-readable summary they export
(--export-json → `{schema, generated_at, metadata, metrics|walk_forward|...}`).

The argv-building methods (`backtest_args` / `robustness_args`) are separated
from execution so they can be unit-tested without a running framework.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from adapters import env_bridge
from adapters.env_bridge import ToolError

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover
    import logging
    logger = logging.getLogger("adapters.algo_framework_client")

_STRAT_PREFIX = "STRATEGIES_JSON:"
_DEFCFG_PREFIX = "DEFCFG:"


class AlgoFrameworkClient:
    def __init__(
        self,
        algo_dir: str,
        datasea_root: str,
        datasea_table: str = "mt5_ohlcv_ftmo",
        python_exec: str = "python",
        conda_env: Optional[str] = None,
        backtest_timeout_s: float = 1800,
    ):
        self.algo_dir = Path(algo_dir)
        self.datasea_root = str(datasea_root)
        self.datasea_table = datasea_table
        self.python_exec = python_exec
        self.conda_env = conda_env or None
        self.timeout = backtest_timeout_s
        self._strategies_cache: Optional[list[str]] = None

    # ── execution helper ──────────────────────────────────────────────
    def _run(self, script_args, timeout=None) -> env_bridge.CommandResult:
        return env_bridge.run(
            script_args, cwd=self.algo_dir, timeout=timeout or self.timeout,
            python_exec=self.python_exec, conda_env=self.conda_env,
        )

    # ── argv builders (pure, testable) ────────────────────────────────
    def backtest_args(self, strategy, symbol=None, timeframe=None, params=None,
                      enforce_prop=False, out_json=None, table=None) -> list[str]:
        args = ["backtester.py", "--strategy", strategy,
                "--datasea", self.datasea_root,
                "--datasea-table", table or self.datasea_table]
        if symbol:
            args += ["--symbol", symbol]
        if timeframe:
            args += ["--tf", timeframe]
        if params:
            args += ["--params", json.dumps(params, separators=(",", ":"))]
        if not enforce_prop:
            args += ["--no-prop"]
        # --export-json has nargs="?"; bare token => tool's default path.
        args += ["--export-json"] + ([out_json] if out_json else [])
        return args

    def robustness_args(self, strategy, symbol=None, timeframe=None, params=None,
                        monte_carlo=1000, wf_train=6, wf_test=2, wf_step=2,
                        out_json=None, table=None) -> list[str]:
        args = ["robustness.py", "--strategy", strategy,
                "--datasea", self.datasea_root,
                "--datasea-table", table or self.datasea_table,
                "--monte-carlo", str(monte_carlo),
                "--wf-train", str(wf_train), "--wf-test", str(wf_test),
                "--wf-step", str(wf_step)]
        if symbol:
            args += ["--symbol", symbol]
        if timeframe:
            args += ["--tf", timeframe]
        if params:
            args += ["--params", json.dumps(params, separators=(",", ":"))]
        args += ["--export-json"] + ([out_json] if out_json else [])
        return args

    # ── public API ────────────────────────────────────────────────────
    def list_strategies(self, refresh: bool = False) -> list[str]:
        """Registry discovery via introspection (not stdout scraping of --list)."""
        if self._strategies_cache is not None and not refresh:
            return self._strategies_cache
        code = (
            "import sys, json; sys.path.insert(0, '.'); "
            "from core.registry import StrategyRegistry; "
            "StrategyRegistry.discover('strategies'); "
            f"print('{_STRAT_PREFIX}' + json.dumps(StrategyRegistry.list_strategies()))"
        )
        res = self._run(["-c", code], timeout=120)
        if not res.ok:
            raise ToolError(f"list_strategies failed:\n{res.stderr[-2000:]}")
        names = _parse_strategies(res.stdout)
        if names is None:
            raise ToolError(f"could not parse strategy list from:\n{res.stdout[-2000:]}")
        self._strategies_cache = names
        return names

    def get_default_config(self, strategy: str) -> dict:
        """Introspect a strategy's default_config (the valid keys for --params)."""
        code = (
            "import sys, json; sys.path.insert(0, '.'); "
            "from core.registry import StrategyRegistry as R; "
            "R.discover('strategies'); s = R.get(%r); "
            "print('%s' + json.dumps(getattr(s, 'default_config', {})))"
            % (strategy, _DEFCFG_PREFIX)
        )
        res = self._run(["-c", code], timeout=120)
        if not res.ok:
            raise ToolError(f"get_default_config({strategy}) failed:\n{res.stderr[-2000:]}")
        for line in res.stdout.splitlines():
            s = line.strip()
            if s.startswith(_DEFCFG_PREFIX):
                return json.loads(s[len(_DEFCFG_PREFIX):])
        raise ToolError(f"could not parse default_config from:\n{res.stdout[-2000:]}")

    def run_backtest(self, strategy, symbol=None, timeframe=None, params=None,
                     enforce_prop=False, out_json=None, timeout=None, table=None) -> dict:
        """Run a single backtest. Returns the parsed summary JSON.
        Raises ToolError on failure (non-zero exit, timeout, unknown --params key)."""
        args = self.backtest_args(strategy, symbol, timeframe, params,
                                  enforce_prop, out_json, table=table)
        res = self._run(args, timeout=timeout)
        if not res.ok:
            raise ToolError(_fmt_fail("backtest", strategy, res))
        return env_bridge.read_json_result(res)

    def run_robustness(self, strategy, symbol=None, timeframe=None, params=None,
                       monte_carlo=1000, wf_train=6, wf_test=2, wf_step=2,
                       out_json=None, timeout=None, table=None) -> dict:
        """Run walk-forward + Monte Carlo. Returns the parsed robustness JSON."""
        args = self.robustness_args(strategy, symbol, timeframe, params,
                                    monte_carlo, wf_train, wf_test, wf_step, out_json,
                                    table=table)
        res = self._run(args, timeout=timeout)
        if not res.ok:
            raise ToolError(_fmt_fail("robustness", strategy, res))
        return env_bridge.read_json_result(res)


def _parse_strategies(stdout: str) -> Optional[list[str]]:
    for line in stdout.splitlines():
        s = line.strip()
        if s.startswith(_STRAT_PREFIX):
            return json.loads(s[len(_STRAT_PREFIX):])
    return None


def _fmt_fail(kind: str, strategy: str, res: env_bridge.CommandResult) -> str:
    return (
        f"{kind} for '{strategy}' failed (rc={res.returncode}, "
        f"timed_out={res.timed_out}, {res.duration_s}s).\n"
        f"stderr tail:\n{res.stderr[-2000:]}"
    )
